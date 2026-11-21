//! Packet parsing and rewriting for the WinDivert transport.
//!
//! This is pure Rust on a byte buffer: no driver, no FFI. Everything here is
//! testable, and it is the part most likely to be silently wrong: an
//! off-by-one in a header offset or a broken checksum produces packets that
//! look fine and are dropped by the peer. So the layout math and the checksums
//! live here, behind tests, and the FFI layer in `sys` only shuttles buffers.
//!
//! Supported: IPv4 and IPv6, TCP and UDP, with and without options/extension
//! headers. Anything else (fragments with a non-zero offset, ICMP, unknown
//! next headers) is reported as `None` rather than guessed at.

/// Which transport protocol a packet carries.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Proto {
    Tcp,
    Udp,
}

/// A parsed view of a captured packet: the outer IP header, the transport
/// header, and the application payload, each as a byte range.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Packet<'a> {
    pub raw: &'a [u8],
    /// `true` for IPv6.
    pub ipv6: bool,
    /// Bit 0 of the IP DSCP/version-adjacent flags: the "this is outbound" bit
    /// WinDivert sets. We do not rely on it for direction, but keep it.
    pub proto: Proto,
    /// Range of the IP header.
    pub ip_header: std::ops::Range<usize>,
    /// Range of the transport header (TCP or UDP).
    pub transport_header: std::ops::Range<usize>,
    /// Range of the application payload.
    pub payload: std::ops::Range<usize>,
    /// Source and destination ports, read from the transport header.
    pub src_port: u16,
    pub dst_port: u16,
    /// TCP sequence number (0 for UDP).
    pub seq: u32,
    /// True when this TCP packet sets SYN (0 for UDP).
    pub syn: bool,
    /// True when this TCP packet sets FIN.
    pub fin: bool,
    /// True when this TCP packet sets RST.
    pub rst: bool,
    /// True when this TCP packet sets PSH.
    pub psh: bool,
    /// True when this TCP packet sets ACK.
    pub ack: bool,
}

const IPPROTO_TCP: u8 = 6;
const IPPROTO_UDP: u8 = 17;

impl<'a> Packet<'a> {
    /// Parses a captured packet. Returns `None` for anything this layer does
    /// not model: IPv4 fragments with a non-zero offset, non-TCP/UDP, or a
    /// truncated header.
    pub fn parse(raw: &'a [u8]) -> Option<Self> {
        let version = raw.first()? >> 4;
        match version {
            4 => Self::parse_v4(raw),
            6 => Self::parse_v6(raw),
            _ => None,
        }
    }

    fn parse_v4(raw: &'a [u8]) -> Option<Self> {
        if raw.len() < 20 {
            return None;
        }
        let ihl = (raw[0] & 0x0F) as usize * 4;
        if ihl < 20 || raw.len() < ihl {
            return None;
        }
        // Fragment offset must be zero; we do not reassemble.
        let frag = u16::from_be_bytes([raw[6], raw[7]]);
        if frag & 0x1FFF != 0 {
            return None;
        }
        let proto = match raw[9] {
            IPPROTO_TCP => Proto::Tcp,
            IPPROTO_UDP => Proto::Udp,
            _ => return None,
        };
        let total_len = u16::from_be_bytes([raw[2], raw[3]]) as usize;
        let end = total_len.min(raw.len()).max(ihl);
        Self::parse_transport(raw, false, proto, 0..ihl, ihl, end)
    }

