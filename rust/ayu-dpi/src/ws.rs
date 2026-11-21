//! Third upstream for the userspace transport: the WebSocket front of the
//! Telegram web clients (`wss://kws{dc}.web.telegram.org/apiws`).
//!
//! Reason to exist: where the data centre addresses themselves are blocked but
//! the Telegram web fronts are still reachable, the plain MTProto stream cannot
//! get out and no amount of first-segment splitting helps. The client already
//! speaks the obfuscated transport, so this module carries its bytes inside
//! binary WebSocket frames and only fixes the data centre number the front
//! routes by (see [`set_dc`]).
//!
//! Blocking on purpose: no async runtime, no dependency beyond TLS. The socket
//! is non-blocking and driven by hand (`read_tls`/`process_new_packets`/
//! `write_tls`), so a poll timeout is a plain `WouldBlock` that never reaches
//! the TLS state machine. Partial reads live in an internal buffer, so a poll
//! or timeout never desynchronises the frame stream.

use std::io::{self, Read, Write};
use std::net::{IpAddr, Ipv4Addr, TcpStream, ToSocketAddrs};
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

use aes::Aes256;
use aes::cipher::{KeyIvInit, StreamCipher};
use ctr::Ctr128BE;
use rustls::pki_types::ServerName;
use rustls::{ClientConfig, ClientConnection, RootCertStore};
use webpki_roots::TLS_SERVER_ROOTS;

/// Default data centre when the requested address is not a known Telegram one.
pub const DEFAULT_DC: i16 = 2;

/// The web front used when the name does not resolve (DNS blocking); TLS still
/// verifies the certificate for the name, so a wrong front fails closed.
const FALLBACK_IP: Ipv4Addr = Ipv4Addr::new(149, 154, 167, 220);

const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(30);

/// How long the relay idles before it polls the client again when both sides
/// are quiet. It bounds the added latency of the single-threaded loop.
const POLL_INTERVAL: Duration = Duration::from_millis(2);

/// Upper bound on one WebSocket frame, before allocating for it.
const MAX_FRAME: usize = 1 << 26;

const OPCODE_CONT: u8 = 0x0;
const OPCODE_BIN: u8 = 0x2;
const OPCODE_CLOSE: u8 = 0x8;
const OPCODE_PING: u8 = 0x9;
const OPCODE_PONG: u8 = 0xA;

/// Client frames must be masked; the mask value itself is free, so a fixed one
/// is used instead of pulling in a random source.
const CLIENT_MASK: [u8; 4] = [0x12, 0x34, 0x56, 0x78];

/// rustls with the ring provider and the Mozilla root certificates.
fn tls_config() -> Arc<ClientConfig> {
    static CONFIG: OnceLock<Arc<ClientConfig>> = OnceLock::new();
    CONFIG
        .get_or_init(|| {
            let roots = RootCertStore {
                roots: TLS_SERVER_ROOTS.to_vec(),
            };
            let config = ClientConfig::builder_with_provider(Arc::new(
                rustls::crypto::ring::default_provider(),
            ))
            .with_safe_default_protocol_versions()
            .expect("ring supports the safe default protocol versions")
            .with_root_certificates(roots)
            .with_no_client_auth();
            Arc::new(config)
        })
        .clone()
}

/// The host name of a data centre's WebSocket front.
fn host(dc: i16) -> String {
    format!("kws{}.web.telegram.org", dc.unsigned_abs())
}

