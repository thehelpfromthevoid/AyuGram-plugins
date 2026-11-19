//! Pure name mapping shared by the plugin shim.
//!
//! Every function here is a straight port of a hot, deterministic helper from
//! the Python shim. They take and return plain strings (or an integer), do no
//! I/O, and touch no Python object, so the native result is byte-identical to
//! the Python one and the caller can fall back without noticing.
//!
//! The Python originals:
//! * `hook_utils._desktop_hook_name`   - normalises a plugin hook name
//! * `base_plugin._is_container`       - Telegram update containers
//! * `hook_utils.pick_update_name`     - which Telegram name an event carries
//! * `client_utils._telegram_method_name` - the TL method a request stands for
//!
//! They are called on every hook registration and on every incoming message,
//! which is why they are here rather than in Python.

/// Alias table from `hook_utils._desktop_hook_name`. Keys are already lower
/// cased with `-` replaced by `_`, which the caller does before lookup.
const ALIASES: &[(&str, &str)] = &[
    ("on_message", "on_message"),
    ("message", "on_message"),
    ("new_message", "on_message"),
    ("on_new_message", "on_message"),
    ("message_received", "on_message"),
    ("on_message_received", "on_message"),
    ("on_message_sending", "on_message_sending"),
    ("on_send_message", "on_message_sending"),
    ("send_message", "on_message_sending"),
    ("before_send_message", "on_message_sending"),
    ("before_send", "on_message_sending"),
    ("on_message_deleted", "on_message_deleted"),
    ("message_deleted", "on_message_deleted"),
    ("on_delete_messages", "on_message_deleted"),
    ("deleted_messages", "on_message_deleted"),
    ("delete_messages", "on_message_deleted"),
    ("on_chat_opened", "on_chat_opened"),
    ("chat_opened", "on_chat_opened"),
    ("chat_open", "on_chat_opened"),
    ("on_open_chat", "on_chat_opened"),
    ("on_app_start", "on_app_start"),
    ("app_start", "on_app_start"),
    ("on_app_stop", "on_app_stop"),
    ("app_stop", "on_app_stop"),
    ("on_setting_changed", "on_setting_changed"),
    ("setting_changed", "on_setting_changed"),
    ("on_settings_changed", "on_setting_changed"),
    ("settings_changed", "on_setting_changed"),
    ("on_setting_change", "on_setting_changed"),
    ("on_new_message", "on_message"),
    ("new_messages", "on_message"),
    ("incoming_message", "on_message"),
    ("on_incoming_message", "on_message"),
    ("on_message_new", "on_message"),
    ("on_delete_message", "on_message_deleted"),
    ("on_messages_deleted", "on_message_deleted"),
    ("message_delete", "on_message_deleted"),
    ("on_before_send", "on_message_sending"),
    ("before_message_send", "on_message_sending"),
    ("on_outgoing_message", "on_message_sending"),
    ("on_app_launch", "on_app_start"),
    ("on_app_quit", "on_app_stop"),
    ("on_quit", "on_app_stop"),
    ("on_typing", "on_typing"),
    ("typing", "on_typing"),
    ("user_typing", "on_typing"),
    ("send_action", "on_typing"),
    ("on_send_action", "on_typing"),
    ("on_media_sending", "on_media_sending"),
    ("media_sending", "on_media_sending"),
    ("before_send_media", "on_media_sending"),
    ("on_send_media", "on_media_sending"),
];

fn alias_for(low: &str) -> Option<&'static str> {
    ALIASES
        .iter()
        .find(|(key, _)| *key == low)
        .map(|(_, value)| *value)
}

