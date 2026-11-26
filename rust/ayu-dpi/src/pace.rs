//! Execution of the two stealth knobs this userspace transport can honour for
//! real: the per-connection spacing before the first payload
//! (`connection_pattern`) and the uplink pacing of the data phase (`timing`).
//!
//! The numbers are not invented here. They are the tables of the macOS 13.2
//! beta carried over value for value: `ConnectionSpacing` in
//! `mtproto/proxy/mtproxy/handshake_plan.cpp` and `TlsSocket::recordPacingDelay`
//! in `mtproto/proxy/mtproxy/tls_socket_records.cpp`.
//!
//! Two deliberate divergences are documented where they live. First, the
//! beta's `Browser` entry (250 ms) is the MTProxy TLS socket's own default
//! pacing and this relay has no such front, so both `Off` and the client
//! default `Browser` add no delay here (see [`connection_spacing`]). Second,
//! the beta paces TLS *records*; this transport carries the obfuscated MTProto
//! stream, which has no TLS records, so the same delay values are applied at
//! the boundaries of the writes this relay itself makes (see
//! [`record_pacing_delay`]).

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use ayu_core::dpi::stealth::{ConnectionPattern, Timing};

/// Milliseconds to wait between the upstream connection and the first client
/// payload, the beta's `ConnectionSpacing`.
///
/// `Off` and `Browser` both mean "the relay adds nothing". `Browser` is the
/// client's own default in the beta and stands for the MTProxy TLS socket's
/// pacing, which this transport does not have; treating it as zero keeps the
/// default state free of an added delay, while the toggle the page offers
/// (`Soft`) reaches the wire as its beta value of 150 ms.
pub fn connection_spacing(pattern: ConnectionPattern) -> u32 {
    match pattern {
        ConnectionPattern::Off | ConnectionPattern::Browser => 0,
        ConnectionPattern::Soft => 150,
        ConnectionPattern::Quiet => 400,
        ConnectionPattern::Strict => 700,
    }
}

/// Frames after which pacing stops, the beta's `kMaxPacedFrames`: a pace is a
/// handshake-phase shape, not a permanent throttle on a live connection.
const MAX_PACED_FRAMES: u32 = 24;

/// Milliseconds to wait before the next uplink write, the beta's
/// `recordPacingDelay`.
///
/// `frame_index` is 1-based: the number of writes already made, so the very
/// first chunk is `1` and pacing has stopped by `MAX_PACED_FRAMES + 1`. `Off`
/// returns zero, which is what makes the default path pace nothing.
///
/// The beta counts TLS records here; this transport has none, so a "frame" is
/// one write this relay makes. That keeps the beta's timing and its 24-frame
/// cap while being honest that the boundaries are this relay's, not the TLS
/// record layer's.
pub fn record_pacing_delay(timing: Timing, frame_index: u32) -> u32 {
    if frame_index == 0 || frame_index > MAX_PACED_FRAMES {
        return 0;
    }
    match timing {
        Timing::Off => 0,
        // `8 + RandomIndex(14) + RandomIndex(25)`, value set `[8, 45]`.
        Timing::Gentle => 8 + jitter(14) + jitter(25),
        // `20 + RandomIndex(28) + RandomIndex(54)`, value set `[20, 100]`.
        Timing::Balanced => 20 + jitter(28) + jitter(54),
    }
}

/// A uniform value in `[0, max)`, the shape of the beta's `base::RandomIndex`.
///
/// Drawn from the wall clock and a process-wide counter. No new dependency:
/// pacing jitter is a traffic shape, not security material, and the transport
/// already seeds its WebSocket key the same way.
fn jitter(max: u32) -> u32 {
    if max == 0 {
        return 0;
    }
    fill() % max
}

fn fill() -> u32 {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let step = COUNTER.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0);
    let mut x = nanos.wrapping_mul(0x2545_F491_4F6C_DD1D)
        ^ step.wrapping_mul(0x9E37_79B9_7F4A_7C15)
        ^ 0xD1B5_4A32_D192_ED03;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    (x >> 32) as u32
}

/// Sleeps for `ms` milliseconds, unless that is zero.
pub fn sleep_ms(ms: u32) {
    if ms > 0 {
        std::thread::sleep(Duration::from_millis(u64::from(ms)));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn connection_spacing_matches_the_beta_table() {
        // The three explicit levels are the beta's `ConnectionSpacing` values;
        // `Off` and the client default `Browser` add nothing on this transport.
        assert_eq!(connection_spacing(ConnectionPattern::Off), 0);
        assert_eq!(connection_spacing(ConnectionPattern::Browser), 0);
        assert_eq!(connection_spacing(ConnectionPattern::Soft), 150);
        assert_eq!(connection_spacing(ConnectionPattern::Quiet), 400);
        assert_eq!(connection_spacing(ConnectionPattern::Strict), 700);
    }

    #[test]
    fn pacing_is_off_unless_a_timing_mode_is_set() {
        for frame in [0u32, 1, 10, 24, 25, 100] {
            assert_eq!(record_pacing_delay(Timing::Off, frame), 0, "frame {frame}");
        }
    }

    #[test]
    fn pacing_stops_after_the_beta_frame_cap() {
        // The beta paces at most `kMaxPacedFrames` frames; after that the delay
        // is zero whatever the mode.
        for timing in [Timing::Gentle, Timing::Balanced] {
            assert!(record_pacing_delay(timing, 24) > 0, "{timing:?} at 24");
            assert_eq!(record_pacing_delay(timing, 25), 0, "{timing:?} at 25");
            assert_eq!(record_pacing_delay(timing, 1000), 0, "{timing:?} at 1000");
        }
    }

    #[test]
    fn gentle_and_balanced_stay_inside_their_beta_ranges() {
        // Gentle is `8 + RandomIndex(14) + RandomIndex(25)`, so `[8, 45]`;
        // Balanced is `20 + RandomIndex(28) + RandomIndex(54)`, so `[20, 100]`.
        let mut gentle_seen = std::collections::BTreeSet::new();
        let mut balanced_seen = std::collections::BTreeSet::new();
        for frame in 1..=MAX_PACED_FRAMES {
            for _ in 0..64 {
                let g = record_pacing_delay(Timing::Gentle, frame);
                let b = record_pacing_delay(Timing::Balanced, frame);
                assert!((8..=45).contains(&g), "gentle {g}");
                assert!((20..=100).contains(&b), "balanced {b}");
                gentle_seen.insert(g);
                balanced_seen.insert(b);
            }
        }
        // The jitter is real: more than one value is produced in each mode.
        assert!(gentle_seen.len() > 1, "gentle jitter is constant");
        assert!(balanced_seen.len() > 1, "balanced jitter is constant");
    }
}
