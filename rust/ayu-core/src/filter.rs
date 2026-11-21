//! Literal replacement used by the message filters.
//!
//! The Python plugin layer does `text.replace(k, v)` once per pair, and that
//! loop is the contract: replacements apply in order and later passes see what
//! earlier ones produced. This module reproduces that loop exactly, but in
//! Rust, so a plugin can hand the work over without its output changing.
//!
//! Why not a single pass. An Aho-Corasick leftmost-longest rewrite is O(len)
//! and looks like the obvious implementation, but it is *not* equivalent to the
//! sequential loop, and the difference is not detectable by inspecting the pair
//! set. Two ways it diverges:
//!
//! * a replacement joins with the text around it and forms another needle, so
//!   `("ab" -> "c"), ("cd" -> "X")` turns `"abd"` into `"cd"` in one pass but
//!   into `"X"` in the loop;
//! * a deletion joins two neighbours, so `("a" -> ""), ("bb" -> "")` turns
//!   `"bab"` into `"bb"` in one pass but into `""` in the loop.
//!
//! Those sets contain no needle inside a needle and no needle inside a
//! replacement, which is every cheap "is it safe" test there is. The property
//! was searched exhaustively over small pair sets before this was written: no
//! such test exists, so the loop itself is implemented instead of guessed at.
//!
//! `is_match` is a different question - "does any needle occur" - and that one
//! the automaton answers exactly, so it is kept.

use aho_corasick::{AhoCorasick, AhoCorasickBuilder, BuildError};

/// A compiled set of literal `(needle, replacement)` pairs.
#[derive(Debug)]
pub struct WordFilter {
    automaton: AhoCorasick,
    /// Ordered, de-duplicated, non-empty needles with their replacements.
    pairs: Vec<(String, String)>,
}

impl WordFilter {
    /// Compiles the pairs. Empty needles are skipped and duplicate needles keep
    /// their first replacement, exactly like the Python dict the plugins build.
    pub fn new<S: AsRef<str>>(pairs: &[(S, S)]) -> Result<Self, BuildError> {
        let mut seen = std::collections::HashSet::new();
        let mut needles: Vec<&str> = Vec::with_capacity(pairs.len());
        let mut kept: Vec<(String, String)> = Vec::with_capacity(pairs.len());
        for (needle, replacement) in pairs {
            let needle = needle.as_ref();
            if needle.is_empty() || !seen.insert(needle) {
                continue;
            }
            needles.push(needle);
            kept.push((needle.to_owned(), replacement.as_ref().to_owned()));
        }
        let automaton = AhoCorasickBuilder::new()
            .match_kind(aho_corasick::MatchKind::LeftmostLongest)
            .build(needles)?;
        Ok(Self {
            automaton,
            pairs: kept,
        })
    }

    pub fn is_empty(&self) -> bool {
        self.pairs.is_empty()
    }

    pub fn len(&self) -> usize {
        self.pairs.len()
    }

    /// True when at least one needle occurs in `haystack`.
    pub fn is_match(&self, haystack: &str) -> bool {
        !self.is_empty() && self.automaton.is_match(haystack)
    }

    /// Rewrites `haystack` into `out`, reusing its allocation. Returns whether
    /// the result differs from the input. `out` is always a complete copy of the
    /// input when nothing matches.
    pub fn replace_into(&self, haystack: &str, out: &mut String) -> bool {
        out.clear();
        out.push_str(haystack);
        if self.pairs.is_empty() {
            return false;
        }
        // One pass per pair, in order, into a scratch buffer - the same
        // sequence of `str.replace` calls the plugin makes.
        let mut scratch = String::with_capacity(haystack.len());
        for (needle, replacement) in &self.pairs {
            if !out.contains(needle.as_str()) {
                continue;
            }
            replace_all_into(out.as_str(), needle, replacement, &mut scratch);
            std::mem::swap(out, &mut scratch);
        }
        out.as_str() != haystack
    }

    /// Convenience wrapper that allocates a new string.
    pub fn replace(&self, haystack: &str) -> (String, bool) {
        let mut out = String::with_capacity(haystack.len());
        let changed = self.replace_into(haystack, &mut out);
        (out, changed)
    }
}

