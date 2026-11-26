//! Gram (TON) transfer service messages, ported from the macOS 13.2 beta
//! (`Chat.Service[Item].GramTransfer*`).
//!
//! The beta splits one sentence into three: outgoing ("You sent ..."), incoming
//! with the sender named ("<name> sent you ...") and anonymous ("Someone sent
//! you ..."), plus a parenthesised fiat suffix. The desktop client being ported
//! into renders a single sentence (`lng_action_gram_transfer`) and loses the
//! direction, so the port adds the missing branches.
//!
//! Which branch applies is decided by the same two flags the client already
//! computes for gift messages in `history_item.cpp` (`_from->isSelf()` and
//! `_from->isServiceUser()`), so this module reuses them rather than inventing a
//! second source of truth.
//!
//! The strings are the beta's own, kept verbatim; placeholder substitution stays
//! the caller's job, exactly as with the `--stealth` argument list.

/// Which of the beta's sentences describes a transfer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Variant {
    /// The local user sent the Grams: `You sent %1$@ %2$@`.
    Outgoing,
    /// A known peer sent them: `%1$@ sent you %2$@`.
    Incoming,
    /// The sender is hidden: `Someone sent you %1$@`.
    Anonymous,
}

/// One variant's text in both layouts the beta ships: `Chat.Service` (the
/// sentence carries the sender itself) and `Chat.ServiceItem` (the sentence is
/// an item under a name shown separately). The two are identical for Gram
/// transfers in this build, but the beta keeps both keys, so both are recorded.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Sentences {
    pub variant: Variant,
    pub service: &'static str,
    pub service_item: &'static str,
    /// How many `%N$@` substitutions the text expects. Kept explicit so a
    /// mistyped string fails a test instead of silently dropping an argument.
    pub placeholders: usize,
}

/// Every variant, in the order the beta lists them.
pub const TABLE: [Sentences; 3] = [
    Sentences {
        variant: Variant::Incoming,
        service: "%1$@ sent you %2$@",
        service_item: "%1$@ sent you %2$@",
        placeholders: 2,
    },
    Sentences {
        variant: Variant::Outgoing,
        service: "You sent %1$@ %2$@",
        service_item: "You sent %1$@ %2$@",
        placeholders: 2,
    },
    Sentences {
        variant: Variant::Anonymous,
        service: "Someone sent you %1$@",
        service_item: "Someone sent you %1$@",
        placeholders: 1,
    },
];

/// `Chat.Service.GramTransferIncoming`: the incoming wording used when the
/// sender's name is already shown elsewhere, so the sentence continues from it.
pub const SERVICE_INCOMING_BARE: &str = "sent you %1$@";

/// `Chat.ServiceItem.GramTransferFiat`: the fiat value in parentheses, appended
/// right after the amount. The leading space is part of the beta's string.
pub const FIAT_SUFFIX: &str = " (%1$@)";

/// Picks the sentence for a transfer. The local user's own message always reads
/// as outgoing, a hidden sender as anonymous, anything else names the sender.
pub fn variant(is_self: bool, is_service_user: bool) -> Variant {
    if is_self {
        Variant::Outgoing
    } else if is_service_user {
        Variant::Anonymous
    } else {
        Variant::Incoming
    }
}

/// The record for one variant, so callers can look up the text without matching.
pub fn sentences(variant: Variant) -> &'static Sentences {
    TABLE
        .iter()
        .find(|entry| entry.variant == variant)
        .expect("every variant is in the table")
}

/// Appends the beta's fiat suffix to an already formatted amount. An absent or
/// empty fiat leaves the amount untouched, which is what the beta does when it
/// has no rate to show.
pub fn with_fiat(amount: &str, fiat: Option<&str>) -> String {
    match fiat {
        Some(fiat) if !fiat.is_empty() => {
            format!("{amount}{}", FIAT_SUFFIX.replace("%1$@", fiat))
        }
        _ => amount.to_owned(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn substitutions(text: &str) -> usize {
        // Count `%N$@` in the literal, independent of the `placeholders` field.
        let bytes = text.as_bytes();
        let mut count = 0;
        let mut index = 0;
        while index + 2 < bytes.len() {
            if bytes[index] == b'%' && bytes[index + 1].is_ascii_digit() && bytes[index + 2] == b'$'
            {
                count += 1;
                index += 3;
            } else {
                index += 1;
            }
        }
        count
    }

    #[test]
    fn every_sentence_is_the_beta_text_verbatim() {
        // The literals read out of the macOS 13.2 beta's Localizable.strings.
        assert_eq!(sentences(Variant::Incoming).service, "%1$@ sent you %2$@");
        assert_eq!(
            sentences(Variant::Incoming).service_item,
            "%1$@ sent you %2$@"
        );
        assert_eq!(sentences(Variant::Outgoing).service, "You sent %1$@ %2$@");
        assert_eq!(
            sentences(Variant::Outgoing).service_item,
            "You sent %1$@ %2$@"
        );
        assert_eq!(
            sentences(Variant::Anonymous).service,
            "Someone sent you %1$@"
        );
        assert_eq!(
            sentences(Variant::Anonymous).service_item,
            "Someone sent you %1$@"
        );
        assert_eq!(SERVICE_INCOMING_BARE, "sent you %1$@");
        assert_eq!(FIAT_SUFFIX, " (%1$@)");
    }

    #[test]
    fn the_declared_placeholder_count_matches_the_text() {
        for entry in &TABLE {
            assert_eq!(
                substitutions(entry.service),
                entry.placeholders,
                "{:?} service",
                entry.variant
            );
            assert_eq!(
                substitutions(entry.service_item),
                entry.placeholders,
                "{:?} item",
                entry.variant
            );
        }
        // The two standalone strings carry one substitution each.
        assert_eq!(substitutions(SERVICE_INCOMING_BARE), 1);
        assert_eq!(substitutions(FIAT_SUFFIX), 1);
    }

    #[test]
    fn the_table_covers_every_variant_exactly_once() {
        assert_eq!(TABLE.len(), 3);
        for variant in [Variant::Incoming, Variant::Outgoing, Variant::Anonymous] {
            let matches = TABLE.iter().filter(|e| e.variant == variant).count();
            assert_eq!(matches, 1, "{variant:?}");
        }
    }

    #[test]
    fn the_direction_decision_matches_the_client_flags() {
        // `_from->isSelf()` wins: your own transfer is outgoing.
        assert_eq!(variant(true, false), Variant::Outgoing);
        // A self message is never a service user, but if both were set the
        // outgoing branch still must win, or the user would read "Someone".
        assert_eq!(variant(true, true), Variant::Outgoing);
        // A hidden sender is anonymous...
        assert_eq!(variant(false, true), Variant::Anonymous);
        // ...and a real peer names itself.
        assert_eq!(variant(false, false), Variant::Incoming);
    }

    #[test]
    fn the_fiat_suffix_is_appended_only_when_there_is_one() {
        assert_eq!(with_fiat("5 Grams", Some("$1.20")), "5 Grams ($1.20)");
        // Empty and missing fiat both leave the amount alone.
        assert_eq!(with_fiat("5 Grams", Some("")), "5 Grams");
        assert_eq!(with_fiat("5 Grams", None), "5 Grams");
        // The suffix keeps the beta's leading space.
        assert!(with_fiat("5 Grams", Some("$1.20")).contains(" ("));
    }
}
