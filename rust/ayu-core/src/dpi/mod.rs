//! The desync strategy language: parse zapret's `--dpi-desync*` options into a
//! plan, then turn a byte stream into the segments that defeat a DPI.
//!
//! The engine is transport-free. It answers one question: given a payload and a
//! connection's stream position, which byte ranges go out, in which order, with
//! which TCP sequence numbers. WinDivert and the userspace SOCKS5 proxy both
//! consume the same plan, so a strategy works identically either way; only the
//! primitives `fake` (needs packet spoofing) and `badseq` (needs a forged
//! sequence) are gated on the transport, and each reports whether it can honour
//! a plan rather than silently dropping it.
//!
//! The model mirrors zapret's option names one for one, so a `general.bat` line
//! maps to a plan without translation.

use crate::dpi::tls::{ClientHello, seqovl_overlap};

pub mod options;
pub mod tls;

/// `--dpi-desync-badseq-increment` default, matching zapret's
/// `BADSEQ_INCREMENT_DEFAULT` (`-10000`), kept as the two's-complement bit
/// pattern a `u32` sequence arithmetic uses.
pub const BADSEQ_INCREMENT_DEFAULT: u32 = -10_000i32 as u32;

/// How the first bytes of a stream are cut up.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Desync {
    /// Send nothing special; the transport decides.
    None,
    /// Send a fake segment, then the real one.
    Fake,
    /// Split the data at every position, sending the pieces in order.
    Multisplit,
    /// Split and send the pieces out of order.
    Multidisorder,
    /// Fake handshake with a bogus sequence number.
    Syndata,
    /// Fake against the split, with a host-derived fake.
    FakedSplit,
    /// Host-derived fake split.
    HostFakeSplit,
}

impl Desync {
    /// Parses one comma-separated element of `--dpi-desync`.
    pub fn parse(token: &str) -> Option<Self> {
        Some(match token {
            "fake" => Self::Fake,
            "multisplit" => Self::Multisplit,
            "multidisorder" => Self::Multidisorder,
            "syndata" => Self::Syndata,
            "fakedsplit" => Self::FakedSplit,
            "hostfakesplit" => Self::HostFakeSplit,
            _ => return None,
        })
    }
}

/// A split position, resolved against the payload.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SplitPos {
    /// Absolute offset from the start of the payload.
    Absolute(usize),
    /// The middle of the second-level domain in the SNI.
    MidSld,
    /// The offset just after the host name.
    HostEnd,
    /// `host+delta`, as zapret writes `host+N` / `host-N`.
    HostOffset(isize),
}

impl SplitPos {
    /// Parses one element of `--dpi-desync-split-pos`.
    pub fn parse(token: &str) -> Option<Self> {
        Some(match token {
            "midsld" => Self::MidSld,
            "host" => Self::HostOffset(0),
            other => {
                if let Some(rest) = other.strip_prefix("host") {
                    // `rest` is a `&str`, so a multi-byte first character is
                    // legal input (`hosté`); slicing by byte index would panic
                    // on the char boundary. Walk by chars instead.
                    let mut chars = rest.chars();
                    let sign = chars.next()?;
                    let n: isize = chars.as_str().parse().ok()?;
                    match sign {
                        '+' => Self::HostOffset(n),
                        '-' => Self::HostOffset(-n),
                        _ => return None,
                    }
                } else {
                    Self::Absolute(other.parse().ok()?)
                }
            }
        })
    }

    /// Resolves the position to a byte offset within `payload`.
    pub fn resolve(self, payload: &[u8]) -> Option<usize> {
        match self {
            Self::Absolute(n) => Some(n),
            Self::MidSld => {
                let ch = ClientHello::parse(payload)?;
                ch.midsld_offset(payload)
            }
            Self::HostEnd => {
                let ch = ClientHello::parse(payload)?;
                Some(ch.sni.end)
            }
            Self::HostOffset(delta) => {
                let ch = ClientHello::parse(payload)?;
                let base = ch.host_start() as isize + delta;
                Some(base.max(0) as usize)
            }
        }
    }
}

