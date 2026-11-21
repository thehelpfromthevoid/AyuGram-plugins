//! Command-line entry point: run the userspace desync proxy.
//!
//! This is the transport the client can use without a kernel driver or admin
//! rights: point TDLib at `socks5://127.0.0.1:PORT` and the proxy splits the
//! first TLS record on each connection.
//!
//! Usage:
//!
//! ```text
//! ayu-dpi --listen 127.0.0.1:1080 \
//!   --config "--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1,midsld" \
//!   --seqovl-pattern path/to/tls_clienthello.bin
//! ```
//!
//! `--config` may be repeated; each is parsed as one `--new`-separated set.

use std::net::SocketAddr;
use std::process::ExitCode;

use ayu_dpi::windivert;
use ayu_dpi::{OwnedResources, Proxy};

use ayu_core::dpi::options::Config;

const USAGE: &str = "\
ayu-dpi: userspace DPI-desync SOCKS5 proxy

USAGE:
    ayu-dpi --listen ADDR [--config LINE]... [--seqovl-pattern FILE]
            [--fake-tls FILE] [--fake-http FILE] [--fake-quic FILE]
            [--report-only]
    ayu-dpi --probe-windivert [--windivert-dir DIR] [--probe-filter FILTER]

OPTIONS:
    --listen ADDR          Address to bind, e.g. 127.0.0.1:1080
    --config LINE          A zapret-style rule set (repeatable)
    --seqovl-pattern FILE  Bytes for --dpi-desync-split-seqovl-pattern
    --fake-tls FILE        Template for --dpi-desync-fake-tls (reported, not
                           usable over the stream transport)
    --fake-http FILE       Template for --dpi-desync-fake-http
    --fake-quic FILE       Template for --dpi-desync-fake-quic
    --report-only          Print what the strategies can and cannot do, then exit
    --ws                   Send data centre connections through the WebSocket
                           front (wss://kws{dc}.web.telegram.org/apiws) instead
                           of a direct socket; other hosts take the plain path
    --probe-windivert      Load WinDivert and capture one packet, then exit
                           (needs administrator rights and WinDivert64.sys)
    --windivert-dir DIR    Directory holding WinDivert.dll for the probe
    --probe-filter FILTER  Capture filter for the probe (default tcp)
    --lists-dir DIR        Base directory for --hostlist/--ipset files
                           (default: the current directory)
    -h, --help             Print this help
";

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("ayu-dpi: {message}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), String> {
    let mut listen: Option<String> = None;
    let mut lines: Vec<String> = Vec::new();
    let mut seqovl: Option<String> = None;
    let mut fake_tls: Option<String> = None;
    let mut fake_http: Option<String> = None;
    let mut fake_quic: Option<String> = None;
    let mut report_only = false;
    let mut ws_mode = false;
    let mut probe_windivert = false;
    let mut windivert_dir: Option<String> = None;
    let mut probe_filter = "tcp".to_owned();
    let mut lists_dir: Option<String> = None;

    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "-h" | "--help" => {
                print!("{USAGE}");
                return Ok(());
            }
            "--listen" => listen = Some(next_value(&mut args, "--listen")?),
            "--config" => lines.push(next_value(&mut args, "--config")?),
            "--seqovl-pattern" => seqovl = Some(next_value(&mut args, "--seqovl-pattern")?),
            "--fake-tls" => fake_tls = Some(next_value(&mut args, "--fake-tls")?),
            "--fake-http" => fake_http = Some(next_value(&mut args, "--fake-http")?),
            "--fake-quic" => fake_quic = Some(next_value(&mut args, "--fake-quic")?),
            "--report-only" => report_only = true,
            "--ws" => ws_mode = true,
            "--probe-windivert" => probe_windivert = true,
            "--windivert-dir" => windivert_dir = Some(next_value(&mut args, "--windivert-dir")?),
            "--probe-filter" => probe_filter = next_value(&mut args, "--probe-filter")?,
            "--lists-dir" => lists_dir = Some(next_value(&mut args, "--lists-dir")?),
            other => return Err(format!("unknown argument {other}\n\n{USAGE}")),
        }
    }

    // The WinDivert probe is a separate mode: it needs no config.
    if probe_windivert {
        let dir = windivert_dir.as_deref().map(std::path::Path::new);
        return match windivert::Capture::probe(dir, &probe_filter) {
            Ok(message) => {
                println!("ayu-dpi: WinDivert probe: {message}");
                Ok(())
            }
            Err(reason) => Err(format!("WinDivert probe failed: {reason}")),
        };
    }

    let listen = listen.ok_or("--listen is required")?;
    let addr: SocketAddr = listen
        .parse()
        .map_err(|e| format!("--listen {listen}: {e}"))?;

    // WebSocket mode needs no desync rules: the transport itself is the bypass,
    // and rules for hosts other than data centres still take the plain path.
    let mut config = if lines.is_empty() {
        if !ws_mode {
            return Err("at least one --config is required (or --ws)".to_owned());
        }
        Config::default()
    } else {
        // Each --config line is its own set; joining with --new makes them one
        // ordered rule list, which is what the engine expects.
        let joined = lines.join(" --new ");
        ayu_core::dpi::options::parse_line(&joined)
    };
    if config.rules.is_empty() && !ws_mode {
        return Err("the config produced no rules".to_owned());
    }

    // Hostlists and ipsets are files; read them once so the gate is decided by
    // the file contents, not by the fact that a file was named.
    let base = lists_dir
        .as_deref()
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| std::path::PathBuf::from("."));
    config.resolve_lists(&base);
    for rule in &config.rules {
        for path in rule
            .filter
            .hostlist
            .iter()
            .chain(rule.filter.hostlist_exclude.iter())
            .chain(rule.filter.ipset.iter())
            .chain(rule.filter.ipset_exclude.iter())
        {
            if !config.lists.is_read(path) {
                eprintln!(
                    "ayu-dpi: list file {path} (under {}) could not be read; the rule using it will not match",
                    base.display()
                );
            }
        }
    }

    let resources = OwnedResources {
        seqovl_pattern: read_optional(&seqovl)?,
        fake_tls: read_optional(&fake_tls)?,
        fake_http: read_optional(&fake_http)?,
        fake_quic: read_optional(&fake_quic)?,
        fake_unknown_udp: None,
    };

    let proxy = Proxy::with_ws_mode(config, resources, ws_mode)?;

    if proxy.ws_mode {
        println!("ayu-dpi: WebSocket upstream enabled for data centre addresses");
    }
    let report = proxy.capability_report();
    if report.is_empty() {
        println!("ayu-dpi: every rule runs in full on this transport");
    } else {
        for line in &report {
            println!("ayu-dpi: {line}");
        }
    }
    if report_only {
        return Ok(());
    }

    println!("ayu-dpi: listening on socks5://{addr}");
    proxy.run(addr).map_err(|e| format!("proxy stopped: {e}"))
}

fn next_value(args: &mut impl Iterator<Item = String>, flag: &str) -> Result<String, String> {
    args.next().ok_or_else(|| format!("{flag} needs a value"))
}

/// Reads a template file when a path was given.
fn read_optional(path: &Option<String>) -> Result<Option<Vec<u8>>, String> {
    match path {
        None => Ok(None),
        Some(p) => std::fs::read(p)
            .map(Some)
            .map_err(|e| format!("reading {p}: {e}")),
    }
}
