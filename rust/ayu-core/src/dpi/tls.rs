//! TLS record and ClientHello parsing, and the ClientHello rewrites the desync
//! strategies need.
//!
//! Two things in the strategy language are expressed in terms of the bytes a
//! TLS client actually sends, and neither can be approximated by a fixed
//! offset:
//!
//! * a split point of `midsld` means "the middle of the second-level domain in
//!   the SNI", and the SNI moves with every hostname;
//! * a fake ClientHello carries a *different* SNI from a template file, so the
//!   template's length fields have to be rebuilt around the new name.
//!
//! Everything here works on a byte slice and does not allocate except when it
//! rebuilds a record. Malformed input yields `None` rather than a panic: the
//! payload is network data.

/// Byte range of a field inside a buffer.
pub type Span = std::ops::Range<usize>;

const CONTENT_TYPE_HANDSHAKE: u8 = 0x16;
const HANDSHAKE_CLIENT_HELLO: u8 = 0x01;
const EXT_SERVER_NAME: u16 = 0x0000;
const SNI_HOST_NAME: u8 = 0x00;

fn be16(buf: &[u8], at: usize) -> Option<u16> {
    let hi = *buf.get(at)? as u16;
    let lo = *buf.get(at + 1)? as u16;
    Some((hi << 8) | lo)
}

fn be24(buf: &[u8], at: usize) -> Option<usize> {
    let a = *buf.get(at)? as usize;
    let b = *buf.get(at + 1)? as usize;
    let c = *buf.get(at + 2)? as usize;
    Some((a << 16) | (b << 8) | c)
}

fn put_be16(buf: &mut [u8], at: usize, value: u16) {
    let bytes = value.to_be_bytes();
    buf[at] = bytes[0];
    buf[at + 1] = bytes[1];
}

/// Where the interesting fields of a ClientHello live.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClientHello {
    /// Offset of the 2-byte SNI *host_name* length field.
    pub sni_len_at: usize,
    /// Offset of the 2-byte SNI list length (the field that wraps the single
    /// host_name entry inside the extension body).
    pub sni_list_len_at: usize,
    /// Offset of the 2-byte `server_name` extension length.
    pub ext_len_at: usize,
    /// Byte range of the SNI hostname itself.
    pub sni: Span,
    /// Byte range of the 32-byte ClientHello random.
    pub random: Span,
    /// Byte range of the session id.
    pub session_id: Span,
    /// Offset of the 2-byte outer extensions length.
    pub extensions_len_at: usize,
    /// Offset of the 3-byte handshake length.
    pub handshake_len_at: usize,
    /// Offset of the 2-byte TLS record length.
    pub record_len_at: usize,
}

impl ClientHello {
    /// Parses the ClientHello out of a TLS record.
    ///
    /// Returns `None` when the buffer is not a handshake record holding a
    /// ClientHello with a `server_name` extension. Callers that only need the
    /// SNI span and the split anchor treat that as "no advanced positions".
    pub fn parse(record: &[u8]) -> Option<Self> {
        if *record.first()? != CONTENT_TYPE_HANDSHAKE {
            return None;
        }
        let record_len_at = 3;
        if *record.get(5)? != HANDSHAKE_CLIENT_HELLO {
            return None;
        }
        let handshake_len_at = 6;
        let mut at = 9; // client_version
        at += 2;
        let random = at..at + 32;
        at += 32; // random
        let session_id_len = *record.get(at)? as usize;
        let session_id = at + 1..at + 1 + session_id_len;
        at += 1 + session_id_len;
        let suites_len = be16(record, at)? as usize;
        at += 2 + suites_len;
        let compression_len = *record.get(at)? as usize;
        at += 1 + compression_len;
        let extensions_len_at = at;
        let extensions_len = be16(record, at)? as usize;
        at += 2;
        let extensions_end = at.checked_add(extensions_len)?;
        if extensions_end > record.len() {
            return None;
        }

        while at + 4 <= extensions_end {
            let ext_type = be16(record, at)?;
            let ext_len = be16(record, at + 2)? as usize;
            let ext_body = at + 4;
            if ext_body + ext_len > extensions_end {
                return None;
            }
            if ext_type == EXT_SERVER_NAME {
                // The extension body is the SNI list: a 2-byte list length,
                // then one or more names. Each name is name_type (1 byte),
                // host_name length (2 bytes) and the bytes themselves.
                let list_len = be16(record, ext_body)? as usize;
                if ext_body + 2 + list_len > ext_body + ext_len {
                    return None;
                }
                let name_type_at = ext_body + 2;
                if *record.get(name_type_at)? != SNI_HOST_NAME {
                    return None;
                }
                let sni_len_at = name_type_at + 1;
                let sni_len = be16(record, sni_len_at)? as usize;
                let sni_start = sni_len_at + 2;
                if sni_start + sni_len > ext_body + ext_len {
                    return None;
                }
                return Some(Self {
                    sni_len_at,
                    sni_list_len_at: ext_body,
                    ext_len_at: at + 2,
                    sni: sni_start..sni_start + sni_len,
                    random,
                    session_id,
                    extensions_len_at,
                    handshake_len_at,
                    record_len_at,
                });
            }
            at = ext_body + ext_len;
        }
        None
    }

