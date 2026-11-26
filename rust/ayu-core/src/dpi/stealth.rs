//! Stealth options of the MTProxy / WSS client path, ported from ZaStoGram.
//!
//! ZaStoGram is a tdesktop fork with a hardened fake-TLS front for networks
//! under DPI censorship. Its C++ sources carry three things worth owning in
//! Rust: the option model the proxy settings page writes (`proxy_data.h`), the
//! ClientHello profile table and its measured "withheld" marking
//! (`client_hello_profile.*`), and the ClientHello fragmentation plan
//! (`client_hello_fragmentation.cpp`). This module reproduces all three as
//! plain Rust with no Qt, so the same rules can be unit tested here and reused
//! by the `ayu-dpi` transport.
//!
//! What is deliberately *not* here: the ClientHello *builder* (the template
//! interpreter that emits the bytes). That is a large piece and it belongs
//! next to the transport that sends the hello, not in the option model. This
//! module answers "what did the user ask for" and "where does the hello get
//! cut", which is what the settings page and the fragmenting transport need.

use std::ops::Range;

/// Which ClientHello the fake-TLS front presents.
///
/// The order and the names mirror `ProxyTlsProfile` in `proxy_data.h` one for
/// one, so a value stored by the C++ client round-trips through `as_str` and
/// `parse` without renaming.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TlsProfile {
    /// Pick the compile-time default (see [`default_client_hello_profile`]).
    Auto,
    Firefox,
    AndroidChrome,
    Yandex,
    FirefoxAndroid,
    AndroidOkHttp,
    /// Re-pick on every connection instead of pinning one template.
    AutoRotate,
    ChromeModern,
}

impl TlsProfile {
    /// Stable slug, matching the `id` field of the profile table.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Firefox => "firefox",
            Self::AndroidChrome => "android_chrome",
            Self::Yandex => "yandex",
            Self::FirefoxAndroid => "firefox_android",
            Self::AndroidOkHttp => "android_okhttp",
            Self::AutoRotate => "auto_rotate",
            Self::ChromeModern => "chrome_modern",
        }
    }

    /// Parses the slug back. The `auto`/`auto_rotate` spellings are also
    /// accepted with the hyphen form for command-line friendliness.
    pub fn parse(text: &str) -> Option<Self> {
        Some(match text.trim() {
            "auto" => Self::Auto,
            "firefox" => Self::Firefox,
            "android_chrome" => Self::AndroidChrome,
            "yandex" => Self::Yandex,
            "firefox_android" => Self::FirefoxAndroid,
            "android_okhttp" => Self::AndroidOkHttp,
            "auto_rotate" | "auto-rotate" => Self::AutoRotate,
            "chrome_modern" => Self::ChromeModern,
            _ => return None,
        })
    }
}

/// How the ClientHello is cut before it leaves, `ProxyClientHelloFragmentation`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ClientHelloFragmentation {
    #[default]
    Off,
    /// Cut once and pause briefly between the two pieces.
    Soft,
}

/// How the dial itself is spread in time, `ProxyConnectionPattern`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ConnectionPattern {
    Off,
    Soft,
    Quiet,
    Strict,
    #[default]
    Browser,
}

/// How outgoing TLS records are sized, `ProxyRecordSizing`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum RecordSizing {
    #[default]
    Off,
    Conservative,
    Varied,
}

/// Inter-record pacing, `ProxyTiming`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Timing {
    #[default]
    Off,
    Gentle,
    Balanced,
}

/// How the very start of the connection is masked, `ProxyStartupCover`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum StartupCover {
    #[default]
    Off,
    Soft,
    Strict,
}

/// Which transport carries MTProto, `ProxyTransport`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Transport {
    Tcp,
    #[default]
    Wss,
}

/// The preset a value belongs to, `ProxyStealthLevel`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum StealthLevel {
    CompatStrict,
    CompatModern,
    DpiAdaptiveHandshake,
    #[default]
    DpiAdaptiveData,
    Experimental,
}