/// Port of `hook_utils._desktop_hook_name`.
///
/// `name` is normalised to lower case with `-` turned into `_`, looked up in the
/// alias table, then matched by substring; anything still unknown comes back
/// unchanged. A `tl_`-prefixed or dotted name that matches no rule becomes an
/// empty string, which the caller reads as "no Desktop event".
pub fn desktop_hook_name(name: &str) -> String {
    let raw = name;
    let low = raw.to_lowercase().replace('-', "_");

    if let Some(mapped) = alias_for(&low) {
        return mapped.to_owned();
    }
    if low.contains("send") && low.contains("message") {
        return "on_message_sending".to_owned();
    }
    if low.contains("delete") && low.contains("message") {
        return "on_message_deleted".to_owned();
    }
    if low.contains("chat") && low.contains("open") {
        return "on_chat_opened".to_owned();
    }
    // TL and Java hook names: the same events under Telegram's own names, so
    // they are matched on the words after stripping the prefix.
    if low.starts_with("tl_") || raw.contains('.') {
        let plain = if let Some(stripped) = low.strip_prefix("tl_") {
            stripped.to_owned()
        } else {
            low.replace('.', "_")
        };
        if plain.contains("typing") {
            return "on_typing".to_owned();
        }
        if plain.contains("delete") || plain.contains("deleted") {
            return "on_message_deleted".to_owned();
        }
        let squashed = plain.replace('_', "");
        if squashed.contains("newmessage") || (plain.contains("message") && plain.contains("new")) {
            return "on_message".to_owned();
        }
        if squashed.contains("shortmessage") || squashed.contains("shortchatmessage") {
            return "on_message".to_owned();
        }
        return String::new();
    }
    raw.to_owned()
}

/// Port of `base_plugin._is_container`.
pub fn is_container(name: &str) -> bool {
    let low = name.to_lowercase();
    low.starts_with("tl_updates") || low == "updates" || low == "updatescombined"
}

/// Port of `hook_utils.pick_update_name`.
///
/// `kind` replaces the `getattr(src, "kind", -1)` the Python side reads before
/// calling: 0 user, 1 chat, 2 channel, anything else unknown.
pub fn pick_update_name(names: &[String], event: &str, kind: i64) -> String {
    if names.is_empty() {
        return String::new();
    }
    if event == "on_typing" {
        let want = match kind {
            0 => Some("user"),
            1 => Some("chat"),
            2 => Some("channel"),
            _ => None,
        };
        if let Some(want) = want {
            for candidate in names {
                if candidate.to_lowercase().contains(want) {
                    return candidate.clone();
                }
            }
        }
        for candidate in names {
            if candidate.to_lowercase().contains("typing") {
                return candidate.clone();
            }
        }
        return names[0].clone();
    }
    if event == "on_message" {
        for wanted in ["TL_updateNewMessage", "TL_updateNewChannelMessage"] {
            if names.iter().any(|n| n == wanted) {
                return wanted.to_owned();
            }
        }
        return names[0].clone();
    }
    if event == "on_message_deleted" {
        for candidate in names {
            if candidate.to_lowercase().contains("delete") {
                return candidate.clone();
            }
        }
        return names[0].clone();
    }
    names[0].clone()
}

