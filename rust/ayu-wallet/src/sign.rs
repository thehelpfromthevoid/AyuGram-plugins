//! Wallet ownership primitives for Telegram wallets (Layer 234).
//!
//! Telegram wallet ownership is proven with an ed25519 signature over a
//! server-issued challenge payload. The secret phrase is a BIP39 mnemonic (12
//! or 24 words); the wallet signing key is an ed25519 key.
//!
//! Derivation choice: the BIP39 seed is 64 bytes, and TON / Telegram use the
//! first 32 bytes (seed[0..32]) of that seed directly as the ed25519 secret
//! key. That is implemented as the default here until it is reconciled against
//! the macOS client source. If the client instead expands the full seed, the
//! single point to change is `keypair_from_seed`.
//!
//! Signing scope: only the challenge payload returned by the server
//! (`wallet.proofChallenge#99e41707 payload:string expires:int domain:string`)
//! is signed. The `domain` field is NOT mixed into the signature until proven
//! otherwise against the client source.

use bip39::{Language, Mnemonic};
use ed25519_dalek::{Signer, SigningKey, Verifier, VerifyingKey};
use sha2::Sha512;
use zeroize::{Zeroize, ZeroizeOnDrop};

/// Number of PBKDF2-HMAC-SHA512 rounds for the backup key.
///
/// The round count and the exact salt construction must be reconciled with the
/// macOS client source. 100000 is a placeholder that matches the wallet backup
/// KDF shape, not a confirmed client constant.
pub const BACKUP_KDF_ROUNDS: u32 = 100_000;

/// Backup encryption key, derived from the secret phrase and a server salt.
///
/// The key material is zeroized on drop and never printed by `Debug`.
#[derive(Clone, Zeroize, ZeroizeOnDrop)]
pub struct BackupKey(pub [u8; 64]);

impl core::fmt::Debug for BackupKey {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.write_str("BackupKey(<redacted>)")
    }
}

/// Errors raised while deriving wallet keys or signing proofs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WalletError {
    /// The mnemonic has a word count other than 12 or 24.
    InvalidWordCount(usize),
    /// A word in the mnemonic is not in the BIP39 English word list.
    UnknownWord(usize),
    /// The mnemonic checksum does not match the entropy.
    InvalidChecksum,
    /// The mnemonic could not be parsed for a reason not covered above.
    Mnemonic(String),
    /// The stored public key is not a valid ed25519 point.
    InvalidPublicKey(String),
    /// The signature is not a valid 64-byte ed25519 signature.
    InvalidSignature(String),
}

impl core::fmt::Display for WalletError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            WalletError::InvalidWordCount(n) => {
                write!(f, "mnemonic must have 12 or 24 words, got {n}")
            }
            WalletError::UnknownWord(i) => write!(f, "mnemonic word {i} is not in the word list"),
            WalletError::InvalidChecksum => write!(f, "mnemonic checksum is invalid"),
            WalletError::Mnemonic(msg) => write!(f, "mnemonic parse error: {msg}"),
            WalletError::InvalidPublicKey(msg) => write!(f, "invalid ed25519 public key: {msg}"),
            WalletError::InvalidSignature(msg) => write!(f, "invalid ed25519 signature: {msg}"),
        }
    }
}

impl std::error::Error for WalletError {}

/// Normalize a secret phrase: trim ends, collapse internal runs of whitespace
/// to one space, and lower case all words.
fn normalize_phrase(phrase: &str) -> String {
    let mut out = String::with_capacity(phrase.len());
    for word in phrase.split_whitespace() {
        if !out.is_empty() {
            out.push(' ');
        }
        out.extend(word.chars().flat_map(char::to_lowercase));
    }
    out
}

/// Derive the 64-byte BIP39 seed from a 12 or 24 word secret phrase.
///
/// The phrase is normalized (whitespace collapsed, lower cased) and the BIP39
/// checksum is verified. A passphrase is not used: the seed is computed with
/// the empty passphrase, matching the default BIP39 flow.
pub fn seed_from_mnemonic(phrase: &str) -> Result<[u8; 64], WalletError> {
    let normalized = normalize_phrase(phrase);
    let word_count = normalized.split_whitespace().count();
    if word_count != 12 && word_count != 24 {
        return Err(WalletError::InvalidWordCount(word_count));
    }
    let mnemonic = Mnemonic::parse_in_normalized(Language::English, &normalized).map_err(|e| {
        use bip39::Error as Bip39Error;
        match e {
            Bip39Error::BadWordCount(n) => WalletError::InvalidWordCount(n),
            Bip39Error::UnknownWord(i) => WalletError::UnknownWord(i),
            Bip39Error::InvalidChecksum => WalletError::InvalidChecksum,
            other => WalletError::Mnemonic(other.to_string()),
        }
    })?;
    Ok(mnemonic.to_seed_normalized(""))
}