impl StealthLevel {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::CompatStrict => "compat_strict",
            Self::CompatModern => "compat_modern",
            Self::DpiAdaptiveHandshake => "dpi_adaptive_handshake",
            Self::DpiAdaptiveData => "dpi_adaptive_data",
            Self::Experimental => "experimental",
        }
    }

    pub fn parse(text: &str) -> Option<Self> {
        Some(match text.trim() {
            "compat_strict" | "compat-strict" => Self::CompatStrict,
            "compat_modern" | "compat-modern" => Self::CompatModern,
            "dpi_adaptive_handshake" | "dpi-adaptive-handshake" => Self::DpiAdaptiveHandshake,
            "dpi_adaptive_data" | "dpi-adaptive-data" => Self::DpiAdaptiveData,
            "experimental" => Self::Experimental,
            _ => return None,
        })
    }
}

/// The full set of knobs the proxy page saves, `ProxyStealthOptions`.
///
/// Field defaults match the C++ struct initializers exactly, so an empty
/// value behaves the same on both sides.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StealthOptions {
    pub level: StealthLevel,
    pub tls_profile: TlsProfile,
    pub client_hello_fragmentation: ClientHelloFragmentation,
    pub connection_pattern: ConnectionPattern,
    pub record_sizing: RecordSizing,
    pub timing: Timing,
    pub startup_cover: StartupCover,
    pub synthetic_psk: bool,
    pub transport: Transport,
    /// WSS front to use instead of the default `kws*.web.telegram.org`.
    pub wss_custom_host: String,
    pub wss_custom_port: u16,
    pub wss_custom_path: String,
    pub wss_custom_domain: String,
}

impl Default for StealthOptions {
    fn default() -> Self {
        // Mirrors `ProxyStealthOptions` in proxy_data.h: level
        // DpiAdaptiveData, profile Auto, fragmentation Off, pattern Browser,
        // record sizing Off, timing Off, startup cover Off, no synthetic PSK,
        // WSS transport on port 443.
        Self {
            level: StealthLevel::DpiAdaptiveData,
            tls_profile: TlsProfile::Auto,
            client_hello_fragmentation: ClientHelloFragmentation::Off,
            connection_pattern: ConnectionPattern::Browser,
            record_sizing: RecordSizing::Off,
            timing: Timing::Off,
            startup_cover: StartupCover::Off,
            synthetic_psk: false,
            transport: Transport::Wss,
            wss_custom_host: String::new(),
            wss_custom_port: 443,
            wss_custom_path: String::new(),
            wss_custom_domain: String::new(),
        }
    }
}

/// Whether a profile's shape was measured against a capture, `Validation`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Validation {
    /// Follows the browser it names but was never byte-compared to a capture.
    Claimed,
    /// Checked against a real capture with a JA4 match.
    Validated,
}

/// Static description of one ClientHello template, `ClientHelloProfileInfo`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ProfileInfo {
    pub profile: TlsProfile,
    pub id: &'static str,
    pub validation: Validation,
    pub capture_source: &'static str,
    pub capture_version: &'static str,
    pub expected_ja4: &'static str,
    /// A fingerprint measured to be refused on a filtered network. It stays a
    /// correct capture, but nothing should send it any more.
    pub withheld: bool,
    pub withheld_reason: &'static str,
}

/// Why `chrome_modern` and `android_chrome` are withheld, copied verbatim from
/// `client_hello_profile.cpp`. The deciding trait is the hello's tail: the two
/// withheld templates end with a GREASE extension carrying a single zero byte,
/// which is what a filtered network matches on.
const WITHHELD_REASON: &str = "measured refused: 3/12 answered on a filtered \
network, 12/12 for every profile whose last extension is not a GREASE one \
carrying a zero byte, on the same relay in the same minutes";