    fn parse_v6(raw: &'a [u8]) -> Option<Self> {
        if raw.len() < 40 {
            return None;
        }
        let mut next = raw[6];
        let mut at = 40usize;
        // Walk the extension headers we understand; anything else ends the walk.
        loop {
            match next {
                IPPROTO_TCP | IPPROTO_UDP => break,
                0 | 43 | 60 => {
                    // hop-by-hop, routing, destination options: 8-byte units
                    let len = (*raw.get(at + 1)? as usize + 1) * 8;
                    next = *raw.get(at)?;
                    at += len;
                    if at > raw.len() {
                        return None;
                    }
                }
                44 => {
                    // fragment header: refuse (no reassembly)
                    return None;
                }
                51 => {
                    // AH: length is in 4-byte units, minus 2
                    let len = (*raw.get(at + 1)? as usize + 2) * 4;
                    next = *raw.get(at)?;
                    at += len;
                    if at > raw.len() {
                        return None;
                    }
                }
                _ => return None,
            }
        }
        let proto = if next == IPPROTO_TCP {
            Proto::Tcp
        } else {
            Proto::Udp
        };
        let payload_len = u16::from_be_bytes([raw[4], raw[5]]) as usize;
        let end = (40 + payload_len).min(raw.len()).max(at);
        Self::parse_transport(raw, true, proto, 0..40, at, end)
    }

    fn parse_transport(
        raw: &'a [u8],
        ipv6: bool,
        proto: Proto,
        ip_header: std::ops::Range<usize>,
        start: usize,
        end: usize,
    ) -> Option<Self> {
        if end > raw.len() {
            return None;
        }
        match proto {
            Proto::Tcp => {
                if end < start + 20 {
                    return None;
                }
                let src_port = u16::from_be_bytes([raw[start], raw[start + 1]]);
                let dst_port = u16::from_be_bytes([raw[start + 2], raw[start + 3]]);
                let seq = u32::from_be_bytes([
                    raw[start + 4],
                    raw[start + 5],
                    raw[start + 6],
                    raw[start + 7],
                ]);
                let doff = (raw[start + 12] >> 4) as usize * 4;
                if doff < 20 || end < start + doff {
                    return None;
                }
                let flags = raw[start + 13];
                Some(Self {
                    raw,
                    ipv6,
                    proto,
                    ip_header,
                    transport_header: start..start + doff,
                    payload: start + doff..end,
                    src_port,
                    dst_port,
                    seq,
                    fin: flags & 0x01 != 0,
                    syn: flags & 0x02 != 0,
                    rst: flags & 0x04 != 0,
                    psh: flags & 0x08 != 0,
                    ack: flags & 0x10 != 0,
                })
            }
            Proto::Udp => {
                if end < start + 8 {
                    return None;
                }
                Some(Self {
                    raw,
                    ipv6,
                    proto,
                    ip_header,
                    transport_header: start..start + 8,
                    payload: start + 8..end,
                    src_port: u16::from_be_bytes([raw[start], raw[start + 1]]),
                    dst_port: u16::from_be_bytes([raw[start + 2], raw[start + 3]]),
                    seq: 0,
                    syn: false,
                    fin: false,
                    rst: false,
                    psh: false,
                    ack: false,
                })
            }
        }
    }

    /// The application payload bytes.
    pub fn payload_bytes(&self) -> &'a [u8] {
        &self.raw[self.payload.clone()]
    }

    /// The whole transport segment: header plus payload. This is exactly the
    /// range the TCP/UDP checksum covers, so verification code should slice
    /// here and not at `transport_header`.
    pub fn l4_segment(&self) -> std::ops::Range<usize> {
        self.transport_header.start..self.payload.end
    }

    /// Index in the buffer of the TCP sequence field.
    pub fn seq_offset(&self) -> Option<usize> {
        (self.proto == Proto::Tcp).then(|| self.transport_header.start + 4)
    }
}

/// RFC 1071 one's-complement checksum over `data`, with an optional 16-bit
/// pseudo-header sum already accumulated.
///
/// `initial` seeds the sum, which is how the IPv6/TCP pseudo-header is folded
/// in: accumulate the pseudo-header words first, pass the running sum here.
pub fn ones_complement_sum(data: &[u8], initial: u32) -> u16 {
    let mut sum = initial;
    let (pairs, remainder) = data.as_chunks::<2>();
    for pair in pairs {
        sum += u16::from_be_bytes(*pair) as u32;
    }
    if let [last] = remainder {
        sum += (*last as u32) << 8;
    }
    while sum >> 16 != 0 {
        sum = (sum & 0xFFFF) + (sum >> 16);
    }
    !(sum as u16)
}

