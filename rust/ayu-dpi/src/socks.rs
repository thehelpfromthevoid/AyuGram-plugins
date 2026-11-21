//! The SOCKS5 server loop and the desynchronising relay.
//!
//! Protocol scope is deliberately small: `CONNECT` with no authentication, the
//! three address types (IPv4, domain, IPv6), and a `NO AUTH` method. That is
//! everything a client pointing at a local proxy for TLS needs, and nothing
//! else is implemented rather than half-implemented.
//!
//! The desync happens on the first payload the client sends toward the remote,
//! because that payload is the TLS ClientHello whose first bytes carry the SNI.
//! The writer sends it as separate `write_all` calls with `TCP_NODELAY` set, so
//! the kernel emits separate TCP segments; a DPI that only inspects the first
//! segment sees a fragment with no complete SNI.

use std::io::{self, Read, Write};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr, TcpListener, TcpStream, ToSocketAddrs};
use std::sync::atomic::Ordering;
use std::time::Duration;

use ayu_core::dpi::Strategy;

use crate::{Proxy, ws};

const SOCKS_VERSION: u8 = 0x05;
const AUTH_NONE: u8 = 0x00;
const AUTH_UNACCEPTABLE: u8 = 0xFF;
const CMD_CONNECT: u8 = 0x01;
const ATYP_IPV4: u8 = 0x01;
const ATYP_DOMAIN: u8 = 0x03;
const ATYP_IPV6: u8 = 0x04;
const REP_SUCCESS: u8 = 0x00;
const REP_HOST_UNREACHABLE: u8 = 0x04;
const REP_COMMAND_UNSUPPORTED: u8 = 0x07;

/// Upper bound on the first payload buffered to complete a TLS record.
const MAX_FIRST_PAYLOAD: usize = 16 * 1024;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);

/// Runs the accept loop until the proxy's shutdown flag is set.
pub fn serve(proxy: &Proxy, listen: SocketAddr) -> io::Result<()> {
    let listener = TcpListener::bind(listen)?;
    listener.set_nonblocking(true)?;
    while !proxy.is_shutdown() {
        match listener.accept() {
            Ok((stream, _peer)) => {
                let proxy = proxy.clone();
                std::thread::spawn(move || {
                    // On Windows an accepted socket inherits the listener's
                    // non-blocking mode; the handler uses blocking reads.
                    let _ = stream.set_nonblocking(false);
                    let _ = stream.set_nodelay(true);
                    if let Err(error) = handle(&proxy, stream) {
                        // A peer that opens and closes without speaking (a
                        // readiness probe, a port scanner) ends in a clean EOF,
                        // which is not worth a log line.
                        let benign = error.kind() == io::ErrorKind::UnexpectedEof
                            || error.kind() == io::ErrorKind::ConnectionReset
                            || error.kind() == io::ErrorKind::BrokenPipe;
                        if !benign && !proxy.is_shutdown() {
                            eprintln!("ayu-dpi: connection ended: {error}");
                        }
                    }
                });
            }
            Err(ref e) if e.kind() == io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(10));
            }
            Err(e) => return Err(e),
        }
    }
    Ok(())
}

/// One client connection: negotiate, read the request, connect upstream, reply,
/// then relay. In WebSocket mode a data-centre address goes out through the
/// Telegram web front instead of a direct socket.
fn handle(proxy: &Proxy, mut client: TcpStream) -> io::Result<()> {
    negotiate(&mut client)?;
    let (host, port) = match read_request(&mut client) {
        Ok(target) => target,
        Err(_) => {
            reply(&mut client, REP_COMMAND_UNSUPPORTED)?;
            return Ok(());
        }
    };

    if proxy.ws_mode
        && let Ok(ip) = host.parse::<IpAddr>()
        && ws::dc_of(ip).is_some()
    {
        return relay_over_websocket(proxy, client, ip);
    }

    let remote = match connect_upstream(&host, port) {
        Ok(stream) => stream,
        Err(_) => {
            reply(&mut client, REP_HOST_UNREACHABLE)?;
            return Ok(());
        }
    };
    remote.set_nodelay(true)?;
    reply(&mut client, REP_SUCCESS)?;
    proxy.connections.fetch_add(1, Ordering::Relaxed);

    let strategy = proxy.strategy_for(&host, port).cloned();
    relay(client, remote, strategy.as_ref());
    Ok(())
}