    /// The SNI hostname as a string, if it is valid UTF-8.
    pub fn hostname<'a>(&self, record: &'a [u8]) -> Option<&'a str> {
        std::str::from_utf8(record.get(self.sni.clone())?).ok()
    }

    /// Offset in the record of the middle of the second-level domain.
    ///
    /// This is zapret's `midsld`. Scanning labels from the right, the SLD is
    /// the label after the second dot; with fewer than two dots the whole name
    /// is treated as the SLD. The returned offset is the midpoint of that
    /// label, counted from the start of the record.
    pub fn midsld_offset(&self, record: &[u8]) -> Option<usize> {
        let host = record.get(self.sni.clone())?;
        let sld_start = second_level_domain_start(host);
        // The SLD runs from its first byte to the next dot (or the end).
        let sld_end = host[sld_start..]
            .iter()
            .position(|&b| b == b'.')
            .map(|p| sld_start + p)
            .unwrap_or(host.len());
        let sld_len = sld_end - sld_start;
        Some(self.sni.start + sld_start + sld_len / 2)
    }

    /// Offset of the byte after the host, i.e. `host+len`. zapret accepts
    /// `host+N`, so exposing the base lets the strategy layer add N.
    pub fn host_start(&self) -> usize {
        self.sni.start
    }

    /// Rebuilds the record with the SNI replaced by `new_host`, fixing every
    /// length field on the way up. Returns the new record.
    ///
    /// The extension list length, handshake length and record length all grow
    /// or shrink by the same delta as the hostname, and each is rewritten. The
    /// delta must keep each field in range for its width.
    pub fn rewrite_hostname(&self, record: &[u8], new_host: &str) -> Option<Vec<u8>> {
        let new_len = new_host.len();
        if new_len > u16::MAX as usize {
            return None;
        }
        let old_len = self.sni.len();
        let delta = new_len as isize - old_len as isize;

        let mut out = Vec::with_capacity((record.len() as isize + delta) as usize);
        out.extend_from_slice(&record[..self.sni.start]);
        out.extend_from_slice(new_host.as_bytes());
        out.extend_from_slice(&record[self.sni.end..]);

        // host_name length
        put_be16(&mut out, self.sni_len_at, new_len as u16);
        // SNI list length: name_type (1) + host_name length (2) + host bytes.
        let list = 3isize + new_len as isize;
        if !(0..=u16::MAX as isize).contains(&list) {
            return None;
        }
        put_be16(&mut out, self.sni_list_len_at, list as u16);
        // server_name extension length: the 2-byte list length plus the list.
        let ext_body = be16(record, self.ext_len_at)? as isize + delta;
        if !(0..=u16::MAX as isize).contains(&ext_body) {
            return None;
        }
        put_be16(&mut out, self.ext_len_at, ext_body as u16);
        // extensions length (2 bytes)
        let ext = be16(record, self.extensions_len_at)? as isize + delta;
        if !(0..=u16::MAX as isize).contains(&ext) {
            return None;
        }
        put_be16(&mut out, self.extensions_len_at, ext as u16);
        // handshake length (3 bytes)
        let hs = be24(record, self.handshake_len_at)? as isize + delta;
        if !(0..=0xFF_FFFF).contains(&hs) {
            return None;
        }
        let hs = hs as usize;
        out[self.handshake_len_at] = (hs >> 16) as u8;
        out[self.handshake_len_at + 1] = (hs >> 8) as u8;
        out[self.handshake_len_at + 2] = hs as u8;
        // record length (2 bytes)
        let rec = be16(record, self.record_len_at)? as isize + delta;
        if !(0..=u16::MAX as isize).contains(&rec) {
            return None;
        }
        put_be16(&mut out, self.record_len_at, rec as u16);
        Some(out)
    }

    /// Applies `--dpi-desync-fake-tls-mod` to a template record: swaps the SNI,
    /// randomises the ClientHello random, and duplicates the session id, in
    /// that order (the order zapret documents).
    ///
    /// `random_fill` is called with the byte count and must return exactly that
    /// many bytes; passing a fixed generator keeps tests deterministic. Returns
    /// `None` when a requested rewrite cannot be applied to the template.
    pub fn apply_mods(
        &self,
        record: &[u8],
        sni: Option<&str>,
        randomize: bool,
        dup_session_id: bool,
        random_fill: &mut dyn FnMut(usize) -> Vec<u8>,
    ) -> Option<Vec<u8>> {
        let mut out = record.to_vec();
        if let Some(host) = sni {
            out = self.rewrite_hostname(&out, host)?;
            // The offsets are unchanged by a same-layout rewrite only when the
            // host length is unchanged; re-parse so every later field offset is
            // correct for the new record.
            let reparsed = ClientHello::parse(&out)?;
            return reparsed.apply_mods(&out, None, randomize, dup_session_id, random_fill);
        }
        if randomize {
            let n = self.random.len();
            let bytes = random_fill(n);
            if bytes.len() != n {
                return None;
            }
            out[self.random.clone()].copy_from_slice(&bytes);
        }
        if dup_session_id {
            let sid = out[self.session_id.clone()].to_vec();
            if !sid.is_empty() {
                // Append a second copy of the session id by letting the parser
                // tell us where its length byte is, then rebuilding outward.
                let mut doubled = Vec::with_capacity(out.len() + sid.len());
                doubled.extend_from_slice(&out[..self.session_id.start - 1]);
                doubled.push((sid.len() * 2) as u8);
                doubled.extend_from_slice(&sid);
                doubled.extend_from_slice(&sid);
                doubled.extend_from_slice(&out[self.session_id.end..]);
                let delta = sid.len() as isize;
                // extensions length
                let el = be16(&out, self.extensions_len_at)? as isize + delta;
                if !(0..=u16::MAX as isize).contains(&el) {
                    return None;
                }
                put_be16(&mut doubled, self.extensions_len_at, el as u16);
                // handshake length
                let hl = be24(&out, self.handshake_len_at)? as isize + delta;
                if !(0..=0xFF_FFFF).contains(&hl) {
                    return None;
                }
                let hl = hl as usize;
                doubled[self.handshake_len_at] = (hl >> 16) as u8;
                doubled[self.handshake_len_at + 1] = (hl >> 8) as u8;
                doubled[self.handshake_len_at + 2] = hl as u8;
                // record length
                let rl = be16(&out, self.record_len_at)? as isize + delta;
                if !(0..=u16::MAX as isize).contains(&rl) {
                    return None;
                }
                put_be16(&mut doubled, self.record_len_at, rl as u16);
                out = doubled;
            }
        }
        Some(out)
    }
}

