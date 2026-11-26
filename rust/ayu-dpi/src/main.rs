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
use ayu_core::dpi::stealth::{
    ClientHelloFragmentation, ConnectionPattern, RecordSizing, StartupCover, StealthLevel,
    StealthOptions, Timing, TlsProfile, Transport,
};

const USAGE: &str = "\
ayu-dpi: userspace DPI-desync SOCKS5 proxy

USAGE:
    ayu-dpi --listen ADDR [--config LINE]... [--seqovl-pattern FILE]
            [--fake-tls FILE] [--fake-http FILE] [--fake-quic FILE]
            [--stealth KEY=VALUE]... [--report-only]
    ayu-dpi --probe-windivert [--windivert-dir DIR] [--probe-filter FILTER]

OPTIONS:
    --listen ADDR          Address to bind, e.g. 127.0.0.1:1080
    --config LINE          A zapret-style rule set (repeatable)
    --seqovl-pattern FILE  Bytes for --dpi-desync-split-seqovl-pattern
    --fake-tls FILE        Template for --dpi-desync-fake-tls (reported, not
                           usable over the stream transport)
    --fake-http FILE       Template for --dpi-desync-fake-http
    --fake-quic FILE       Template for --dpi-desync-fake-quic
    --stealth KEY=VALUE    One stealth knob from the proxy page (repeatable).
                           Keys: level, tls_profile, fragmentation (alias
                           client_hello_fragmentation), connection_pattern,
                           record_sizing, timing, startup_cover, synthetic_psk,
                           transport, wss_host, wss_port, wss_path, wss_domain.
                           Values are the slugs of ayu-core's dpi::stealth.
    --report-only          Print what the strategies and the stealth options can
                           and cannot do, then exit
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
    let mut stealth_lines: Vec<String> = Vec::new();

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
            "--stealth" => stealth_lines.push(next_value(&mut args, "--stealth")?),
            "--report-only" => report_only = true,
            "--ws" => ws_mode = true,
            "--probe-windivert" => probe_windivert = true,
            "--windivert-dir" => windivert_dir = Some(next_value(&mut args, "--windivert-dir")?),
            "--probe-filter" => probe_filter = next_value(&mut args, "--probe-filter")?,
            "--lists-dir" => lists_dir = Some(next_value(&mut args, "--lists-dir")?),
            other => return Err(format!("unknown argument {other}\n\n{USAGE}")),
        }
    }

    // The stealth knobs are parsed before anything touches the network so a bad
    // key fails the launch instead of being silently dropped.
    let mut stealth = StealthOptions::default();
    for line in &stealth_lines {
        apply_stealth_entry(&mut stealth, line)?;
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

    let proxy = Proxy::with_options(config, resources, ws_mode, stealth)?;

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
    for line in proxy.stealth_report() {
        println!("ayu-dpi: stealth: {line}");
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

/// Parses one `--stealth KEY=VALUE` entry onto the option set.
///
/// Keys and values are the ones the proxy page and `ayu-core`'s
/// `dpi::stealth` use, so the C++ side can forward a saved option without a
/// translation table. An unknown key, a missing value or an unparsable value is
/// an error, not a silent default.
fn apply_stealth_entry(options: &mut StealthOptions, entry: &str) -> Result<(), String> {
    let (key, value) = entry
        .split_once('=')
        .ok_or_else(|| format!("--stealth {entry}: expected KEY=VALUE"))?;
    let key = key.trim();
    let value = value.trim();
    // Both spellings the settings page may emit for fragmentation.
    let key = if key == "fragmentation" {
        "client_hello_fragmentation"
    } else {
        key
    };
    match key {
        "level" => {
            options.level = StealthLevel::parse(value)
                .ok_or_else(|| format!("--stealth level: unknown value {value}"))?;
        }
        "tls_profile" => {
            options.tls_profile = TlsProfile::parse(value)
                .ok_or_else(|| format!("--stealth tls_profile: unknown value {value}"))?;
        }
        "client_hello_fragmentation" => {
            options.client_hello_fragmentation = parse_fragmentation(value)
                .ok_or_else(|| format!("--stealth fragmentation: unknown value {value}"))?;
        }
        "connection_pattern" => {
            options.connection_pattern = parse_connection_pattern(value)
                .ok_or_else(|| format!("--stealth connection_pattern: unknown value {value}"))?;
        }
        "record_sizing" => {
            options.record_sizing = parse_record_sizing(value)
                .ok_or_else(|| format!("--stealth record_sizing: unknown value {value}"))?;
        }
        "timing" => {
            options.timing = parse_timing(value)
                .ok_or_else(|| format!("--stealth timing: unknown value {value}"))?;
        }
        "startup_cover" => {
            options.startup_cover = parse_startup_cover(value)
                .ok_or_else(|| format!("--stealth startup_cover: unknown value {value}"))?;
        }
        "synthetic_psk" => {
            options.synthetic_psk = parse_bool(value)
                .ok_or_else(|| format!("--stealth synthetic_psk: expected on/off, got {value}"))?;
        }
        "transport" => {
            options.transport = match value {
                "tcp" => Transport::Tcp,
                "wss" => Transport::Wss,
                _ => {
                    return Err(format!(
                        "--stealth transport: expected tcp or wss, got {value}"
                    ));
                }
            };
        }
        "wss_host" => options.wss_custom_host = value.to_owned(),
        "wss_path" => options.wss_custom_path = value.to_owned(),
        "wss_domain" => options.wss_custom_domain = value.to_owned(),
        "wss_port" => {
            options.wss_custom_port = value
                .parse()
                .map_err(|_| format!("--stealth wss_port: expected a port, got {value}"))?;
        }
        other => return Err(format!("--stealth: unknown key {other}")),
    }
    Ok(())
}

fn parse_bool(value: &str) -> Option<bool> {
    Some(match value {
        "on" | "true" | "1" | "yes" => true,
        "off" | "false" | "0" | "no" => false,
        _ => return None,
    })
}

fn parse_fragmentation(value: &str) -> Option<ClientHelloFragmentation> {
    Some(match value {
        "off" => ClientHelloFragmentation::Off,
        "soft" => ClientHelloFragmentation::Soft,
        _ => return None,
    })
}

fn parse_connection_pattern(value: &str) -> Option<ConnectionPattern> {
    Some(match value {
        "off" => ConnectionPattern::Off,
        "soft" => ConnectionPattern::Soft,
        "quiet" => ConnectionPattern::Quiet,
        "strict" => ConnectionPattern::Strict,
        "browser" => ConnectionPattern::Browser,
        _ => return None,
    })
}

fn parse_record_sizing(value: &str) -> Option<RecordSizing> {
    Some(match value {
        "off" => RecordSizing::Off,
        "conservative" => RecordSizing::Conservative,
        "varied" => RecordSizing::Varied,
        _ => return None,
    })
}

fn parse_timing(value: &str) -> Option<Timing> {
    Some(match value {
        "off" => Timing::Off,
        "gentle" => Timing::Gentle,
        "balanced" => Timing::Balanced,
        _ => return None,
    })
}

fn parse_startup_cover(value: &str) -> Option<StartupCover> {
    Some(match value {
        "off" => StartupCover::Off,
        "soft" => StartupCover::Soft,
        "strict" => StartupCover::Strict,
        _ => return None,
    })
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

#[cfg(test)]
mod tests {
    use super::*;

    /// Every `KEY=VALUE` pair `Ayu::Dpi::StealthArguments` can emit, with the
    /// option field it must set. The C++ side is the only producer of these
    /// strings, so a pair accepted here but spelled differently there (or vice
    /// versa) is a silent mismatch that would make a saved toggle inert; this
    /// test is the contract between the two halves.
    #[test]
    fn every_pair_the_cpp_side_sends_lands_on_the_right_field() {
        let mut o = StealthOptions::default();
        apply_stealth_entry(&mut o, "client_hello_fragmentation=soft").expect("fragmentation");
        apply_stealth_entry(&mut o, "connection_pattern=soft").expect("pattern");
        apply_stealth_entry(&mut o, "startup_cover=soft").expect("cover");
        apply_stealth_entry(&mut o, "record_sizing=conservative").expect("sizing");
        apply_stealth_entry(&mut o, "timing=gentle").expect("timing");
        apply_stealth_entry(&mut o, "synthetic_psk=on").expect("psk");
        apply_stealth_entry(&mut o, "tls_profile=chrome_modern").expect("profile");

        assert_eq!(o.client_hello_fragmentation, ClientHelloFragmentation::Soft);
        assert_eq!(o.connection_pattern, ConnectionPattern::Soft);
        assert_eq!(o.startup_cover, StartupCover::Soft);
        assert_eq!(o.record_sizing, RecordSizing::Conservative);
        assert_eq!(o.timing, Timing::Gentle);
        assert!(o.synthetic_psk);
        assert_eq!(o.tls_profile, TlsProfile::ChromeModern);
    }

    /// The seven profile slugs map 1:1 onto the `ProxyTlsProfile` numbers the
    /// settings page stores, so each must parse and no two may collide.
    #[test]
    fn every_tls_profile_slug_parses_to_a_distinct_profile() {
        let slugs = [
            "auto",
            "firefox",
            "android_chrome",
            "yandex",
            "firefox_android",
            "android_okhttp",
            "auto_rotate",
            "chrome_modern",
        ];
        let mut seen = Vec::new();
        for slug in slugs {
            let mut o = StealthOptions::default();
            apply_stealth_entry(&mut o, &format!("tls_profile={slug}"))
                .unwrap_or_else(|e| panic!("{slug}: {e}"));
            assert!(!seen.contains(&o.tls_profile), "{slug} collides");
            seen.push(o.tls_profile);
        }
        assert_eq!(seen.len(), slugs.len());
    }

    /// `fragmentation` is the alias the older settings page may emit; both
    /// spellings must be the same knob.
    #[test]
    fn fragmentation_is_an_alias_of_client_hello_fragmentation() {
        let mut aliased = StealthOptions::default();
        let mut canonical = StealthOptions::default();
        apply_stealth_entry(&mut aliased, "fragmentation=soft").expect("alias");
        apply_stealth_entry(&mut canonical, "client_hello_fragmentation=soft").expect("canonical");
        assert_eq!(
            aliased.client_hello_fragmentation,
            canonical.client_hello_fragmentation
        );
    }

    /// A typo must fail loudly at startup, never fall back to a default: a
    /// silent default is a bypass that looks enabled and is not.
    #[test]
    fn unknown_keys_and_values_are_errors_not_defaults() {
        for bad in [
            "who_knows=soft",
            "timing=loud",
            "tls_profile=netscape",
            "synthetic_psk=maybe",
            "transport=quic",
            "record_sizing=aggressive",
            "connection_pattern=random",
            "startup_cover=max",
            "fragmentation=sometimes",
            "level=paranoid",
        ] {
            let mut o = StealthOptions::default();
            assert!(
                apply_stealth_entry(&mut o, bad).is_err(),
                "{bad} was accepted"
            );
        }
    }

    #[test]
    fn a_missing_equals_sign_is_an_error() {
        let mut o = StealthOptions::default();
        let err = apply_stealth_entry(&mut o, "timing").expect_err("must fail");
        assert!(err.contains("KEY=VALUE"), "{err}");
    }

    #[test]
    fn wss_overrides_are_taken_verbatim() {
        let mut o = StealthOptions::default();
        apply_stealth_entry(&mut o, "transport=wss").expect("transport");
        apply_stealth_entry(&mut o, "wss_host=example.org").expect("host");
        apply_stealth_entry(&mut o, "wss_port=8443").expect("port");
        apply_stealth_entry(&mut o, "wss_path=/apiws").expect("path");
        apply_stealth_entry(&mut o, "wss_domain=example.org").expect("domain");
        assert_eq!(o.transport, Transport::Wss);
        assert_eq!(o.wss_custom_host, "example.org");
        assert_eq!(o.wss_custom_port, 8443);
        assert_eq!(o.wss_custom_path, "/apiws");
        assert_eq!(o.wss_custom_domain, "example.org");
    }

    #[test]
    fn a_non_numeric_wss_port_is_an_error() {
        let mut o = StealthOptions::default();
        assert!(apply_stealth_entry(&mut o, "wss_port=nine").is_err());
    }

    /// The all-off startup sends no `--stealth` at all, so the option set must
    /// stay exactly at its defaults - the state the old `ayu-dpi.exe` assumes.
    #[test]
    fn the_defaults_are_the_all_off_state() {
        let o = StealthOptions::default();
        assert_eq!(o.client_hello_fragmentation, ClientHelloFragmentation::Off);
        assert!(!o.synthetic_psk);
        assert_eq!(o.tls_profile, TlsProfile::Auto);
    }
}
