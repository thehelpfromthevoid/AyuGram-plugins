//! Parsing a zapret `winws.exe` command line into rules.
//!
//! A zapret invocation is one global capture filter (`--wf-tcp` / `--wf-udp`)
//! followed by a sequence of rules separated by `--new`. Each rule pairs a match
//! filter (`--filter-tcp`, `--hostlist`, `--ipset`, ...) with a desync strategy
//! (`--dpi-desync*`). The vocabulary is closed and shown by the shipped
//! `general*.bat` files, so this module is a direct translation of that grammar
//! rather than a guess.
//!
//! Values are taken from real lines, e.g.
//!
//! ```text
//! --wf-tcp=80,443 --filter-tcp=443 --hostlist="list-google.txt" --ip-id=zero
//!   --dpi-desync=multisplit --dpi-desync-split-seqovl=681
//!   --dpi-desync-split-pos=1 --dpi-desync-split-seqovl-pattern="tls.bin"
//! ```
//!
//! Unknown keys are collected in `Rule::unknown` instead of aborting the parse:
//! a newer zapret adding an option should degrade to "that one option is off",
//! not to a client that cannot start.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::net::IpAddr;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::UNIX_EPOCH;

use crate::dpi::{Desync, FakeTls, SplitPos, Strategy};

/// A single port or an inclusive range.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PortRange {
    pub lo: u16,
    pub hi: u16,
}

impl PortRange {
    pub fn contains(&self, port: u16) -> bool {
        (self.lo..=self.hi).contains(&port)
    }
}

/// A comma-separated port list, possibly with `lo-hi` ranges.
pub type PortList = Vec<PortRange>;

/// One `address/prefix` block from an `--ipset` file. A bare address is accepted
/// and treated as a host route (`/32` or `/128`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cidr {
    pub addr: IpAddr,
    pub prefix: u8,
}

impl Cidr {
    /// Parses `91.108.56.0/22`, `149.154.160.0/20` or `2001:b28:f23d::/48`.
    pub fn parse(text: &str) -> Option<Self> {
        let text = text.trim();
        if text.is_empty() {
            return None;
        }
        match text.split_once('/') {
            Some((addr, prefix)) => {
                let addr: IpAddr = addr.trim().parse().ok()?;
                let prefix: u8 = prefix.trim().parse().ok()?;
                let bits = if addr.is_ipv4() { 32 } else { 128 };
                if prefix > bits {
                    return None;
                }
                Some(Self { addr, prefix })
            }
            None => {
                let addr: IpAddr = text.parse().ok()?;
                let prefix = if addr.is_ipv4() { 32 } else { 128 };
                Some(Self { addr, prefix })
            }
        }
    }

    /// True when `ip` is inside this block. Families must match.
    pub fn contains(&self, ip: IpAddr) -> bool {
        match (self.addr, ip) {
            (IpAddr::V4(net), IpAddr::V4(ip)) => {
                let bits = self.prefix.min(32) as u32;
                let mask = if bits == 0 {
                    0
                } else {
                    u32::MAX << (32 - bits)
                };
                (u32::from(net) & mask) == (u32::from(ip) & mask)
            }
            (IpAddr::V6(net), IpAddr::V6(ip)) => {
                let bits = self.prefix.min(128) as u32;
                let mask = if bits == 0 {
                    0
                } else {
                    u128::MAX << (128 - bits)
                };
                (u128::from(net) & mask) == (u128::from(ip) & mask)
            }
            _ => false,
        }
    }
}

/// Parses `80,443,19294-19344` into ranges. A malformed or empty element makes
/// the whole list fail, exactly as zapret rejects the line, so a typo cannot
/// silently drop an element and widen or narrow the filter.
pub fn parse_ports(text: &str) -> Option<PortList> {
    let mut out = Vec::new();
    for part in text.split(',') {
        let part = part.trim();
        if part.is_empty() {
            return None;
        }
        if let Some((lo, hi)) = part.split_once('-') {
            let lo: u16 = lo.trim().parse().ok()?;
            let hi: u16 = hi.trim().parse().ok()?;
            if lo > hi {
                return None;
            }
            out.push(PortRange { lo, hi });
        } else {
            let p: u16 = part.parse().ok()?;
            out.push(PortRange { lo: p, hi: p });
        }
    }
    Some(out)
}

/// Parses a zapret signed 32-bit network-order increment: `-10000`, `1000`,
/// `0x10`, or `-0x10`. The result is the two's-complement bit pattern, so a
/// later add to a sequence wraps exactly as `net32_add` does in zapret.
fn parse_signed_net32(text: &str) -> Option<u32> {
    let text = text.trim();
    let (negative, body) = match text.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, text),
    };
    let magnitude = match body.strip_prefix("0x").or_else(|| body.strip_prefix("0X")) {
        Some(hex) => u32::from_str_radix(hex, 16).ok()?,
        None => body.parse::<i32>().ok()? as u32,
    };
    let value = if negative {
        magnitude.wrapping_neg()
    } else {
        magnitude
    };
    Some(value)
}

/// The global WinDivert capture filter.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Capture {
    pub tcp: PortList,
    pub udp: PortList,
}