/// One parsed `--dpi-desync*` set: the full strategy for a filter.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Strategy {
    pub desyncs: Vec<Desync>,
    pub split_positions: Vec<SplitPos>,
    pub split_seqovl: Option<usize>,
    /// `--dpi-desync-split-seqovl-pattern`: a file whose bytes replace the
    /// overlap instead of the real prefix. Resolved to bytes by the transport.
    pub split_seqovl_pattern: Option<String>,
    pub repeats: u32,
    /// `--dpi-desync-fooling=badseq`: forge the sequence of a fake segment.
    pub fooling_badseq: bool,
    /// `--dpi-desync-badseq-increment=<int|0xHEX>`: the signed increment added
    /// to a fake segment's sequence under `badseq`, as a two's-complement bit
    /// pattern (zapret's default is `-10000`).
    pub badseq_increment: u32,
    /// `--dpi-desync-any-protocol`: apply the split regardless of L7 protocol.
    pub any_protocol: bool,
    /// `--dpi-desync-cutoff=nN`: stop desyncing after N packets on a connection.
    pub cutoff: Option<String>,
    pub fake_tls: Option<FakeTls>,
    /// `--dpi-desync-fake-http`: template file used to fake an HTTP request.
    pub fake_http: Option<String>,
    /// `--dpi-desync-fake-quic`: QUIC Initial template file.
    pub fake_quic: Option<String>,
    /// `--dpi-desync-fake-unknown-udp`: template for non-QUIC UDP.
    pub fake_unknown_udp: Option<String>,
}

/// The `--dpi-desync-fake-tls*` options.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct FakeTls {
    /// `0x...` literal bytes given directly on the command line.
    pub literal: Option<Vec<u8>>,
    /// A template record read from `--dpi-desync-fake-tls-mod` and friends.
    pub template: Option<Vec<u8>>,
    /// `sni=...` inside `--dpi-desync-fake-tls-mod`.
    pub sni: Option<String>,
    /// `rnd` in `--dpi-desync-fake-tls-mod`: randomise the random field.
    pub randomize: bool,
    /// `dupsid` in `--dpi-desync-fake-tls-mod`.
    pub dup_session_id: bool,
}

impl Strategy {
    /// A strategy with nothing enabled.
    pub fn none() -> Self {
        Self {
            desyncs: Vec::new(),
            split_positions: Vec::new(),
            split_seqovl: None,
            split_seqovl_pattern: None,
            repeats: 1,
            fooling_badseq: false,
            badseq_increment: BADSEQ_INCREMENT_DEFAULT,
            any_protocol: false,
            cutoff: None,
            fake_tls: None,
            fake_http: None,
            fake_quic: None,
            fake_unknown_udp: None,
        }
    }

    /// Whether `--dpi-desync-fake-*` (packet-level faking) is required.
    pub fn needs_fake_packets(&self) -> bool {
        self.desyncs.iter().any(|d| {
            matches!(
                d,
                Desync::Fake | Desync::Syndata | Desync::FakedSplit | Desync::HostFakeSplit
            )
        })
    }

    /// Whether the plan needs a forged sequence number.
    pub fn needs_badseq(&self) -> bool {
        self.fooling_badseq
    }

    /// Builds the split offsets for `payload` from the configured positions,
    /// clamped to the payload and de-duplicated in order.
    pub fn split_offsets(&self, payload: &[u8]) -> Vec<usize> {
        let mut out = Vec::new();
        for pos in &self.split_positions {
            if let Some(at) = pos.resolve(payload)
                && at < payload.len()
                && !out.contains(&at)
            {
                out.push(at);
            }
        }
        out.sort_unstable();
        out
    }
}

/// Which transport will send the plan. Capabilities differ: a userspace proxy
/// can reorder bytes on the real TCP stream but cannot forge packets or
/// sequence numbers, while WinDivert can do both.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransportKind {
    /// Userspace SOCKS5 proxy: real stream, ordered writes, no packet forgery.
    Userspace,
    /// WinDivert: packet capture and injection, real sequence manipulation.
    WinDivert,
}

