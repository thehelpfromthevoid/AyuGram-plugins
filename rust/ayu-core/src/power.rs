//! Power saving mode, ported from the macOS 13.2 beta string set
//! (`LiteMode.*`, 27 keys). Of those, 21 reach the caller: The nine
//! `LiteMode.Key.<key>.{Title,Info}` pairs plus `Title`, `Info` and
//! `LowPower.InfoFull`. The beta's own on/off captions (`Enabled`, `Disabled`,
//! `EnableText`, `DisableAlert`) and the parameterised
//! `LiteMode.LowPower.Info` are not surfaced, because this client builds those
//! labels from its own localisation instead.
//!
//! The beta exposes a single switch plus nine independent animation toggles and
//! an optional battery threshold that turns the mode on automatically. All of it
//! is client-local: nothing here talks to a server, which is why it is the first
//! beta feature worth carrying over.
//!
//! The on-disk representation is deliberately the same shape as the `--stealth`
//! argument list used by the DPI process, so the C++ side has one parser shape
//! to emit and the client keeps the exact user-visible values.

use std::collections::BTreeMap;

/// One toggle. `title` and `info` are the beta's own strings, kept verbatim so
/// the ported UI reads exactly like the original.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Toggle {
    pub key: &'static str,
    pub title: &'static str,
    pub info: &'static str,
    /// The beta lists this toggle on macOS only (the Tahoe liquid-glass effect);
    /// on other platforms it parses but has nothing to switch.
    pub mac_only: bool,
}

/// Every toggle the beta ships, in the order its string table lists them.
pub const TOGGLES: [Toggle; 9] = [
    Toggle {
        key: "animations",
        title: "Interface Animations",
        info: "Other animations that make Telegram look amazing.",
        mac_only: false,
    },
    Toggle {
        key: "blur",
        title: "Blur Effects",
        info: "Blur transparent interface elements.",
        mac_only: false,
    },
    Toggle {
        key: "emoji",
        title: "Emoji Animations",
        info: "Loop animated emoji in messages, reactions and statuses.",
        mac_only: false,
    },
    Toggle {
        key: "emoji_effects",
        title: "Emoji Effects",
        info: "Show animated effects for reactions and premium stickers.",
        mac_only: false,
    },
    Toggle {
        key: "gif",
        title: "Autoplay GIFs",
        info: "Autoplay and loop GIFs in chats and in the keyboard.",
        mac_only: false,
    },
    Toggle {
        key: "liquidGlass",
        title: "Enable Liquid Glass",
        info: "Render UI surfaces with the macOS Tahoe liquid-glass effect. \
               Turn off to fall back to a lightweight system blur and save GPU.",
        mac_only: true,
    },
    Toggle {
        key: "menu_animations",
        title: "Menu Animations",
        info: "Animate icons in context menus.",
        mac_only: false,
    },
    Toggle {
        key: "stickers",
        title: "Sticker Animations",
        info: "Loop animated stickers, in chats and in the keyboard.",
        mac_only: false,
    },
    Toggle {
        key: "video",
        title: "Autoplay Videos",
        info: "Autoplay and loop videos and video messages in chats.",
        mac_only: false,
    },
];

/// `LiteMode.Title`, `LiteMode.Info`.
pub const MODE_TITLE: &str = "Power Saving";
pub const MODE_INFO: &str = "Reduce all power-intensive animations and improve performance.";

/// `LiteMode.LowPower.InfoFull`.
pub const LOW_POWER_INFO: &str =
    "Power saving mode can be turned on automatically based on battery level.";