/// The IPv4-style pseudo-header sum for a TCP/UDP segment over IPv4.
fn pseudo_v4(src: &[u8], dst: &[u8], proto: u8, len: u16) -> u32 {
    let mut sum = 0u32;
    for pair in src
        .as_chunks::<2>()
        .0
        .iter()
        .chain(dst.as_chunks::<2>().0.iter())
    {
        sum += u16::from_be_bytes(*pair) as u32;
    }
    sum += proto as u32;
    sum += len as u32;
    sum
}

/// The IPv6 pseudo-header sum (RFC 2460 §8.1).
fn pseudo_v6(src: &[u8], dst: &[u8], proto: u8, len: u32) -> u32 {
    let mut sum = 0u32;
    for pair in src
        .as_chunks::<2>()
        .0
        .iter()
        .chain(dst.as_chunks::<2>().0.iter())
    {
        sum += u16::from_be_bytes(*pair) as u32;
    }
    sum += (len >> 16) & 0xFFFF;
    sum += len & 0xFFFF;
    sum += proto as u32;
    sum
}

/// Recomputes and writes the IPv4 header checksum in place.
pub fn fix_ipv4_header_checksum(buf: &mut [u8]) -> Option<()> {
    if buf.len() < 20 || buf[0] >> 4 != 4 {
        return None;
    }
    let ihl = (buf[0] & 0x0F) as usize * 4;
    if buf.len() < ihl {
        return None;
    }
    buf[10] = 0;
    buf[11] = 0;
    let sum = ones_complement_sum(&buf[..ihl], 0);
    buf[10] = (sum >> 8) as u8;
    buf[11] = sum as u8;
    Some(())
}