/// Resolved byte blobs a plan may need. The caller loads template files once and
/// passes the bytes, so the engine never touches the filesystem.
#[derive(Default, Clone, Copy)]
pub struct Resources<'a> {
    /// Bytes of `--dpi-desync-split-seqovl-pattern`.
    pub seqovl_pattern: Option<&'a [u8]>,
    /// Bytes of the `--dpi-desync-fake-tls` template (already modded).
    pub fake_tls: Option<&'a [u8]>,
    /// Bytes of the `--dpi-desync-fake-http` template.
    pub fake_http: Option<&'a [u8]>,
    /// Bytes of the `--dpi-desync-fake-quic` template.
    pub fake_quic: Option<&'a [u8]>,
    /// Bytes of the `--dpi-desync-fake-unknown-udp` template.
    pub fake_unknown_udp: Option<&'a [u8]>,
}

/// One segment the transport must send: a byte range, an optional sequence
/// override, and whether it is a fake that the peer is expected to discard.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Segment {
    /// Bytes to send. Empty means "reuse the transport's own fake payload".
    pub data: Vec<u8>,
    /// When set, send with this absolute TCP sequence number (badseq fooling).
    pub seq_override: Option<u32>,
    /// True for a segment the peer is expected to discard (out-of-window fake).
    pub fake: bool,
}

impl Strategy {
    /// What this strategy cannot do on `kind`, as human-readable reasons.
    ///
    /// An empty result means the plan is fully honourable. A non-empty result
    /// is not an error by itself: the caller decides whether to degrade (run the
    /// honourable part) or refuse. Reporting it beats silently dropping a fake.
    ///
    /// The userspace transport is a real TCP byte stream through a SOCKS5
    /// socket, so four things are structurally impossible there and no amount
    /// of cleverness fixes them:
    ///
    /// * packet injection (`fake`, `syndata`): the forged bytes would land in
    ///   the real stream and corrupt it, not be discarded by the peer's stack;
    /// * `badseq`: the sequence number is the kernel's, not ours;
    /// * `multidisorder`: a byte stream has one order; it cannot be reordered;
    /// * `seqovl`: the overlap is bytes *behind* the current sequence, and the
    ///   stream cannot be rewound.
    pub fn unsupported(&self, kind: TransportKind) -> Vec<&'static str> {
        let mut out = Vec::new();
        if kind == TransportKind::Userspace {
            if self.needs_fake_packets() {
                out.push("fake packets (no packet injection over a stream)");
            }
            if self.fooling_badseq {
                out.push("badseq (the sequence number belongs to the kernel)");
            }
            if self.desyncs.contains(&Desync::Multidisorder) {
                out.push("multidisorder (a byte stream cannot be reordered)");
            }
            if self.split_seqovl.is_some() {
                out.push("seqovl (the sequence cannot be rewound)");
            }
        }
        out
    }

    /// The ordered, non-overlapping byte chunks a userspace transport should
    /// write for `payload`, honouring only the parts it can: an ascending split
    /// at the resolved positions. Fakes, seqovl and reordering are dropped here
    /// because `unsupported(Userspace)` already named them; the caller decides
    /// whether that degradation is acceptable.
    ///
    /// Each chunk is a separate write so the kernel emits separate segments
    /// (with `TCP_NODELAY`), which is the whole point of the split.
    pub fn userspace_chunks(&self, payload: &[u8]) -> Vec<Vec<u8>> {
        let splits = self.split_offsets(payload);
        let mut bounds = vec![0usize];
        bounds.extend(splits);
        bounds.push(payload.len());
        bounds
            .windows(2)
            .map(|pair| payload[pair[0]..pair[1]].to_vec())
            .collect()
    }
}