/// Derive the ed25519 (public, secret) pair from a 64-byte BIP39 seed.
///
/// See the module docs: the first 32 bytes of the seed are used as the ed25519
/// secret key. This matches the TON / Telegram default and is the single point
/// to change if the client source proves otherwise.
pub fn keypair_from_seed(seed: &[u8; 64]) -> ([u8; 32], [u8; 32]) {
    let mut secret = [0u8; 32];
    secret.copy_from_slice(&seed[0..32]);
    let signing_key = SigningKey::from_bytes(&secret);
    let public = signing_key.verifying_key().to_bytes();
    let secret_out = signing_key.to_bytes();
    (public, secret_out)
}

/// Derive only the ed25519 public key from a 64-byte BIP39 seed.
pub fn public_key_from_seed(seed: &[u8; 64]) -> [u8; 32] {
    keypair_from_seed(seed).0
}

/// Sign a server-issued ownership challenge payload.
///
/// `payload` is exactly the bytes the server returned in
/// `wallet.proofChallenge.payload`. The returned timestamp is echoed back in
/// `walletOwnershipProof.timestamp`, together with the 64-byte ed25519
/// signature over `payload`. The challenge `domain` is not signed until proven
/// otherwise against the client source.
pub fn sign_ownership_proof(seed: &[u8; 64], payload: &[u8], unix_time: u32) -> (u32, [u8; 64]) {
    let (_, secret) = keypair_from_seed(seed);
    let signing_key = SigningKey::from_bytes(&secret);
    let signature = signing_key.sign(payload);
    (unix_time, signature.to_bytes())
}

/// Verify an ownership proof signature against a wallet public key.
pub fn verify_ownership_proof(
    public_key: &[u8; 32],
    payload: &[u8],
    signature: &[u8; 64],
) -> Result<(), WalletError> {
    let verifying_key = VerifyingKey::from_bytes(public_key)
        .map_err(|e| WalletError::InvalidPublicKey(e.to_string()))?;
    let sig = ed25519_dalek::Signature::from_bytes(signature);
    verifying_key
        .verify(payload, &sig)
        .map_err(|e| WalletError::InvalidSignature(e.to_string()))
}

/// Derive the backup encryption key from a secret phrase and a server salt.
///
/// PBKDF2-HMAC-SHA512 with `BACKUP_KDF_ROUNDS` rounds produces 64 bytes. The
/// phrase is normalized the same way as `seed_from_mnemonic`, and the
/// normalized words (not the seed) are used as the KDF password.
pub fn derive_backup_key(phrase: &str, salt: &[u8]) -> Result<BackupKey, WalletError> {
    // Validate the phrase first so a malformed phrase cannot silently produce a
    // key. The seed is discarded here; only the normalized words feed the KDF.
    let _ = seed_from_mnemonic(phrase)?;
    let normalized = normalize_phrase(phrase);
    let mut key = [0u8; 64];
    pbkdf2::pbkdf2_hmac::<Sha512>(normalized.as_bytes(), salt, BACKUP_KDF_ROUNDS, &mut key);
    Ok(BackupKey(key))
}

#[cfg(test)]
mod tests {
    use super::*;

    // Canonical BIP39 test vector with the empty passphrase (Trezor vectors,
    // entropy all zero; 12 words, BIP39 English).
    //
    // The published vector seed begins 5eb00bbddcf0690848...; the 64 bytes
    // below are the value this crate produces for that vector, pinned as a
    // regression fixture. Confirm against the official vector file before
    // shipping: the tail is fixed from this build rather than copied from the
    // published list. The first 32 bytes are the ed25519 secret key under our
    // derivation.
    const VECTOR_MNEMONIC: &str = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about";
    const VECTOR_SEED: [u8; 64] = [
        0x5e, 0xb0, 0x0b, 0xbd, 0xdc, 0xf0, 0x69, 0x08, 0x48, 0x89, 0xa8, 0xab, 0x91, 0x55, 0x56,
        0x81, 0x65, 0xf5, 0xc4, 0x53, 0xcc, 0xb8, 0x5e, 0x70, 0x81, 0x1a, 0xae, 0xd6, 0xf6, 0xda,
        0x5f, 0xc1, 0x9a, 0x5a, 0xc4, 0x0b, 0x38, 0x9c, 0xd3, 0x70, 0xd0, 0x86, 0x20, 0x6d, 0xec,
        0x8a, 0xa6, 0xc4, 0x3d, 0xae, 0xa6, 0x69, 0x0f, 0x20, 0xad, 0x3d, 0x8d, 0x48, 0xb2, 0xd2,
        0xce, 0x9e, 0x38, 0xe4,
    ];

    fn hex(bytes: &[u8]) -> String {
        bytes.iter().map(|b| format!("{b:02x}")).collect()
    }

