//! Cheap content fingerprints.
//!
//! The Python loader decides whether a plugin changed by comparing
//! `(st_mtime_ns, st_size)`. That misses edits made within the same timestamp
//! granularity on some filesystems and cannot be compared across copies. FNV-1a
//! over the file bytes is not cryptographic, it only has to notice that bytes
//! differ, and it runs at memory speed.

use std::fs::File;
use std::io::{self, Read};
use std::path::Path;

const FNV_OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
const FNV_PRIME: u64 = 0x0000_0100_0000_01b3;

pub fn fnv1a64(bytes: &[u8]) -> u64 {
    let mut hash = FNV_OFFSET;
    for &byte in bytes {
        hash ^= u64::from(byte);
        hash = hash.wrapping_mul(FNV_PRIME);
    }
    hash
}

/// Hashes a file without loading it whole. Returns `(hash, size)`.
pub fn file_fingerprint(path: impl AsRef<Path>) -> io::Result<(u64, u64)> {
    let mut file = File::open(path)?;
    let mut buffer = [0u8; 64 * 1024];
    let mut hash = FNV_OFFSET;
    let mut size = 0u64;
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        size += read as u64;
        for &byte in &buffer[..read] {
            hash ^= u64::from(byte);
            hash = hash.wrapping_mul(FNV_PRIME);
        }
    }
    Ok((hash, size))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fnv_known_vectors() {
        assert_eq!(fnv1a64(b""), 0xcbf2_9ce4_8422_2325);
        assert_eq!(fnv1a64(b"a"), 0xaf63_dc4c_8601_ec8c);
        assert_eq!(fnv1a64(b"foobar"), 0x85944171f73967e8);
    }

    #[test]
    fn file_hash_changes_with_content() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("plugin.json");
        fs_write(&path, br#"{"id":"a"}"#);
        let (first, size) = file_fingerprint(&path).expect("hash");
        assert_eq!(size, 10);

        fs_write(&path, br#"{"id":"b"}"#);
        let (second, _) = file_fingerprint(&path).expect("hash");
        assert_ne!(first, second);
    }

    #[test]
    fn missing_file_is_an_error() {
        let dir = tempfile::tempdir().expect("tempdir");
        assert!(file_fingerprint(dir.path().join("nope")).is_err());
    }

    fn fs_write(path: &Path, bytes: &[u8]) {
        std::fs::write(path, bytes).expect("write");
    }
}