/// The match filter of one rule. Paths are kept as written; the caller resolves
/// them against its list directory, exactly as zapret does with `%LISTS%`.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Filter {
    pub tcp: PortList,
    pub udp: PortList,
    pub l3: Vec<String>,
    pub l7: Vec<String>,
    pub hostlist: Vec<String>,
    pub hostlist_domains: Vec<String>,
    pub hostlist_exclude: Vec<String>,
    pub ipset: Vec<String>,
    pub ipset_exclude: Vec<String>,
    /// `--ip-id=zero`.
    pub ip_id_zero: bool,
    /// Set when a `--filter-tcp` / `--filter-udp` value was present but did not
    /// parse. Such a rule must match nothing: treating the failed list as empty
    /// would turn a typo into "any port" in [`Filter::matches_tcp_connection`].
    pub ports_malformed: bool,
}

impl Filter {
    /// True when the filter names nothing to match on. Such a rule would apply
    /// to every captured packet; zapret treats that as "the whole capture".
    pub fn is_empty(&self) -> bool {
        self.tcp.is_empty()
            && self.udp.is_empty()
            && self.l3.is_empty()
            && self.l7.is_empty()
            && self.hostlist.is_empty()
            && self.hostlist_domains.is_empty()
            && self.ipset.is_empty()
    }

    /// True when `port` is matched on TCP (empty tcp list means "any port",
    /// unless the filter value failed to parse, which matches nothing).
    pub fn matches_tcp_port(&self, port: u16) -> bool {
        if self.ports_malformed {
            return false;
        }
        self.tcp.is_empty() || self.tcp.iter().any(|r| r.contains(port))
    }

    /// True when this rule's transport scope covers a TCP connection to `port`.
    ///
    /// A rule that names only UDP ports is UDP-scoped and must never be chosen
    /// for a TCP stream (the userspace proxy desyncs a SOCKS5 `CONNECT`, which
    /// is TCP). A rule with no port filter at all still applies to every TCP
    /// port, exactly as it does in zapret. A rule whose `--filter-tcp`/`--filter-udp`
    /// value was present but did not parse matches no connection: an empty list
    /// would otherwise be read as "any port" and a typo would widen the rule.
    pub fn matches_tcp_connection(&self, port: u16) -> bool {
        if self.ports_malformed {
            return false;
        }
        if self.tcp.is_empty() {
            self.udp.is_empty()
        } else {
            self.tcp.iter().any(|r| r.contains(port))
        }
    }
}

/// One `--new`-separated rule.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rule {
    pub filter: Filter,
    pub strategy: Strategy,
    /// Keys this parser does not understand, kept for diagnostics.
    pub unknown: Vec<String>,
}

/// A whole command line.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Config {
    pub capture: Capture,
    pub rules: Vec<Rule>,
    /// Contents of the `--hostlist` / `--ipset` files, read once by
    /// [`Config::resolve_lists`]. Empty until resolved, so `parse_line` stays a
    /// pure function of its text.
    pub lists: ListStore,
}

impl Config {
    /// Reads every file named by a rule's `--hostlist*` / `--ipset*` filter,
    /// resolving relative names against `base`, exactly as zapret resolves
    /// `%LISTS%` against its own directory.
    ///
    /// A file that cannot be read is recorded as unread rather than ignored: a
    /// rule gated on a list that is missing must not silently start matching
    /// every host.
    pub fn resolve_lists(&mut self, base: &Path) {
        let mut wanted: Vec<String> = Vec::new();
        for rule in &self.rules {
            let f = &rule.filter;
            wanted.extend(f.hostlist.iter().cloned());
            wanted.extend(f.hostlist_exclude.iter().cloned());
            wanted.extend(f.ipset.iter().cloned());
            wanted.extend(f.ipset_exclude.iter().cloned());
        }
        self.lists = ListStore::load(wanted.iter().map(|name| (name.clone(), base.join(name))));
    }

    /// Picks the strategy for a TCP connection to `host:port`.
    ///
    /// The port and the host gate are consulted. A rule with no host gate
    /// matches any host on its ports; a rule gated by `--hostlist`/`--ipset`
    /// matches only a host the resolved list covers; a UDP-only rule never
    /// matches a TCP stream.
    pub fn strategy_for(&self, host: &str, port: u16) -> Option<&Strategy> {
        self.rules
            .iter()
            .find(|r| {
                r.filter.matches_tcp_connection(port) && self.host_gate_matches(&r.filter, host)
            })
            .map(|r| &r.strategy)
    }

    /// True when the rule's host gate admits `host`.
    ///
    /// The gate is a union of positive restrictions (an inline
    /// `--hostlist-domains`, an `--hostlist` file, an `--ipset` file when the
    /// host is an IP literal) minus any `--hostlist-exclude` / `--ipset-exclude`
    /// match. A rule with no restriction at all admits every host. A restriction
    /// the engine cannot evaluate on a TCP/TLS stream (an L7 protocol name, an
    /// unreadable list file) does not admit the host: the rule is skipped rather
    /// than widened.
    fn host_gate_matches(&self, filter: &Filter, host: &str) -> bool {
        if self.host_excluded(filter, host) {
            return false;
        }
        let has_inline = !filter.hostlist_domains.is_empty();
        let has_hostlist = !filter.hostlist.is_empty();
        let has_l7 = !filter.l7.is_empty();
        let has_ipset = !filter.ipset.is_empty();
        if !has_inline && !has_hostlist && !has_l7 && !has_ipset {
            return true;
        }
        if has_inline && matches_any_host(host, &filter.hostlist_domains) {
            return true;
        }
        if has_hostlist
            && filter
                .hostlist
                .iter()
                .any(|p| self.lists.matches_domain(host, p))
        {
            return true;
        }
        if has_ipset
            && let Ok(ip) = host.parse::<IpAddr>()
            && filter.ipset.iter().any(|p| self.lists.matches_ip(ip, p))
        {
            return true;
        }
        false
    }