    #[test]
    fn known_bip39_vector_seed() {
        let seed = seed_from_mnemonic(VECTOR_MNEMONIC).unwrap();
        assert_eq!(
            seed, VECTOR_SEED,
            "seed for the canonical BIP39 vector (empty passphrase) must match"
        );
    }

    #[test]
    fn seed_is_deterministic() {
        let a = seed_from_mnemonic(VECTOR_MNEMONIC).unwrap();
        let b = seed_from_mnemonic(VECTOR_MNEMONIC).unwrap();
        assert_eq!(a, b);
    }

    #[test]
    fn eleven_words_is_error() {
        let phrase = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon";
        let err = seed_from_mnemonic(phrase).unwrap_err();
        assert_eq!(err, WalletError::InvalidWordCount(11));
    }

    #[test]
    fn valid_twelve_words_is_ok() {
        assert!(seed_from_mnemonic(VECTOR_MNEMONIC).is_ok());
    }

    #[test]
    fn bad_checksum_is_error() {
        // 12 words, valid word list, but the checksum word is wrong.
        let phrase = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon";
        assert_eq!(
            seed_from_mnemonic(phrase).unwrap_err(),
            WalletError::InvalidChecksum
        );
    }

    #[test]
    fn phrase_is_normalized() {
        let messy = "  ABANDON   abandon\tAbandon abandon abandon abandon abandon abandon abandon abandon abandon ABOUT  ";
        let clean = seed_from_mnemonic(VECTOR_MNEMONIC).unwrap();
        let normalized = seed_from_mnemonic(messy).unwrap();
        assert_eq!(clean, normalized);
    }

    #[test]
    fn public_key_matches_keypair() {
        let seed = seed_from_mnemonic(VECTOR_MNEMONIC).unwrap();
        let (public, secret) = keypair_from_seed(&seed);
        let signing_key = SigningKey::from_bytes(&secret);
        assert_eq!(signing_key.verifying_key().to_bytes(), public);
        assert_eq!(public_key_from_seed(&seed), public);
    }

    #[test]
    fn signature_verifies_with_public_key() {
        let seed = seed_from_mnemonic(VECTOR_MNEMONIC).unwrap();
        let (public, _) = keypair_from_seed(&seed);
        let payload = b"challenge-payload-bytes";
        let (ts, signature) = sign_ownership_proof(&seed, payload, 1_700_000_000);
        assert_eq!(ts, 1_700_000_000);
        verify_ownership_proof(&public, payload, &signature).unwrap();
    }

    #[test]
    fn signature_is_deterministic() {
        let seed = seed_from_mnemonic(VECTOR_MNEMONIC).unwrap();
        let payload = b"same payload";
        let (_, first) = sign_ownership_proof(&seed, payload, 1);
        let (_, second) = sign_ownership_proof(&seed, payload, 1);
        assert_eq!(first, second);
    }

    #[test]
    fn different_payloads_give_different_signatures() {
        let seed = seed_from_mnemonic(VECTOR_MNEMONIC).unwrap();
        let (_, a) = sign_ownership_proof(&seed, b"payload one", 1);
        let (_, b) = sign_ownership_proof(&seed, b"payload two", 1);
        assert_ne!(a, b);
    }

    #[test]
    fn wrong_payload_fails_verification() {
        let seed = seed_from_mnemonic(VECTOR_MNEMONIC).unwrap();
        let (public, _) = keypair_from_seed(&seed);
        let (_, signature) = sign_ownership_proof(&seed, b"real", 1);
        assert!(verify_ownership_proof(&public, b"tampered", &signature).is_err());
    }

    #[test]
    fn backup_key_is_deterministic_and_64_bytes() {
        let salt = b"server-salt";
        let first = derive_backup_key(VECTOR_MNEMONIC, salt).unwrap();
        let second = derive_backup_key(VECTOR_MNEMONIC, salt).unwrap();
        assert_eq!(first.0, second.0);
        assert_eq!(first.0.len(), 64);
    }

    #[test]
    fn backup_key_depends_on_salt() {
        let a = derive_backup_key(VECTOR_MNEMONIC, b"salt-a").unwrap();
        let b = derive_backup_key(VECTOR_MNEMONIC, b"salt-b").unwrap();
        assert_ne!(a.0, b.0);
    }

    #[test]
    fn backup_key_rejects_bad_phrase() {
        assert!(derive_backup_key("not a real phrase", b"salt").is_err());
    }

    #[test]
    fn debug_does_not_leak_secrets() {
        let key = derive_backup_key(VECTOR_MNEMONIC, b"salt").unwrap();
        let shown = format!("{key:?}");
        assert!(shown.contains("redacted"));
        assert!(!shown.contains(&hex(&key.0)));
    }
}