/// Which data centre an address belongs to (Telegram's published ranges).
/// Same table the other clients use.
pub fn dc_of(ip: IpAddr) -> Option<i16> {
    match ip {
        IpAddr::V4(ip) => {
            let [a, b, c, d] = ip.octets();
            match (a, b, c) {
                (149, 154, 175) => Some(if !(100..200).contains(&d) { 1 } else { 3 }),
                (149, 154, 167) => Some(if (90..150).contains(&d) { 4 } else { 2 }),
                (149, 154, 164..=166) => Some(4),
                (91, 108, 56..=59) => Some(5),
                (91, 108, 4..=7) => Some(4),
                (91, 108, 8..=11) => Some(3),
                (91, 108, 12..=15) => Some(1),
                (91, 108, 16..=19) => Some(2),
                (95, 161, 76) => Some(2),
                _ => None,
            }
        }
        // 2001:b28:f23d:f001::a is DC 1; the fourth group ends with the number.
        IpAddr::V6(ip) => {
            let group = ip.segments()[3];
            let dc = (group & 0x000f) as i16;
            ((group & 0xfff0) == 0xf000 && (1..=5).contains(&dc)).then_some(dc)
        }
    }
}

/// Number of bytes in the obfuscated transport header, of which the last eight
/// carry the transport tag and the data centre number.
pub const OBFUSCATED_HEADER: usize = 64;

/// Writes the data centre number into bytes 60..62 of the obfuscated header,
/// in place.
///
/// Bytes 56..64 are sent encrypted with the stream cipher whose key and counter
/// live in bytes 8..56. They are decrypted with that stream, the data centre
/// number is placed at offset 60, and bytes 56..64 are encrypted again. Bytes
/// 0..56 are not touched, so the client's own cipher never desynchronises.
pub fn set_dc(head: &mut [u8], dc: i16) {
    if head.len() < OBFUSCATED_HEADER {
        return;
    }
    let mut key = [0u8; 32];
    key.copy_from_slice(&head[8..40]);
    let mut iv = [0u8; 16];
    iv.copy_from_slice(&head[40..56]);
    let mut keystream = [0u8; OBFUSCATED_HEADER];
    Ctr128BE::<Aes256>::new(&key.into(), &iv.into()).apply_keystream(&mut keystream);

    let mut plain = [0u8; 8];
    for i in 0..8 {
        plain[i] = head[56 + i] ^ keystream[56 + i];
    }
    plain[4..6].copy_from_slice(&dc.to_le_bytes());
    for i in 0..8 {
        head[56 + i] = plain[i] ^ keystream[56 + i];
    }
}

/// `Sec-WebSocket-Key` for the upgrade: sixteen random bytes, base64 encoded, as
/// the RFC requires. A fresh key per connection, because a well-known constant
/// is trivially recognisable and some fronts refuse it.
fn websocket_key() -> String {
    let mut bytes = [0u8; 16];
    // Seed from the wall clock and a stack address: enough to make the key
    // unpredictable without pulling in a random-number dependency.
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0);
    let stack = &nanos as *const u64 as u64;
    let mut state = nanos ^ stack.rotate_left(17) ^ 0x9E37_79B9_7F4A_7C15;
    for chunk in bytes.chunks_mut(8) {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        let le = state.to_le_bytes();
        chunk.copy_from_slice(&le[..chunk.len()]);
    }
    base64(&bytes)
}

/// Standard base64 with padding, as the WebSocket handshake spells it.
fn base64(input: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(input.len().div_ceil(3) * 4);
    for group in input.chunks(3) {
        let b = [
            group[0],
            group.get(1).copied().unwrap_or(0),
            group.get(2).copied().unwrap_or(0),
        ];
        let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        out.push(TABLE[(n >> 18) as usize & 63] as char);
        out.push(TABLE[(n >> 12) as usize & 63] as char);
        out.push(if group.len() > 1 {
            TABLE[(n >> 6) as usize & 63] as char
        } else {
            '='
        });
        out.push(if group.len() > 2 {
            TABLE[n as usize & 63] as char
        } else {
            '='
        });
    }
    out
}

/// True when `AYU_DPI_DEBUG` is set, for tracing the WebSocket relay.
fn debug_enabled() -> bool {
    static DEBUG: OnceLock<bool> = OnceLock::new();
    *DEBUG.get_or_init(|| std::env::var_os("AYU_DPI_DEBUG").is_some())
}