/// Sends the client's stream over the WebSocket front of the data centre the
/// requested address belongs to.
fn relay_over_websocket(proxy: &Proxy, client: TcpStream, ip: IpAddr) -> io::Result<()> {
    let dc = ws::dc_of(ip).unwrap_or(ws::DEFAULT_DC);
    let frames = match ws::connect(dc) {
        Ok(frames) => frames,
        Err(_) => {
            let mut client = client;
            reply(&mut client, REP_HOST_UNREACHABLE)?;
            return Ok(());
        }
    };
    let mut client = client;
    reply(&mut client, REP_SUCCESS)?;
    proxy.connections.fetch_add(1, Ordering::Relaxed);
    ws::relay(client, frames, dc);
    Ok(())
}

/// Reads the version/method greeting and answers `NO AUTH`.
fn negotiate(client: &mut TcpStream) -> io::Result<()> {
    let mut head = [0u8; 2];
    client.read_exact(&mut head)?;
    if head[0] != SOCKS_VERSION {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("socks version {}", head[0]),
        ));
    }
    let mut methods = vec![0u8; head[1] as usize];
    client.read_exact(&mut methods)?;
    if !methods.contains(&AUTH_NONE) {
        client.write_all(&[SOCKS_VERSION, AUTH_UNACCEPTABLE])?;
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "client does not offer no-auth",
        ));
    }
    client.write_all(&[SOCKS_VERSION, AUTH_NONE])?;
    client.flush()
}

/// Reads a `CONNECT` request, returning `(host, port)`.
fn read_request(client: &mut TcpStream) -> io::Result<(String, u16)> {
    let mut head = [0u8; 4];
    client.read_exact(&mut head)?;
    if head[0] != SOCKS_VERSION {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "bad version"));
    }
    if head[1] != CMD_CONNECT {
        return Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "only CONNECT is supported",
        ));
    }
    let host = match head[3] {
        ATYP_IPV4 => {
            let mut raw = [0u8; 4];
            client.read_exact(&mut raw)?;
            Ipv4Addr::from(raw).to_string()
        }
        ATYP_DOMAIN => {
            let mut len = [0u8; 1];
            client.read_exact(&mut len)?;
            let mut name = vec![0u8; len[0] as usize];
            client.read_exact(&mut name)?;
            String::from_utf8(name)
                .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "non-UTF-8 domain"))?
        }
        ATYP_IPV6 => {
            let mut raw = [0u8; 16];
            client.read_exact(&mut raw)?;
            Ipv6Addr::from(raw).to_string()
        }
        other => {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("address type {other}"),
            ));
        }
    };
    let mut port = [0u8; 2];
    client.read_exact(&mut port)?;
    Ok((host, u16::from_be_bytes(port)))
}

/// Sends a SOCKS5 reply with a zero bound address (we do not expose it).
fn reply(client: &mut TcpStream, code: u8) -> io::Result<()> {
    client.write_all(&[SOCKS_VERSION, code, 0x00, ATYP_IPV4, 0, 0, 0, 0, 0, 0])?;
    client.flush()
}

/// Resolves and connects to the target.
fn connect_upstream(host: &str, port: u16) -> io::Result<TcpStream> {
    let addrs = (host, port).to_socket_addrs()?;
    let mut last = None;
    for addr in addrs {
        match TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT) {
            Ok(stream) => return Ok(stream),
            Err(e) => last = Some(e),
        }
    }
    Err(last.unwrap_or_else(|| io::Error::new(io::ErrorKind::NotFound, "no address resolved")))
}

/// Relays both directions, desynchronising the first client payload when a
/// strategy with splits applies.
fn relay(client: TcpStream, remote: TcpStream, strategy: Option<&Strategy>) {
    // One handle per direction; TcpStream clones share the socket.
    let mut client_read = match client.try_clone() {
        Ok(c) => c,
        Err(_) => return,
    };
    let mut client_write = client;
    let mut remote_read = match remote.try_clone() {
        Ok(r) => r,
        Err(_) => return,
    };
    let mut remote_write = remote;

    let owned = strategy.cloned();
    // client -> remote, with the first-payload desync.
    let forward = std::thread::spawn(move || {
        let first = match read_first_payload(&mut client_read) {
            Ok(buf) => buf,
            Err(_) => return,
        };
        if !write_first_payload(&mut remote_write, &first, owned.as_ref()) {
            return;
        }
        let _ = io::copy(&mut client_read, &mut remote_write);
        let _ = remote_write.shutdown(std::net::Shutdown::Write);
    });

    // remote -> client, unchanged.
    let _ = io::copy(&mut remote_read, &mut client_write);
    let _ = client_write.shutdown(std::net::Shutdown::Write);
    let _ = forward.join();
}