/// The profile table, in the same order as `kProfiles`. The first entry is the
/// fallback for an unknown value, so the default stands at the front
/// (`Yandex`, then `ChromeModern`, then the rest as in the C++ array).
const PROFILES: &[ProfileInfo] = &[
    ProfileInfo {
        profile: TlsProfile::Yandex,
        id: "yandex",
        validation: Validation::Claimed,
        capture_source: "",
        capture_version: "",
        expected_ja4: "",
        withheld: false,
        withheld_reason: "",
    },
    ProfileInfo {
        profile: TlsProfile::ChromeModern,
        id: "chrome_modern",
        validation: Validation::Validated,
        capture_source: "local_chrome_headless_capture",
        capture_version: "Google Chrome 149.0.7827.155",
        expected_ja4: "t13d1516h2_8daaf6152771_d8a2da3f94cd",
        withheld: true,
        withheld_reason: WITHHELD_REASON,
    },
    ProfileInfo {
        profile: TlsProfile::AndroidChrome,
        id: "android_chrome",
        validation: Validation::Claimed,
        capture_source: "",
        capture_version: "",
        expected_ja4: "",
        withheld: true,
        withheld_reason: WITHHELD_REASON,
    },
    ProfileInfo {
        profile: TlsProfile::Firefox,
        id: "firefox",
        validation: Validation::Claimed,
        capture_source: "",
        capture_version: "",
        expected_ja4: "",
        withheld: false,
        withheld_reason: "",
    },
    ProfileInfo {
        profile: TlsProfile::FirefoxAndroid,
        id: "firefox_android",
        validation: Validation::Claimed,
        capture_source: "",
        capture_version: "",
        expected_ja4: "",
        withheld: false,
        withheld_reason: "",
    },
    ProfileInfo {
        profile: TlsProfile::AndroidOkHttp,
        id: "android_okhttp",
        validation: Validation::Claimed,
        capture_source: "",
        capture_version: "",
        expected_ja4: "",
        withheld: false,
        withheld_reason: "",
    },
];

/// Plain lookup: answers about the named profile, including a withheld one.
///
/// `Auto` and `AutoRotate` resolve to the default before lookup, exactly as
/// `ClientHelloProfile` does.
pub fn client_hello_profile(profile: TlsProfile) -> &'static ProfileInfo {
    let normalized = match profile {
        TlsProfile::Auto | TlsProfile::AutoRotate => default_client_hello_profile(),
        other => other,
    };
    PROFILES
        .iter()
        .find(|info| info.profile == normalized)
        .unwrap_or(&PROFILES[0])
}

/// What to actually send for a configured value: resolves the two auto names
/// and steers away from a withheld fingerprint, so a setting saved before the
/// measurement does not keep a client on a template no relay answers.
pub fn effective_client_hello_profile(profile: TlsProfile) -> TlsProfile {
    let info = client_hello_profile(profile);
    if info.withheld {
        default_client_hello_profile()
    } else {
        info.profile
    }
}

/// Yandex over the Chrome templates: both answer everywhere the client was
/// measured, and this one is not the shape a filtered network refuses.
pub fn default_client_hello_profile() -> TlsProfile {
    TlsProfile::Yandex
}

/// Whether the named profile was validated against a real capture.
pub fn is_client_hello_profile_validated(profile: TlsProfile) -> bool {
    client_hello_profile(profile).validation == Validation::Validated
}