/// Appends one line to the file named by `AYU_DPI_TRACE`, flushing each time so
/// the trace survives an abrupt process end. Falls back to stderr when unset.
fn trace(message: &str) {
    if !debug_enabled() {
        return;
    }
    if let Some(path) = std::env::var_os("AYU_DPI_TRACE")
        && let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
    {
        let _ = writeln!(file, "{message}");
        let _ = file.flush();
        return;
    }
    eprintln!("{message}");
}

/// One TLS connection over a non-blocking socket, driven by hand.
///
/// `Read`/`Write` mirror the plain `rustls::StreamOwned` contract but never
/// block internally and never let a socket timeout reach the TLS state machine:
/// when no plaintext is ready they return `WouldBlock`, and the caller decides
/// how long to wait.
pub struct TlsStream {
    conn: ClientConnection,
    sock: TcpStream,
    eof: bool,
}

impl TlsStream {
    /// Reads whatever rustls already decoded, without touching the socket.
    fn plaintext(&mut self, buf: &mut [u8]) -> io::Result<Option<usize>> {
        let mut reader = self.conn.reader();
        match reader.read(buf) {
            Ok(0) => Ok(None),
            Ok(n) => Ok(Some(n)),
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => Ok(None),
            Err(e) => Err(e),
        }
    }

    /// Writes every TLS record rustls currently has queued. Stops on the first
    /// `WouldBlock`; the remainder stays buffered inside rustls.
    fn flush_tls(&mut self) -> io::Result<()> {
        while self.conn.wants_write() {
            match self.conn.write_tls(&mut self.sock) {
                Ok(0) => break,
                Ok(_) => {}
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => break,
                Err(e) => return Err(e),
            }
        }
        self.sock.flush()
    }

    /// Reads one plaintext chunk, driving the TLS layer as needed. Returns
    /// `WouldBlock` when the socket has nothing more right now. Pending TLS
    /// writes are always pushed out first, so a write is never left stranded
    /// inside rustls while this side waits for input.
    fn read_some(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        self.flush_tls()?;
        if let Some(n) = self.plaintext(buf)? {
            return Ok(n);
        }
        if self.eof {
            return Ok(0);
        }
        let n = match self.conn.read_tls(&mut self.sock) {
            Ok(n) => n,
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                return Err(io::Error::from(io::ErrorKind::WouldBlock));
            }
            Err(e) => return Err(e),
        };
        if n == 0 {
            self.eof = true;
        }
        self.conn
            .process_new_packets()
            .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
        // The handshake may have queued records (alerts, the request, keys).
        self.flush_tls()?;
        match self.plaintext(buf)? {
            Some(n) => Ok(n),
            None => Err(io::Error::from(io::ErrorKind::WouldBlock)),
        }
    }
}

impl Read for TlsStream {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        self.read_some(buf)
    }
}

impl Write for TlsStream {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let n = self.conn.writer().write(buf)?;
        self.flush_tls()?;
        Ok(n)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.flush_tls()
    }
}

/// A WebSocket connection carrying the MTProto byte stream.
///
/// `S` is generic so the framing can be tested without TLS: the handshake and
/// the frames only need `Read + Write`.
pub struct Frames<S> {
    stream: S,
    /// Bytes read from the transport but not yet consumed by a frame. Kept so a
    /// poll in the middle of a frame never loses data.
    buf: Vec<u8>,
}

impl<S: Read + Write> Frames<S> {
    pub fn new(stream: S) -> Self {
        Self {
            stream,
            buf: Vec::new(),
        }
    }

