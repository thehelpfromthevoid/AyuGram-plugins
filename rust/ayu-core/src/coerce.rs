//! Coercion helpers behind `settings_store.coerce` and `client_utils._reply_ids`.
//!
//! These are ports, not reimplementations, and the difference matters. Python's
//! `int("١٢")` is 12, `int("1_0")` is 10 and integers are unbounded; Rust's
//! `parse` accepts none of that. Reimplementing the whole cast would therefore
//! *change* what a plugin reads back for some inputs - which is exactly the
//! class of bug this layer exists to avoid.
//!
//! So every function here answers three ways: a value, "not mine", or an error.
//! It only claims an input when Python and Rust provably agree on it - plain
//! ASCII digits, ASCII-only booleans, a strict float grammar, comma lists - and
//! declines everything else. A declined input runs the Python original, so the
//! worst case is today's speed, never today's wrong answer.

/// Result of a coercion: a value, or "the caller should use its own path".
#[derive(Debug, Clone, PartialEq)]
pub enum Coerced<T> {
    Value(T),
    Decline,
}

/// True when every character is ASCII, which keeps Python's `str.strip`,
/// `str.lower` and `int` in the territory Rust reproduces exactly.
fn is_ascii(text: &str) -> bool {
    text.is_ascii()
}

/// Python `raw.strip().lower() in ("1", "true", "yes", "on", "да")`.
///
/// `"да"` is Cyrillic, so an input containing it is never ASCII and the native
/// side declines - the Python path keeps answering it, unchanged.
pub fn coerce_bool(raw: &str) -> Coerced<bool> {
    if !is_ascii(raw) {
        return Coerced::Decline;
    }
    let folded = raw.trim().to_ascii_lowercase();
    match folded.as_str() {
        "1" | "true" | "yes" | "on" => Coerced::Value(true),
        "0" | "false" | "no" | "off" | "" => {
            // An empty string is what Python turns into False as well; the
            // original only ever spells out the truthy set and treats the rest
            // as false, so anything ASCII that is not truthy is false.
            Coerced::Value(false)
        }
        _ => Coerced::Value(false),
    }
}

/// Python `int(raw.strip())` for the subset where the two languages agree.
///
/// Accepts an optional leading `-` followed by ASCII digits only. `+`, `_`,
/// internal whitespace, non-ASCII digits and values outside `i64` are declined.
pub fn coerce_int(raw: &str) -> Coerced<i64> {
    if !is_ascii(raw) {
        return Coerced::Decline;
    }
    let text = raw.trim();
    let digits = text.strip_prefix('-').unwrap_or(text);
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return Coerced::Decline;
    }
    match text.parse::<i64>() {
        Ok(value) => Coerced::Value(value),
        // Out of range for i64: Python would keep going, so it must not be
        // answered here.
        Err(_) => Coerced::Decline,
    }
}

/// Python `float(raw.strip())` for a strict grammar both languages round the
/// same way: `[-]digits[.digits][e[+-]digits]`.
///
/// `inf`, `nan`, `Infinity`, `1_0.5` and anything non-ASCII are declined; Python
/// keeps handling them exactly as before.
pub fn coerce_float(raw: &str) -> Coerced<f64> {
    if !is_ascii(raw) {
        return Coerced::Decline;
    }
    let text = raw.trim();
    if !is_strict_float(text) {
        return Coerced::Decline;
    }
    match text.parse::<f64>() {
        Ok(value) if value.is_finite() => Coerced::Value(value),
        _ => Coerced::Decline,
    }
}

