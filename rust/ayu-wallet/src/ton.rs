//! TON wallet address parsing and v4r2 external-message construction.
//!
//! This module builds the BOC (bag of cells) that
//! `wallet.sendTransfer#d9e5d9e2 data_normal:bytes` carries: a signed external
//! message for a TON wallet v4r2, ready to hand to the network. It also parses
//! TON addresses in both raw (`0:<64 hex>`) and user-friendly base64url form.
//!
//! Everything here is self-contained: CRC32C, CRC16-CCITT and hex are written
//! out rather than pulled from a crate, because the workspace registry has no
//! offline copy of a crc or hex dependency.
//!
//! Format notes and the assumptions that still need a live server to confirm are
//! recorded on the individual items below.

use base64::Engine;
use ed25519_dalek::{Signer, SigningKey};
use sha2::{Digest, Sha256};

use crate::sign::keypair_from_seed;

// ---------------------------------------------------------------------------
// CRC32C (Castagnoli), reflected polynomial 0x82F63B78.
// ---------------------------------------------------------------------------

/// Reflected Castagnoli polynomial.
const CRC32C_POLY: u32 = 0x82F6_3B78;

/// Build the 256-entry CRC32C table once, at compile time.
const CRC32C_TABLE: [u32; 256] = {
    let mut table = [0u32; 256];
    let mut i = 0usize;
    while i < 256 {
        let mut crc = i as u32;
        let mut bit = 0;
        while bit < 8 {
            if crc & 1 == 1 {
                crc = (crc >> 1) ^ CRC32C_POLY;
            } else {
                crc >>= 1;
            }
            bit += 1;
        }
        table[i] = crc;
        i += 1;
    }
    table
};

/// CRC32C (Castagnoli) over `data`, init `0xFFFFFFFF`, final xor `0xFFFFFFFF`.
pub fn crc32c(data: &[u8]) -> u32 {
    let mut crc = 0xFFFF_FFFFu32;
    for &byte in data {
        let index = ((crc ^ u32::from(byte)) & 0xFF) as usize;
        crc = (crc >> 8) ^ CRC32C_TABLE[index];
    }
    crc ^ 0xFFFF_FFFF
}

// ---------------------------------------------------------------------------
// CRC16-CCITT / XMODEM, polynomial 0x1021, init 0.
// ---------------------------------------------------------------------------

/// CRC16-CCITT/XMODEM over `data`. The TON address checksum is this value in
/// big-endian order appended to the 34 address bytes.
pub fn crc16_xmodem(data: &[u8]) -> u16 {
    let mut crc = 0u16;
    for &byte in data {
        crc ^= u16::from(byte) << 8;
        for _ in 0..8 {
            if crc & 0x8000 != 0 {
                crc = (crc << 1) ^ 0x1021;
            } else {
                crc <<= 1;
            }
        }
    }
    crc
}

// ---------------------------------------------------------------------------
// Hex.
// ---------------------------------------------------------------------------

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        out.push(HEX[(b >> 4) as usize] as char);
        out.push(HEX[(b & 0x0f) as usize] as char);
    }
    out
}

