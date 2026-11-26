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
use ayu_core::dpi::stealth::{
    ClientHelloFragmentation, StealthOptions, Timing, prepare_client_hello_fragmentation,
};

use crate::pace::{connection_spacing, record_pacing_delay, sleep_ms};
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
    relay(client, remote, strategy.as_ref(), proxy.stealth.as_ref());
    Ok(())
}

/// Sends the client's stream over the WebSocket front of the data centre the
/// requested address belongs to.
fn relay_over_websocket(proxy: &Proxy, client: TcpStream, ip: IpAddr) -> io::Result<()> {
    let dc = ws::dc_of(ip).unwrap_or(ws::DEFAULT_DC);
    let frames = match ws::connect(dc, proxy.stealth.as_ref()) {
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
fn relay(
    client: TcpStream,
    remote: TcpStream,
    strategy: Option<&Strategy>,
    stealth: &StealthOptions,
) {
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
    let fragmentation = stealth.client_hello_fragmentation;
    let spacing_ms = connection_spacing(stealth.connection_pattern);
    let timing = stealth.timing;
    // client -> remote, with the first-payload desync, the connection spacing
    // and the data-phase pacing.
    let forward = std::thread::spawn(move || {
        let first = match read_first_payload(&mut client_read) {
            Ok(buf) => buf,
            Err(_) => return,
        };
        // The beta holds the ClientHello back for the configured spacing after
        // the TCP connect (`TlsSocket::plainConnected`); the equivalent point on
        // this transport is between the upstream connect and the first payload.
        if !first.is_empty() {
            sleep_ms(spacing_ms);
        }
        if !write_first_payload(&mut remote_write, &first, owned.as_ref(), fragmentation) {
            return;
        }
        // The first payload is the ClientHello, the beta's first record, so the
        // data-phase pacing starts counting from one.
        let frames_written = u32::from(!first.is_empty());
        pace_uplink(&mut client_read, &mut remote_write, timing, frames_written);
        let _ = remote_write.shutdown(std::net::Shutdown::Write);
    });

    // remote -> client, unchanged.
    let _ = io::copy(&mut remote_read, &mut client_write);
    let _ = client_write.shutdown(std::net::Shutdown::Write);
    let _ = forward.join();
}

/// Forwards the rest of the client stream to the remote, applying the beta's
/// data-phase pacing (`TlsSocket::recordPacingDelay`): every write after the
/// first is preceded by a delay drawn from how many writes have already gone
/// out, and pacing stops after the beta's frame cap. `frames_written` is the
/// count already sent before this loop (the ClientHello is one). With
/// `Timing::Off` every delay is zero, so this is a plain copy and the proven
/// path adds no latency.
///
/// The beta paces TLS records; this transport carries the obfuscated MTProto
/// stream, so a frame here is one write this relay makes. The delay values and
/// the frame cap are the beta's; only the boundary they attach to is this
/// relay's own.
fn pace_uplink(
    client: &mut TcpStream,
    remote: &mut TcpStream,
    timing: Timing,
    frames_written: u32,
) {
    let mut buf = vec![0u8; 32 * 1024];
    let mut written = frames_written;
    loop {
        let n = match client.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => n,
            Err(ref e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => break,
        };
        sleep_ms(record_pacing_delay(timing, written));
        if remote.write_all(&buf[..n]).is_err() {
            break;
        }
        written += 1;
    }
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

/// Writes the first payload, applying the split strategy when present and the
/// stealth ClientHello fragmentation when it applies.
///
/// The two mechanisms both cut the first payload, so they do not stack: the
/// strategy's own splits win when it has any, because the configured strategy
/// is the operator's explicit desync line and it is the one proven end to end;
/// the stealth fragmentation then applies only to a payload the strategy does
/// not already split. [`Proxy::stealth_report`] says when this happens.
/// Returns false when nothing could be written (connection is dead).
fn write_first_payload(
    remote: &mut TcpStream,
    payload: &[u8],
    strategy: Option<&Strategy>,
    fragmentation: ClientHelloFragmentation,
) -> bool {
    let (chunks, delay_ms) = plan_first_payload(payload, strategy, fragmentation);
    for (index, chunk) in chunks.iter().enumerate() {
        if index > 0 && delay_ms > 0 {
            // The pause between the two hello pieces is the point of the soft
            // fragmentation: the DPI must not see them as one burst.
            std::thread::sleep(Duration::from_millis(u64::from(delay_ms)));
        }
        if remote.write_all(chunk).is_err() {
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

/// The pure decision `write_first_payload` makes: the ordered chunks to write
/// for the first payload and the pause to take between them.
///
/// Kept separate from the socket so a test can assert the exact cut points,
/// which is the only way to tell "the strategy split" from "the stealth
/// fragmentation split" - both rejoin to the same bytes.
fn plan_first_payload(
    payload: &[u8],
    strategy: Option<&Strategy>,
    fragmentation: ClientHelloFragmentation,
) -> (Vec<Vec<u8>>, u16) {
    if payload.is_empty() {
        return (Vec::new(), 0);
    }
    let strategy_chunks = match strategy {
        Some(s) if !s.split_positions.is_empty() => {
            let chunks = s.userspace_chunks(payload);
            if chunks.len() > 1 { Some(chunks) } else { None }
        }
        _ => None,
    };

    match strategy_chunks {
        Some(chunks) => (chunks, 0u16),
        None => match prepare_client_hello_fragmentation(payload, fragmentation) {
            Some(plan) if plan.first_size < payload.len() => (
                vec![
                    payload[..plan.first_size].to_vec(),
                    payload[plan.first_size..].to_vec(),
                ],
                plan.second_delay_ms,
            ),
            _ => (vec![payload.to_vec()], 0),
        },
    }
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
    use ayu_core::dpi::stealth::ConnectionPattern;

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

    /// A socket pair whose sender goes through `write_first_payload`, so a test
    /// can read back what actually crossed, including the split.
    fn roundtrip(
        payload: &[u8],
        strategy: Option<&Strategy>,
        fragmentation: ClientHelloFragmentation,
    ) -> Vec<u8> {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr");
        let mut sender = TcpStream::connect(addr).expect("connect");
        let (mut receiver, _) = listener.accept().expect("accept");

        assert!(write_first_payload(
            &mut sender,
            payload,
            strategy,
            fragmentation
        ));
        sender
            .shutdown(std::net::Shutdown::Write)
            .expect("shutdown");

        let mut got = Vec::new();
        receiver.read_to_end(&mut got).expect("read");
        got
    }

    #[test]
    fn write_first_payload_splits_then_joins_intact() {
        let cfg = parse_line("--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1");
        let strategy = cfg.rules[0].strategy.clone();
        let payload = b"CLIENTHELLO";
        let got = roundtrip(payload, Some(&strategy), ClientHelloFragmentation::Off);
        assert_eq!(got, payload);
    }

    #[test]
    fn no_strategy_writes_the_whole_payload() {
        let got = roundtrip(b"x", None, ClientHelloFragmentation::Off);
        assert_eq!(got, b"x");
    }

    /// A well-formed ClientHello-shaped buffer above the 384-byte fragment
    /// floor, with a real SNI in the hostname range, so the stealth plan is the
    /// one the C++ side would also choose.
    fn hello_fixture() -> Vec<u8> {
        let mut hello = vec![
            0x16, 0x03, 0x01, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x03, 0x03,
        ];
        hello.extend_from_slice(&[0u8; 32]);
        hello.push(0);
        hello.extend_from_slice(&[0x00, 0x02, 0x13, 0x01]);
        hello.push(1);
        hello.push(0);
        let host = b"fragment.test";
        let mut sni = Vec::new();
        sni.extend_from_slice(&[0x00, 0x00]);
        sni.extend_from_slice(&(host.len() as u16 + 5).to_be_bytes());
        sni.extend_from_slice(&(host.len() as u16 + 3).to_be_bytes());
        sni.push(0);
        sni.extend_from_slice(&(host.len() as u16).to_be_bytes());
        sni.extend_from_slice(host);
        hello.extend_from_slice(&(sni.len() as u16).to_be_bytes());
        hello.extend_from_slice(&sni);
        // Pad the handshake body to cross 384 bytes.
        while hello.len() < 400 {
            hello.push(0);
        }
        // Fix the record and handshake lengths so the buffer is well formed.
        let hs_len = hello.len() - 5 - 4;
        hello[6] = (hs_len >> 16) as u8;
        hello[7] = (hs_len >> 8) as u8;
        hello[8] = hs_len as u8;
        let rec_len = hello.len() - 5;
        hello[3] = (rec_len >> 8) as u8;
        hello[4] = rec_len as u8;
        hello
    }

    #[test]
    fn stealth_fragmentation_cuts_the_hello_and_joins_it_back() {
        let hello = hello_fixture();
        let got = roundtrip(&hello, None, ClientHelloFragmentation::Soft);
        // The bytes survive a split with a pause in between.
        assert_eq!(got, hello);
    }

    #[test]
    fn the_strategy_splits_win_over_the_stealth_fragmentation() {
        // Both mechanisms can cut the first payload; a configured strategy is
        // the operator's explicit line, so it must be the one applied. The
        // observable difference is the split point: this strategy cuts after
        // byte 4, while the stealth plan would cut inside the SNI range far
        // to the right. A rejoin-only assertion cannot tell them apart.
        let cfg = parse_line("--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=4");
        let strategy = cfg.rules[0].strategy.clone();
        let payload = b"0123456789abcdef";
        let (chunks, delay) =
            plan_first_payload(payload, Some(&strategy), ClientHelloFragmentation::Soft);
        assert_eq!(chunks.len(), 2, "{chunks:?}");
        assert_eq!(chunks[0], b"0123");
        assert_eq!(chunks[1], b"456789abcdef");
        // The strategy's split carries no stealth pause.
        assert_eq!(delay, 0);
        // And the bytes still cross intact.
        assert_eq!(
            roundtrip(payload, Some(&strategy), ClientHelloFragmentation::Soft),
            payload
        );
    }

    #[test]
    fn a_hello_shaped_payload_is_actually_cut_by_the_stealth_plan() {
        // With no strategy splits at all, the stealth plan is the one that cuts,
        // and it must cut strictly inside the buffer, not hand back one chunk.
        let hello = hello_fixture();
        let (chunks, _delay) = plan_first_payload(&hello, None, ClientHelloFragmentation::Soft);
        assert!(chunks.len() >= 2, "the hello was not cut: {chunks:?}");
        assert!(chunks.iter().map(Vec::len).sum::<usize>() == hello.len());
        assert_eq!(chunks[0], hello[..chunks[0].len()]);
    }

    #[test]
    fn stealth_fragmentation_ignores_a_short_payload() {
        // Below the 384-byte floor the plan is None, so the payload goes out
        // whole. The bytes are unchanged either way, which is the invariant.
        let got = roundtrip(b"short", None, ClientHelloFragmentation::Soft);
        assert_eq!(got, b"short");
    }

    /// Runs the real relay between a client-side pair and an upstream-side pair.
    /// Returns the joined relay thread, the writing end the test uses as the
    /// client, and the reading end that stands for the upstream server.
    fn relay_pair(
        stealth: StealthOptions,
    ) -> (
        std::thread::JoinHandle<()>,
        TcpStream,
        TcpStream,
        std::net::TcpListener,
    ) {
        // The upstream socket the relay dials is one end of `up_listener`; the
        // test reads the payload from the other end.
        let up_listener = TcpListener::bind("127.0.0.1:0").expect("bind upstream");
        let up_addr = up_listener.local_addr().expect("addr");
        let remote = TcpStream::connect(up_addr).expect("connect upstream");
        let (up_server, _) = up_listener.accept().expect("accept upstream");

        // The client socket the relay serves is one end of `cli_listener`.
        let cli_listener = TcpListener::bind("127.0.0.1:0").expect("bind client");
        let cli_addr = cli_listener.local_addr().expect("addr");
        let client = TcpStream::connect(cli_addr).expect("connect client");
        let (cli_server, _) = cli_listener.accept().expect("accept client");

        let handle = std::thread::spawn(move || {
            relay(cli_server, remote, None, &stealth);
        });
        (handle, client, up_server, cli_listener)
    }

    /// The connection spacing really delays the first payload, and `Off` does
    /// not: the beta's `ConnectionSpacing` reached the wire with its own value.
    #[test]
    fn connection_spacing_delays_the_first_payload() {
        let stealth = StealthOptions {
            connection_pattern: ConnectionPattern::Strict, // 700 ms
            ..StealthOptions::default()
        };
        let (_handle, mut client, mut up_server, _l) = relay_pair(stealth);

        let start = std::time::Instant::now();
        client.write_all(b"CLIENTHELLO").expect("write");
        client.flush().expect("flush");
        let mut got = [0u8; 11];
        up_server.read_exact(&mut got).expect("read");
        let elapsed = start.elapsed();

        assert_eq!(&got, b"CLIENTHELLO");
        // 700 ms minus generous scheduling slack; the point is that the delay
        // is real, not that it is exact.
        assert!(
            elapsed >= Duration::from_millis(600),
            "first payload crossed in {elapsed:?}, spacing was not applied"
        );
    }

    #[test]
    fn no_spacing_when_the_pattern_is_off() {
        let stealth = StealthOptions {
            connection_pattern: ConnectionPattern::Off,
            ..StealthOptions::default()
        };
        let (_handle, mut client, mut up_server, _l) = relay_pair(stealth);

        let start = std::time::Instant::now();
        client.write_all(b"CLIENTHELLO").expect("write");
        client.flush().expect("flush");
        let mut got = [0u8; 11];
        up_server.read_exact(&mut got).expect("read");
        let elapsed = start.elapsed();

        assert_eq!(&got, b"CLIENTHELLO");
        assert!(
            elapsed < Duration::from_millis(150),
            "first payload took {elapsed:?} with spacing off"
        );
    }

    /// Pacing delays every chunk after the first, so two chunks written at once
    /// arrive apart, while a single chunk is not delayed at all.
    #[test]
    fn data_phase_pacing_separates_chunks() {
        let stealth = StealthOptions {
            timing: Timing::Balanced, // 20..100 ms per gap
            ..StealthOptions::default()
        };
        let (_handle, mut client, mut up_server, _l) = relay_pair(stealth);

        let start = std::time::Instant::now();
        client.write_all(b"ONE").expect("write");
        client.flush().expect("flush");
        let mut first = [0u8; 3];
        up_server.read_exact(&mut first).expect("read first");
        // The first chunk itself is not paced (delay is drawn from the count of
        // chunks already written, which is zero here).
        assert!(
            start.elapsed() < Duration::from_millis(80),
            "the first chunk was paced: {:?}",
            start.elapsed()
        );

        let second_start = std::time::Instant::now();
        client.write_all(b"TWO").expect("write");
        client.flush().expect("flush");
        let mut second = [0u8; 3];
        up_server.read_exact(&mut second).expect("read second");
        assert_eq!(&first, b"ONE");
        assert_eq!(&second, b"TWO");
        // The gap before the second chunk is the beta's balanced delay, at
        // least its lower bound of 20 ms minus scheduling slack.
        assert!(
            second_start.elapsed() >= Duration::from_millis(10),
            "second chunk was not paced: {:?}",
            second_start.elapsed()
        );
    }
}
