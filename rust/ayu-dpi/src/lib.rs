//! A local SOCKS5 proxy that desynchronises the first bytes of each outbound
//! stream.
//!
//! This is the transport-free half of the DPI work: instead of capturing
//! packets with a kernel driver, AyuGram points its own connection (TDLib) at
//! this proxy, and the proxy writes the first payload of each connection as the
//! ordered chunks from [`ayu_core::dpi::Strategy::userspace_chunks`]. A
//! `multisplit` at position 1 therefore goes out as two writes with
//! `TCP_NODELAY`, and the kernel emits two segments, which is what defeats a
//! DPI that only inspects the first segment.
//!
//! What this transport cannot do (no packet injection, no `badseq`, no
//! reordering, no `seqovl`) is not silently dropped: the strategy reports it
//! through `unsupported(TransportKind::Userspace)`, and this module refuses to
//! pretend otherwise at startup.
//!
//! The design borrows the one rule that matters from the RKN-facing tools: only
//! the *first* application payload on a connection is desynchronised. Later
//! application data is passed through unchanged, because after the handshake
//! there is nothing left for the DPI to SNI-inspect.

pub mod socks;
pub mod windivert;
pub mod ws;

use std::io;
use std::net::SocketAddr;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

use ayu_core::dpi::options::Config;
use ayu_core::dpi::{Resources, Strategy, TransportKind};

/// Everything a connection handler needs, cloned cheaply per connection.
///
/// Every field is behind an `Arc`, so a clone shares the shutdown flag, the
/// connection counter and the parsed config rather than copying them.
#[derive(Clone)]
pub struct Proxy {
    pub config: Arc<Config>,
    /// Bytes for `--dpi-desync-split-seqovl-pattern` and the fake templates,
    /// loaded once at startup. The userspace transport ignores the fake blobs
    /// (it reports them unsupported) but keeps them so the same struct serves
    /// the WinDivert backend too.
    pub resources: Arc<OwnedResources>,
    pub shutdown: Arc<AtomicBool>,
    pub connections: Arc<AtomicU64>,
    /// When set, connections whose target is a Telegram data centre address go
    /// out through the WebSocket front of that centre instead of a direct
    /// socket. Everything else takes the plain path.
    pub ws_mode: bool,
}

/// Owned copy of [`Resources`], because the borrowed form cannot outlive the
/// file reads that produced it.
#[derive(Debug, Default)]
pub struct OwnedResources {
    pub seqovl_pattern: Option<Vec<u8>>,
    pub fake_tls: Option<Vec<u8>>,
    pub fake_http: Option<Vec<u8>>,
    pub fake_quic: Option<Vec<u8>>,
    pub fake_unknown_udp: Option<Vec<u8>>,
}

impl OwnedResources {
    /// A borrowing view for the engine.
    pub fn as_resources(&self) -> Resources<'_> {
        Resources {
            seqovl_pattern: self.seqovl_pattern.as_deref(),
            fake_tls: self.fake_tls.as_deref(),
            fake_http: self.fake_http.as_deref(),
            fake_quic: self.fake_quic.as_deref(),
            fake_unknown_udp: self.fake_unknown_udp.as_deref(),
        }
    }
}

impl Proxy {
    /// Builds a proxy from a parsed config. Returns an error only when the
    /// config cannot be served at all (no rules); unsupported strategy options
    /// are reported by [`Proxy::capability_report`] instead.
    pub fn new(config: Config, resources: OwnedResources) -> Result<Self, String> {
        Self::with_ws_mode(config, resources, false)
    }

    /// Same as [`Proxy::new`] with the WebSocket upstream selected or not.
    pub fn with_ws_mode(
        config: Config,
        resources: OwnedResources,
        ws_mode: bool,
    ) -> Result<Self, String> {
        if config.rules.is_empty() && !ws_mode {
            return Err("no desync rules in the config".to_owned());
        }
        Ok(Self {
            config: Arc::new(config),
            resources: Arc::new(resources),
            shutdown: Arc::new(AtomicBool::new(false)),
            connections: Arc::new(AtomicU64::new(0)),
            ws_mode,
        })
    }

    /// What each rule loses on the userspace transport, for the startup log.
    /// Empty means every configured rule runs in full.
    pub fn capability_report(&self) -> Vec<String> {
        let mut out = Vec::new();
        for (i, rule) in self.config.rules.iter().enumerate() {
            for reason in rule.strategy.unsupported(TransportKind::Userspace) {
                out.push(format!("rule {i}: cannot honour {reason}"));
            }
        }
        out
    }