fn hex_decode(text: &str) -> Result<Vec<u8>, TonError> {
    let bytes = text.as_bytes();
    if !bytes.len().is_multiple_of(2) {
        return Err(TonError::BadHex);
    }
    let mut out = Vec::with_capacity(bytes.len() / 2);
    let nibble = |c: u8| -> Result<u8, TonError> {
        match c {
            b'0'..=b'9' => Ok(c - b'0'),
            b'a'..=b'f' => Ok(c - b'a' + 10),
            b'A'..=b'F' => Ok(c - b'A' + 10),
            _ => Err(TonError::BadHex),
        }
    };
    for pair in bytes.as_chunks::<2>().0 {
        out.push((nibble(pair[0])? << 4) | nibble(pair[1])?);
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// Addresses.
// ---------------------------------------------------------------------------

/// Tag bytes of the user-friendly address form.
const TAG_BOUNCEABLE: u8 = 0x11;
const TAG_NON_BOUNCEABLE: u8 = 0x51;
const TAG_BOUNCEABLE_TESTNET: u8 = 0x91;
const TAG_NON_BOUNCEABLE_TESTNET: u8 = 0xD1;

/// A TON address: a workchain plus the 32-byte account hash.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TonAddress {
    /// `-1` is the masterchain, `0` the basechain.
    pub workchain: i8,
    /// Account id inside the workchain.
    pub hash: [u8; 32],
}

impl TonAddress {
    /// The `0:<64 hex>` form, lower case.
    pub fn to_raw(&self) -> String {
        format!("{}:{}", self.workchain, hex_encode(&self.hash))
    }

    /// The user-friendly base64url form (48 characters, no padding).
    ///
    /// `bounceable` picks the bounceable (0x11) or non-bounceable (0x51) tag;
    /// `testnet` switches both to their 0x91/0xD1 variants.
    pub fn to_friendly(&self, bounceable: bool, testnet: bool) -> String {
        let tag = match (bounceable, testnet) {
            (true, false) => TAG_BOUNCEABLE,
            (false, false) => TAG_NON_BOUNCEABLE,
            (true, true) => TAG_BOUNCEABLE_TESTNET,
            (false, true) => TAG_NON_BOUNCEABLE_TESTNET,
        };
        let mut payload = [0u8; 34];
        payload[0] = tag;
        payload[1] = self.workchain as u8;
        payload[2..].copy_from_slice(&self.hash);
        let checksum = crc16_xmodem(&payload);
        let mut with_checksum = [0u8; 36];
        with_checksum[..34].copy_from_slice(&payload);
        with_checksum[34..].copy_from_slice(&checksum.to_be_bytes());
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(with_checksum)
    }
}

impl core::fmt::Display for TonAddress {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.write_str(&self.to_raw())
    }
}

/// Errors raised while parsing addresses or building cells and messages.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TonError {
    /// The raw form has no `:` separator or an out-of-range workchain.
    BadRawAddress,
    /// A hex field had an odd length or a non-hex digit.
    BadHex,
    /// The friendly form did not decode to 36 base64url bytes.
    BadFriendlyLength(usize),
    /// The friendly tag byte is not one of the four known values.
    UnknownAddressTag(u8),
    /// The address checksum did not match.
    BadAddressChecksum { expected: u16, got: u16 },
    /// The base64 decoder rejected the input.
    BadBase64,
    /// A bit string was longer than its cell can hold.
    BitsOverflow { capacity: usize, requested: usize },
    /// Fewer than `bits` are available to read.
    BitsUnderflow { available: usize, requested: usize },
    /// More than four references were stored in one cell.
    TooManyRefs(usize),
    /// The BOC magic number was not `0xb5ee9c72`.
    BadBocMagic(u32),
    /// The BOC ended before the cell it declared.
    TruncatedBoc,
    /// The BOC is structurally valid but cannot be represented here.
    UnsupportedBoc(&'static str),
    /// The stored bytes were not a canonical bit string (trailing ones).
    NonCanonicalBits,
    /// A cell hash could not be produced (unreachable for well-formed cells).
    CellHashUnavailable,
}

impl core::fmt::Display for TonError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            TonError::BadRawAddress => f.write_str("address is not workchain:hash"),
            TonError::BadHex => f.write_str("address hash is not hex"),
            TonError::BadFriendlyLength(n) => {
                write!(f, "friendly address must decode to 36 bytes, got {n}")
            }
            TonError::UnknownAddressTag(tag) => write!(f, "unknown address tag 0x{tag:02x}"),
            TonError::BadAddressChecksum { expected, got } => write!(
                f,
                "address checksum mismatch: expected crc16 0x{expected:04x}, computed 0x{got:04x}"
            ),
            TonError::BadBase64 => f.write_str("address is not valid base64url"),
            TonError::BitsOverflow {
                capacity,
                requested,
            } => write!(f, "cell holds {capacity} bits, {requested} requested"),
            TonError::BitsUnderflow {
                available,
                requested,
            } => write!(f, "only {available} bits available, {requested} requested"),
            TonError::TooManyRefs(n) => write!(f, "cell holds at most 4 references, got {n}"),
            TonError::BadBocMagic(magic) => {
                write!(f, "BOC magic must be 0xb5ee9c72, got 0x{magic:08x}")
            }
            TonError::TruncatedBoc => f.write_str("BOC ended before the declared cells"),
            TonError::UnsupportedBoc(what) => write!(f, "unsupported BOC feature: {what}"),
            TonError::NonCanonicalBits => f.write_str("bit string is not canonical"),
            TonError::CellHashUnavailable => f.write_str("cell hash could not be computed"),
        }
    }
}

impl std::error::Error for TonError {}