    /// True when an exclusion list names `host`, which removes the rule.
    ///
    /// `--hostlist-exclude` and `--ipset-exclude` name files, so only the file
    /// contents are consulted; the path itself is not a domain pattern.
    fn host_excluded(&self, filter: &Filter, host: &str) -> bool {
        if filter
            .hostlist_exclude
            .iter()
            .any(|p| self.lists.matches_domain(host, p))
        {
            return true;
        }
        if let Ok(ip) = host.parse::<IpAddr>()
            && filter
                .ipset_exclude
                .iter()
                .any(|p| self.lists.matches_ip(ip, p))
        {
            return true;
        }
        false
    }
}

/// The parsed contents of every list file a config names.
///
/// Each file is interpreted on read: a line that parses as an address block
/// becomes a [`Cidr`], anything else as a domain pattern. The same store serves
/// both `--hostlist` (domains) and `--ipset` (blocks), because a rule only ever
/// asks one of the two questions of a given path.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ListStore {
    files: std::collections::BTreeMap<String, Arc<FileList>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
struct FileList {
    domains: HashSet<String>,
    nets: Vec<Cidr>,
    unread: bool,
}

/// One parsed list file held in the process-wide cache: the parsed contents and
/// the file signature they were parsed from.
struct CachedList {
    signature: FileSignature,
    list: Arc<FileList>,
}

/// Identity of a file: its size and modification time. A file that still carries
/// the same signature is not re-read, so resolving the same config repeatedly
/// (the FFI does, once per call) does not re-parse a multi-megabyte list every
/// time. Any change to the file changes the signature and forces a re-read.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct FileSignature {
    len: u64,
    modified: Option<(u64, u32)>,
}

fn list_cache() -> &'static Mutex<HashMap<PathBuf, CachedList>> {
    static CACHE: OnceLock<Mutex<HashMap<PathBuf, CachedList>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn file_signature(path: &Path) -> Option<FileSignature> {
    let meta = fs::metadata(path).ok()?;
    let modified = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| (d.as_secs(), d.subsec_nanos()));
    Some(FileSignature {
        len: meta.len(),
        modified,
    })
}

/// Returns the parsed contents of one list file, re-reading it only when its
/// signature changed. A file that cannot be read is not cached: it is cheap to
/// retry, and a file that appears later must be noticed.
fn parse_cached(path: &Path) -> Arc<FileList> {
    // The cache key must not alias one file with another, so relative paths are
    // resolved to an absolute one. An already-absolute path is its own key,
    // which saves a `canonicalize` on the common FFI path (the base is the
    // process working directory, so the joined path is absolute).
    let key = if path.is_absolute() {
        path.to_path_buf()
    } else {
        fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
    };
    let signature = file_signature(path);
    if let Some(sig) = signature {
        let guard = list_cache().lock().unwrap_or_else(|e| e.into_inner());
        if let Some(entry) = guard.get(&key)
            && entry.signature == sig
        {
            return entry.list.clone();
        }
    }
    let list = Arc::new(read_file(path));
    if let (Some(sig), false) = (signature, list.unread) {
        let mut guard = list_cache().lock().unwrap_or_else(|e| e.into_inner());
        guard.insert(
            key,
            CachedList {
                signature: sig,
                list: list.clone(),
            },
        );
    }
    list
}

/// Reads and interprets one list file. Each line is a comment (`#`), a blank, a
/// `address/prefix` block, or a domain pattern.
fn read_file(path: &Path) -> FileList {
    let contents = fs::read_to_string(path)
        .or_else(|_| fs::read(path).map(|bytes| String::from_utf8_lossy(&bytes).into_owned()));
    match contents {
        Ok(text) => {
            let mut file = FileList::default();
            for line in text.lines() {
                let line = line.trim().trim_start_matches('\u{feff}');
                if line.is_empty() || line.starts_with('#') {
                    continue;
                }
                // A trailing comment is not part of the entry.
                let line = line.split('#').next().unwrap_or("").trim();
                if line.is_empty() {
                    continue;
                }
                if let Some(net) = Cidr::parse(line) {
                    file.nets.push(net);
                } else {
                    file.domains
                        .insert(line.trim_start_matches('.').to_ascii_lowercase());
                }
            }
            file
        }
        Err(_) => FileList {
            unread: true,
            ..FileList::default()
        },
    }
}

impl ListStore {
    /// Reads each `(name, path)` pair, recording a missing or unreadable file as
    /// unread. `name` is the path exactly as written in the config, which is the
    /// key a rule later looks up. Parsed files are served from a process-wide
    /// cache keyed by their signature, so a caller that resolves the same config
    /// repeatedly pays the read once.
    pub fn load<I, N, P>(entries: I) -> Self
    where
        I: IntoIterator<Item = (N, P)>,
        N: Into<String>,
        P: AsRef<Path>,
    {
        let mut files = std::collections::BTreeMap::new();
        for (name, path) in entries {
            let name = name.into();
            if files.contains_key(&name) {
                continue;
            }
            files.insert(name, parse_cached(path.as_ref()));
        }
        Self { files }
    }