/// The byte range of the SNI hostname inside a TLS ClientHello record.
///
/// A direct port of `ClientHelloSniHostRange`: it walks the record header, the
/// handshake header, the session id, the cipher-suite and compression lists,
/// the extensions list and finally the `server_name` extension, returning
/// `None` the moment any length field would run past its parent. Network input
/// is untrusted, so every step is a bounds check, never a slice that can panic.
pub fn sni_host_range(data: &[u8]) -> Option<Range<usize>> {
    let read16 = |at: usize| -> Option<usize> {
        let hi = *data.get(at)? as usize;
        let lo = *data.get(at + 1)? as usize;
        Some((hi << 8) | lo)
    };
    let read24 = |at: usize| -> Option<usize> {
        let a = *data.get(at)? as usize;
        let b = *data.get(at + 1)? as usize;
        let c = *data.get(at + 2)? as usize;
        Some((a << 16) | (b << 8) | c)
    };

    if data.len() < 9 || data[0] != 0x16 {
        return None;
    }
    let record_length = read16(3)?;
    let record_end = 5 + record_length;
    if record_length < 4 || record_end > data.len() {
        return None;
    }
    let mut position = 5;
    if data[position] != 0x01 {
        return None;
    }
    let handshake_length = read24(position + 1)?;
    position += 4;
    let handshake_end = position + handshake_length;
    if handshake_end > record_end {
        return None;
    }
    if position + 34 > handshake_end {
        return None;
    }
    position += 34;
    if position + 1 > handshake_end {
        return None;
    }
    let session_id_length = data[position] as usize;
    position += 1;
    if position + session_id_length > handshake_end {
        return None;
    }
    position += session_id_length;
    let cipher_suites_length = read16(position)?;
    position += 2;
    if position + cipher_suites_length > handshake_end {
        return None;
    }
    position += cipher_suites_length;
    if position + 1 > handshake_end {
        return None;
    }
    let compression_length = data[position] as usize;
    position += 1;
    if position + compression_length > handshake_end {
        return None;
    }
    position += compression_length;
    let extensions_length = read16(position)?;
    position += 2;
    let extensions_end = position + extensions_length;
    if extensions_end > handshake_end {
        return None;
    }
    while position + 4 <= extensions_end {
        let ext_type = read16(position)?;
        let ext_length = read16(position + 2)?;
        let value = position + 4;
        let next = value + ext_length;
        if next > extensions_end {
            return None;
        }
        if ext_type != 0x0000 {
            position = next;
            continue;
        }
        // server_name extension body: a 2-byte list length, then names.
        let list_length = read16(value)?;
        let mut list_position = value + 2;
        let list_end = list_position + list_length;
        if list_end > next {
            return None;
        }
        while list_position + 3 <= list_end {
            let name_type = data[list_position];
            let name_length = read16(list_position + 1)?;
            let name_offset = list_position + 3;
            if name_offset + name_length > list_end {
                return None;
            }
            if name_type == 0 && name_length > 1 {
                return Some(name_offset..name_offset + name_length);
            }
            list_position = name_offset + name_length;
        }
        return None;
    }
    None
}

/// A fragmentation plan, `ClientHelloFragmentationPlan`: the size of the first
/// piece and the delay before the second, in milliseconds.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FragmentationPlan {
    pub first_size: usize,
    pub second_delay_ms: u16,
}

// `kClientHelloFragmentDelayMin` / `Max` from client_hello_constants.h.
const FRAGMENT_DELAY_MIN_MS: u16 = 2;
const FRAGMENT_DELAY_MAX_MS: u16 = 7;

/// Chooses the split point, `ClientHelloFragmentSplit`: the middle of the SNI
/// when it can find one, a middle-of-hello fallback otherwise.
fn fragment_split(data: &[u8]) -> usize {
    if let Some(range) = sni_host_range(data) {
        // `range.offset + 1 + RandomIndex(range.length - 1)`: never at the very
        // first or very last byte of the hostname.
        let span = range.len();
        return range.start + 1 + random_index(span.saturating_sub(1));
    }
    let size = data.len();
    let max_first = 768.min(size.saturating_sub(96));
    let min_first = 224.min(max_first);
    let fallback_range = if max_first > min_first {
        max_first - min_first + 1
    } else {
        1
    };
    min_first + random_index(fallback_range)
}