/// Recomputes and writes the TCP/UDP checksum using the IP addresses from the
/// packet itself.
pub fn fix_transport_checksum(packet: &mut [u8]) -> Option<()> {
    let version = packet.first()? >> 4;
    let (ip_hdr_len, proto, src, dst, l4_start, l4_len) = match version {
        4 => {
            if packet.len() < 20 {
                return None;
            }
            let ihl = (packet[0] & 0x0F) as usize * 4;
            if packet.len() < ihl {
                return None;
            }
            let proto = packet[9];
            let src = packet[12..16].to_vec();
            let dst = packet[16..20].to_vec();
            let total = u16::from_be_bytes([packet[2], packet[3]]) as usize;
            let l4_start = ihl;
            let l4_len = total.saturating_sub(ihl).min(packet.len() - ihl);
            (ihl, proto, src, dst, l4_start, l4_len)
        }
        6 => {
            if packet.len() < 40 {
                return None;
            }
            let mut next = packet[6];
            let mut at = 40usize;
            loop {
                match next {
                    IPPROTO_TCP | IPPROTO_UDP => break,
                    0 | 43 | 60 => {
                        let len = (*packet.get(at + 1)? as usize + 1) * 8;
                        next = *packet.get(at)?;
                        at += len;
                        if at > packet.len() {
                            return None;
                        }
                    }
                    51 => {
                        let len = (*packet.get(at + 1)? as usize + 2) * 4;
                        next = *packet.get(at)?;
                        at += len;
                        if at > packet.len() {
                            return None;
                        }
                    }
                    _ => return None,
                }
            }
            let src = packet[8..24].to_vec();
            let dst = packet[24..40].to_vec();
            let payload_len = u16::from_be_bytes([packet[4], packet[5]]) as usize;
            let l4_start = at;
            let l4_len = (40 + payload_len).saturating_sub(at).min(packet.len() - at);
            (at, next, src, dst, l4_start, l4_len)
        }
        _ => return None,
    };
    let _ = ip_hdr_len;

    let checksum_at = match proto {
        IPPROTO_TCP => {
            if l4_len < 20 {
                return None;
            }
            l4_start + 16
        }
        IPPROTO_UDP => {
            if l4_len < 8 {
                return None;
            }
            // UDP checksum is optional over IPv4 (0 means "not computed").
            l4_start + 6
        }
        _ => return None,
    };
    packet[checksum_at] = 0;
    packet[checksum_at + 1] = 0;
    let segment = &packet[l4_start..l4_start + l4_len];
    let initial = if version == 4 {
        pseudo_v4(&src, &dst, proto, l4_len as u16)
    } else {
        pseudo_v6(&src, &dst, proto, l4_len as u32)
    };
    let sum = ones_complement_sum(segment, initial);
    packet[checksum_at] = (sum >> 8) as u8;
    packet[checksum_at + 1] = sum as u8;
    Some(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Builds a minimal IPv4 + TCP packet around `payload`.
    fn ipv4_tcp(payload: &[u8], seq: u32, flags: u8) -> Vec<u8> {
        let mut tcp = vec![0u8; 20];
        tcp[0..2].copy_from_slice(&12345u16.to_be_bytes());
        tcp[2..4].copy_from_slice(&443u16.to_be_bytes());
        tcp[4..8].copy_from_slice(&seq.to_be_bytes());
        tcp[12] = 5 << 4; // data offset 5
        tcp[13] = flags;
        tcp.extend_from_slice(payload);

        let total = 20 + tcp.len();
        let mut ip = vec![0u8; 20];
        ip[0] = 0x45;
        ip[2..4].copy_from_slice(&(total as u16).to_be_bytes());
        ip[8] = 64; // TTL
        ip[9] = IPPROTO_TCP;
        ip[12..16].copy_from_slice(&[10, 0, 0, 1]);
        ip[16..20].copy_from_slice(&[10, 0, 0, 2]);
        let mut packet = ip;
        packet.extend_from_slice(&tcp);
        fix_ipv4_header_checksum(&mut packet).unwrap();
        fix_transport_checksum(&mut packet).unwrap();
        packet
    }

    fn udp(payload: &[u8], v6: bool) -> Vec<u8> {
        let mut udp = vec![0u8; 8];
        udp[0..2].copy_from_slice(&53000u16.to_be_bytes());
        udp[2..4].copy_from_slice(&443u16.to_be_bytes());
        udp[4..6].copy_from_slice(&((8 + payload.len()) as u16).to_be_bytes());
        udp.extend_from_slice(payload);
        if !v6 {
            let total = 20 + udp.len();
            let mut ip = vec![0u8; 20];
            ip[0] = 0x45;
            ip[2..4].copy_from_slice(&(total as u16).to_be_bytes());
            ip[9] = IPPROTO_UDP;
            ip[12..16].copy_from_slice(&[10, 0, 0, 1]);
            ip[16..20].copy_from_slice(&[10, 0, 0, 2]);
            let mut packet = ip;
            packet.extend_from_slice(&udp);
            fix_ipv4_header_checksum(&mut packet).unwrap();
            fix_transport_checksum(&mut packet).unwrap();
            packet
        } else {
            let mut ip = vec![0u8; 40];
            ip[0] = 0x60;
            ip[4..6].copy_from_slice(&(udp.len() as u16).to_be_bytes());
            ip[6] = IPPROTO_UDP;
            ip[7] = 64;
            ip[8..24]
                .copy_from_slice(&[0x20, 0x01, 0x0d, 0xb8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]);
            ip[24..40]
                .copy_from_slice(&[0x20, 0x01, 0x0d, 0xb8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2]);
            let mut packet = ip;
            packet.extend_from_slice(&udp);
            fix_transport_checksum(&mut packet).unwrap();
            packet
        }
    }

    #[test]
    fn parses_an_ipv4_tcp_packet() {
        let p = ipv4_tcp(b"CLIENTHELLO", 1000, 0x18); // PSH|ACK
        let parsed = Packet::parse(&p).expect("parse");
        assert!(!parsed.ipv6);
        assert_eq!(parsed.proto, Proto::Tcp);
        assert_eq!(parsed.dst_port, 443);
        assert_eq!(parsed.src_port, 12345);
        assert_eq!(parsed.seq, 1000);
        assert!(parsed.psh && parsed.ack);
        assert!(!parsed.syn);
        assert_eq!(parsed.payload_bytes(), b"CLIENTHELLO");
    }

    #[test]
    fn a_correct_ipv4_checksum_verifies_to_zero() {
        let p = ipv4_tcp(b"data", 1, 0x18);
        // Recomputing over the header *including* the stored checksum must
        // yield 0 in one's-complement arithmetic, the standard verification
        // property, independent of the value we wrote.
        let sum = ones_complement_sum(&p[..20], 0);
        assert_eq!(sum, 0);
    }

    #[test]
    fn a_correct_tcp_checksum_verifies_to_zero() {
        let p = ipv4_tcp(b"payload", 42, 0x18);
        let parsed = Packet::parse(&p).expect("parse");
        let tcp = &p[parsed.l4_segment()];
        let src = [10u8, 0, 0, 1];
        let dst = [10u8, 0, 0, 2];
        let initial = pseudo_v4(&src, &dst, IPPROTO_TCP, tcp.len() as u16);
        assert_eq!(ones_complement_sum(tcp, initial), 0);
    }

    #[test]
    fn parses_ipv6_udp() {
        let p = udp(b"quicbytes", true);
        let parsed = Packet::parse(&p).expect("parse");
        assert!(parsed.ipv6);
        assert_eq!(parsed.proto, Proto::Udp);
        assert_eq!(parsed.dst_port, 443);
        assert_eq!(parsed.payload_bytes(), b"quicbytes");
    }

    #[test]
    fn ipv4_udp_checksum_verifies_to_zero() {
        let p = udp(b"quicbytes", false);
        let parsed = Packet::parse(&p).expect("parse");
        let udp = &p[parsed.l4_segment()];
        let initial = pseudo_v4(
            &[10, 0, 0, 1],
            &[10, 0, 0, 2],
            IPPROTO_UDP,
            udp.len() as u16,
        );
        assert_eq!(ones_complement_sum(udp, initial), 0);
    }

    #[test]
    fn ipv6_udp_checksum_verifies_to_zero() {
        let p = udp(b"quicbytes", true);
        let parsed = Packet::parse(&p).expect("parse");
        let udp = &p[parsed.l4_segment()];
        let src = p[8..24].to_vec();
        let dst = p[24..40].to_vec();
        let initial = pseudo_v6(&src, &dst, IPPROTO_UDP, udp.len() as u32);
        assert_eq!(ones_complement_sum(udp, initial), 0);
    }

    #[test]
    fn rejects_an_ipv4_fragment_with_a_nonzero_offset() {
        let mut p = ipv4_tcp(b"x", 0, 0x18);
        p[6] = 0x00;
        p[7] = 0x08; // fragment offset 1
        assert!(Packet::parse(&p).is_none());
    }

    #[test]
    fn rejects_a_truncated_header() {
        let p = ipv4_tcp(b"x", 0, 0x18);
        assert!(Packet::parse(&p[..10]).is_none());
    }

    #[test]
    fn rewriting_ports_keeps_checksums_valid() {
        let mut p = ipv4_tcp(b"hello", 7, 0x18);
        // Simulate a rewrite: change the source port and fix checksums.
        let parsed = Packet::parse(&p).expect("parse");
        let start = parsed.transport_header.start;
        p[start..start + 2].copy_from_slice(&55555u16.to_be_bytes());
        fix_transport_checksum(&mut p).unwrap();
        let parsed = Packet::parse(&p).expect("reparse");
        assert_eq!(parsed.src_port, 55555);
        let tcp = &p[parsed.l4_segment()];
        let initial = pseudo_v4(
            &[10, 0, 0, 1],
            &[10, 0, 0, 2],
            IPPROTO_TCP,
            tcp.len() as u16,
        );
        assert_eq!(ones_complement_sum(tcp, initial), 0);
    }

    #[test]
    fn seq_offset_points_at_the_sequence_field() {
        let p = ipv4_tcp(b"x", 0xDEADBEEF, 0x18);
        let parsed = Packet::parse(&p).expect("parse");
        let at = parsed.seq_offset().expect("seq offset");
        assert_eq!(&p[at..at + 4], &0xDEADBEEFu32.to_be_bytes());
    }
}