    /// True when the named list was read successfully.
    pub fn is_read(&self, name: &str) -> bool {
        self.files.get(name).map(|f| !f.unread).unwrap_or(false)
    }

    /// True when `host` is named by the list `name`. An unread or unknown list
    /// never matches, so a missing file cannot widen a rule.
    pub fn matches_domain(&self, host: &str, name: &str) -> bool {
        match self.files.get(name) {
            Some(file) if !file.unread => {
                let host = host.trim_end_matches('.').to_ascii_lowercase();
                let mut candidate = host.as_str();
                loop {
                    if file.domains.contains(candidate) {
                        return true;
                    }
                    match candidate.split_once('.') {
                        Some((_, rest)) if !rest.is_empty() => candidate = rest,
                        _ => return false,
                    }
                }
            }
            _ => false,
        }
    }

    /// True when `ip` is inside a block in the list `name`.
    pub fn matches_ip(&self, ip: IpAddr, name: &str) -> bool {
        match self.files.get(name) {
            Some(file) if !file.unread => file.nets.iter().any(|net| net.contains(ip)),
            _ => false,
        }
    }
}

/// True when `host` equals or is a subdomain of any entry in `patterns`.
/// An empty pattern list never matches; use it as "no host restriction".
pub fn matches_any_host(host: &str, patterns: &[String]) -> bool {
    let host = host.trim_end_matches('.').to_ascii_lowercase();
    patterns.iter().any(|raw| {
        let pat = raw.trim().trim_start_matches('.').to_ascii_lowercase();
        if pat.is_empty() {
            return false;
        }
        host == pat || host.ends_with(&format!(".{pat}"))
    })
}

/// Splits a value that may be quoted, stripping one layer of double quotes.
fn unquote(value: &str) -> &str {
    let v = value.trim();
    if v.len() >= 2 && v.starts_with('"') && v.ends_with('"') {
        &v[1..v.len() - 1]
    } else {
        v
    }
}

/// Parses `--dpi-desync-fake-tls=...` values: `0x` hex, `@file`, or `!` reset.
fn parse_fake_value(raw: &str) -> FakeValue {
    let v = unquote(raw);
    if v == "!" {
        return FakeValue::Reset;
    }
    if let Some(file) = v.strip_prefix('@') {
        return FakeValue::File(file.to_string());
    }
    if let Some(hex) = v.strip_prefix("0x")
        && let Some(bytes) = hex_to_bytes(hex)
    {
        return FakeValue::Bytes(bytes);
    }
    FakeValue::Unknown
}

enum FakeValue {
    Reset,
    File(String),
    Bytes(Vec<u8>),
    Unknown,
}

fn hex_to_bytes(hex: &str) -> Option<Vec<u8>> {
    if !hex.len().is_multiple_of(2) {
        return None;
    }
    let mut out = Vec::with_capacity(hex.len() / 2);
    let b = hex.as_bytes();
    let mut i = 0;
    while i < b.len() {
        let hi = (b[i] as char).to_digit(16)? as u8;
        let lo = (b[i + 1] as char).to_digit(16)? as u8;
        out.push((hi << 4) | lo);
        i += 2;
    }
    Some(out)
}

/// Splits one argv token into `(key, value)`. A bare `--flag` yields `None`.
fn split_key(token: &str) -> (&str, Option<&str>) {
    match token.split_once('=') {
        Some((k, v)) => (k, Some(v)),
        None => (token, None),
    }
}

/// Parses an already-tokenized command line (as the process would see it).
pub fn parse_args<I, S>(args: I) -> Config
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let mut config = Config::default();
    let mut current = Rule {
        filter: Filter::default(),
        strategy: Strategy::none(),
        unknown: Vec::new(),
    };
    let mut started = false;

    for arg in args {
        let arg = arg.as_ref();
        if arg.is_empty() {
            continue;
        }
        if arg == "--new" {
            if started {
                config.rules.push(current);
            }
            current = Rule {
                filter: Filter::default(),
                strategy: Strategy::none(),
                unknown: Vec::new(),
            };
            started = true;
            continue;
        }
        if !arg.starts_with("--") {
            continue;
        }
        let (key, value) = split_key(arg);
        let value = value.map(unquote);

        // Global capture filter.
        match key {
            "--wf-tcp" => {
                if let Some(v) = value {
                    config.capture.tcp = parse_ports(v).unwrap_or_default();
                }
                continue;
            }
            "--wf-udp" => {
                if let Some(v) = value {
                    config.capture.udp = parse_ports(v).unwrap_or_default();
                }
                continue;
            }
            _ => {}
        }

        if apply_filter(&mut current.filter, key, value)
            || apply_strategy(&mut current.strategy, key, value)
        {
            started = true;
            continue;
        }
        current.unknown.push(arg.to_string());
        started = true;
    }
    if started {
        config.rules.push(current);
    }
    config
}

/// Convenience wrapper: splits `text` on whitespace first.
pub fn parse_line(text: &str) -> Config {
    parse_args(text.split_whitespace())
}