/// Reads the first payload, extending it to cover a whole TLS record when the
/// buffer starts one. A DPI inspects the ClientHello, so splitting only makes
/// sense once the record's bytes are all in hand.
fn read_first_payload(client: &mut TcpStream) -> io::Result<Vec<u8>> {
    let mut buf = vec![0u8; 8192];
    let n = client.read(&mut buf)?;
    buf.truncate(n);
    if n == 0 {
        return Ok(buf);
    }
    if let Some(need) = tls_record_len(&buf) {
        while buf.len() < need && buf.len() < MAX_FIRST_PAYLOAD {
            let want = (need - buf.len()).min(8192);
            let mut more = vec![0u8; want];
            let m = client.read(&mut more)?;
            if m == 0 {
                break;
            }
            buf.extend_from_slice(&more[..m]);
        }
    }
    Ok(buf)
}

/// Writes the first payload, applying the split strategy when present.
/// Returns false when nothing could be written (connection is dead).
fn write_first_payload(
    remote: &mut TcpStream,
    payload: &[u8],
    strategy: Option<&Strategy>,
) -> bool {
    if payload.is_empty() {
        return true;
    }
    let chunks = match strategy {
        Some(s) if !s.split_positions.is_empty() => {
            let chunks = s.userspace_chunks(payload);
            if chunks.len() > 1 {
                chunks
            } else {
                vec![payload.to_vec()]
            }
        }
        _ => vec![payload.to_vec()],
    };
    for chunk in chunks {
        if remote.write_all(&chunk).is_err() {
            return false;
        }
        // A flush between chunks is what makes the kernel emit separate
        // segments; TCP_NODELAY removes the coalescing delay.
        if remote.flush().is_err() {
            return false;
        }
    }
    true
}

/// The total length of a TLS record starting at `buf[0]`, if `buf` starts one.
fn tls_record_len(buf: &[u8]) -> Option<usize> {
    if buf.len() < 5 || buf[0] != 0x16 {
        return None;
    }
    let len = ((buf[3] as usize) << 8) | buf[4] as usize;
    if (4..=16384).contains(&len) {
        Some(5 + len)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ayu_core::dpi::options::parse_line;

    #[test]
    fn tls_record_len_reads_the_header() {
        let rec = vec![0x16, 0x03, 0x01, 0x00, 0x20, 0x01];
        assert_eq!(tls_record_len(&rec), Some(5 + 0x20));
    }

    #[test]
    fn tls_record_len_rejects_non_handshake() {
        let rec = vec![0x17, 0x03, 0x01, 0x00, 0x20];
        assert_eq!(tls_record_len(&rec), None);
        assert_eq!(tls_record_len(&[0x16, 0x03]), None);
    }

    #[test]
    fn write_first_payload_splits_then_joins_intact() {
        // A local socket pair to observe that bytes survive the split.
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr");
        let mut sender = TcpStream::connect(addr).expect("connect");
        let (mut receiver, _) = listener.accept().expect("accept");

        let cfg = parse_line("--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1");
        let strategy = cfg.rules[0].strategy.clone();
        let payload = b"CLIENTHELLO";
        assert!(write_first_payload(&mut sender, payload, Some(&strategy)));
        sender
            .shutdown(std::net::Shutdown::Write)
            .expect("shutdown");

        let mut got = Vec::new();
        receiver.read_to_end(&mut got).expect("read");
        assert_eq!(got, payload);
    }

    #[test]
    fn no_strategy_writes_the_whole_payload() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr");
        let mut sender = TcpStream::connect(addr).expect("connect");
        let (mut receiver, _) = listener.accept().expect("accept");
        assert!(write_first_payload(&mut sender, b"x", None));
        sender
            .shutdown(std::net::Shutdown::Write)
            .expect("shutdown");
        let mut got = Vec::new();
        receiver.read_to_end(&mut got).expect("read");
        assert_eq!(got, b"x");
    }
}