/// Port of `client_utils._telegram_method_name`.
///
/// `class_name` is `type(request).__name__`; `values` are the string-valued
/// `__tl_name__` / `_tl_name` / `method` / `__name__` attributes in that order
/// (`None` when the attribute was absent or not a string).
pub fn telegram_method_name(class_name: &str, values: &[Option<String>]) -> String {
    if class_name.starts_with("TL_") && class_name.contains('.') {
        return class_name.to_owned();
    }
    for value in values.iter().flatten() {
        if value.starts_with("TL_") && value.contains('.') {
            return value.clone();
        }
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_aliases_map_to_desktop_events() {
        assert_eq!(desktop_hook_name("on_message"), "on_message");
        assert_eq!(desktop_hook_name("New-Message"), "on_message");
        assert_eq!(desktop_hook_name("message_received"), "on_message");
        assert_eq!(desktop_hook_name("on_settings_changed"), "on_setting_changed");
        assert_eq!(desktop_hook_name("on_app_quit"), "on_app_stop");
        assert_eq!(desktop_hook_name("on_send_media"), "on_media_sending");
    }

    #[test]
    fn substring_rules_are_not_aliases() {
        // "send" + "message" but not in the table: the substring rule answers.
        assert_eq!(desktop_hook_name("please_send_message"), "on_message_sending");
        assert_eq!(desktop_hook_name("when_chat_will_open"), "on_chat_opened");
        assert_eq!(desktop_hook_name("message_delete_later"), "on_message_deleted");
    }

    #[test]
    fn tl_names_are_matched_on_words() {
        assert_eq!(desktop_hook_name("TL_updateNewMessage"), "on_message");
        assert_eq!(desktop_hook_name("TL_updateShortChatMessage"), "on_message");
        assert_eq!(desktop_hook_name("TL_updateDeleteMessages"), "on_message_deleted");
        assert_eq!(desktop_hook_name("TL_updateUserTyping"), "on_typing");
        assert_eq!(desktop_hook_name("TL_updateSomethingElse"), "");
        assert_eq!(desktop_hook_name("com.example.Notify"), "");
    }

    #[test]
    fn unknown_plain_names_pass_through_unchanged() {
        assert_eq!(desktop_hook_name("my_custom_event"), "my_custom_event");
        assert_eq!(desktop_hook_name(""), "");
        // Lower casing is only used for the decision; the value returned is raw.
        assert_eq!(desktop_hook_name("My_Custom_Event"), "My_Custom_Event");
    }

    #[test]
    fn hyphen_and_case_normalisation() {
        assert_eq!(desktop_hook_name("On-Message"), "on_message");
        assert_eq!(desktop_hook_name("ON_APP_START"), "on_app_start");
    }

    #[test]
    fn containers_are_recognised() {
        assert!(is_container("TL_updates"));
        assert!(is_container("updates"));
        assert!(is_container("UpdatesCombined"));
        assert!(!is_container("TL_updateNewMessage"));
        assert!(!is_container(""));
    }

    #[test]
    fn typing_name_follows_the_kind() {
        let names = vec![
            "TL_updateUserTyping".to_owned(),
            "TL_updateChatUserTyping".to_owned(),
            "TL_updateChannelUserTyping".to_owned(),
        ];
        assert_eq!(pick_update_name(&names, "on_typing", 1), "TL_updateChatUserTyping");
        assert_eq!(
            pick_update_name(&names, "on_typing", 2),
            "TL_updateChannelUserTyping"
        );
        assert_eq!(pick_update_name(&names, "on_typing", 0), "TL_updateUserTyping");
        // Unknown kind falls back to any typing name, then to the first.
        assert_eq!(pick_update_name(&names, "on_typing", 9), "TL_updateUserTyping");
    }

    #[test]
    fn typing_name_without_a_typing_candidate() {
        let names = vec!["weird_name".to_owned(), "other".to_owned()];
        assert_eq!(pick_update_name(&names, "on_typing", 0), "weird_name");
    }

    #[test]
    fn message_name_prefers_the_known_updates() {
        let names = vec!["a".to_owned(), "TL_updateNewChannelMessage".to_owned()];
        assert_eq!(pick_update_name(&names, "on_message", 0), "TL_updateNewChannelMessage");
        let names = vec!["a".to_owned(), "b".to_owned()];
        assert_eq!(pick_update_name(&names, "on_message", 0), "a");
    }

    #[test]
    fn deleted_name_uses_the_delete_word() {
        let names = vec!["x".to_owned(), "TL_updateDeleteMessages".to_owned()];
        assert_eq!(pick_update_name(&names, "on_message_deleted", 0), "TL_updateDeleteMessages");
    }

    #[test]
    fn empty_name_list_answers_empty() {
        assert_eq!(pick_update_name(&[], "on_typing", 0), "");
        assert_eq!(pick_update_name(&[], "on_message", 0), "");
    }

    #[test]
    fn telegram_method_from_class_name() {
        assert_eq!(
            telegram_method_name("TL_users.getFullUser", &[]),
            "TL_users.getFullUser"
        );
        // No dot in the class name: fall back to the attribute list.
        assert_eq!(telegram_method_name("Request", &[]), "");
        assert_eq!(
            telegram_method_name(
                "Request",
                &[
                    None,
                    Some("not_a_tl".to_owned()),
                    Some("TL_account.updateProfile".to_owned()),
                ]
            ),
            "TL_account.updateProfile"
        );
        assert_eq!(telegram_method_name("Request", &[Some("plain".to_owned())]), "");
    }
}