/// The ClientHello fragmentation plan, `PrepareClientHelloFragmentation`.
///
/// Only `Soft` fragments, and only once the hello is at least 384 bytes: a
/// shorter one has no room to cut without leaving the relay's own lengths
/// inconsistent.
pub fn prepare_client_hello_fragmentation(
    data: &[u8],
    mode: ClientHelloFragmentation,
) -> Option<FragmentationPlan> {
    let size = data.len();
    if mode != ClientHelloFragmentation::Soft || size < 384 {
        return None;
    }
    let delay_range = FRAGMENT_DELAY_MAX_MS - FRAGMENT_DELAY_MIN_MS + 1;
    Some(FragmentationPlan {
        first_size: fragment_split(data),
        second_delay_ms: FRAGMENT_DELAY_MIN_MS + random_index(delay_range as usize) as u16,
    })
}

/// The strictly-compatible option set, `CompatStrictProxyStealthOptions`.
pub fn compat_strict_options(mut options: StealthOptions) -> StealthOptions {
    options.level = StealthLevel::CompatStrict;
    options.transport = Transport::Tcp;
    options.client_hello_fragmentation = ClientHelloFragmentation::Off;
    options.record_sizing = RecordSizing::Off;
    options.timing = Timing::Off;
    options.startup_cover = StartupCover::Off;
    options.synthetic_psk = false;
    options.connection_pattern = ConnectionPattern::Off;
    options.tls_profile = TlsProfile::Auto;
    options
}

/// Applies a preset level to the option set, `ApplyProxyStealthLevel`. The
/// `Experimental` level applies nothing, which is what "experimental" means
/// here: keep whatever the user set.
pub fn apply_stealth_level(mut options: StealthOptions, level: StealthLevel) -> StealthOptions {
    options.level = level;
    match level {
        StealthLevel::CompatStrict => compat_strict_options(options),
        StealthLevel::CompatModern => {
            options.client_hello_fragmentation = ClientHelloFragmentation::Off;
            options.record_sizing = RecordSizing::Off;
            options.timing = Timing::Off;
            options.startup_cover = StartupCover::Off;
            options.synthetic_psk = false;
            options.connection_pattern = ConnectionPattern::Off;
            options
        }
        StealthLevel::DpiAdaptiveHandshake => {
            options.client_hello_fragmentation = ClientHelloFragmentation::Off;
            options.record_sizing = RecordSizing::Off;
            options.timing = Timing::Off;
            options.startup_cover = StartupCover::Off;
            options.synthetic_psk = false;
            options.connection_pattern = ConnectionPattern::Soft;
            options
        }
        StealthLevel::DpiAdaptiveData => {
            options.client_hello_fragmentation = ClientHelloFragmentation::Off;
            options.record_sizing = RecordSizing::Conservative;
            options.timing = Timing::Gentle;
            options.startup_cover = StartupCover::Soft;
            options.synthetic_psk = false;
            if options.connection_pattern == ConnectionPattern::Off {
                options.connection_pattern = ConnectionPattern::Soft;
            }
            options
        }
        StealthLevel::Experimental => options,
    }
}