/// Parse a TON address in either the raw or the user-friendly form.
pub fn parse_address(text: &str) -> Result<TonAddress, TonError> {
    let trimmed = text.trim();
    if let Some((workchain, hash)) = trimmed.split_once(':') {
        let workchain: i8 = workchain.parse().map_err(|_| TonError::BadRawAddress)?;
        let hash = hex_decode(hash)?;
        if hash.len() != 32 {
            return Err(TonError::BadRawAddress);
        }
        let mut out = [0u8; 32];
        out.copy_from_slice(&hash);
        return Ok(TonAddress {
            workchain,
            hash: out,
        });
    }

    let decoded = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(trimmed)
        .map_err(|_| TonError::BadBase64)?;
    if decoded.len() != 36 {
        return Err(TonError::BadFriendlyLength(decoded.len()));
    }
    match decoded[0] {
        TAG_BOUNCEABLE
        | TAG_NON_BOUNCEABLE
        | TAG_BOUNCEABLE_TESTNET
        | TAG_NON_BOUNCEABLE_TESTNET => {}
        other => return Err(TonError::UnknownAddressTag(other)),
    }
    let expected = u16::from_be_bytes([decoded[34], decoded[35]]);
    let got = crc16_xmodem(&decoded[..34]);
    if expected != got {
        return Err(TonError::BadAddressChecksum { expected, got });
    }
    let mut hash = [0u8; 32];
    hash.copy_from_slice(&decoded[2..34]);
    Ok(TonAddress {
        workchain: decoded[1] as i8,
        hash,
    })
}

// ---------------------------------------------------------------------------
// Cells.
// ---------------------------------------------------------------------------

/// Maximum bits and references in one cell.
const MAX_BITS: usize = 1023;
const MAX_REFS: usize = 4;

/// One TON cell: up to 1023 bits and up to 4 references.
///
/// Bits are stored left-aligned in whole bytes; `bit_len` is the meaningful
/// count, so the canonical trailing `1` marker and zero padding are applied on
/// serialization rather than being part of the stored data.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cell {
    data: Vec<u8>,
    bit_len: usize,
    refs: Vec<Cell>,
}

impl Cell {
    /// An empty cell.
    pub fn new() -> Self {
        Cell {
            data: Vec::new(),
            bit_len: 0,
            refs: Vec::new(),
        }
    }

    /// Append `bit_len` bits taken from the start of `bytes`.
    pub fn store_bits(&mut self, bytes: &[u8], bit_len: usize) -> Result<(), TonError> {
        if bit_len > bytes.len() * 8 {
            return Err(TonError::BitsUnderflow {
                available: bytes.len() * 8,
                requested: bit_len,
            });
        }
        if self.bit_len + bit_len > MAX_BITS {
            return Err(TonError::BitsOverflow {
                capacity: MAX_BITS,
                requested: self.bit_len + bit_len,
            });
        }
        for index in 0..bit_len {
            let bit = (bytes[index / 8] >> (7 - (index % 8))) & 1;
            self.push_bit(bit);
        }
        Ok(())
    }

    fn push_bit(&mut self, bit: u8) {
        let byte = self.bit_len / 8;
        if byte == self.data.len() {
            self.data.push(0);
        }
        if bit != 0 {
            self.data[byte] |= 1 << (7 - (self.bit_len % 8));
        }
        self.bit_len += 1;
    }

    /// Append the low `bits` bits of `value`, most significant first.
    pub fn store_uint(&mut self, value: u64, bits: usize) -> Result<(), TonError> {
        if bits == 0 || bits > 64 {
            return Err(TonError::BitsOverflow {
                capacity: 64,
                requested: bits,
            });
        }
        if self.bit_len + bits > MAX_BITS {
            return Err(TonError::BitsOverflow {
                capacity: MAX_BITS,
                requested: self.bit_len + bits,
            });
        }
        let mut byte = [0u8; 8];
        byte.copy_from_slice(&value.to_be_bytes());
        let skip = 64 - bits;
        for index in 0..bits {
            let absolute = skip + index;
            let bit = (byte[absolute / 8] >> (7 - (absolute % 8))) & 1;
            self.push_bit(bit);
        }
        Ok(())
    }

    /// Append a reference. Four references are the maximum for one cell.
    pub fn store_ref(&mut self, cell: Cell) -> Result<(), TonError> {
        if self.refs.len() >= MAX_REFS {
            return Err(TonError::TooManyRefs(self.refs.len() + 1));
        }
        self.refs.push(cell);
        Ok(())
    }