    /// Picks the strategy for a destination. The rule table and its host
    /// matching live in `ayu_core::dpi::options::Config`, so the engine that
    /// plans packets and the FFI that reports capabilities agree on which rule
    /// applies; this is only a convenience forwarder.
    pub fn strategy_for(&self, host: &str, port: u16) -> Option<&Strategy> {
        self.config.strategy_for(host, port)
    }

    /// Runs the accept loop until `shutdown` is set. Blocking.
    pub fn run(&self, listen: SocketAddr) -> io::Result<()> {
        socks::serve(self, listen)
    }

    /// Asks a running proxy to stop.
    pub fn request_shutdown(&self) {
        self.shutdown.store(true, Ordering::SeqCst);
    }

    pub fn is_shutdown(&self) -> bool {
        self.shutdown.load(Ordering::SeqCst)
    }
}

/// True when `host` equals or is a subdomain of any entry in `patterns`.
/// An empty pattern list never matches; use it as "no host restriction".
#[cfg(test)]
use ayu_core::dpi::options::matches_any_host;

#[cfg(test)]
mod tests {
    use super::*;
    use ayu_core::dpi::options::parse_line;
    use std::str::FromStr;

    fn proxy_from(text: &str) -> Proxy {
        let cfg = parse_line(text);
        Proxy::new(cfg, OwnedResources::default()).expect("proxy")
    }

    #[test]
    fn empty_config_is_refused() {
        assert!(Proxy::new(Config::default(), OwnedResources::default()).is_err());
    }

    #[test]
    fn selects_a_rule_by_port() {
        let p = proxy_from(
            "--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1 \
             --new --filter-tcp=80 --dpi-desync=multisplit --dpi-desync-split-pos=2",
        );
        let s443 = p.strategy_for("example.com", 443).expect("443");
        assert_eq!(
            s443.split_positions[0],
            ayu_core::dpi::SplitPos::Absolute(1)
        );
        let s80 = p.strategy_for("example.com", 80).expect("80");
        assert_eq!(s80.split_positions[0], ayu_core::dpi::SplitPos::Absolute(2));
        assert!(p.strategy_for("example.com", 8080).is_none());
    }

    #[test]
    fn hostlist_domains_restricts_a_rule() {
        let p = proxy_from(
            "--filter-tcp=443 --hostlist-domains=discord.media \
             --dpi-desync=multisplit --dpi-desync-split-pos=1 \
             --new --filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=2",
        );
        // discord.media and its subdomains match the first rule
        assert_eq!(
            p.strategy_for("cdn.discord.media", 443)
                .unwrap()
                .split_positions[0],
            ayu_core::dpi::SplitPos::Absolute(1)
        );
        // other hosts fall through to the second rule
        assert_eq!(
            p.strategy_for("telegram.org", 443).unwrap().split_positions[0],
            ayu_core::dpi::SplitPos::Absolute(2)
        );
    }

    #[test]
    fn host_matching_handles_subdomains_and_case() {
        assert!(matches_any_host("a.b.example.com", &["example.com".into()]));
        assert!(matches_any_host("EXAMPLE.COM", &["example.com".into()]));
        assert!(!matches_any_host("notexample.com", &["example.com".into()]));
        assert!(!matches_any_host(
            "example.com.evil.ru",
            &["example.com".into()]
        ));
        assert!(!matches_any_host("x", &[]));
    }

    #[test]
    fn capability_report_lists_unsupported_options() {
        let p = proxy_from(
            "--filter-tcp=443 --dpi-desync=fake,multidisorder \
             --dpi-desync-split-pos=1 --dpi-desync-fooling=badseq \
             --dpi-desync-split-seqovl=100",
        );
        let report = p.capability_report();
        // fake, badseq, multidisorder, seqovl -> four losses
        assert_eq!(report.len(), 4, "{report:?}");
        assert!(report.iter().any(|r| r.contains("multidisorder")));
    }

    #[test]
    fn plain_split_has_a_clean_capability_report() {
        let p = proxy_from("--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1");
        assert!(p.capability_report().is_empty());
    }

    #[test]
    fn listen_addr_parses() {
        let addr = SocketAddr::from_str("127.0.0.1:0").expect("addr");
        assert_eq!(addr.port(), 0);
    }
}