    /// Reads from the transport until at least `need` bytes are buffered.
    /// `TimedOut`/`WouldBlock` propagate unchanged, with the buffer intact.
    fn fill(&mut self, need: usize) -> io::Result<()> {
        while self.buf.len() < need {
            let mut chunk = [0u8; 16 * 1024];
            let n = self.stream.read(&mut chunk)?;
            if n == 0 {
                return Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "websocket closed",
                ));
            }
            if debug_enabled() {
                trace(&format!(
                    "ayu-dpi: ws raw {n} bytes: {:02x?}",
                    &chunk[..n.min(32)]
                ));
            }
            self.buf.extend_from_slice(&chunk[..n]);
        }
        Ok(())
    }

    /// HTTP upgrade on an already open connection. Leftover bytes after the
    /// response stay in `buf` as the first frame. `WouldBlock` is treated as
    /// "not yet" and retried until `deadline`.
    pub fn handshake(&mut self, host: &str) -> io::Result<()> {
        let key = websocket_key();
        let request = format!(
            "GET /apiws HTTP/1.1\r\n\
             Host: {host}\r\n\
             Upgrade: websocket\r\n\
             Connection: Upgrade\r\n\
             Sec-WebSocket-Key: {key}\r\n\
             Sec-WebSocket-Version: 13\r\n\
             Sec-WebSocket-Protocol: binary\r\n\r\n"
        );
        self.stream.write_all(request.as_bytes())?;
        self.stream.flush()?;
        let deadline = Instant::now() + HANDSHAKE_TIMEOUT;
        loop {
            if let Some(end) = find(&self.buf, b"\r\n\r\n") {
                let head = String::from_utf8_lossy(&self.buf[..end]).to_string();
                self.buf.drain(..end + 4);
                let status = head.lines().next().unwrap_or("");
                if debug_enabled() {
                    trace(&format!("ayu-dpi: ws upgrade response:\n{head}"));
                }
                if status.starts_with("HTTP/1.1 101") || status.starts_with("HTTP/1.0 101") {
                    return Ok(());
                }
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!("websocket upgrade refused: {status}"),
                ));
            }
            if self.buf.len() > 8192 {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "websocket upgrade header too large",
                ));
            }
            let want = self.buf.len() + 1;
            match self.fill(want) {
                Ok(()) => {}
                Err(ref e)
                    if e.kind() == io::ErrorKind::WouldBlock
                        || e.kind() == io::ErrorKind::TimedOut =>
                {
                    if Instant::now() >= deadline {
                        return Err(io::Error::new(
                            io::ErrorKind::TimedOut,
                            "websocket upgrade timed out",
                        ));
                    }
                    std::thread::sleep(POLL_INTERVAL);
                }
                Err(e) => return Err(e),
            }
        }
    }

    /// Writes one masked frame with the given opcode.
    fn write_frame(&mut self, opcode: u8, payload: &[u8]) -> io::Result<()> {
        let len = payload.len();
        let mut head = Vec::with_capacity(14);
        head.push(0x80 | opcode);
        if len < 126 {
            head.push(0x80 | len as u8);
        } else if len <= u16::MAX as usize {
            head.push(0x80 | 126);
            head.extend_from_slice(&(len as u16).to_be_bytes());
        } else {
            head.push(0x80 | 127);
            head.extend_from_slice(&(len as u64).to_be_bytes());
        }
        head.extend_from_slice(&CLIENT_MASK);
        let mut masked = payload.to_vec();
        for (i, b) in masked.iter_mut().enumerate() {
            *b ^= CLIENT_MASK[i & 3];
        }
        self.stream.write_all(&head)?;
        self.stream.write_all(&masked)?;
        self.stream.flush()
    }

    pub fn write_binary(&mut self, payload: &[u8]) -> io::Result<()> {
        if debug_enabled() {
            trace(&format!(
                "ayu-dpi: ws send frame opcode=2 len={} head={:02x?}",
                payload.len(),
                &payload[..payload.len().min(16)]
            ));
        }
        self.write_frame(OPCODE_BIN, payload)
    }

    /// Reads one application message into `out`, answering pings and skipping
    /// pongs. Returns false on close. Fragments are reassembled. A partial frame
    /// leaves the buffer intact, so a `WouldBlock` never loses data.
    pub fn read_message(&mut self, out: &mut Vec<u8>) -> io::Result<bool> {
        out.clear();
        loop {
            self.fill(2)?;
            let b0 = self.buf[0];
            let b1 = self.buf[1];
            let fin = b0 & 0x80 != 0;
            let opcode = b0 & 0x0f;
            let masked = b1 & 0x80 != 0;
            let mut len = (b1 & 0x7f) as usize;
            let head_len;
            if len == 126 {
                self.fill(4)?;
                len = u16::from_be_bytes([self.buf[2], self.buf[3]]) as usize;
                head_len = 4;
            } else if len == 127 {
                self.fill(10)?;
                len = u64::from_be_bytes(self.buf[2..10].try_into().expect("8 bytes")) as usize;
                head_len = 10;
            } else {
                head_len = 2;
            }
            if len > MAX_FRAME {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!("websocket frame of {len} bytes is too large"),
                ));
            }
            let mask_len = if masked { 4 } else { 0 };
            let total = head_len + mask_len + len;
            self.fill(total)?;

            let mask = if masked {
                [
                    self.buf[head_len],
                    self.buf[head_len + 1],
                    self.buf[head_len + 2],
                    self.buf[head_len + 3],
                ]
            } else {
                [0u8; 4]
            };
            let mut payload = self.buf[head_len + mask_len..total].to_vec();
            self.buf.drain(..total);
            if masked {
                for (i, b) in payload.iter_mut().enumerate() {
                    *b ^= mask[i & 3];
                }
            }

            match opcode {
                OPCODE_BIN | OPCODE_CONT => {
                    out.extend_from_slice(&payload);
                    if fin {
                        return Ok(true);
                    }
                }
                OPCODE_PING => self.write_frame(OPCODE_PONG, &payload)?,
                OPCODE_CLOSE => return Ok(false),
                _ => {}
            }
        }
    }
}