/// A uniform index in `[0, count)`, drawn from the OS entropy source.
///
/// The C++ `base::RandomIndex` draws from the same source. `count == 0`
/// returns 0; callers must not ask for an empty range, and none do.
fn random_index(count: usize) -> usize {
    if count == 0 {
        return 0;
    }
    let mut buf = [0u8; 4];
    if getrandom::fill(&mut buf).is_err() {
        return 0;
    }
    (u32::from_le_bytes(buf) as usize) % count
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A ClientHello with the given SNI, built the same way the tls tests do.
    fn hello(host: &str) -> Vec<u8> {
        let mut sni_list = vec![0u8];
        sni_list.extend_from_slice(&(host.len() as u16).to_be_bytes());
        sni_list.extend_from_slice(host.as_bytes());
        let mut sni = Vec::new();
        sni.extend_from_slice(&(sni_list.len() as u16).to_be_bytes());
        sni.extend_from_slice(&sni_list);
        let mut ext = Vec::new();
        ext.extend_from_slice(&0u16.to_be_bytes());
        ext.extend_from_slice(&(sni.len() as u16).to_be_bytes());
        ext.extend_from_slice(&sni);
        let mut body = Vec::new();
        body.extend_from_slice(&[0x03, 0x03]);
        body.extend_from_slice(&[0u8; 32]);
        body.push(0);
        body.extend_from_slice(&2u16.to_be_bytes());
        body.extend_from_slice(&[0x13, 0x01]);
        body.push(1);
        body.push(0);
        body.extend_from_slice(&(ext.len() as u16).to_be_bytes());
        body.extend_from_slice(&ext);
        let mut hs = vec![0x01u8];
        let l = body.len();
        hs.extend_from_slice(&[(l >> 16) as u8, (l >> 8) as u8, l as u8]);
        hs.extend_from_slice(&body);
        let mut rec = vec![0x16u8, 0x03, 0x01];
        rec.extend_from_slice(&(hs.len() as u16).to_be_bytes());
        rec.extend_from_slice(&hs);
        rec
    }

    #[test]
    fn default_options_match_the_cpp_initializers() {
        let o = StealthOptions::default();
        assert_eq!(o.level, StealthLevel::DpiAdaptiveData);
        assert_eq!(o.tls_profile, TlsProfile::Auto);
        assert_eq!(o.client_hello_fragmentation, ClientHelloFragmentation::Off);
        assert_eq!(o.connection_pattern, ConnectionPattern::Browser);
        assert_eq!(o.record_sizing, RecordSizing::Off);
        assert_eq!(o.timing, Timing::Off);
        assert_eq!(o.startup_cover, StartupCover::Off);
        assert!(!o.synthetic_psk);
        assert_eq!(o.transport, Transport::Wss);
        assert_eq!(o.wss_custom_port, 443);
    }

    #[test]
    fn profile_slug_round_trips() {
        for p in [
            TlsProfile::Auto,
            TlsProfile::Firefox,
            TlsProfile::AndroidChrome,
            TlsProfile::Yandex,
            TlsProfile::FirefoxAndroid,
            TlsProfile::AndroidOkHttp,
            TlsProfile::AutoRotate,
            TlsProfile::ChromeModern,
        ] {
            assert_eq!(TlsProfile::parse(p.as_str()), Some(p), "{p:?}");
        }
        assert_eq!(TlsProfile::parse("nonsense"), None);
    }

    #[test]
    fn auto_and_auto_rotate_resolve_to_the_default() {
        assert_eq!(default_client_hello_profile(), TlsProfile::Yandex);
        assert_eq!(
            client_hello_profile(TlsProfile::Auto).profile,
            TlsProfile::Yandex
        );
        assert_eq!(
            client_hello_profile(TlsProfile::AutoRotate).profile,
            TlsProfile::Yandex
        );
    }

    #[test]
    fn withheld_profiles_steer_to_the_default() {
        // ChromeModern and AndroidChrome are withheld, so what is actually
        // sent must be the default, not the withheld template.
        assert!(client_hello_profile(TlsProfile::ChromeModern).withheld);
        assert!(client_hello_profile(TlsProfile::AndroidChrome).withheld);
        assert_eq!(
            effective_client_hello_profile(TlsProfile::ChromeModern),
            TlsProfile::Yandex
        );
        assert_eq!(
            effective_client_hello_profile(TlsProfile::AndroidChrome),
            TlsProfile::Yandex
        );
        // A profile that is not withheld is sent as itself.
        assert_eq!(
            effective_client_hello_profile(TlsProfile::Firefox),
            TlsProfile::Firefox
        );
    }

    #[test]
    fn only_chrome_modern_is_validated() {
        assert!(is_client_hello_profile_validated(TlsProfile::ChromeModern));
        assert!(!is_client_hello_profile_validated(TlsProfile::Yandex));
        assert!(!is_client_hello_profile_validated(TlsProfile::Firefox));
        // Its expected JA4 is recorded verbatim from the capture.
        assert_eq!(
            client_hello_profile(TlsProfile::ChromeModern).expected_ja4,
            "t13d1516h2_8daaf6152771_d8a2da3f94cd"
        );
    }

    #[test]
    fn sni_host_range_finds_the_hostname() {
        let rec = hello("www.google.com");
        let range = sni_host_range(&rec).expect("sni");
        assert_eq!(&rec[range], b"www.google.com");
    }

    #[test]
    fn sni_host_range_rejects_malformed_input() {
        assert_eq!(sni_host_range(&[]), None);
        assert_eq!(sni_host_range(&[0x16, 0x03, 0x01]), None);
        // A record whose declared length runs past the buffer.
        let mut rec = hello("a.example");
        rec[3] = 0xff;
        rec[4] = 0xff;
        assert_eq!(sni_host_range(&rec), None);
    }

    #[test]
    fn fragmentation_is_off_unless_soft() {
        let rec = hello("a.example.com");
        assert_eq!(
            prepare_client_hello_fragmentation(&rec, ClientHelloFragmentation::Off),
            None
        );
    }

    #[test]
    fn fragmentation_needs_at_least_384_bytes() {
        let short = hello("a.example");
        assert!(short.len() < 384);
        assert_eq!(
            prepare_client_hello_fragmentation(&short, ClientHelloFragmentation::Soft),
            None
        );
    }

    /// A ClientHello built with an explicit session id length and cipher-suite
    /// padding, so the total can be pushed over the 384-byte soft floor while
    /// the SNI extension stays parseable.
    fn hello_sized(host: &str, session_id_len: usize, suites_pad: usize) -> Vec<u8> {
        let mut sni_list = vec![0u8];
        sni_list.extend_from_slice(&(host.len() as u16).to_be_bytes());
        sni_list.extend_from_slice(host.as_bytes());
        let mut sni = Vec::new();
        sni.extend_from_slice(&(sni_list.len() as u16).to_be_bytes());
        sni.extend_from_slice(&sni_list);
        let mut ext = Vec::new();
        ext.extend_from_slice(&0u16.to_be_bytes());
        ext.extend_from_slice(&(sni.len() as u16).to_be_bytes());
        ext.extend_from_slice(&sni);
        let mut body = Vec::new();
        body.extend_from_slice(&[0x03, 0x03]);
        body.extend_from_slice(&[0u8; 32]);
        body.push(session_id_len as u8);
        body.extend(std::iter::repeat_n(0u8, session_id_len));
        let suites_len = 2 + suites_pad;
        body.extend_from_slice(&(suites_len as u16).to_be_bytes());
        body.extend_from_slice(&[0x13, 0x01]);
        body.extend(std::iter::repeat_n(0u8, suites_pad));
        body.push(1);
        body.push(0);
        body.extend_from_slice(&(ext.len() as u16).to_be_bytes());
        body.extend_from_slice(&ext);
        let mut hs = vec![0x01u8];
        let l = body.len();
        hs.extend_from_slice(&[(l >> 16) as u8, (l >> 8) as u8, l as u8]);
        hs.extend_from_slice(&body);
        let mut rec = vec![0x16u8, 0x03, 0x01];
        rec.extend_from_slice(&(hs.len() as u16).to_be_bytes());
        rec.extend_from_slice(&hs);
        rec
    }

    #[test]
    fn soft_fragmentation_splits_inside_the_sni() {
        let host = "fragment.test";
        let rec = hello_sized(host, 200, 150);
        assert!(rec.len() >= 384, "built {} bytes", rec.len());

        let range = sni_host_range(&rec).expect("sni parses");
        assert_eq!(&rec[range.clone()], host.as_bytes());

        let plan =
            prepare_client_hello_fragmentation(&rec, ClientHelloFragmentation::Soft).expect("plan");
        // The split lands past the first SNI byte and before the last.
        assert!(plan.first_size > range.start);
        assert!(plan.first_size < range.end);
        assert!(plan.first_size < rec.len());
        assert!((FRAGMENT_DELAY_MIN_MS..=FRAGMENT_DELAY_MAX_MS).contains(&plan.second_delay_ms));
    }

    #[test]
    fn soft_fragmentation_falls_back_when_there_is_no_sni() {
        // No SNI: a handshake record whose server_name extension is absent.
        // The fallback split must still land inside the buffer.
        let mut rec = hello_sized("a.example", 200, 150);
        // Overwrite the server_name extension type with an unknown one.
        let range = sni_host_range(&rec).expect("sni parses before edit");
        let ext_type_at = range.start - 9;
        rec[ext_type_at] = 0x0b;
        rec[ext_type_at + 1] = 0x0b;
        assert_eq!(sni_host_range(&rec), None);

        let plan =
            prepare_client_hello_fragmentation(&rec, ClientHelloFragmentation::Soft).expect("plan");
        assert!(plan.first_size > 0);
        assert!(plan.first_size < rec.len());
    }

    #[test]
    fn compat_strict_clears_every_stealth_knob() {
        let o = StealthOptions {
            transport: Transport::Wss,
            client_hello_fragmentation: ClientHelloFragmentation::Soft,
            record_sizing: RecordSizing::Varied,
            timing: Timing::Balanced,
            startup_cover: StartupCover::Strict,
            synthetic_psk: true,
            connection_pattern: ConnectionPattern::Browser,
            tls_profile: TlsProfile::ChromeModern,
            ..StealthOptions::default()
        };

        let strict = compat_strict_options(o);
        assert_eq!(strict.level, StealthLevel::CompatStrict);
        assert_eq!(strict.transport, Transport::Tcp);
        assert_eq!(
            strict.client_hello_fragmentation,
            ClientHelloFragmentation::Off
        );
        assert_eq!(strict.record_sizing, RecordSizing::Off);
        assert_eq!(strict.timing, Timing::Off);
        assert_eq!(strict.startup_cover, StartupCover::Off);
        assert!(!strict.synthetic_psk);
        assert_eq!(strict.connection_pattern, ConnectionPattern::Off);
        assert_eq!(strict.tls_profile, TlsProfile::Auto);
    }

    #[test]
    fn dpi_adaptive_data_enables_the_gentle_set() {
        let applied = apply_stealth_level(StealthOptions::default(), StealthLevel::DpiAdaptiveData);
        assert_eq!(applied.record_sizing, RecordSizing::Conservative);
        assert_eq!(applied.timing, Timing::Gentle);
        assert_eq!(applied.startup_cover, StartupCover::Soft);
        assert!(!applied.synthetic_psk);
        // Pattern was Browser by default, so it is left as-is.
        assert_eq!(applied.connection_pattern, ConnectionPattern::Browser);
    }

    #[test]
    fn dpi_adaptive_data_promotes_an_off_pattern_to_soft() {
        let base = StealthOptions {
            connection_pattern: ConnectionPattern::Off,
            ..StealthOptions::default()
        };
        let applied = apply_stealth_level(base, StealthLevel::DpiAdaptiveData);
        assert_eq!(applied.connection_pattern, ConnectionPattern::Soft);
    }

    #[test]
    fn experimental_leaves_the_options_untouched() {
        let base = StealthOptions {
            record_sizing: RecordSizing::Varied,
            ..StealthOptions::default()
        };
        let applied = apply_stealth_level(base.clone(), StealthLevel::Experimental);
        assert_eq!(applied.record_sizing, RecordSizing::Varied);
        assert_eq!(applied.level, StealthLevel::Experimental);
    }
}
