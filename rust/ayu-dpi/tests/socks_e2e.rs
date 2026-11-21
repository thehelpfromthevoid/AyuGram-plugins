//! End-to-end tests for the userspace SOCKS5 desync proxy.
//!
//! These start the real proxy on an ephemeral port, speak SOCKS5 to it from a
//! hand-rolled client, and check that the bytes arrive intact at a real
//! upstream listener. Unit tests prove the planner and the parser; this proves
//! the transport actually moves a byte stream.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::thread;
use std::time::Duration;

use ayu_core::dpi::options::parse_line;
use ayu_dpi::{OwnedResources, Proxy};

/// A SOCKS5 client that connects and returns the stream after the reply.
fn socks_connect(proxy_addr: std::net::SocketAddr, host: &str, port: u16) -> TcpStream {
    let mut s = TcpStream::connect(proxy_addr).expect("connect proxy");
    s.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
    // greeting: version, one method, no-auth
    s.write_all(&[0x05, 0x01, 0x00]).unwrap();
    let mut resp = [0u8; 2];
    s.read_exact(&mut resp).unwrap();
    assert_eq!(resp, [0x05, 0x00], "method selection");
    // CONNECT with a domain name
    let mut req = vec![0x05, 0x01, 0x00, 0x03, host.len() as u8];
    req.extend_from_slice(host.as_bytes());
    req.extend_from_slice(&port.to_be_bytes());
    s.write_all(&req).unwrap();
    let mut reply = [0u8; 10];
    s.read_exact(&mut reply).unwrap();
    assert_eq!(reply[0], 0x05, "socks version");
    assert_eq!(reply[1], 0x00, "reply code");
    s
}

/// Starts a proxy on an ephemeral port with the given config; returns its
/// address and a shutdown handle.
fn start_proxy(config_line: &str) -> (std::net::SocketAddr, Proxy) {
    let cfg = parse_line(config_line);
    let proxy = Proxy::new(cfg, OwnedResources::default()).expect("proxy");
    let listener = TcpListener::bind("127.0.0.1:0").expect("reserve port");
    let addr = listener.local_addr().unwrap();
    drop(listener);
    let moving = proxy.clone();
    thread::spawn(move || {
        let _ = moving.run(addr);
    });
    // Give the accept loop a moment to bind.
    for _ in 0..100 {
        if TcpStream::connect(addr).is_ok() {
            break;
        }
        thread::sleep(Duration::from_millis(10));
    }
    (addr, proxy)
}

/// An upstream echo server: reads to EOF and echoes nothing, but records what
/// it received. Returns its address and a receiver for the captured bytes.
fn start_echo() -> (std::net::SocketAddr, std::sync::mpsc::Receiver<Vec<u8>>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind echo");
    let addr = listener.local_addr().unwrap();
    let (tx, rx) = std::sync::mpsc::channel();
    thread::spawn(move || {
        if let Ok((mut stream, _)) = listener.accept() {
            let mut got = Vec::new();
            let _ = stream.read_to_end(&mut got);
            let _ = tx.send(got);
        }
    });
    (addr, rx)
}

#[test]
fn a_split_strategy_forwards_the_stream_intact() {
    let (upstream, rx) = start_echo();
    let (proxy_addr, proxy) = start_proxy(&format!(
        "--filter-tcp={} --dpi-desync=multisplit --dpi-desync-split-pos=1,midsld",
        upstream.port()
    ));

    // A payload that looks like a start of a TLS record so the proxy buffers
    // a full "record" before splitting.
    let mut payload = vec![0x16, 0x03, 0x01, 0x00, 0x0c];
    payload.extend_from_slice(b"hello world!");
    let mut client = socks_connect(proxy_addr, "127.0.0.1", upstream.port());
    client.write_all(&payload).unwrap();
    client.flush().unwrap();
    drop(client); // closes -> upstream sees EOF -> read_to_end returns

    let got = rx
        .recv_timeout(Duration::from_secs(5))
        .expect("upstream bytes");
    assert_eq!(got, payload, "bytes must survive the split unchanged");
    proxy.request_shutdown();
    assert!(proxy.is_shutdown());
}

#[test]
fn a_connection_with_no_matching_rule_passes_through() {
    let (upstream, rx) = start_echo();
    // Rule only matches port 443; we connect to the echo port, so it must fall
    // back to a plain relay.
    let (proxy_addr, proxy) =
        start_proxy("--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1");
    let mut client = socks_connect(proxy_addr, "127.0.0.1", upstream.port());
    client.write_all(b"raw bytes").unwrap();
    client.flush().unwrap();
    drop(client);
    let got = rx
        .recv_timeout(Duration::from_secs(5))
        .expect("upstream bytes");
    assert_eq!(got, b"raw bytes");
    proxy.request_shutdown();
}

#[test]
fn the_engine_reports_a_split_for_the_first_record() {
    // The planner, exercised through the same config the proxy uses, so the
    // expectation is not re-derived by hand.
    let cfg = parse_line("--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1");
    let strategy = &cfg.rules[0].strategy;
    let chunks = strategy.userspace_chunks(b"abcdef");
    assert_eq!(chunks.len(), 2);
    assert_eq!(chunks[0], b"a");
    assert_eq!(chunks[1], b"bcdef");
}
