//! The WinDivert transport: full zapret capability (fake packets, `badseq`).
//!
//! This sits on top of two pieces: [`packet`], which parses and rewrites
//! raw IP packets, and [`sys`], which loads `WinDivert.dll` at runtime. The
//! interesting logic, deciding for a captured packet which packets to
//! inject and in what order, is a pure function, [`decisions`], so it is
//! tested without the driver; the driver loop only applies the result.
//!
//! Running this for real needs administrator rights and the signed
//! `WinDivert64.sys` driver. When either is missing, [`WinDivert::load`] and
//! `open_handle` return a typed [`sys::Unavailable`] that names the next step;
//! nothing about a missing driver is allowed to look like a working capture.

pub mod packet;
pub mod sys;

use ayu_core::dpi::{Desync, Resources, Segment, Strategy, TransportKind, plan};

use packet::Packet;
use sys::WinDivert;

/// One packet to emit, built from a plan segment plus the template packet it
/// inherits its addresses and ports from.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Outgoing {
    /// The full packet bytes to send.
    pub packet: Vec<u8>,
    /// True when this is a fake the peer should discard.
    pub fake: bool,
    /// When set, overwrite the TCP sequence field with this value (badseq).
    pub seq_override: Option<u32>,
}

/// Decides which packets to send for one captured TCP packet carrying `payload`.
///
/// The captured packet's headers (addresses, ports, options) are reused
/// verbatim, so the injected packets belong to the same flow; only the payload
/// and, for splits, the TCP header flags/sequence are adjusted. A segment that
/// reuses the whole payload is returned as the original bytes unchanged.
pub fn decisions(captured: &[u8], strategy: &Strategy, resources: &Resources<'_>) -> Vec<Outgoing> {
    let parsed = match Packet::parse(captured) {
        Some(p) => p,
        None => return Vec::new(),
    };
    if parsed.proto != packet::Proto::Tcp {
        return Vec::new();
    }
    let us = strategy.unsupported(TransportKind::WinDivert);
    if !us.is_empty() {
        // The WinDivert transport is defined to honour everything; a report
        // here means the strategy uses something not implemented yet.
        return Vec::new();
    }

    let payload = parsed.payload_bytes();
    let segments = plan(strategy, payload, parsed.seq, resources);
    build_outgoing(captured, &parsed, &segments)
}

/// Builds the outgoing packet list from plan segments. Split into its own
/// function so the packet surgery is testable on its own.
fn build_outgoing(captured: &[u8], parsed: &Packet<'_>, segments: &[Segment]) -> Vec<Outgoing> {
    let mut out = Vec::new();
    for seg in segments {
        let mut packet = captured[..parsed.payload.start].to_vec();
        packet.extend_from_slice(&seg.data);
        // Fix the IPv4 total-length and both checksums for the new size.
        rewrite_lengths(&mut packet, parsed);
        let mut outgoing = Outgoing {
            packet,
            fake: seg.fake,
            seq_override: seg.seq_override,
        };
        if let Some(seq) = seg.seq_override
            && let Some(at) = Packet::parse(&outgoing.packet).and_then(|p| p.seq_offset())
        {
            outgoing.packet[at..at + 4].copy_from_slice(&seq.to_be_bytes());
            let _ = packet::fix_ipv4_header_checksum(&mut outgoing.packet);
            let _ = packet::fix_transport_checksum(&mut outgoing.packet);
        }
        out.push(outgoing);
    }
    out
}

/// Rewrites the IPv4 total length (or IPv6 payload length) after a payload
/// change, then fixes both checksums.
fn rewrite_lengths(packet: &mut [u8], parsed: &Packet<'_>) {
    if !parsed.ipv6 {
        let total = packet.len() as u16;
        packet[2..4].copy_from_slice(&total.to_be_bytes());
        let _ = packet::fix_ipv4_header_checksum(packet);
    } else {
        // The IP header carries the payload length (everything after it).
        let ip_len = parsed.ip_header.end - parsed.ip_header.start;
        let payload = (packet.len() - ip_len) as u16;
        packet[4..6].copy_from_slice(&payload.to_be_bytes());
    }
    let _ = packet::fix_transport_checksum(packet);
}

/// Whether this strategy needs the WinDivert transport at all. A strategy that
/// only splits can run over the userspace proxy; one that fakes or forges a
/// sequence cannot.
pub fn requires_windivert(strategy: &Strategy) -> bool {
    strategy.desyncs.iter().any(|d| {
        matches!(
            d,
            Desync::Fake | Desync::Syndata | Desync::FakedSplit | Desync::HostFakeSplit
        )
    }) || strategy.fooling_badseq
}