/// Opens the TLS connection to `kws{dc}` and completes the WebSocket upgrade.
pub fn connect(dc: i16) -> io::Result<Frames<TlsStream>> {
    let name = host(dc);
    let sock = connect_tcp(&name)?;
    sock.set_nodelay(true)?;
    let server_name = ServerName::try_from(name.clone())
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidInput, format!("{name}: {e}")))?;
    let conn = ClientConnection::new(tls_config(), server_name)
        .map_err(|e| io::Error::other(format!("tls: {e}")))?;
    // Non-blocking from the start: the upgrade loop waits on its own deadline,
    // and the relay never lets a socket timeout reach the TLS state machine.
    sock.set_nonblocking(true)?;
    let stream = TlsStream {
        conn,
        sock,
        eof: false,
    };
    let mut frames = Frames::new(stream);
    frames.handshake(&name)?;
    Ok(frames)
}

/// Resolves and connects to `host:443`, falling back to a known front address
/// when the name does not resolve.
fn connect_tcp(name: &str) -> io::Result<TcpStream> {
    match (name, 443u16).to_socket_addrs() {
        Ok(addrs) => {
            let mut last = None;
            for addr in addrs {
                match TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT) {
                    Ok(stream) => return Ok(stream),
                    Err(e) => last = Some(e),
                }
            }
            match last {
                Some(e) => Err(e),
                None => Err(io::Error::new(io::ErrorKind::NotFound, "no address")),
            }
        }
        Err(_) => TcpStream::connect_timeout(&(FALLBACK_IP, 443).into(), CONNECT_TIMEOUT),
    }
}