/// Offset of the first byte of the second-level domain inside a hostname.
fn second_level_domain_start(host: &[u8]) -> usize {
    let mut dots = 0;
    let mut i = host.len();
    while i > 0 {
        i -= 1;
        if host[i] == b'.' {
            dots += 1;
            if dots == 2 {
                return i + 1;
            }
        }
    }
    0
}

/// Splits `payload` at `at` and prepends `pattern`'s bytes as an overlap,
/// returning `(overlap, rest)`.
///
/// This is `--dpi-desync-split-seqovl`: the second segment re-sends the last
/// `n` bytes before the split, and when a pattern is configured those bytes are
/// the pattern instead of the real data. The overlap is returned separately so
/// the transport can send it with the right sequence arithmetic.
pub fn seqovl_overlap(pattern: Option<&[u8]>, real_prefix: &[u8], n: usize) -> Vec<u8> {
    let mut overlap = Vec::with_capacity(n);
    if let Some(pat) = pattern
        && !pat.is_empty()
    {
        while overlap.len() < n {
            let take = (n - overlap.len()).min(pat.len());
            overlap.extend_from_slice(&pat[..take]);
        }
        overlap.truncate(n);
        return overlap;
    }
    let start = real_prefix.len().saturating_sub(n);
    overlap.extend_from_slice(&real_prefix[start..]);
    overlap
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Builds a minimal but structurally valid ClientHello for `host`.
    fn client_hello(host: &str) -> Vec<u8> {
        let mut ext = Vec::new();
        // server_name
        let mut sni_list = Vec::new();
        sni_list.push(SNI_HOST_NAME);
        sni_list.extend_from_slice(&(host.len() as u16).to_be_bytes());
        sni_list.extend_from_slice(host.as_bytes());
        let mut sni = Vec::new();
        sni.extend_from_slice(&(sni_list.len() as u16).to_be_bytes());
        sni.extend_from_slice(&sni_list);
        ext.extend_from_slice(&EXT_SERVER_NAME.to_be_bytes());
        ext.extend_from_slice(&(sni.len() as u16).to_be_bytes());
        ext.extend_from_slice(&sni);

        let mut body = Vec::new();
        body.extend_from_slice(&[0x03, 0x03]); // client_version
        body.extend_from_slice(&[0u8; 32]); // random
        body.push(0); // session id length
        body.extend_from_slice(&2u16.to_be_bytes()); // cipher suites length
        body.extend_from_slice(&[0x13, 0x01]); // one suite
        body.push(1); // compression methods length
        body.push(0); // null
        body.extend_from_slice(&(ext.len() as u16).to_be_bytes());
        body.extend_from_slice(&ext);

        let mut hs = Vec::new();
        hs.push(HANDSHAKE_CLIENT_HELLO);
        let l = body.len();
        hs.extend_from_slice(&[(l >> 16) as u8, (l >> 8) as u8, l as u8]);
        hs.extend_from_slice(&body);

        let mut rec = Vec::new();
        rec.push(CONTENT_TYPE_HANDSHAKE);
        rec.extend_from_slice(&[0x03, 0x01]);
        rec.extend_from_slice(&(hs.len() as u16).to_be_bytes());
        rec.extend_from_slice(&hs);
        rec
    }

    #[test]
    fn parses_sni_span() {
        let rec = client_hello("telegram.org");
        let ch = ClientHello::parse(&rec).expect("parse");
        assert_eq!(ch.hostname(&rec), Some("telegram.org"));
        assert_eq!(&rec[ch.sni.clone()], b"telegram.org");
    }

    #[test]
    fn midsld_points_inside_the_second_level_domain() {
        let rec = client_hello("www.google.com");
        let ch = ClientHello::parse(&rec).expect("parse");
        let pos = ch.midsld_offset(&rec).expect("midsld");
        // SLD is "google"; its middle must fall strictly inside it.
        let google_start = ch.sni.start + 4; // after "www."
        let google_end = google_start + 6;
        assert!(pos > google_start && pos < google_end, "pos={pos}");
    }

    #[test]
    fn midsld_for_two_label_host_is_middle_of_the_name() {
        let rec = client_hello("example.ru");
        let ch = ClientHello::parse(&rec).expect("parse");
        assert_eq!(
            ch.midsld_offset(&rec),
            Some(ch.sni.start + "example".len() / 2)
        );
    }

    #[test]
    fn rejects_non_handshake_records() {
        let mut rec = client_hello("x.com");
        rec[0] = 0x17; // application data
        assert!(ClientHello::parse(&rec).is_none());
    }

    #[test]
    fn rejects_truncated_records() {
        let rec = client_hello("telegram.org");
        assert!(ClientHello::parse(&rec[..10]).is_none());
    }

    #[test]
    fn rewrite_keeps_every_length_consistent() {
        let rec = client_hello("telegram.org");
        let ch = ClientHello::parse(&rec).expect("parse");
        let out = ch
            .rewrite_hostname(&rec, "www.google.com")
            .expect("rewrite");
        let reparsed = ClientHello::parse(&out).expect("reparse");
        assert_eq!(reparsed.hostname(&out), Some("www.google.com"));
        // record length field must match the actual record length
        assert_eq!(u16::from_be_bytes([out[3], out[4]]) as usize, out.len() - 5);
        let hs = ((out[6] as usize) << 16) | ((out[7] as usize) << 8) | out[8] as usize;
        assert_eq!(hs, out.len() - 5 - 4);
    }

    #[test]
    fn rewrite_shrinks_when_the_new_host_is_shorter() {
        let rec = client_hello("www.google.com");
        let ch = ClientHello::parse(&rec).expect("parse");
        let out = ch.rewrite_hostname(&rec, "t.me").expect("rewrite");
        let reparsed = ClientHello::parse(&out).expect("reparse");
        assert_eq!(reparsed.hostname(&out), Some("t.me"));
        assert!(out.len() < rec.len());
    }

    #[test]
    fn seqovl_uses_the_pattern_when_present() {
        let pat = b"CLIENTHELLO";
        let overlap = seqovl_overlap(Some(pat), b"abcdef", 8);
        assert_eq!(&overlap, b"CLIENTHE");
    }

    #[test]
    fn seqovl_falls_back_to_real_bytes() {
        let overlap = seqovl_overlap(None, b"abcdef", 3);
        assert_eq!(&overlap, b"def");
    }

    #[test]
    fn seqovl_clamps_to_the_available_prefix() {
        let overlap = seqovl_overlap(None, b"ab", 5);
        assert_eq!(&overlap, b"ab");
    }

    #[test]
    fn apply_mods_randomizes_the_client_random() {
        let rec = client_hello("telegram.org");
        let ch = ClientHello::parse(&rec).expect("parse");
        let mut fill = |n: usize| vec![0xABu8; n];
        let out = ch
            .apply_mods(&rec, None, true, false, &mut fill)
            .expect("mods");
        let reparsed = ClientHello::parse(&out).expect("reparse");
        assert_eq!(&out[reparsed.random.clone()], &[0xABu8; 32][..]);
        assert_eq!(reparsed.hostname(&out), Some("telegram.org"));
    }

    #[test]
    fn apply_mods_swaps_the_sni() {
        let rec = client_hello("telegram.org");
        let ch = ClientHello::parse(&rec).expect("parse");
        let mut fill = |n: usize| vec![0u8; n];
        let out = ch
            .apply_mods(&rec, Some("www.google.com"), false, false, &mut fill)
            .expect("mods");
        let reparsed = ClientHello::parse(&out).expect("reparse");
        assert_eq!(reparsed.hostname(&out), Some("www.google.com"));
    }

    #[test]
    fn apply_mods_swaps_sni_and_randomizes_together() {
        let rec = client_hello("telegram.org");
        let ch = ClientHello::parse(&rec).expect("parse");
        let mut fill = |n: usize| vec![0x5Au8; n];
        let out = ch
            .apply_mods(&rec, Some("example.com"), true, false, &mut fill)
            .expect("mods");
        let reparsed = ClientHello::parse(&out).expect("reparse");
        assert_eq!(reparsed.hostname(&out), Some("example.com"));
        assert_eq!(&out[reparsed.random.clone()], &[0x5Au8; 32][..]);
    }
}