/// A running capture: the loaded DLL plus the open handle. Dropping it closes
/// the handle and unloads the DLL.
pub struct Capture {
    dll: WinDivert,
    handle: sys::Handle,
}

impl Capture {
    /// Loads WinDivert (optionally from `dir`) and opens a capture for `filter`.
    pub fn start(dir: Option<&std::path::Path>, filter: &str) -> Result<Self, sys::Unavailable> {
        let dll = WinDivert::load(dir)?;
        let handle = dll.open_handle(filter, 0, 0)?;
        Ok(Self { dll, handle })
    }

    /// Captures and re-injects one packet, applying `strategy`. Returns the
    /// number of packets written when a packet was handled, or `Ok(None)` when
    /// the packet was passed through untouched or not relevant.
    pub fn pump_once(
        &self,
        buffer: &mut [u8],
        strategy: &Strategy,
        resources: &Resources<'_>,
    ) -> Result<Option<usize>, std::io::Error> {
        let (len, addr) = self.dll.recv(self.handle, buffer)?;
        let captured = &buffer[..len];
        let outgoing = decisions(captured, strategy, resources);
        if outgoing.is_empty() {
            // Not ours to touch: reinject unchanged.
            self.dll.send(self.handle, captured, &addr)?;
            return Ok(None);
        }
        let mut written = 0;
        for packet in &outgoing {
            let mut bytes = packet.packet.clone();
            self.dll.calc_checksums(&mut bytes);
            self.dll.send(self.handle, &bytes, &addr)?;
            written += 1;
        }
        Ok(Some(written))
    }

    /// Loads the DLL and opens a capture, then reads one packet: a live probe
    /// that proves whether the driver is usable on this machine without
    /// entering the loop.
    pub fn probe(dir: Option<&std::path::Path>, filter: &str) -> Result<String, sys::Unavailable> {
        let capture = Self::start(dir, filter)?;
        let mut buffer = vec![0u8; 65535];
        match capture.dll.recv(capture.handle, &mut buffer) {
            Ok((len, _)) => Ok(format!("captured one packet ({len} bytes)")),
            Err(e) => Ok(format!("driver open but no packet read: {e}")),
        }
    }
}