/// Relays the client stream and the WebSocket until either side closes.
///
/// One thread pumps client bytes into a channel; this thread both writes those
/// bytes as frames and reads frames back into the client. The TLS stream is
/// non-blocking, so when both directions are quiet the loop sleeps briefly
/// instead of spinning.
///
/// `dc` is written into the first 64-byte obfuscated header when one is seen,
/// because the WebSocket front routes by that number. A client that already
/// writes its own (tdesktop does) sees the same value written back.
pub fn relay(client: TcpStream, mut frames: Frames<TlsStream>, dc: i16) {
    let debug = std::env::var_os("AYU_DPI_DEBUG").is_some();
    let Some(mut reader) = client.try_clone().ok() else {
        return;
    };
    let _ = client.set_write_timeout(Some(Duration::from_secs(30)));
    let mut writer = client;
    let (tx, rx) = std::sync::mpsc::channel::<Vec<u8>>();

    let upload = std::thread::spawn(move || {
        let mut buf = vec![0u8; 32 * 1024];
        loop {
            match reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    if debug {
                        trace(&format!("ayu-dpi: ws upload {n} bytes"));
                    }
                    if tx.send(buf[..n].to_vec()).is_err() {
                        break;
                    }
                }
                Err(ref e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(e) => {
                    if debug {
                        trace(&format!("ayu-dpi: ws upload ended: {e}"));
                    }
                    break;
                }
            }
        }
    });

    // The header is buffered until its 64 bytes are complete, so the data
    // centre number can be written before the first frame goes out.
    let mut head: Vec<u8> = Vec::with_capacity(OBFUSCATED_HEADER);
    let mut head_sent = false;
    let mut payload = Vec::new();
    loop {
        let mut closed = false;
        let mut sent_any = false;
        loop {
            match rx.try_recv() {
                Ok(chunk) => {
                    sent_any = true;
                    let data = if head_sent {
                        chunk
                    } else {
                        head.extend_from_slice(&chunk);
                        if head.len() < OBFUSCATED_HEADER {
                            continue;
                        }
                        head_sent = true;
                        let mut assembled = std::mem::take(&mut head);
                        set_dc(&mut assembled, dc);
                        trace(&format!("ayu-dpi: ws header dc written as {dc}"));
                        assembled
                    };
                    if let Err(e) = frames.write_binary(&data) {
                        if debug {
                            trace(&format!("ayu-dpi: ws frame write failed: {e}"));
                        }
                        closed = true;
                        break;
                    }
                }
                Err(std::sync::mpsc::TryRecvError::Empty) => break,
                Err(std::sync::mpsc::TryRecvError::Disconnected) => {
                    closed = true;
                    break;
                }
            }
        }
        if closed {
            break;
        }
        match frames.read_message(&mut payload) {
            Ok(true) => {
                if debug {
                    trace(&format!("ayu-dpi: ws download {} bytes", payload.len()));
                }
                if writer.write_all(&payload).is_err() {
                    break;
                }
            }
            Ok(false) => {
                if debug {
                    trace("ayu-dpi: ws closed by server");
                }
                break;
            }
            Err(ref e)
                if e.kind() == io::ErrorKind::TimedOut || e.kind() == io::ErrorKind::WouldBlock =>
            {
                if !sent_any {
                    std::thread::sleep(POLL_INTERVAL);
                }
            }
            Err(e) => {
                if debug {
                    trace(&format!("ayu-dpi: ws read failed: {e}"));
                }
                break;
            }
        }
    }

    let _ = writer.shutdown(std::net::Shutdown::Both);
    let _ = upload.join();
}