/// Replaces every non-overlapping occurrence of `needle`, left to right, into
/// `out`. This is the behaviour of `str.replace` in Python and of
/// `String::replace` in Rust, written out so the exact scan order is visible.
fn replace_all_into(text: &str, needle: &str, replacement: &str, out: &mut String) {
    out.clear();
    let mut rest = text;
    loop {
        match rest.find(needle) {
            Some(position) => {
                out.push_str(&rest[..position]);
                out.push_str(replacement);
                rest = &rest[position + needle.len()..];
            }
            None => {
                out.push_str(rest);
                return;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn filter(pairs: &[(&str, &str)]) -> WordFilter {
        WordFilter::new(pairs).expect("automaton")
    }

    /// The contract the plugins rely on: `str.replace` in order.
    fn sequential(text: &str, pairs: &[(&str, &str)]) -> String {
        let mut out = text.to_owned();
        for (needle, replacement) in pairs {
            if !needle.is_empty() {
                out = out.replace(needle, replacement);
            }
        }
        out
    }

    #[test]
    fn replaces_literals() {
        let pairs = [("brb", "be right back"), ("imo", "in my opinion")];
        let f = filter(&pairs);
        let (out, changed) = f.replace("brb, imo");
        assert_eq!(out, sequential("brb, imo", &pairs));
        assert_eq!(out, "be right back, in my opinion");
        assert!(changed);
    }

    #[test]
    fn leaves_text_untouched_when_nothing_matches() {
        let f = filter(&[("brb", "be right back")]);
        let (out, changed) = f.replace("hello there");
        assert_eq!(out, "hello there");
        assert!(!changed);
    }

    #[test]
    fn cascades_like_a_replace_loop() {
        let pairs = [("a", "b"), ("b", "c")];
        let f = filter(&pairs);
        let (out, _) = f.replace("a b");
        assert_eq!(out, sequential("a b", &pairs));
        assert_eq!(out, "c c");
    }

    #[test]
    fn replacement_joining_the_next_character_is_not_missed() {
        // The single pass used to answer "cd" here; the loop answers "X".
        let pairs = [("ab", "c"), ("cd", "X")];
        let f = filter(&pairs);
        let (out, _) = f.replace("abd");
        assert_eq!(out, sequential("abd", &pairs));
        assert_eq!(out, "X");
    }

    #[test]
    fn deletion_joining_two_neighbours_is_not_missed() {
        // Removing "a" from "bab" forms "bb", which the next pass replaces.
        let pairs = [("a", ""), ("bb", "b")];
        let f = filter(&pairs);
        let (out, _) = f.replace("bab");
        assert_eq!(out, sequential("bab", &pairs));
        assert_eq!(out, "b");
    }

    #[test]
    fn longest_needle_does_not_win_when_the_loop_says_otherwise() {
        // One pass, leftmost-longest, would pick "aa" first and answer "21".
        // The loop replaces "a" first, so "aa" is gone before it is reached.
        let pairs = [("a", "1"), ("aa", "2")];
        let f = filter(&pairs);
        let (out, _) = f.replace("aaa");
        assert_eq!(out, sequential("aaa", &pairs));
        assert_eq!(out, "111");
    }

    #[test]
    fn skips_empty_and_duplicate_needles() {
        let f = filter(&[("", "x"), ("brb", "one"), ("brb", "two")]);
        assert_eq!(f.len(), 1);
        let (out, _) = f.replace("brb");
        assert_eq!(out, "one");
    }

    #[test]
    fn handles_unicode_and_byte_boundaries() {
        let pairs = [("привет", "здравствуйте"), ("🙂", ":smile:")];
        let f = filter(&pairs);
        let (out, changed) = f.replace("привет 🙂 мир");
        assert_eq!(out, sequential("привет 🙂 мир", &pairs));
        assert_eq!(out, "здравствуйте :smile: мир");
        assert!(changed);
    }

    #[test]
    fn is_match_sees_needles() {
        let f = filter(&[("spam", "ham")]);
        assert!(f.is_match("buy spam now"));
        assert!(!f.is_match("buy ham now"));
    }

    #[test]
    fn replace_into_reuses_the_buffer() {
        let f = filter(&[("x", "y")]);
        let mut out = String::from("stale contents");
        assert!(f.replace_into("x", &mut out));
        assert_eq!(out, "y");
        assert!(!f.replace_into("z", &mut out));
        assert_eq!(out, "z");
    }

    #[test]
    fn unchanged_when_the_replacement_equals_the_needle() {
        let f = filter(&[("same", "same")]);
        let (out, changed) = f.replace("same");
        assert_eq!(out, "same");
        assert!(!changed, "a no-op replacement must not report a change");
    }

    #[test]
    fn matches_a_sequential_loop_over_small_inputs() {
        let needles = ["a", "b", "ab", "ba", "aa", "bb"];
        let replacements = ["", "a", "b", "ab", "ba"];
        let texts = [
            "", "a", "b", "ab", "ba", "bab", "aab", "bba", "abab", "aaab",
        ];
        let mut checked = 0u32;
        for r1 in replacements {
            for r2 in replacements {
                for n1 in needles {
                    for n2 in needles {
                        if n1 == n2 {
                            continue;
                        }
                        let pairs = [(n1, r1), (n2, r2)];
                        let f = filter(&pairs);
                        for text in texts {
                            let (out, changed) = f.replace(text);
                            let expected = sequential(text, &pairs);
                            assert_eq!(
                                out, expected,
                                "pairs={pairs:?} text={text:?} native={out:?} loop={expected:?}"
                            );
                            assert_eq!(changed, out != text, "pairs={pairs:?} text={text:?}");
                            checked += 1;
                        }
                    }
                }
            }
        }
        assert!(checked > 1000, "the sweep must actually run: {checked}");
    }
}