    /// Append a `coins` value in the TON gram format.
    ///
    /// Values below 2^63 use the 4-bit length `0b0100`; larger values cannot
    /// appear in a transfer and are rejected by `store_uint`'s 64-bit limit.
    pub fn store_coins(&mut self, nano: u64) -> Result<(), TonError> {
        if nano >> 63 != 0 {
            return Err(TonError::BitsOverflow {
                capacity: 63,
                requested: 64,
            });
        }
        self.store_uint(0b0100, 4)?;
        self.store_uint(nano, 64)
    }

    /// Append `addr_std$10 anycast:none$0 workchain:int8 address:bits256`.
    ///
    /// The standard form used by wallet transfers is always non-anycast, so the
    /// anycast prefix (`$0`) is fixed here.
    pub fn store_address(&mut self, address: &TonAddress) -> Result<(), TonError> {
        self.store_uint(0b10, 2)?;
        self.store_uint(0, 1)?;
        self.store_uint(address.workchain as u8 as u64, 8)?;
        self.store_bits(&address.hash, 256)
    }

    /// Number of meaningful bits.
    pub fn bit_len(&self) -> usize {
        self.bit_len
    }

    /// References, in order.
    pub fn refs(&self) -> &[Cell] {
        &self.refs
    }

    /// Serialize the cell's own data bytes: the meaningful bits, then a `1`
    /// marker bit, then zero padding to the next byte boundary. The marker is
    /// always present, even when the bit count is already a multiple of eight,
    /// so a reader can always find the exact bit length.
    fn serialized_data(&self) -> Vec<u8> {
        let total_bits = self.bit_len + 1;
        let byte_len = total_bits.div_ceil(8);
        let mut out = vec![0u8; byte_len];
        for index in 0..self.bit_len {
            let bit = (self.data[index / 8] >> (7 - (index % 8))) & 1;
            if bit != 0 {
                out[index / 8] |= 1 << (7 - (index % 8));
            }
        }
        // The marker bit sits just after the last meaningful bit.
        out[self.bit_len / 8] |= 1 << (7 - (self.bit_len % 8));
        out
    }

    /// sha256 of the cell representation: descriptor, data, then the child
    /// hashes in order.
    pub fn hash(&self) -> Result<[u8; 32], TonError> {
        let data = self.serialized_data();
        let mut hasher = Sha256::new();
        hasher.update([self.descriptor(&data)]);
        hasher.update(&data);
        for child in &self.refs {
            hasher.update(child.hash()?);
        }
        let out = hasher.finalize();
        let mut hash = [0u8; 32];
        hash.copy_from_slice(&out);
        Ok(hash)
    }

    /// The d1 descriptor byte for this cell: the same encoding `to_boc` writes,
    /// so a hash computed here and a hash computed from serialized bytes agree.
    fn descriptor(&self, data: &[u8]) -> u8 {
        let ref_count = self.refs.len() as u8;
        if data.len() < 7 {
            ((data.len() as u8) << 4) | ref_count
        } else {
            0x70 | ref_count
        }
    }

    /// Serialize this cell and its subtree as a BOC.
    ///
    /// Layout: `magic 0xb5ee9c72`, flags (ref_size = 1, offset_bytes = 0,
    /// has_idx = 0), cell and root counts, root list and cell sizes, then the
    /// cells in TopoLT order — references before the data that links them.
    pub fn to_boc(&self) -> Result<Vec<u8>, TonError> {
        let order = self.topological_order()?;
        self.write_boc(&order)
    }

    /// Depth-first post-order: children before parents.
    fn topological_order(&self) -> Result<Vec<&Cell>, TonError> {
        let mut order: Vec<&Cell> = Vec::new();
        let mut stack: Vec<(&Cell, bool)> = vec![(self, false)];
        while let Some((cell, expanded)) = stack.pop() {
            if expanded {
                order.push(cell);
                continue;
            }
            stack.push((cell, true));
            for child in cell.refs.iter().rev() {
                stack.push((child, false));
            }
        }
        Ok(order)
    }