fn apply_filter(filter: &mut Filter, key: &str, value: Option<&str>) -> bool {
    let v = match value {
        Some(v) => v,
        None => return false,
    };
    match key {
        "--filter-tcp" | "--filter-udp" => {
            let parsed = parse_ports(v);
            // A value that did not parse must not become an empty list: an
            // empty list reads as "any port". Record it so the rule matches
            // nothing instead.
            filter.ports_malformed |= parsed.is_none();
            let list = parsed.unwrap_or_default();
            if key == "--filter-tcp" {
                filter.tcp = list;
            } else {
                filter.udp = list;
            }
        }
        "--filter-l3" => filter.l3 = split_csv(v),
        "--filter-l7" => filter.l7 = split_csv(v),
        "--hostlist" => filter.hostlist.push(v.to_string()),
        "--hostlist-domains" => filter.hostlist_domains = split_csv(v),
        "--hostlist-exclude" => filter.hostlist_exclude.push(v.to_string()),
        "--ipset" => filter.ipset.push(v.to_string()),
        "--ipset-exclude" => filter.ipset_exclude.push(v.to_string()),
        "--ip-id" => {
            if v == "zero" {
                filter.ip_id_zero = true;
            }
        }
        _ => return false,
    }
    true
}

fn apply_strategy(strategy: &mut Strategy, key: &str, value: Option<&str>) -> bool {
    let v = match value {
        Some(v) => v,
        None => return false,
    };
    match key {
        "--dpi-desync" => {
            strategy.desyncs.clear();
            for token in split_csv(v) {
                if let Some(d) = Desync::parse(&token) {
                    strategy.desyncs.push(d);
                }
            }
        }
        "--dpi-desync-split-pos" => {
            strategy.split_positions.clear();
            for token in split_csv(v) {
                if let Some(p) = SplitPos::parse(&token) {
                    strategy.split_positions.push(p);
                }
            }
        }
        "--dpi-desync-split-seqovl" => {
            strategy.split_seqovl = v.parse().ok();
        }
        "--dpi-desync-split-seqovl-pattern" => {
            strategy.split_seqovl_pattern = Some(v.to_string());
        }
        "--dpi-desync-repeats" => {
            if let Ok(n) = v.parse::<u32>() {
                strategy.repeats = n;
            }
        }
        "--dpi-desync-fooling" => {
            strategy.fooling_badseq = split_csv(v).iter().any(|t| t == "badseq");
        }
        "--dpi-desync-badseq-increment" => {
            if let Some(n) = parse_signed_net32(v) {
                strategy.badseq_increment = n;
            }
        }
        "--dpi-desync-any-protocol" => {
            strategy.any_protocol = v != "0";
        }
        "--dpi-desync-cutoff" => {
            strategy.cutoff = Some(v.to_string());
        }
        "--dpi-desync-fake-tls" => match parse_fake_value(v) {
            FakeValue::Reset => strategy.fake_tls = None,
            FakeValue::Bytes(b) => {
                strategy
                    .fake_tls
                    .get_or_insert_with(FakeTls::default)
                    .literal = Some(b);
            }
            FakeValue::File(f) => {
                strategy
                    .fake_tls
                    .get_or_insert_with(FakeTls::default)
                    .template = Some(f.into_bytes());
            }
            FakeValue::Unknown => {}
        },
        "--dpi-desync-fake-tls-mod" => {
            let fake = strategy.fake_tls.get_or_insert_with(FakeTls::default);
            for token in split_csv(v) {
                if token == "rnd" {
                    fake.randomize = true;
                } else if token == "dupsid" {
                    fake.dup_session_id = true;
                } else if let Some(sni) = token.strip_prefix("sni=") {
                    fake.sni = Some(sni.to_string());
                }
            }
        }
        "--dpi-desync-fake-http" => strategy.fake_http = Some(v.to_string()),
        "--dpi-desync-fake-quic" => strategy.fake_quic = Some(v.to_string()),
        "--dpi-desync-fake-unknown-udp" => strategy.fake_unknown_udp = Some(v.to_string()),
        _ => return false,
    }
    true
}