/// The beta refuses a threshold outside 0..=100, where 0 means "never switch
/// automatically" (`LiteMode.LowPower.Info` is only shown for a real value).
pub const MAX_THRESHOLD: u8 = 100;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Settings {
    /// Whether the mode is on at all. Off is the default, like every other
    /// behaviour-changing switch in this client.
    pub enabled: bool,
    /// Toggle state per key, `true` meaning the animation is kept. The mode is
    /// meant to be switched on by clearing these, but each one is independent:
    /// the beta lets a user keep GIFs and drop blur.
    ///
    /// Private on purpose: the map must always carry exactly the keys in
    /// [`TOGGLES`], otherwise `parse(arguments(x)) == x` breaks for a hand-edited
    /// state. [`keeps`](Self::keeps), [`set_toggle`](Self::set_toggle) and
    /// [`set_enabled`](Self::set_enabled) are the only ways in.
    toggles: BTreeMap<&'static str, bool>,
    /// Battery percentage below which the mode turns itself on. `0` disables
    /// the automatic switch.
    ///
    /// Private for the same reason as `toggles`: a bare `255` here would make
    /// `arguments` write a `low_power=` value the parser refuses, breaking the
    /// round trip. [`low_power_threshold`](Self::low_power_threshold) and
    /// [`set_low_power_threshold`](Self::set_low_power_threshold) are the way in.
    low_power_threshold: u8,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            enabled: false,
            toggles: TOGGLES.iter().map(|t| (t.key, true)).collect(),
            low_power_threshold: 0,
        }
    }
}

/// The preset behind the main switch: turning the mode on clears every toggle,
/// turning it off restores every toggle.
pub fn preset(enabled: bool) -> BTreeMap<&'static str, bool> {
    TOGGLES.iter().map(|t| (t.key, !enabled)).collect()
}

pub fn is_known_toggle(key: &str) -> bool {
    TOGGLES.iter().any(|t| t.key == key)
}

/// The toggle names a caller may show, filtered to what this platform can act
/// on. `mac_only` entries are dropped on non-macOS builds.
pub fn visible_toggles(macos: bool) -> impl Iterator<Item = &'static Toggle> {
    TOGGLES.iter().filter(move |t| macos || !t.mac_only)
}

impl Settings {
    fn toggle_mut(&mut self, key: &str) -> Option<&mut bool> {
        self.toggles.get_mut(key)
    }

    /// Reads whether a toggle currently keeps its animation. An unknown key is
    /// `false` rather than a panic: callers iterate `TOGGLES`, so a miss means a
    /// stale caller, not a corrupt file.
    pub fn keeps(&self, key: &str) -> bool {
        self.toggles.get(key).copied().unwrap_or(false)
    }