/// Plans the segments for one outbound payload at stream position `stream_pos`.
///
/// `stream_pos` is the absolute TCP sequence number of the payload's first
/// byte. The returned segments are in transmission order. Fakes come first when
/// the strategy asks for `fake`, then the split pieces in the order
/// `multisplit` (ascending) or `multidisorder` (first piece last) dictates.
pub fn plan(
    strategy: &Strategy,
    payload: &[u8],
    stream_pos: u32,
    resources: &Resources<'_>,
) -> Vec<Segment> {
    if payload.is_empty() {
        return Vec::new();
    }
    let mut segments: Vec<Segment> = Vec::new();

    // A fake precedes the real data; the first configured template wins.
    if strategy.desyncs.contains(&Desync::Fake)
        && let Some(fake) = resources
            .fake_tls
            .or(resources.fake_http)
            .or(resources.fake_quic)
            .or(resources.fake_unknown_udp)
    {
        // `--dpi-desync-fooling=badseq` forges the sequence of the fake, not
        // of the real segment: zapret passes `desync_fooling_mode` (which
        // carries `FOOL_BADSEQ`) to the fake packet only, and the real
        // segments go out with `fooling_orig`. The forged number is the
        // stream position plus the signed `--dpi-desync-badseq-increment`
        // (default -10000), which places the fake behind the window so the
        // peer discards it.
        let seq_override = strategy
            .fooling_badseq
            .then(|| stream_pos.wrapping_add(strategy.badseq_increment));
        segments.push(Segment {
            data: fake.to_vec(),
            seq_override,
            fake: true,
        });
    }

    let splits = strategy.split_offsets(payload);
    let wants_split = strategy
        .desyncs
        .iter()
        .any(|d| matches!(d, Desync::Multisplit | Desync::Multidisorder));

    if !wants_split {
        segments.push(Segment {
            data: payload.to_vec(),
            seq_override: None,
            fake: false,
        });
    } else {
        // Cut the payload at the split offsets.
        let mut bounds = vec![0usize];
        bounds.extend(splits.iter().copied());
        bounds.push(payload.len());
        let mut pieces: Vec<Vec<u8>> = Vec::new();
        for pair in bounds.windows(2) {
            pieces.push(payload[pair[0]..pair[1]].to_vec());
        }

        // seqovl: the second piece re-sends the overlay bytes before its own,
        // either from the configured pattern or from the first piece's tail.
        if let Some(n) = strategy.split_seqovl
            && pieces.len() >= 2
            && n > 0
        {
            let overlap = seqovl_overlap(resources.seqovl_pattern, &pieces[0], n);
            let mut merged = overlap;
            merged.extend_from_slice(&pieces[1]);
            pieces[1] = merged;
        }

        let disorder = strategy.desyncs.contains(&Desync::Multidisorder);
        if disorder {
            for piece in pieces.iter().skip(1) {
                segments.push(Segment {
                    data: piece.clone(),
                    seq_override: None,
                    fake: false,
                });
            }
            segments.push(Segment {
                data: pieces[0].clone(),
                seq_override: None,
                fake: false,
            });
        } else {
            for piece in pieces {
                segments.push(Segment {
                    data: piece,
                    seq_override: None,
                    fake: false,
                });
            }
        }
    }

    segments
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hello(host: &str) -> Vec<u8> {
        // Minimal ClientHello; mirrors the tls tests' builder.
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
    fn parses_split_positions() {
        assert_eq!(SplitPos::parse("1"), Some(SplitPos::Absolute(1)));
        assert_eq!(SplitPos::parse("midsld"), Some(SplitPos::MidSld));
        assert_eq!(SplitPos::parse("host+2"), Some(SplitPos::HostOffset(2)));
        assert_eq!(SplitPos::parse("host-3"), Some(SplitPos::HostOffset(-3)));
        assert_eq!(SplitPos::parse("nonsense"), None);
    }

    #[test]
    fn split_pos_with_a_multibyte_suffix_does_not_panic() {
        // `rest[1..]` used to slice on a byte index that is not a char
        // boundary for a multi-byte first char, aborting the parse. These must
        // return None, not panic.
        assert_eq!(SplitPos::parse("hosté"), None);
        assert_eq!(SplitPos::parse("host中"), None);
        assert_eq!(SplitPos::parse("host+2"), Some(SplitPos::HostOffset(2)));
    }

    #[test]
    fn parses_desync_tokens() {
        assert_eq!(Desync::parse("fake"), Some(Desync::Fake));
        assert_eq!(Desync::parse("multidisorder"), Some(Desync::Multidisorder));
        assert_eq!(Desync::parse("bogus"), None);
    }

    #[test]
    fn resolves_midsld_against_the_actual_hello() {
        let rec = hello("www.google.com");
        let at = SplitPos::MidSld.resolve(&rec).expect("midsld");
        assert!(at > 5 && at < rec.len());
        let ch = ClientHello::parse(&rec).unwrap();
        assert!(at > ch.sni.start && at < ch.sni.end);
    }

    #[test]
    fn multisplit_cuts_at_every_position() {
        let mut s = Strategy::none();
        s.desyncs.push(Desync::Multisplit);
        s.split_positions = vec![SplitPos::Absolute(1), SplitPos::Absolute(3)];
        let payload = b"abcdef";
        let segs = plan(&s, payload, 1000, &Resources::default());
        assert_eq!(segs.len(), 3);
        assert_eq!(segs[0].data, b"a");
        assert_eq!(segs[1].data, b"bc");
        assert_eq!(segs[2].data, b"def");
        let joined: Vec<u8> = segs.iter().flat_map(|s| s.data.clone()).collect();
        assert_eq!(joined, payload);
    }

    #[test]
    fn multidisorder_sends_the_first_piece_last() {
        let mut s = Strategy::none();
        s.desyncs.push(Desync::Multidisorder);
        s.split_positions = vec![SplitPos::Absolute(2)];
        let segs = plan(&s, b"abcdef", 0, &Resources::default());
        assert_eq!(segs.len(), 2);
        assert_eq!(segs[0].data, b"cdef");
        assert_eq!(segs[1].data, b"ab");
    }

    #[test]
    fn no_split_gives_one_segment() {
        let s = Strategy::none();
        let segs = plan(&s, b"abcdef", 0, &Resources::default());
        assert_eq!(segs.len(), 1);
        assert_eq!(segs[0].data, b"abcdef");
    }

    #[test]
    fn seqovl_repeats_the_tail_before_the_second_piece() {
        let mut s = Strategy::none();
        s.desyncs.push(Desync::Multisplit);
        s.split_positions = vec![SplitPos::Absolute(3)];
        s.split_seqovl = Some(2);
        let segs = plan(&s, b"abcdef", 0, &Resources::default());
        assert_eq!(segs[0].data, b"abc");
        // overlap of the first piece tail of length 2 -> "bc", then "def".
        assert_eq!(segs[1].data, b"bcdef");
    }

    #[test]
    fn seqovl_prefers_the_configured_pattern() {
        let mut s = Strategy::none();
        s.desyncs.push(Desync::Multisplit);
        s.split_positions = vec![SplitPos::Absolute(3)];
        s.split_seqovl = Some(4);
        let pattern = b"PATTERN";
        let res = Resources {
            seqovl_pattern: Some(pattern),
            ..Resources::default()
        };
        let segs = plan(&s, b"abcdef", 0, &res);
        assert_eq!(segs[0].data, b"abc");
        assert_eq!(segs[1].data, b"PATTdef");
    }

    #[test]
    fn fake_segment_precedes_the_real_data() {
        let mut s = Strategy::none();
        s.desyncs.push(Desync::Fake);
        let fake = b"FAKEHELLO";
        let res = Resources {
            fake_tls: Some(fake),
            ..Resources::default()
        };
        let segs = plan(&s, b"real", 0, &res);
        assert_eq!(segs.len(), 2);
        assert!(segs[0].fake);
        assert_eq!(segs[0].data, fake);
        assert!(!segs[1].fake);
        assert_eq!(segs[1].data, b"real");
    }

    #[test]
    fn badseq_forges_the_fake_segment_not_the_real_one() {
        let mut s = Strategy::none();
        s.desyncs.push(Desync::Fake);
        s.desyncs.push(Desync::Multidisorder);
        s.split_positions = vec![SplitPos::Absolute(1)];
        s.fooling_badseq = true;
        let res = Resources {
            fake_tls: Some(b"FAKE"),
            ..Resources::default()
        };
        let segs = plan(&s, b"abc", 777, &res);
        // fake, then "bc", then "a"
        assert!(segs[0].fake);
        // The fake carries the forged sequence: stream position plus the
        // default increment (-10000), never the real position.
        assert_eq!(
            segs[0].seq_override,
            Some(777u32.wrapping_add(BADSEQ_INCREMENT_DEFAULT))
        );
        // The real segments keep the kernel's sequence.
        assert_eq!(segs[1].seq_override, None);
        assert_eq!(segs[2].seq_override, None);
    }

    #[test]
    fn badseq_without_a_fake_leaves_every_segment_alone() {
        // `badseq` fools a fake packet; a plain split has no fake to forge, so
        // the option must not touch a real segment's sequence.
        let mut s = Strategy::none();
        s.desyncs.push(Desync::Multisplit);
        s.split_positions = vec![SplitPos::Absolute(2)];
        s.fooling_badseq = true;
        let segs = plan(&s, b"abcdef", 1000, &Resources::default());
        assert_eq!(segs.len(), 2);
        assert!(segs.iter().all(|seg| seg.seq_override.is_none()));
    }

    #[test]
    fn userspace_transport_reports_fake_and_badseq_as_unsupported() {
        let mut s = Strategy::none();
        s.desyncs.push(Desync::Fake);
        s.fooling_badseq = true;
        let reasons = s.unsupported(TransportKind::Userspace);
        assert_eq!(reasons.len(), 2);
        assert!(s.unsupported(TransportKind::WinDivert).is_empty());
    }

    #[test]
    fn a_plain_split_is_supported_everywhere() {
        let mut s = Strategy::none();
        s.desyncs.push(Desync::Multisplit);
        s.split_positions = vec![SplitPos::Absolute(1)];
        assert!(s.unsupported(TransportKind::Userspace).is_empty());
        assert!(s.unsupported(TransportKind::WinDivert).is_empty());
    }

    #[test]
    fn userspace_names_every_stream_impossibility() {
        let mut s = Strategy::none();
        s.desyncs.push(Desync::Fake);
        s.desyncs.push(Desync::Multidisorder);
        s.fooling_badseq = true;
        s.split_seqovl = Some(10);
        let reasons = s.unsupported(TransportKind::Userspace);
        assert_eq!(reasons.len(), 4, "{reasons:?}");
    }

    #[test]
    fn userspace_chunks_split_without_reordering() {
        let mut s = Strategy::none();
        s.desyncs.push(Desync::Multisplit);
        s.split_positions = vec![SplitPos::Absolute(2), SplitPos::Absolute(4)];
        let chunks = s.userspace_chunks(b"abcdef");
        assert_eq!(chunks, vec![b"ab".to_vec(), b"cd".to_vec(), b"ef".to_vec()]);
        let joined: Vec<u8> = chunks.into_iter().flatten().collect();
        assert_eq!(joined, b"abcdef");
    }

    #[test]
    fn userspace_chunks_with_no_split_is_the_whole_payload() {
        let s = Strategy::none();
        let chunks = s.userspace_chunks(b"abcdef");
        assert_eq!(chunks, vec![b"abcdef".to_vec()]);
    }

    #[test]
    fn fake_desync_is_reported_as_needing_packets() {
        let mut s = Strategy::none();
        s.desyncs.push(Desync::Fake);
        assert!(s.needs_fake_packets());
        assert!(!Strategy::none().needs_fake_packets());
    }
}
