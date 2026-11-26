//! Compile probe: exercises the exact upstream API surface the real modules use.
//! Replaced by the real module list once the surface is confirmed.

pub mod sign;
pub mod ton;

use bip39::{Language, Mnemonic};
use ed25519_dalek::{Signer, SigningKey, Verifier};
use sha2::{Digest, Sha512};

pub fn hmac_sha512(key: &[u8], data: &[u8]) -> [u8; 64] {
    use hmac::{Hmac, KeyInit, Mac};
    let mut mac = Hmac::<Sha512>::new_from_slice(key).expect("hmac key");
    mac.update(data);
    let out = mac.finalize().into_bytes();
    let mut res = [0u8; 64];
    res.copy_from_slice(out.as_ref());
    res
}

pub fn pbkdf2_sha512(password: &[u8], salt: &[u8], rounds: u32) -> [u8; 64] {
    let mut res = [0u8; 64];
    pbkdf2::pbkdf2_hmac::<Sha512>(password, salt, rounds, &mut res);
    res
}

pub fn sha512(data: &[u8]) -> [u8; 64] {
    let mut hasher = Sha512::new();
    hasher.update(data);
    let out = hasher.finalize();
    let mut res = [0u8; 64];
    res.copy_from_slice(out.as_ref());
    res
}

pub fn keypair(seed: &[u8; 32]) -> ([u8; 32], [u8; 64]) {
    let sk = SigningKey::from_bytes(seed);
    let vk = sk.verifying_key();
    (vk.to_bytes(), sk.to_keypair_bytes())
}

pub fn sign_verify(seed: &[u8; 32], msg: &[u8]) -> bool {
    let sk = SigningKey::from_bytes(seed);
    let sig = sk.sign(msg);
    sk.verifying_key().verify(msg, &sig).is_ok()
}

pub fn b64_roundtrip(bytes: &[u8]) -> Option<Vec<u8>> {
    use base64::Engine;
    let e = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes);
    base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(e)
        .ok()
}

pub fn words_of(phrase: &str) -> Result<Vec<String>, String> {
    let m = Mnemonic::parse_in_normalized_without_checksum_check(Language::English, phrase)
        .map_err(|e| e.to_string())?;
    Ok(m.words().map(str::to_string).collect())
}

pub fn bip39_seed(phrase: &str, pass: &str) -> Result<[u8; 64], String> {
    let m = Mnemonic::parse_in_normalized_without_checksum_check(Language::English, phrase)
        .map_err(|e| e.to_string())?;
    Ok(m.to_seed_normalized(pass))
}

pub fn english_word_count() -> usize {
    Language::English.word_list().len()
}

pub fn json_probe(s: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(s).ok()?;
    Some(v.get("type")?.as_str()?.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sha512_abc_known_vector() {
        let got = sha512(b"abc");
        let hex: String = got.iter().map(|b| format!("{b:02x}")).collect();
        assert_eq!(
            hex,
            "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a\
             2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
            "sha512(\"abc\") must match the published vector"
        );
    }

    #[test]
    fn bip39_english_list_is_2048() {
        assert_eq!(english_word_count(), 2048);
    }
}