fn is_strict_float(text: &str) -> bool {
    let bytes = text.as_bytes();
    let mut i = 0;
    if i < bytes.len() && (bytes[i] == b'-' || bytes[i] == b'+') {
        i += 1;
    }
    let int_start = i;
    while i < bytes.len() && bytes[i].is_ascii_digit() {
        i += 1;
    }
    if i == int_start {
        return false;
    }
    if i < bytes.len() && bytes[i] == b'.' {
        i += 1;
        let frac_start = i;
        while i < bytes.len() && bytes[i].is_ascii_digit() {
            i += 1;
        }
        if i == frac_start {
            return false;
        }
    }
    if i < bytes.len() && (bytes[i] == b'e' || bytes[i] == b'E') {
        i += 1;
        if i < bytes.len() && (bytes[i] == b'-' || bytes[i] == b'+') {
            i += 1;
        }
        let exp_start = i;
        while i < bytes.len() && bytes[i].is_ascii_digit() {
            i += 1;
        }
        if i == exp_start {
            return false;
        }
    }
    i == bytes.len()
}

/// Python `[p.strip() for p in raw.split(",") if p.strip()]`.
///
/// Declines when any part carries non-ASCII whitespace, because the two
/// languages do not quite agree on what that is.
pub fn coerce_list(raw: &str) -> Coerced<Vec<String>> {
    if !is_ascii(raw) {
        return Coerced::Decline;
    }
    let parts: Vec<String> = raw
        .split(',')
        .map(|part| part.trim().to_owned())
        .filter(|part| !part.is_empty())
        .collect();
    Coerced::Value(parts)
}

#[cfg(test)]
mod tests {
    use super::Coerced::{Decline, Value};
    use super::*;

    #[test]
    fn bool_truthy_spellings() {
        assert_eq!(coerce_bool("1"), Value(true));
        assert_eq!(coerce_bool(" true "), Value(true));
        assert_eq!(coerce_bool("YES"), Value(true));
        assert_eq!(coerce_bool("On"), Value(true));
        assert_eq!(coerce_bool("0"), Value(false));
        assert_eq!(coerce_bool("nope"), Value(false));
        assert_eq!(coerce_bool(""), Value(false));
    }

    #[test]
    fn bool_declines_non_ascii() {
        // "да" is truthy in Python; only the Python path may decide that.
        assert_eq!(coerce_bool("да"), Decline);
        assert_eq!(coerce_bool("TRUE "), Value(true));
    }

    #[test]
    fn int_plain_digits() {
        assert_eq!(coerce_int("42"), Value(42));
        assert_eq!(coerce_int(" -7 "), Value(-7));
        assert_eq!(coerce_int("007"), Value(7));
        assert_eq!(coerce_int("-0"), Value(0));
    }

    #[test]
    fn int_declines_python_only_forms() {
        assert_eq!(coerce_int("+5"), Decline);
        assert_eq!(coerce_int("1_0"), Decline);
        assert_eq!(coerce_int("١٢"), Decline);
        assert_eq!(coerce_int(""), Decline);
        assert_eq!(coerce_int("-"), Decline);
        assert_eq!(coerce_int("12.0"), Decline);
        // Beyond i64.
        assert_eq!(coerce_int("99999999999999999999999"), Decline);
    }

    #[test]
    fn float_strict_grammar() {
        assert_eq!(coerce_float("0.5"), Value(0.5));
        assert_eq!(coerce_float(" -2 "), Value(-2.0));
        assert_eq!(coerce_float("1e3"), Value(1000.0));
        assert_eq!(coerce_float("1.5E-2"), Value(0.015));
        assert_eq!(coerce_float(".5"), Decline);
        assert_eq!(coerce_float("5."), Decline);
        assert_eq!(coerce_float("inf"), Decline);
        assert_eq!(coerce_float("nan"), Decline);
        assert_eq!(coerce_float("Infinity"), Decline);
    }

    #[test]
    fn list_splits_and_trims() {
        assert_eq!(
            coerce_list("a, b ,c"),
            Value(vec!["a".to_owned(), "b".to_owned(), "c".to_owned()])
        );
        assert_eq!(coerce_list(" , ,"), Value(Vec::<String>::new()));
        assert_eq!(coerce_list("only"), Value(vec!["only".to_owned()]));
        assert_eq!(coerce_list("да, x"), Decline);
    }
}