/// Index of the first occurrence of `needle` in `hay`.
fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || hay.len() < needle.len() {
        return None;
    }
    hay.windows(needle.len()).position(|w| w == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn data_centres_by_address() {
        let v4 = |s: &str| s.parse::<IpAddr>().expect("ip");
        assert_eq!(dc_of(v4("149.154.175.53")), Some(1));
        assert_eq!(dc_of(v4("149.154.167.51")), Some(2));
        assert_eq!(dc_of(v4("149.154.175.100")), Some(3));
        assert_eq!(dc_of(v4("149.154.167.91")), Some(4));
        assert_eq!(dc_of(v4("91.108.56.130")), Some(5));
        assert_eq!(dc_of(v4("2001:b28:f23d:f001::a")), Some(1));
        assert_eq!(dc_of(v4("2001:67c:4e8:f004::a")), Some(4));
        assert_eq!(dc_of(v4("8.8.8.8")), None);
    }

    #[test]
    fn hosts_are_named_for_their_data_centre() {
        assert_eq!(host(2), "kws2.web.telegram.org");
        assert_eq!(host(-4), "kws4.web.telegram.org");
    }

    #[test]
    fn find_locates_the_header_end() {
        assert_eq!(find(b"abc\r\n\r\nrest", b"\r\n\r\n"), Some(3));
        assert_eq!(find(b"abc", b"\r\n\r\n"), None);
    }

    #[test]
    fn set_dc_writes_the_number_into_the_encrypted_tag() {
        // A header shaped like the obfuscated transport's: random body, a tag in
        // 56..60, then encrypted with the stream key from 8..56.
        let mut head = [0u8; OBFUSCATED_HEADER];
        for (i, b) in head.iter_mut().enumerate() {
            *b = (i as u8).wrapping_mul(7).wrapping_add(3);
        }
        head[56..60].copy_from_slice(&[0xEF, 0xEF, 0xEF, 0xEF]); // abridged tag
        let original = head;

        // Encrypt 56..64 the way the client does.
        let mut key = [0u8; 32];
        key.copy_from_slice(&head[8..40]);
        let mut iv = [0u8; 16];
        iv.copy_from_slice(&head[40..56]);
        let mut stream = [0u8; OBFUSCATED_HEADER];
        Ctr128BE::<Aes256>::new(&key.into(), &iv.into()).apply_keystream(&mut stream);
        for i in 56..64 {
            head[i] ^= stream[i];
        }

        set_dc(&mut head, 4);

        // The key part is untouched, so the client's own cipher is unaffected.
        assert_eq!(head[..56], original[..56]);
        // Decrypting shows DC 4 in 60..62 and the tag preserved in 56..60.
        let mut plain = [0u8; OBFUSCATED_HEADER];
        plain.copy_from_slice(&head);
        Ctr128BE::<Aes256>::new(&key.into(), &iv.into()).apply_keystream(&mut plain);
        assert_eq!(&plain[56..60], &[0xEF, 0xEF, 0xEF, 0xEF]);
        assert_eq!(&plain[60..62], &4i16.to_le_bytes());
    }

    #[test]
    fn set_dc_ignores_a_short_header() {
        let mut head = [0u8; 10];
        set_dc(&mut head, 1);
        assert_eq!(head, [0u8; 10]);
    }

    #[test]
    fn base64_matches_the_rfc_examples() {
        assert_eq!(base64(b""), "");
        assert_eq!(base64(b"f"), "Zg==");
        assert_eq!(base64(b"fo"), "Zm8=");
        assert_eq!(base64(b"foo"), "Zm9v");
        assert_eq!(base64(b"foob"), "Zm9vYg==");
        assert_eq!(base64(b"fooba"), "Zm9vYmE=");
        assert_eq!(base64(b"foobar"), "Zm9vYmFy");
    }

    /// A plain (no TLS) echo server: completes the upgrade, then echoes every
    /// binary frame back unmasked. Returns the bound address.
    fn echo_server() -> std::net::SocketAddr {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr");
        std::thread::spawn(move || {
            let (mut sock, _) = listener.accept().expect("accept");
            // Consume the upgrade request.
            let mut head = Vec::new();
            let mut byte = [0u8; 1];
            while !head.ends_with(b"\r\n\r\n") {
                if sock.read_exact(&mut byte).is_err() {
                    return;
                }
                head.push(byte[0]);
            }
            sock.write_all(
                b"HTTP/1.1 101 Switching Protocols\r\n\
                  Upgrade: websocket\r\n\
                  Connection: Upgrade\r\n\
                  Sec-WebSocket-Accept: x\r\n\r\n",
            )
            .expect("reply");
            loop {
                let mut two = [0u8; 2];
                if sock.read_exact(&mut two).is_err() {
                    return;
                }
                let opcode = two[0] & 0x0f;
                let masked = two[1] & 0x80 != 0;
                assert!(masked, "client frames must be masked");
                let mut len = (two[1] & 0x7f) as usize;
                if len == 126 {
                    let mut ext = [0u8; 2];
                    sock.read_exact(&mut ext).expect("len16");
                    len = u16::from_be_bytes(ext) as usize;
                } else if len == 127 {
                    let mut ext = [0u8; 8];
                    sock.read_exact(&mut ext).expect("len64");
                    len = u64::from_be_bytes(ext) as usize;
                }
                let mut mask = [0u8; 4];
                sock.read_exact(&mut mask).expect("mask");
                let mut payload = vec![0u8; len];
                sock.read_exact(&mut payload).expect("payload");
                for (i, b) in payload.iter_mut().enumerate() {
                    *b ^= mask[i & 3];
                }
                if opcode == OPCODE_CLOSE {
                    return;
                }
                // Echo back unmasked.
                let mut frame = vec![0x80 | OPCODE_BIN];
                if len < 126 {
                    frame.push(len as u8);
                } else if len <= u16::MAX as usize {
                    frame.push(126);
                    frame.extend_from_slice(&(len as u16).to_be_bytes());
                } else {
                    frame.push(127);
                    frame.extend_from_slice(&(len as u64).to_be_bytes());
                }
                frame.extend_from_slice(&payload);
                if sock.write_all(&frame).is_err() {
                    return;
                }
            }
        });
        addr
    }

    #[test]
    fn frames_round_trip_through_a_server() {
        let addr = echo_server();
        let sock = TcpStream::connect(addr).expect("connect");
        let mut frames = Frames::new(sock);
        frames.handshake("localhost").expect("handshake");

        for message in [&b"hello"[..], &[0u8; 200][..], &[7u8; 70000][..]] {
            frames.write_binary(message).expect("write");
            let mut got = Vec::new();
            assert!(frames.read_message(&mut got).expect("read"));
            assert_eq!(got, message, "echo of {} bytes", message.len());
        }
    }

    #[test]
    fn a_refused_upgrade_is_an_error() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr");
        std::thread::spawn(move || {
            let (mut sock, _) = listener.accept().expect("accept");
            let mut head = Vec::new();
            let mut byte = [0u8; 1];
            while !head.ends_with(b"\r\n\r\n") {
                if sock.read_exact(&mut byte).is_err() {
                    return;
                }
                head.push(byte[0]);
            }
            let _ = sock.write_all(b"HTTP/1.1 403 Forbidden\r\n\r\n");
        });
        let sock = TcpStream::connect(addr).expect("connect");
        let mut frames = Frames::new(sock);
        let error = frames.handshake("localhost").expect_err("refused");
        assert!(error.to_string().contains("403"), "{error}");
    }

    #[test]
    fn a_partial_frame_survives_a_timeout() {
        // A server that sends the frame header, waits, then the payload: the
        // reader must not lose the header when the timeout fires in between.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr");
        std::thread::spawn(move || {
            let (mut sock, _) = listener.accept().expect("accept");
            let mut head = Vec::new();
            let mut byte = [0u8; 1];
            while !head.ends_with(b"\r\n\r\n") {
                if sock.read_exact(&mut byte).is_err() {
                    return;
                }
                head.push(byte[0]);
            }
            sock.write_all(b"HTTP/1.1 101 Switching Protocols\r\n\r\n")
                .expect("reply");
            // Header first, then a pause, then the payload.
            sock.write_all(&[0x80 | OPCODE_BIN, 5]).expect("header");
            sock.flush().expect("flush");
            std::thread::sleep(Duration::from_millis(60));
            sock.write_all(b"world").expect("payload");
            sock.flush().expect("flush");
            std::thread::sleep(Duration::from_millis(200));
        });
        let sock = TcpStream::connect(addr).expect("connect");
        sock.set_read_timeout(Some(Duration::from_millis(20)))
            .expect("timeout");
        let mut frames = Frames::new(sock);
        frames.handshake("localhost").expect("handshake");
        let mut got = Vec::new();
        let mut attempts = 0;
        loop {
            match frames.read_message(&mut got) {
                Ok(true) => break,
                Ok(false) => panic!("unexpected close"),
                Err(ref e)
                    if e.kind() == io::ErrorKind::TimedOut
                        || e.kind() == io::ErrorKind::WouldBlock =>
                {
                    attempts += 1;
                    assert!(attempts < 50, "never completed the frame");
                }
                Err(e) => panic!("unexpected error: {e}"),
            }
        }
        assert_eq!(got, b"world");
    }
}