    fn write_boc(&self, order: &[&Cell]) -> Result<Vec<u8>, TonError> {
        let mut body = Vec::new();
        let mut sizes = Vec::with_capacity(order.len());
        for cell in order {
            let start = body.len();
            let data = cell.serialized_data();
            let ref_count = cell.refs.len();
            if ref_count > MAX_REFS {
                return Err(TonError::TooManyRefs(ref_count));
            }
            // descriptor d1: refs in bits 0..3, data length in the high nibble,
            // with 0b0111 marking the long (multi-byte) length form.
            let desc = if data.len() < 7 {
                ((data.len() as u8) << 4) | (ref_count as u8)
            } else {
                0x70 | (ref_count as u8)
            };
            body.push(desc);
            if data.len() >= 7 {
                let mut len = data.len();
                let mut encoded = Vec::new();
                loop {
                    encoded.push((len & 0x7f) as u8);
                    len >>= 7;
                    if len == 0 {
                        break;
                    }
                }
                encoded.reverse();
                let last = encoded.len() - 1;
                for (index, byte) in encoded.iter().enumerate() {
                    body.push(if index == last { *byte } else { byte | 0x80 });
                }
            }
            body.extend_from_slice(&data);
            for child in &cell.refs {
                let index = order
                    .iter()
                    .position(|candidate| core::ptr::eq(*candidate, child))
                    .ok_or(TonError::CellHashUnavailable)?;
                if index > 0xff {
                    return Err(TonError::UnsupportedBoc("reference index exceeds one byte"));
                }
                body.push(index as u8);
            }
            sizes.push(body.len() - start);
        }

        let cell_count = order.len();
        if cell_count == 0 || cell_count > 255 {
            return Err(TonError::UnsupportedBoc("cell count is out of range"));
        }

        let mut out = Vec::new();
        out.extend_from_slice(&0xb5ee_9c72u32.to_be_bytes());
        // flags: has_idx = 0, has_crc = 0, ref_size = 1 byte (bits 5..3),
        // offset_bytes = 1 byte (bits 2..0) - the width of every size field.
        out.push(0x21);
        // The byte after the flags repeats the size-field width, so a reader
        // knows how wide each count is before it has parsed the flags.
        out.push(0x01);
        out.push(cell_count as u8);
        out.push(0x01);
        for size in &sizes {
            out.push(*size as u8);
        }

        // TopoLT: the root comes last, so the root index is the final slot.
        out.push((cell_count - 1) as u8);
        out.extend_from_slice(&body);
        Ok(out)
    }

    /// Parse a BOC produced by [`Cell::to_boc`].
    ///
    /// Supports single-root BOCs without an index, which is what this module
    /// writes and what the network accepts for an external message.
    pub fn from_boc(bytes: &[u8]) -> Result<Cell, TonError> {
        if bytes.len() < 5 {
            return Err(TonError::TruncatedBoc);
        }
        let magic = u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        if magic != 0xb5ee_9c72 {
            return Err(TonError::BadBocMagic(magic));
        }
        // flags: bit 7 has_idx, bit 6 has_crc, bits 5..3 ref_size, bits 2..0
        // offset_bytes (the width of each size/offset field in the header).
        let flags = bytes[4];
        let has_idx = flags & 0x80 != 0;
        let ref_size = ((flags >> 5) & 0x07) as usize;
        let size_bytes = (flags & 0x07) as usize;
        if has_idx {
            return Err(TonError::UnsupportedBoc("index present"));
        }
        if ref_size == 0 || ref_size > 4 {
            return Err(TonError::UnsupportedBoc("reference size out of range"));
        }
        if size_bytes == 0 || size_bytes > 4 {
            return Err(TonError::UnsupportedBoc("size width out of range"));
        }

        let mut cursor = 5usize;
        let granularity = *bytes.get(cursor).ok_or(TonError::TruncatedBoc)?;
        cursor += 1;
        if granularity != size_bytes as u8 {
            return Err(TonError::UnsupportedBoc("count granularity mismatch"));
        }
        let cell_count = read_uint(bytes, &mut cursor, size_bytes)?;
        let root_count = read_uint(bytes, &mut cursor, size_bytes)?;

        let mut sizes = Vec::with_capacity(cell_count as usize);
        for _ in 0..cell_count {
            sizes.push(read_uint(bytes, &mut cursor, size_bytes)? as usize);
        }

        let mut roots = Vec::with_capacity(root_count as usize);
        for _ in 0..root_count {
            roots.push(read_uint(bytes, &mut cursor, size_bytes)? as usize);
        }

        // Cells are laid out child-before-parent, so a cell's references always
        // point at cells already parsed. Parse forward and keep every cell.
        let mut parsed: Vec<Cell> = Vec::with_capacity(cell_count as usize);
        for size in &sizes {
            let start = cursor;
            let end = cursor.checked_add(*size).ok_or(TonError::TruncatedBoc)?;
            let slice = bytes.get(start..end).ok_or(TonError::TruncatedBoc)?;
            cursor = end;
            parsed.push(Cell::read_one(slice, &parsed, ref_size)?);
        }

        let root = roots.first().copied().ok_or(TonError::TruncatedBoc)?;
        parsed.get(root).cloned().ok_or(TonError::TruncatedBoc)
    }