impl Drop for Capture {
    fn drop(&mut self) {
        self.dll.close_handle(self.handle);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ayu_core::dpi::options::parse_line;

    /// Builds an IPv4 TCP packet carrying `payload`, matching the packet
    /// module's test builder so the arithmetic is identical.
    fn tcp_packet(payload: &[u8], seq: u32) -> Vec<u8> {
        let mut tcp = vec![0u8; 20];
        tcp[0..2].copy_from_slice(&12345u16.to_be_bytes());
        tcp[2..4].copy_from_slice(&443u16.to_be_bytes());
        tcp[4..8].copy_from_slice(&seq.to_be_bytes());
        tcp[12] = 5 << 4;
        tcp[13] = 0x18; // PSH|ACK
        tcp.extend_from_slice(payload);
        let total = 20 + tcp.len();
        let mut ip = vec![0u8; 20];
        ip[0] = 0x45;
        ip[2..4].copy_from_slice(&(total as u16).to_be_bytes());
        ip[8] = 64;
        ip[9] = 6; // TCP
        ip[12..16].copy_from_slice(&[10, 0, 0, 1]);
        ip[16..20].copy_from_slice(&[10, 0, 0, 2]);
        let mut packet = ip;
        packet.extend_from_slice(&tcp);
        packet::fix_ipv4_header_checksum(&mut packet).unwrap();
        packet::fix_transport_checksum(&mut packet).unwrap();
        packet
    }

    #[test]
    fn a_split_produces_two_packets() {
        let cfg = parse_line("--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1");
        let captured = tcp_packet(b"CLIENTHELLO", 100);
        let out = decisions(&captured, &cfg.rules[0].strategy, &Resources::default());
        assert_eq!(out.len(), 2);
        // First packet: 1 byte of payload; second: the rest.
        let p0 = Packet::parse(&out[0].packet).unwrap();
        let p1 = Packet::parse(&out[1].packet).unwrap();
        assert_eq!(p0.payload_bytes(), b"C");
        assert_eq!(p1.payload_bytes(), b"LIENTHELLO");
        // Reassembling the payloads gives the original.
        let mut joined = p0.payload_bytes().to_vec();
        joined.extend_from_slice(p1.payload_bytes());
        assert_eq!(joined, b"CLIENTHELLO");
    }

    #[test]
    fn split_packets_keep_valid_checksums() {
        let cfg = parse_line("--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1");
        let captured = tcp_packet(b"CLIENTHELLO", 100);
        let out = decisions(&captured, &cfg.rules[0].strategy, &Resources::default());
        for o in &out {
            let p = Packet::parse(&o.packet).unwrap();
            // IPv4 header checksum verifies to zero.
            assert_eq!(packet::ones_complement_sum(&o.packet[..20], 0), 0);
            // TCP checksum verifies to zero over the whole L4 segment.
            let tcp = &o.packet[p.l4_segment()];
            let src = [10u8, 0, 0, 1];
            let dst = [10u8, 0, 0, 2];
            let initial = {
                let mut s = 0u32;
                for pair in src
                    .as_chunks::<2>()
                    .0
                    .iter()
                    .chain(dst.as_chunks::<2>().0.iter())
                {
                    s += u16::from_be_bytes(*pair) as u32;
                }
                s + 6u32 + tcp.len() as u32
            };
            assert_eq!(packet::ones_complement_sum(tcp, initial), 0);
        }
    }

    #[test]
    fn a_fake_rule_emits_a_fake_then_the_real_packet() {
        let cfg = parse_line("--filter-tcp=443 --dpi-desync=fake --dpi-desync-fake-tls=0x1603");
        let captured = tcp_packet(b"REAL", 500);
        let res = Resources {
            fake_tls: Some(&[0x16, 0x03]),
            ..Resources::default()
        };
        let out = decisions(&captured, &cfg.rules[0].strategy, &res);
        assert_eq!(out.len(), 2);
        assert!(out[0].fake);
        assert!(!out[1].fake);
        assert_eq!(
            Packet::parse(&out[0].packet).unwrap().payload_bytes(),
            &[0x16, 0x03]
        );
        assert_eq!(
            Packet::parse(&out[1].packet).unwrap().payload_bytes(),
            b"REAL"
        );
    }

    #[test]
    fn badseq_with_a_fake_forges_the_fake_sequence() {
        let cfg = parse_line(
            "--filter-tcp=443 --dpi-desync=fake --dpi-desync-fooling=badseq \
             --dpi-desync-badseq-increment=-8",
        );
        let captured = tcp_packet(b"REAL", 0xABCDEF01);
        let resources = Resources {
            fake_tls: Some(&[0x16, 0x03]),
            ..Resources::default()
        };
        let out = decisions(&captured, &cfg.rules[0].strategy, &resources);
        assert_eq!(out.len(), 2);
        // The fake is emitted with the forged sequence; the real packet keeps
        // the captured sequence untouched.
        assert!(out[0].fake);
        assert_eq!(
            Packet::parse(&out[0].packet).unwrap().seq,
            0xABCDEF01u32.wrapping_sub(8)
        );
        assert!(!out[1].fake);
        assert_eq!(Packet::parse(&out[1].packet).unwrap().seq, 0xABCDEF01);
    }

    #[test]
    fn badseq_without_a_fake_leaves_the_sequence_field_alone() {
        let cfg = parse_line(
            "--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1 \
             --dpi-desync-fooling=badseq",
        );
        let captured = tcp_packet(b"XY", 0xABCDEF01);
        let out = decisions(&captured, &cfg.rules[0].strategy, &Resources::default());
        // `badseq` forges a fake packet; with no fake template there is nothing
        // to forge, so every emitted segment keeps the captured sequence.
        assert!(
            out.iter()
                .all(|o| Packet::parse(&o.packet).unwrap().seq == 0xABCDEF01)
        );
    }

    #[test]
    fn a_non_split_strategy_passes_one_packet() {
        let cfg = parse_line("--filter-tcp=443 --dpi-desync=multisplit");
        // No split positions -> one segment with the whole payload.
        let captured = tcp_packet(b"WHOLE", 1);
        let out = decisions(&captured, &cfg.rules[0].strategy, &Resources::default());
        assert_eq!(out.len(), 1);
        assert_eq!(
            Packet::parse(&out[0].packet).unwrap().payload_bytes(),
            b"WHOLE"
        );
    }

    #[test]
    fn udp_is_not_handled_by_the_tcp_path() {
        let mut captured = tcp_packet(b"x", 1);
        captured[9] = 17; // mark as UDP (bytes are still TCP-shaped)
        let cfg = parse_line("--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1");
        let out = decisions(&captured, &cfg.rules[0].strategy, &Resources::default());
        assert!(out.is_empty());
    }

    #[test]
    fn fake_and_badseq_require_windivert() {
        let simple =
            parse_line("--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1");
        assert!(!requires_windivert(&simple.rules[0].strategy));
        let fake = parse_line("--filter-tcp=443 --dpi-desync=fake");
        assert!(requires_windivert(&fake.rules[0].strategy));
    }
}
