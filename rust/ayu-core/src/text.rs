//! Command-prefix parsing behind `hook_utils.command`.
//!
//! `@command("/ping")` inspects every outgoing message, so the split below runs
//! on the send path. The Python original is
//!
//! ```text
//! raw = text.strip()
//! if not raw: return
//! first = raw.split(None, 1)
//! if not first or first[0].lower() not in prefixes: return
//! args = raw[len(first[0]):].strip().split() if len(first) > 1 else []
//! ```
//!
//! Rust and Python agree on all of that only for ASCII text: `str.lower`,
//! `str.strip` and the default `str.split()` each have corners (`\x1c`-`\x1f`
//! are whitespace to Python and not to Rust; some scripts case-fold
//! differently). Rather than chase every corner, the parser refuses any input
//! that is not plain ASCII with ordinary whitespace, and the caller falls back
//! to the Python body. A refused message keeps today's behaviour exactly.

pub use crate::coerce::Coerced;

/// Whitespace both languages treat as a separator.
fn is_shared_space(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\n' | '\u{b}' | '\u{c}' | '\r')
}

/// True when `text` is ASCII and free of the ASCII controls Python counts as
/// whitespace but Rust does not (`\x1c`-`\x1f`).
fn is_plain(text: &str) -> bool {
    text.chars().all(|c| {
        if c.is_ascii() {
            // Reject the file/group/record/unit separators and NUL.
            !(c < ' ' && !is_shared_space(c))
        } else {
            false
        }
    })
}

/// A parsed command: the matched prefix, the raw line, and its arguments.
#[derive(Debug, Clone, PartialEq)]
pub struct Command {
    pub prefix: String,
    pub raw: String,
    pub args: Vec<String>,
}

/// Parses `text` against `prefixes` (already lower-cased and stripped).
///
/// Returns `Value(None)` when nothing matched, `Value(Some(command))` on a
/// match, and `Decline` when the input is not one the two languages agree on.
pub fn command_parse(text: &str, prefixes: &[String]) -> Coerced<Option<Command>> {
    if !is_plain(text) || prefixes.iter().any(|p| !is_plain(p)) {
        return Coerced::Decline;
    }
    let raw = text.trim_matches(is_shared_space);
    if raw.is_empty() {
        return Coerced::Value(None);
    }
    // `raw.split(None, 1)`: the first run of whitespace separates the tokens.
    let mut tokens = raw.splitn(2, is_shared_space);
    let first = tokens.next().unwrap_or("");
    let has_rest = tokens.next().is_some();
    let lowered = first.to_ascii_lowercase();
    if !prefixes.iter().any(|p| p == &lowered) {
        return Coerced::Value(None);
    }
    let args = if has_rest {
        // `raw[len(first[0]):].strip().split()`: keep the byte after the token.
        let rest = &raw[first.len()..];
        rest.split(is_shared_space)
            .filter(|part| !part.is_empty())
            .map(|part| part.to_owned())
            .collect()
    } else {
        Vec::new()
    };
    Coerced::Value(Some(Command {
        prefix: lowered,
        raw: raw.to_owned(),
        args,
    }))
}

#[cfg(test)]
mod tests {
    use super::Coerced::{Decline, Value};
    use super::*;

    fn prefixes(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| (*s).to_owned()).collect()
    }

    #[test]
    fn simple_command_matches() {
        let parsed = command_parse("/ping", &prefixes(&["/ping"]));
        match parsed {
            Value(Some(cmd)) => {
                assert_eq!(cmd.prefix, "/ping");
                assert_eq!(cmd.raw, "/ping");
                assert!(cmd.args.is_empty());
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn arguments_are_split_on_whitespace() {
        let parsed = command_parse("  /echo   one   two ", &prefixes(&["/echo"]));
        match parsed {
            Value(Some(cmd)) => {
                assert_eq!(cmd.prefix, "/echo");
                assert_eq!(cmd.raw, "/echo   one   two");
                assert_eq!(cmd.args, vec!["one", "two"]);
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn case_is_folded_for_matching_only() {
        let parsed = command_parse("/PING x", &prefixes(&["/ping"]));
        match parsed {
            Value(Some(cmd)) => {
                assert_eq!(cmd.prefix, "/ping");
                // The raw line keeps its original case, as the handler gets it.
                assert_eq!(cmd.raw, "/PING x");
                assert_eq!(cmd.args, vec!["x"]);
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn non_matching_and_empty_are_value_none() {
        assert_eq!(command_parse("hello", &prefixes(&["/ping"])), Value(None));
        assert_eq!(command_parse("   ", &prefixes(&["/ping"])), Value(None));
        assert_eq!(command_parse("", &prefixes(&["/ping"])), Value(None));
        // A prefix that is a substring is not a match: only the first token.
        assert_eq!(command_parse("/pinger", &prefixes(&["/ping"])), Value(None));
    }

    #[test]
    fn declines_non_ascii_and_python_only_whitespace() {
        assert_eq!(command_parse("/пинг", &prefixes(&["/ping"])), Decline);
        assert_eq!(command_parse("/ping\x1c", &prefixes(&["/ping"])), Decline);
        assert_eq!(command_parse("/ping", &prefixes(&["/пинг"])), Decline);
    }

    #[test]
    fn extra_whitespace_kinds_are_shared() {
        let parsed = command_parse("/echo\ta\nb", &prefixes(&["/echo"]));
        match parsed {
            Value(Some(cmd)) => assert_eq!(cmd.args, vec!["a", "b"]),
            other => panic!("unexpected {other:?}"),
        }
    }
}