fn split_csv(v: &str) -> Vec<String> {
    v.split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const SIMPLE: &str = concat!(
        "--wf-tcp=80,443,2053,2083,2087,2096,8443 --wf-udp=443,19294-19344,50000-50100 ",
        "--filter-udp=443 --hostlist=\"list-general.txt\" --dpi-desync=fake ",
        "--dpi-desync-repeats=6 --dpi-desync-fake-quic=\"quic_initial_www_google_com.bin\" ",
        "--new ",
        "--filter-tcp=80,443 --hostlist=\"list-general.txt\" ",
        "--dpi-desync=multisplit --dpi-desync-split-seqovl=568 --dpi-desync-split-pos=1 ",
        "--dpi-desync-split-seqovl-pattern=\"tls_clienthello_4pda_to.bin\""
    );

    const FAKE_TLS: &str = concat!(
        "--filter-tcp=2053,2083,2087,2096,8443 --hostlist-domains=discord.media ",
        "--dpi-desync=fake,multidisorder --dpi-desync-split-pos=1,midsld ",
        "--dpi-desync-repeats=11 --dpi-desync-fooling=badseq ",
        "--dpi-desync-fake-tls=0x00000000 --dpi-desync-fake-tls=! ",
        "--dpi-desync-fake-tls-mod=rnd,dupsid,sni=www.google.com ",
        "--dpi-desync-fake-http=\"tls_clienthello_max_ru.bin\""
    );

    #[test]
    fn parses_a_port_range() {
        let ports = parse_ports("19294-19344,50000-50100,443").expect("ports");
        assert_eq!(ports.len(), 3);
        assert!(ports[0].contains(19294));
        assert!(ports[0].contains(19344));
        assert!(!ports[0].contains(19345));
        assert!(ports[2].contains(443));
    }

    #[test]
    fn a_reversed_range_is_rejected() {
        assert!(parse_ports("100-50").is_none());
        assert!(parse_ports("notaport").is_none());
    }

    #[test]
    fn splits_the_capture_from_the_rules() {
        let cfg = parse_line(SIMPLE);
        assert_eq!(cfg.capture.tcp.len(), 7);
        assert_eq!(cfg.capture.udp.len(), 3);
        assert_eq!(cfg.rules.len(), 2);
    }

    #[test]
    fn the_first_rule_is_a_udp_fake_strategy() {
        let cfg = parse_line(SIMPLE);
        let r = &cfg.rules[0];
        assert_eq!(r.strategy.desyncs, vec![Desync::Fake]);
        assert_eq!(r.strategy.repeats, 6);
        assert_eq!(
            r.strategy.fake_quic.as_deref(),
            Some("quic_initial_www_google_com.bin")
        );
        assert_eq!(r.filter.udp.len(), 1);
        assert!(r.filter.udp[0].contains(443));
        assert_eq!(r.filter.hostlist, vec!["list-general.txt".to_string()]);
    }

    #[test]
    fn the_second_rule_is_multisplit_with_seqovl() {
        let cfg = parse_line(SIMPLE);
        let r = &cfg.rules[1];
        assert_eq!(r.strategy.desyncs, vec![Desync::Multisplit]);
        assert_eq!(r.strategy.split_seqovl, Some(568));
        assert_eq!(r.strategy.split_positions, vec![SplitPos::Absolute(1)]);
        assert_eq!(
            r.strategy.split_seqovl_pattern.as_deref(),
            Some("tls_clienthello_4pda_to.bin")
        );
        assert!(r.filter.matches_tcp_port(443));
        assert!(r.filter.matches_tcp_port(80));
        assert!(!r.filter.matches_tcp_port(8080));
    }

    #[test]
    fn fake_tls_auto_rule_keeps_order_and_mod() {
        let cfg = parse_line(FAKE_TLS);
        let r = &cfg.rules[0];
        assert_eq!(
            r.strategy.desyncs,
            vec![Desync::Fake, Desync::Multidisorder]
        );
        assert_eq!(
            r.strategy.split_positions,
            vec![SplitPos::Absolute(1), SplitPos::MidSld]
        );
        assert_eq!(r.strategy.repeats, 11);
        assert!(r.strategy.fooling_badseq);
        // `0x00000000` then `!` resets the literal; the mod then configures it.
        let fake = r.strategy.fake_tls.as_ref().expect("fake tls");
        assert_eq!(fake.literal, None);
        assert!(fake.randomize);
        assert!(fake.dup_session_id);
        assert_eq!(fake.sni.as_deref(), Some("www.google.com"));
        assert_eq!(
            r.strategy.fake_http.as_deref(),
            Some("tls_clienthello_max_ru.bin")
        );
        assert_eq!(r.filter.hostlist_domains, vec!["discord.media".to_string()]);
    }

    #[test]
    fn ip_id_zero_is_recorded() {
        let cfg = parse_line("--filter-tcp=443 --ip-id=zero --dpi-desync=multisplit");
        assert!(cfg.rules[0].filter.ip_id_zero);
    }

    #[test]
    fn unknown_keys_do_not_break_the_rule() {
        let cfg = parse_line("--filter-tcp=443 --dpi-desync=multisplit --dpi-future-option=7");
        assert_eq!(cfg.rules.len(), 1);
        assert_eq!(cfg.rules[0].strategy.desyncs, vec![Desync::Multisplit]);
        assert_eq!(
            cfg.rules[0].unknown,
            vec!["--dpi-future-option=7".to_string()]
        );
    }

    #[test]
    fn fake_tls_hex_literal_is_decoded_until_reset() {
        let cfg = parse_line("--dpi-desync-fake-tls=0x16030100");
        let fake = cfg.rules[0].strategy.fake_tls.as_ref().expect("fake");
        assert_eq!(fake.literal.as_deref(), Some(&[0x16, 0x03, 0x01, 0x00][..]));
    }

    #[test]
    fn any_protocol_and_cutoff_are_captured() {
        let cfg = parse_line(
            "--filter-tcp=80,443 --dpi-desync=multisplit --dpi-desync-any-protocol=1 \
             --dpi-desync-cutoff=n3",
        );
        assert!(cfg.rules[0].strategy.any_protocol);
        assert_eq!(cfg.rules[0].strategy.cutoff.as_deref(), Some("n3"));
    }

    #[test]
    fn a_udp_only_rule_does_not_shadow_a_tcp_rule() {
        // The first rule names only UDP; a SOCKS5 CONNECT on 443 is TCP and must
        // fall through to the second rule, not take the UDP fake strategy.
        let cfg = parse_line(
            "--wf-udp=443 --filter-udp=443 --dpi-desync=fake --dpi-desync-repeats=6 \
             --new --filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1",
        );
        let s = cfg.strategy_for("example.com", 443).expect("tcp rule");
        assert_eq!(s.desyncs, vec![Desync::Multisplit]);
        assert_eq!(s.split_positions, vec![SplitPos::Absolute(1)]);
    }

    #[test]
    fn a_udp_only_rule_matches_no_tcp_connection() {
        let cfg = parse_line("--filter-udp=443 --dpi-desync=fake");
        assert!(cfg.strategy_for("example.com", 443).is_none());
    }

    #[test]
    fn a_rule_without_a_port_filter_still_matches_tcp() {
        let cfg = parse_line(
            "--hostlist-domains=example.com --dpi-desync=multisplit --dpi-desync-split-pos=1",
        );
        assert!(cfg.strategy_for("example.com", 443).is_some());
    }

    /// Writes `files` into a fresh directory and returns a guard for it.
    fn temp_lists(files: &[(&str, &str)]) -> tempfile::TempDir {
        let dir = tempfile::tempdir().expect("temp dir");
        for (name, body) in files {
            std::fs::write(dir.path().join(name), body).expect("write list");
        }
        dir
    }

    #[test]
    fn resolving_the_same_list_twice_reuses_the_parsed_copy() {
        let dir = temp_lists(&[("cached.txt", "example.com\n")]);
        let line = "--filter-tcp=443 --hostlist=cached.txt --dpi-desync=multisplit --dpi-desync-split-pos=1";
        let mut first = parse_line(line);
        first.resolve_lists(dir.path());
        let mut second = parse_line(line);
        second.resolve_lists(dir.path());
        // Both resolves name the same file with the same signature, so the
        // second must be served the very same parsed allocation, not a re-read.
        let a = first.lists.files.get("cached.txt").expect("first").clone();
        let b = second
            .lists
            .files
            .get("cached.txt")
            .expect("second")
            .clone();
        assert!(Arc::ptr_eq(&a, &b));
        assert!(second.strategy_for("example.com", 443).is_some());
    }

    #[test]
    fn changing_a_list_file_forces_a_re_read() {
        let dir = temp_lists(&[("mutable.txt", "first.example\n")]);
        let line = "--filter-tcp=443 --hostlist=mutable.txt --dpi-desync=multisplit --dpi-desync-split-pos=1";
        let mut cfg = parse_line(line);
        cfg.resolve_lists(dir.path());
        assert!(cfg.strategy_for("first.example", 443).is_some());
        assert!(cfg.strategy_for("second.example", 443).is_none());

        // A different file body must be picked up: the signature changed.
        std::fs::write(dir.path().join("mutable.txt"), "second.example\n").expect("write");
        let mut cfg = parse_line(line);
        cfg.resolve_lists(dir.path());
        assert!(cfg.strategy_for("second.example", 443).is_some());
        assert!(cfg.strategy_for("first.example", 443).is_none());
    }

    #[test]
    fn a_hostlist_file_gates_a_rule() {
        let dir = temp_lists(&[("list-google.txt", "youtube.com\ngooglevideo.com\n")]);
        let mut cfg = parse_line(
            "--filter-tcp=443 --hostlist=list-google.txt \
             --dpi-desync=multisplit --dpi-desync-split-pos=7 \
             --new --filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=2",
        );
        cfg.resolve_lists(dir.path());
        // A listed host and its subdomain take the first (split-pos 7) rule.
        assert_eq!(
            cfg.strategy_for("youtube.com", 443)
                .unwrap()
                .split_positions[0],
            SplitPos::Absolute(7)
        );
        assert_eq!(
            cfg.strategy_for("www.youtube.com", 443)
                .unwrap()
                .split_positions[0],
            SplitPos::Absolute(7)
        );
        // Anything else falls through to the second rule.
        assert_eq!(
            cfg.strategy_for("telegram.org", 443)
                .unwrap()
                .split_positions[0],
            SplitPos::Absolute(2)
        );
    }

    #[test]
    fn an_unreadable_hostlist_does_not_widen_the_rule() {
        // The file the rule names does not exist: the rule must not match any
        // host, so the host falls through to the open rule instead.
        let dir = temp_lists(&[]);
        let mut cfg = parse_line(
            "--filter-tcp=443 --hostlist=missing.txt --dpi-desync=multisplit --dpi-desync-split-pos=7 \
             --new --filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=2",
        );
        cfg.resolve_lists(dir.path());
        assert_eq!(
            cfg.strategy_for("youtube.com", 443)
                .unwrap()
                .split_positions[0],
            SplitPos::Absolute(2)
        );
    }

    #[test]
    fn an_ipset_file_gates_a_rule_on_an_ip_literal() {
        let dir = temp_lists(&[("ipset-all.txt", "149.154.160.0/20\n2001:b28:f23d::/48\n")]);
        let mut cfg = parse_line(
            "--filter-tcp=443 --ipset=ipset-all.txt \
             --dpi-desync=multisplit --dpi-desync-split-pos=7 \
             --new --filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=2",
        );
        cfg.resolve_lists(dir.path());
        // In-range literals take the first rule; out-of-range and hostnames do not.
        assert_eq!(
            cfg.strategy_for("149.154.167.51", 443)
                .unwrap()
                .split_positions[0],
            SplitPos::Absolute(7)
        );
        assert_eq!(
            cfg.strategy_for("2001:b28:f23d::1", 443)
                .unwrap()
                .split_positions[0],
            SplitPos::Absolute(7)
        );
        assert_eq!(
            cfg.strategy_for("8.8.8.8", 443).unwrap().split_positions[0],
            SplitPos::Absolute(2)
        );
        assert_eq!(
            cfg.strategy_for("telegram.org", 443)
                .unwrap()
                .split_positions[0],
            SplitPos::Absolute(2)
        );
    }

    #[test]
    fn a_hostlist_exclude_file_removes_the_rule() {
        let dir = temp_lists(&[
            ("list-general.txt", "example.com\ndiscord.com\n"),
            ("list-exclude.txt", "example.com\n"),
        ]);
        let mut cfg = parse_line(
            "--filter-tcp=443 --hostlist=list-general.txt --hostlist-exclude=list-exclude.txt \
             --dpi-desync=multisplit --dpi-desync-split-pos=7 \
             --new --filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=2",
        );
        cfg.resolve_lists(dir.path());
        // Excluded host: the first rule is removed, so it falls through.
        assert_eq!(
            cfg.strategy_for("example.com", 443)
                .unwrap()
                .split_positions[0],
            SplitPos::Absolute(2)
        );
        // Listed and not excluded: the first rule applies.
        assert_eq!(
            cfg.strategy_for("discord.com", 443)
                .unwrap()
                .split_positions[0],
            SplitPos::Absolute(7)
        );
    }

    #[test]
    fn a_malformed_port_filter_matches_nothing() {
        // `44x` is not a port. zapret rejects the line outright; here the rule
        // must not fall back to "any port", which an empty list would mean.
        let cfg = parse_line("--filter-tcp=44x --dpi-desync=multisplit --dpi-desync-split-pos=1");
        assert!(cfg.rules[0].filter.ports_malformed);
        assert!(!cfg.rules[0].filter.matches_tcp_connection(443));
        assert!(!cfg.rules[0].filter.matches_tcp_connection(22));
        assert!(cfg.strategy_for("example.com", 443).is_none());
    }

    #[test]
    fn an_empty_port_filter_matches_nothing() {
        let cfg = parse_line("--filter-tcp= --dpi-desync=multisplit --dpi-desync-split-pos=1");
        assert!(cfg.rules[0].filter.ports_malformed);
        assert!(cfg.strategy_for("example.com", 443).is_none());
    }

    #[test]
    fn a_rule_without_a_port_filter_still_matches_every_port() {
        let cfg = parse_line("--dpi-desync=multisplit --dpi-desync-split-pos=1");
        assert!(!cfg.rules[0].filter.ports_malformed);
        assert!(cfg.strategy_for("example.com", 443).is_some());
        assert!(cfg.strategy_for("example.com", 22).is_some());
    }

    #[test]
    fn a_signed_badseq_increment_parses() {
        assert_eq!(parse_signed_net32("-10000"), Some(-10000i32 as u32));
        assert_eq!(parse_signed_net32("1000"), Some(1000));
        assert_eq!(parse_signed_net32("0x10"), Some(16));
        assert_eq!(parse_signed_net32("-0x10"), Some(-16i32 as u32));
        assert_eq!(parse_signed_net32("nonsense"), None);
    }

    #[test]
    fn badseq_increment_defaults_and_overrides() {
        let cfg = parse_line("--filter-tcp=443 --dpi-desync=fake --dpi-desync-fooling=badseq");
        assert_eq!(
            cfg.rules[0].strategy.badseq_increment,
            crate::dpi::BADSEQ_INCREMENT_DEFAULT
        );
        let cfg = parse_line(
            "--filter-tcp=443 --dpi-desync=fake --dpi-desync-fooling=badseq \
             --dpi-desync-badseq-increment=1000",
        );
        assert_eq!(cfg.rules[0].strategy.badseq_increment, 1000);
    }

    #[test]
    fn cidr_parse_and_contains() {
        let net = Cidr::parse("149.154.160.0/20").expect("cidr");
        assert!(net.contains("149.154.160.1".parse().unwrap()));
        assert!(net.contains("149.154.175.255".parse().unwrap()));
        assert!(!net.contains("149.154.176.0".parse().unwrap()));
        // A bare address is a host route.
        let host = Cidr::parse("8.8.8.8").expect("host route");
        assert_eq!(host.prefix, 32);
        assert!(host.contains("8.8.8.8".parse().unwrap()));
        assert!(!host.contains("8.8.8.9".parse().unwrap()));
        // v6 and a mismatched family.
        let v6 = Cidr::parse("2001:b28:f23d::/48").expect("v6");
        assert!(v6.contains("2001:b28:f23d::1".parse().unwrap()));
        assert!(!v6.contains("8.8.8.8".parse().unwrap()));
        // Malformed inputs are rejected.
        assert!(Cidr::parse("149.154.160.0/33").is_none());
        assert!(Cidr::parse("not-an-address").is_none());
    }
}