    /// The current state as JSON-shaped pairs, in the beta's own order: the
    /// toggle key and whether it is kept. The map is private, so this is the
    /// read side callers use instead of reaching into it.
    pub fn kept_toggles(&self) -> impl Iterator<Item = (&'static str, bool)> + '_ {
        TOGGLES
            .iter()
            .map(|toggle| (toggle.key, self.keeps(toggle.key)))
    }

    /// The battery percentage below which the mode switches itself on, `0`
    /// meaning never. Paired with [`set_low_power_threshold`](Self::set_low_power_threshold).
    pub fn low_power_threshold(&self) -> u8 {
        self.low_power_threshold
    }

    /// Turns the mode on or off as a unit, as the main switch does.
    pub fn set_enabled(&mut self, enabled: bool) {
        self.enabled = enabled;
        self.toggles = preset(enabled);
    }

    /// Switches a single toggle. Out-of-range is refused, matching the setters
    /// on the C++ side that ignore values they cannot store.
    pub fn set_toggle(&mut self, key: &str, keeps: bool) -> Result<(), String> {
        match self.toggle_mut(key) {
            Some(slot) => {
                *slot = keeps;
                Ok(())
            }
            None => Err(format!("unknown power saving key: {key}")),
        }
    }

    /// `None` clears the automatic switch. Out-of-range is refused.
    pub fn set_low_power_threshold(&mut self, percent: u8) -> Result<(), String> {
        if percent > MAX_THRESHOLD {
            return Err(format!(
                "low power threshold out of range: {percent} (0..={MAX_THRESHOLD})"
            ));
        }
        self.low_power_threshold = percent;
        Ok(())
    }

    /// Whether the mode should switch itself on at this battery level. A zero
    /// threshold never triggers, and an already-enabled mode never triggers.
    pub fn should_enable_at(&self, battery_percent: u8) -> bool {
        !self.enabled && self.low_power_threshold > 0 && battery_percent < self.low_power_threshold
    }
}

/// Parses repeated `KEY=VALUE` entries the way the DPI argument list is parsed,
/// so the C++ side emits one shape: `enabled=on|off`, `low_power=<0..100>`,
/// `<toggle>=on|off`.
///
/// Deliberately loyal where it does not change meaning: surrounding whitespace
/// around key and value is trimmed, the threshold accepts a leading `+` and
/// leading zeros (`+5`, `05` both mean 5), and if a key repeats the last
/// occurrence wins. It is strict everywhere a guess could change behaviour:
/// the switch takes only `on`/`off` (not `1`/`true`/`ON`), the threshold must be
/// a decimal `0..=100` (not `5.0` or `0x5`), and an unknown key is an error.
///
/// Entries are applied in two passes: the mode and the threshold first, then
/// the toggles. The main switch rewrites every toggle with its preset, so a
/// single pass would make the result depend on where `enabled=` happened to sit
/// in the argument list. For every state reachable through [`Settings`]' own
/// API (its map always holds exactly the [`TOGGLES`] keys), `parse(arguments(x))`
/// is equal to `x`.
pub fn parse(entries: impl IntoIterator<Item = String>) -> Result<Settings, String> {
    let mut settings = Settings::default();
    let mut toggles: Vec<(String, bool)> = Vec::new();
    for entry in entries {
        let Some((key, value)) = entry.split_once('=') else {
            return Err(format!("expected KEY=VALUE, got: {entry}"));
        };
        let key = key.trim();
        let value = value.trim();
        match key {
            "enabled" => {
                let Some(on) = parse_switch(value) else {
                    return Err(format!("expected on or off, got: {value}"));
                };
                settings.set_enabled(on);
            }
            "low_power" => {
                let Ok(percent) = value.parse::<u8>() else {
                    return Err(format!(
                        "expected a number 0..={MAX_THRESHOLD}, got: {value}"
                    ));
                };
                settings.set_low_power_threshold(percent)?;
            }
            _ => {
                let Some(on) = parse_switch(value) else {
                    return Err(format!("expected on or off, got: {value}"));
                };
                if !is_known_toggle(key) {
                    return Err(format!("unknown power saving key: {key}"));
                }
                toggles.push((key.to_owned(), on));
            }
        }
    }
    for (key, on) in toggles {
        settings.set_toggle(&key, on)?;
    }
    Ok(settings)
}

fn parse_switch(value: &str) -> Option<bool> {
    match value {
        "on" => Some(true),
        "off" => Some(false),
        _ => None,
    }
}

/// The arguments to hand back to the client, in a stable order: the mode, the
/// threshold, then the toggles as the table lists them. A toggle is written
/// only when it differs from the preset the mode itself implies, so a default
/// state produces no arguments at all, an older client keeps working, and the
/// list parses back into exactly the state it was written from.
pub fn arguments(settings: &Settings) -> Vec<String> {
    let mut out = Vec::new();
    let default = Settings::default();
    if settings.enabled != default.enabled {
        out.push(format!(
            "enabled={}",
            if settings.enabled { "on" } else { "off" }
        ));
    }
    if settings.low_power_threshold != default.low_power_threshold {
        out.push(format!("low_power={}", settings.low_power_threshold));
    }
    let baseline = preset(settings.enabled);
    for toggle in &TOGGLES {
        let keeps = settings.keeps(toggle.key);
        let implied = baseline.get(toggle.key).copied().unwrap_or(true);
        if keeps != implied {
            out.push(format!(
                "{}={}",
                toggle.key,
                if keeps { "on" } else { "off" }
            ));
        }
    }
    out
}

/// What the client logs and the settings screen shows, one line per non-default
/// value. The first line is always the mode itself.
pub fn report(settings: &Settings) -> Vec<String> {
    let mut out = vec![format!(
        "powerSaving: {} ({}); lowPower: {}",
        if settings.enabled { "on" } else { "off" },
        MODE_TITLE,
        if settings.low_power_threshold == 0 {
            "off".to_owned()
        } else {
            format!("below {}%", settings.low_power_threshold)
        }
    )];
    let default = Settings::default();
    for toggle in &TOGGLES {
        let keeps = settings.keeps(toggle.key);
        if keeps != default.keeps(toggle.key) {
            out.push(format!(
                "  {} = {}",
                toggle.key,
                if keeps { "kept" } else { "off" }
            ));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_keep_every_animation_and_never_switch_themselves() {
        let settings = Settings::default();
        assert!(!settings.enabled);
        assert_eq!(settings.low_power_threshold, 0);
        for toggle in &TOGGLES {
            assert!(settings.keeps(toggle.key), "{}", toggle.key);
        }
        assert!(arguments(&settings).is_empty());
    }

    #[test]
    fn every_toggle_the_beta_lists_is_known() {
        // The nine keys read out of LiteMode.Key.<key>.Title in the beta strings.
        for key in [
            "animations",
            "blur",
            "emoji",
            "emoji_effects",
            "gif",
            "liquidGlass",
            "menu_animations",
            "stickers",
            "video",
        ] {
            assert!(is_known_toggle(key), "{key}");
        }
        assert!(!is_known_toggle("make_up"));
        assert!(!is_known_toggle(""));
    }

    #[test]
    fn the_main_switch_clears_and_restores_every_toggle() {
        let mut settings = Settings::default();
        settings.set_enabled(true);
        assert!(settings.enabled);
        for toggle in &TOGGLES {
            assert!(!settings.keeps(toggle.key), "{}", toggle.key);
        }
        settings.set_enabled(false);
        for toggle in &TOGGLES {
            assert!(settings.keeps(toggle.key), "{}", toggle.key);
        }
    }

    #[test]
    fn a_single_toggle_can_be_kept_while_the_mode_is_on() {
        let mut settings = Settings::default();
        settings.set_enabled(true);
        settings.set_toggle("gif", true).expect("known key");
        assert!(settings.keeps("gif"));
        assert!(!settings.keeps("video"));
    }

    #[test]
    fn unknown_keys_and_out_of_range_values_are_refused() {
        let mut settings = Settings::default();
        assert!(settings.set_toggle("nope", true).is_err());
        assert!(settings.set_toggle("animations", true).is_ok());
        assert!(settings.set_low_power_threshold(101).is_err());
        assert!(settings.set_low_power_threshold(100).is_ok());
    }

    #[test]
    fn parsing_reads_the_mode_the_threshold_and_the_toggles() {
        let settings = parse([
            "enabled=on".to_owned(),
            "low_power=20".to_owned(),
            "gif=on".to_owned(),
            "blur=off".to_owned(),
        ])
        .expect("parses");
        assert!(settings.enabled);
        assert_eq!(settings.low_power_threshold, 20);
        assert!(settings.keeps("gif"));
        assert!(!settings.keeps("blur"));
    }

    #[test]
    fn parsing_refuses_junk_instead_of_guessing() {
        for bad in [
            "animations",     // no '='
            "animations=yes", // neither on nor off
            "enabled=",       // empty switch
            "low_power=abc",  // not a number
            "low_power=300",  // out of range
            "made_up=on",     // unknown toggle
        ] {
            assert!(parse([bad.to_owned()]).is_err(), "{bad}");
        }
    }

    #[test]
    fn arguments_round_trip_through_the_parser() {
        let mut settings = Settings::default();
        settings.set_enabled(true);
        settings.set_toggle("gif", true).expect("known");
        settings.set_low_power_threshold(15).expect("in range");

        let written = arguments(&settings);
        let parsed = parse(written.clone()).expect("round trip");
        assert_eq!(parsed, settings, "{written:?}");

        // A default state writes nothing, which is what keeps an older client
        // from seeing an argument it does not understand.
        assert!(arguments(&Settings::default()).is_empty());
    }

    #[test]
    fn arguments_are_emitted_in_a_stable_order() {
        let mut settings = Settings::default();
        settings.set_enabled(true);
        settings.set_low_power_threshold(10).expect("in range");
        // Two toggles kept back, listed out of table order here: the emitted
        // list must still follow the table, not this insertion order.
        settings.set_toggle("video", true).expect("known");
        settings.set_toggle("gif", true).expect("known");
        assert_eq!(
            arguments(&settings),
            vec![
                "enabled=on".to_owned(),
                "low_power=10".to_owned(),
                // mode is on, so the preset clears everything and only the two
                // toggles kept back are a deviation worth writing.
                "gif=on".to_owned(),
                "video=on".to_owned(),
            ]
        );
    }

    #[test]
    fn the_mode_and_the_toggles_apply_regardless_of_argument_order() {
        let mut settings = Settings::default();
        settings.set_enabled(true);
        settings.set_toggle("gif", true).expect("known");
        settings.set_low_power_threshold(30).expect("in range");

        let written = arguments(&settings);
        // The main switch rewrites every toggle, so a parser that applied
        // entries in one pass would lose the kept toggle whenever `enabled=`
        // came last. Both orders must land on the same state.
        let mut reordered = written.clone();
        reordered.reverse();
        assert_eq!(parse(reordered).expect("reverse order"), settings);
        assert_eq!(parse(written).expect("written order"), settings);
    }

    #[test]
    fn the_threshold_only_triggers_below_it_and_only_once() {
        let mut settings = Settings::default();
        settings.set_low_power_threshold(20).expect("in range");
        assert!(settings.should_enable_at(19));
        assert!(!settings.should_enable_at(20));
        assert!(!settings.should_enable_at(100));

        // A zero threshold is "never", not "always".
        let disabled = Settings::default();
        assert!(!disabled.should_enable_at(0));

        // Already on: switching again would be a no-op that still saved and
        // restarted, so it does not report as needed.
        let mut on = Settings::default();
        on.set_low_power_threshold(20).expect("in range");
        on.set_enabled(true);
        assert!(!on.should_enable_at(1));
    }

    #[test]
    fn the_battery_threshold_bounds_are_the_beta_percentage() {
        assert_eq!(MAX_THRESHOLD, 100);
        let mut settings = Settings::default();
        assert!(settings.set_low_power_threshold(0).is_ok());
        assert!(settings.set_low_power_threshold(100).is_ok());
        assert!(settings.set_low_power_threshold(101).is_err());
    }

    #[test]
    fn the_report_names_every_change_and_nothing_else() {
        let mut settings = Settings::default();
        assert_eq!(report(&settings).len(), 1);
        assert!(report(&settings)[0].contains("powerSaving: off"));
        assert!(report(&settings)[0].contains("lowPower: off"));

        settings.set_enabled(true);
        let lines = report(&settings);
        assert!(lines[0].contains("powerSaving: on"));
        assert!(lines.iter().any(|l| l.contains("animations = off")));
        assert!(lines.iter().any(|l| l.contains("liquidGlass = off")));

        // A kept toggle is not a change, so it is not reported.
        let mut kept = Settings::default();
        kept.set_enabled(true);
        kept.set_toggle("gif", true).expect("known");
        assert!(!report(&kept).iter().any(|l| l.contains("gif")));
    }

    #[test]
    fn mac_only_toggles_are_hidden_elsewhere() {
        let all: Vec<_> = visible_toggles(true).map(|t| t.key).collect();
        let here: Vec<_> = visible_toggles(false).map(|t| t.key).collect();
        assert!(all.contains(&"liquidGlass"));
        assert!(!here.contains(&"liquidGlass"));
        assert_eq!(all.len(), TOGGLES.len());
        assert_eq!(here.len(), TOGGLES.len() - 1);
        // ...but hiding never makes it unparsable, so a settings file written on
        // macOS still loads on Windows.
        assert!(is_known_toggle("liquidGlass"));
    }

    #[test]
    fn the_ported_strings_match_the_beta_verbatim() {
        assert_eq!(MODE_TITLE, "Power Saving");
        assert_eq!(
            MODE_INFO,
            "Reduce all power-intensive animations and improve performance."
        );
        assert_eq!(
            LOW_POWER_INFO,
            "Power saving mode can be turned on automatically based on battery level."
        );
        // The one string with a placeholder keeps it.
        let gif = TOGGLES.iter().find(|t| t.key == "gif").expect("gif");
        assert_eq!(gif.title, "Autoplay GIFs");
        assert_eq!(
            gif.info,
            "Autoplay and loop GIFs in chats and in the keyboard."
        );
        let video = TOGGLES.iter().find(|t| t.key == "video").expect("video");
        assert_eq!(
            video.info,
            "Autoplay and loop videos and video messages in chats."
        );
    }

    /// The beta's nine keys with both strings, transcribed from
    /// `mac_g_LiteMode.txt`. Deliberately independent of `TOGGLES`: dropping a
    /// key from the table or mistyping a string fails
    /// `the_table_is_exactly_the_beta_nine_verbatim`, which the `TOGGLES.len`
    /// based tests cannot catch.
    const BETA_REFERENCE: [(&str, &str, &str); 9] = [
        (
            "animations",
            "Interface Animations",
            "Other animations that make Telegram look amazing.",
        ),
        (
            "blur",
            "Blur Effects",
            "Blur transparent interface elements.",
        ),
        (
            "emoji",
            "Emoji Animations",
            "Loop animated emoji in messages, reactions and statuses.",
        ),
        (
            "emoji_effects",
            "Emoji Effects",
            "Show animated effects for reactions and premium stickers.",
        ),
        (
            "gif",
            "Autoplay GIFs",
            "Autoplay and loop GIFs in chats and in the keyboard.",
        ),
        (
            "liquidGlass",
            "Enable Liquid Glass",
            "Render UI surfaces with the macOS Tahoe liquid-glass effect. \
             Turn off to fall back to a lightweight system blur and save GPU.",
        ),
        (
            "menu_animations",
            "Menu Animations",
            "Animate icons in context menus.",
        ),
        (
            "stickers",
            "Sticker Animations",
            "Loop animated stickers, in chats and in the keyboard.",
        ),
        (
            "video",
            "Autoplay Videos",
            "Autoplay and loop videos and video messages in chats.",
        ),
    ];

    #[test]
    fn the_table_is_exactly_the_beta_nine_verbatim() {
        assert_eq!(TOGGLES.len(), 9);
        assert_eq!(BETA_REFERENCE.len(), 9);
        for (index, (key, title, info)) in BETA_REFERENCE.iter().enumerate() {
            let toggle = &TOGGLES[index];
            assert_eq!(toggle.key, *key, "key #{index}");
            assert_eq!(toggle.title, *title, "{key} title");
            assert_eq!(toggle.info, *info, "{key} info");
        }
        // Exactly one macOS-only toggle, and it is the liquid-glass one: the
        // filtered counts in `mac_only_toggles_are_hidden_elsewhere` rest on it.
        let mac_only: Vec<_> = TOGGLES
            .iter()
            .filter(|t| t.mac_only)
            .map(|t| t.key)
            .collect();
        assert_eq!(mac_only, vec!["liquidGlass"]);
    }

    #[test]
    fn the_parser_is_loyal_in_documented_ways() {
        // Accepted: surrounding whitespace, a leading plus, leading zeros.
        assert_eq!(
            parse(["low_power= 5 ".to_owned()])
                .expect("trimmed")
                .low_power_threshold,
            5
        );
        assert_eq!(
            parse(["low_power=+5".to_owned()])
                .expect("plus")
                .low_power_threshold,
            5
        );
        assert_eq!(
            parse(["low_power=05".to_owned()])
                .expect("zeros")
                .low_power_threshold,
            5
        );
        // The last occurrence of a key wins.
        let twice = parse(["enabled=on".to_owned(), "enabled=off".to_owned()]).expect("twice");
        assert!(!twice.enabled);
        // Refused: switch aliases other than `on`/`off`, non-decimal numbers.
        for bad in [
            "enabled=ON",
            "gif=1",
            "gif=true",
            "low_power=5.0",
            "low_power=0x5",
        ] {
            assert!(parse([bad.to_owned()]).is_err(), "{bad}");
        }
    }

    #[test]
    fn the_threshold_bounds_hold_through_the_parser() {
        // `the_battery_threshold_bounds_are_the_beta_percentage` only exercises
        // the setter; these must fail on the parsing path instead.
        assert!(parse(["low_power=255".to_owned()]).is_err());
        assert!(parse(["low_power=256".to_owned()]).is_err());
        assert!(parse(["low_power=-1".to_owned()]).is_err());
        assert_eq!(
            parse(["low_power=100".to_owned()])
                .expect("100")
                .low_power_threshold,
            100
        );
        assert_eq!(
            parse(["low_power=0".to_owned()])
                .expect("0")
                .low_power_threshold,
            0
        );
    }
}