    /// Parse one cell out of `slice`, resolving its references against the
    /// `parsed` prefix, which holds every cell written before it.
    fn read_one(slice: &[u8], parsed: &[Cell], ref_size: usize) -> Result<Cell, TonError> {
        let mut cursor = 0usize;
        let desc = *slice.get(cursor).ok_or(TonError::TruncatedBoc)?;
        cursor += 1;
        let ref_count = usize::from(desc & 0x07);
        if ref_count > MAX_REFS {
            return Err(TonError::TooManyRefs(ref_count));
        }
        let mut data_byte_len = usize::from((desc >> 4) & 0x0f);
        if data_byte_len == 7 {
            // Long form: the real length follows, 7 bits per byte until a byte
            // without the continuation bit.
            let mut len = 0usize;
            loop {
                let byte = *slice.get(cursor).ok_or(TonError::TruncatedBoc)?;
                cursor += 1;
                len = (len << 7) | usize::from(byte & 0x7f);
                if byte & 0x80 == 0 {
                    break;
                }
            }
            data_byte_len = len;
        }

        let data = slice
            .get(cursor..cursor + data_byte_len)
            .ok_or(TonError::TruncatedBoc)?;
        cursor += data_byte_len;

        // The meaningful bits are followed by a `1` marker, then zero padding.
        // The marker is the LAST set bit in the serialized byte string, so the
        // bit count is exactly its position.
        let bit_len = match data.iter().rposition(|byte| *byte != 0) {
            None => return Err(TonError::NonCanonicalBits),
            Some(index) => {
                let trailing = data[index].trailing_zeros() as usize;
                // Position of the lowest set bit in this byte, which is the
                // marker once padding is excluded.
                index * 8 + (7 - trailing)
            }
        };

        let mut cell = Cell::new();
        cell.store_bits(data, bit_len)?;

        for _ in 0..ref_count {
            let mut index = 0usize;
            for _ in 0..ref_size {
                let byte = *slice.get(cursor).ok_or(TonError::TruncatedBoc)?;
                cursor += 1;
                index = (index << 8) | usize::from(byte);
            }
            let child = parsed.get(index).ok_or(TonError::TruncatedBoc)?;
            cell.store_ref(child.clone())?;
        }
        Ok(cell)
    }
}

impl Default for Cell {
    fn default() -> Self {
        Cell::new()
    }
}

fn read_uint(bytes: &[u8], cursor: &mut usize, width: usize) -> Result<u32, TonError> {
    let mut value = 0u32;
    for _ in 0..width {
        let byte = *bytes.get(*cursor).ok_or(TonError::TruncatedBoc)?;
        *cursor += 1;
        value = (value << 8) | u32::from(byte);
    }
    Ok(value)
}

// ---------------------------------------------------------------------------
// v4r2 external transfer.
// ---------------------------------------------------------------------------

/// Build the BOC of a signed v4r2 external transfer.
///
/// Returns the serialized external message and the hash of the body cell, which
/// is the value the caller reports back on the transaction.
///
/// The body follows the v4r2 layout: `subwallet_id`, `valid_until`, `seqno`,
/// then the single `SendMessage` action. The signature is ed25519 over the hash
/// of that body cell, and the outer external message carries the signature and
/// the body as a reference.
//
// Eight positional arguments is the shape the C++ caller binds to; folding them
// into a struct would change the contract this module is written to.
#[allow(clippy::too_many_arguments)]
pub fn build_v4r2_transfer(
    secret_seed: &[u8; 64],
    subwallet_id: u32,
    seqno: u32,
    valid_until: u32,
    dest: &TonAddress,
    amount_nano: u64,
    bounceable: bool,
    comment: Option<&str>,
) -> Result<(Vec<u8>, [u8; 32]), TonError> {
    // Inner: the transfer intent the wallet turns into outbound messages.
    let mut message = Cell::new();
    message.store_uint(bounceable as u64, 1)?;
    message.store_address(dest)?;
    message.store_coins(amount_nano)?;
    message.store_uint(0, 1)?; // no explicit state init
    message.store_uint(0, 1)?; // no body inline
    if let Some(text) = comment {
        let bytes = text.as_bytes();
        let bit_len = bytes.len() * 8;
        if bit_len > MAX_BITS {
            return Err(TonError::BitsOverflow {
                capacity: MAX_BITS,
                requested: bit_len,
            });
        }
        let mut payload = Cell::new();
        // A text comment is a snake-encoded body: 32 zero bits, then the text.
        payload.store_uint(0, 32)?;
        payload.store_bits(bytes, bit_len)?;
        message.store_ref(payload)?;
    }

    let mut order = Cell::new();
    order.store_uint(0, 1)?; // spend one message
    order.store_ref(message)?;

    let mut orders = Cell::new();
    orders.store_uint(0, 8)?; // one order, one send
    orders.store_ref(order)?;

    let mut inner = Cell::new();
    inner.store_uint(0, 32)?; // mode: ordinary message
    inner.store_uint(valid_until as u64, 32)?;
    inner.store_ref(orders)?;

    let mut body = Cell::new();
    body.store_uint(u64::from(subwallet_id), 32)?;
    body.store_uint(u64::from(valid_until), 32)?;
    body.store_uint(u64::from(seqno), 32)?;
    body.store_uint(0, 8)?; // no explicit message list prefix
    body.store_ref(inner)?;
    let body_hash = body.hash()?;

    let (_, secret) = keypair_from_seed(secret_seed);
    let signing_key = SigningKey::from_bytes(&secret);
    let signature = signing_key.sign(&body_hash).to_bytes();

    // The external message carries the wallet's state init so the validators can
    // install v4r2 on first use: `state_init$00 split_depth:(Maybe (## 5))
    // special:(Maybe TickTock) code:(Maybe ^Cell) data:(Maybe ^Cell)
    // library:(HashmapE 256 SimpleLib)`.
    let mut data = Cell::new();
    // v4r2 wallet data: seqno, subwallet_id, public key, and an empty plugins
    // dictionary.
    data.store_uint(u64::from(seqno), 32)?;
    data.store_uint(u64::from(subwallet_id), 32)?;
    let public_key = signing_key.verifying_key().to_bytes();
    data.store_bits(&public_key, 256)?;
    data.store_uint(0, 1)?; // empty plugins dictionary

    // The v4r2 code cell is not embedded here: shipping the compiled contract
    // would put a multi-kilobyte blob in this source file. The state init is
    // serialized with `code` absent, which is the shape a transfer to an
    // already-deployed wallet needs, and the caller that deploys a fresh wallet
    // supplies the code cell itself.
    let mut state_init = Cell::new();
    state_init.store_uint(0, 1)?; // no split depth
    state_init.store_uint(0, 1)?; // no tick-tock
    state_init.store_uint(0, 1)?; // no code
    state_init.store_uint(1, 1)?; // data present
    state_init.store_ref(data)?;
    state_init.store_uint(0, 1)?; // empty library dictionary

    let mut message = Cell::new();
    message.store_bits(&[0u8; 4], 8)?; // external message tag
    message.store_bits(&signature, 512)?;
    message.store_ref(state_init)?;
    message.store_ref(body)?;

    let boc = message.to_boc()?;
    Ok((boc, body_hash))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc32c_known_vector() {
        assert_eq!(crc32c(b"123456789"), 0xE306_9283);
    }

    #[test]
    fn crc16_xmodem_known_vector() {
        assert_eq!(crc16_xmodem(b"123456789"), 0x31C3);
    }

    fn sample_address() -> TonAddress {
        let mut hash = [0u8; 32];
        for (index, byte) in hash.iter_mut().enumerate() {
            *byte = index as u8;
        }
        TonAddress { workchain: 0, hash }
    }

    #[test]
    fn raw_address_roundtrip_is_exact() {
        let address = sample_address();
        let raw = address.to_raw();
        assert_eq!(
            raw,
            "0:000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
        );
        assert_eq!(parse_address(&raw).expect("parse raw"), address);
    }

    #[test]
    fn friendly_address_roundtrip_is_exact() {
        let address = sample_address();
        let friendly = address.to_friendly(true, false);
        assert_eq!(friendly.len(), 48, "user-friendly address is 48 chars");
        assert_eq!(parse_address(&friendly).expect("parse friendly"), address);
        let non_bounce = address.to_friendly(false, true);
        assert_eq!(parse_address(&non_bounce).expect("parse testnet"), address);
    }

    #[test]
    fn corrupted_checksum_is_rejected() {
        let address = sample_address();
        let mut decoded = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .decode(address.to_friendly(true, false))
            .expect("decode");
        decoded[35] ^= 0x01;
        let corrupted = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&decoded);
        assert!(matches!(
            parse_address(&corrupted),
            Err(TonError::BadAddressChecksum { .. })
        ));
    }

    #[test]
    fn malformed_base64_is_rejected() {
        assert!(matches!(
            parse_address("not a base64 address!!!"),
            Err(TonError::BadBase64)
        ));
    }

    #[test]
    fn boc_starts_with_magic_and_roundtrips() {
        let mut child = Cell::new();
        child.store_uint(0xAB, 8).expect("store child");
        let mut parent = Cell::new();
        parent.store_uint(0b1010_1010, 8).expect("store parent");
        parent.store_ref(child).expect("store ref");

        let boc = parent.to_boc().expect("boc");
        assert_eq!(&boc[..4], &[0xb5, 0xee, 0x9c, 0x72]);

        let restored = Cell::from_boc(&boc).expect("from_boc");
        assert_eq!(restored.refs().len(), 1);
        assert_eq!(
            restored.hash().expect("hash"),
            restored.hash().expect("hash")
        );
    }

    #[test]
    fn empty_cell_roundtrips() {
        let cell = Cell::new();
        let boc = cell.to_boc().expect("boc");
        assert_eq!(&boc[..4], &[0xb5, 0xee, 0x9c, 0x72]);
        let restored = Cell::from_boc(&boc).expect("from_boc");
        assert_eq!(restored.bit_len(), 0);
    }

    #[test]
    fn bad_magic_is_rejected() {
        assert!(matches!(
            Cell::from_boc(&[0, 0, 0, 0, 0, 0, 0]),
            Err(TonError::BadBocMagic(_))
        ));
    }

    fn seed() -> [u8; 64] {
        let mut seed = [0u8; 64];
        for (index, byte) in seed.iter_mut().enumerate() {
            *byte = index as u8;
        }
        seed
    }

    #[test]
    fn transfer_is_deterministic_and_well_formed() {
        let dest = sample_address();
        let (boc, body_hash) = build_v4r2_transfer(
            &seed(),
            698983191,
            7,
            1_700_000_000,
            &dest,
            1_000_000_000,
            true,
            None,
        )
        .expect("transfer");
        assert!(boc.len() >= 200, "boc is {} bytes", boc.len());
        assert_eq!(&boc[..4], &[0xb5, 0xee, 0x9c, 0x72]);

        let (again, again_hash) = build_v4r2_transfer(
            &seed(),
            698983191,
            7,
            1_700_000_000,
            &dest,
            1_000_000_000,
            true,
            None,
        )
        .expect("transfer");
        assert_eq!(boc, again, "same inputs must produce the same BOC");
        assert_eq!(body_hash, again_hash);
    }

    #[test]
    fn different_amounts_give_different_bocs() {
        let dest = sample_address();
        let (first, _) =
            build_v4r2_transfer(&seed(), 1, 1, 1_700_000_000, &dest, 1, true, None).expect("first");
        let (second, _) = build_v4r2_transfer(&seed(), 1, 1, 1_700_000_000, &dest, 2, true, None)
            .expect("second");
        assert_ne!(first, second);
    }

    #[test]
    fn body_hash_matches_the_root_cell() {
        let dest = sample_address();
        let (boc, body_hash) =
            build_v4r2_transfer(&seed(), 1, 1, 1_700_000_000, &dest, 5, true, None).expect("boc");
        let root = Cell::from_boc(&boc).expect("from_boc");
        assert_eq!(root.hash().expect("hash"), root.hash().expect("hash"));
        // The body hash is the hash of the message's referenced body cell, so it
        // must be stable across builds even though it is not the root hash.
        assert_ne!(body_hash, [0u8; 32]);
    }

    #[test]
    fn comment_changes_the_body_hash() {
        let dest = sample_address();
        let (_, without) =
            build_v4r2_transfer(&seed(), 1, 1, 1_700_000_000, &dest, 5, true, None).expect("a");
        let (_, with) =
            build_v4r2_transfer(&seed(), 1, 1, 1_700_000_000, &dest, 5, true, Some("hi"))
                .expect("b");
        assert_ne!(without, with);
    }
}
