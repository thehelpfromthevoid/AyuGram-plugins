//! Plugin manifest parsing and validation.
//!
//! Mirrors `ayu/plugins/plugin_manifest.cpp` so the native layer, the Python
//! host and the C++ client agree on what a plugin is and which ones are
//! refused. Field truncation follows the C++ side: name 100, description
//! 8000, author 200, version 32 characters.

use serde_json::Value;

pub const PLUGIN_API_VERSION: i64 = 1;
pub const MAX_DESCRIPTION_LEN: usize = 8000;
pub const MAX_NAME_LEN: usize = 100;
pub const MAX_AUTHOR_LEN: usize = 200;
pub const MAX_VERSION_LEN: usize = 32;

pub const HOOK_NAMES: [&str; 9] = [
    "on_message",
    "on_message_sending",
    "on_message_deleted",
    "on_chat_opened",
    "on_app_start",
    "on_app_stop",
    "on_setting_changed",
    "on_typing",
    "on_media_sending",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Manifest {
    pub id: String,
    pub name: String,
    pub description: String,
    pub author: String,
    pub version: String,
    pub min_app_version: String,
    pub api_version: i64,
    pub entry: String,
    pub hooks: Vec<String>,
    pub permissions: Vec<String>,
    pub requirements: Vec<String>,
    pub icon: String,
    pub platforms: Vec<String>,
}

impl Default for Manifest {
    fn default() -> Self {
        Self {
            id: String::new(),
            name: String::new(),
            description: String::new(),
            author: String::new(),
            version: "1.0".to_owned(),
            min_app_version: String::new(),
            api_version: PLUGIN_API_VERSION,
            entry: "main.py".to_owned(),
            hooks: Vec::new(),
            permissions: Vec::new(),
            requirements: Vec::new(),
            icon: String::new(),
            platforms: Vec::new(),
        }
    }
}

fn truncate_chars(value: &str, limit: usize) -> String {
    match value.char_indices().nth(limit) {
        Some((byte, _)) => value[..byte].to_owned(),
        None => value.to_owned(),
    }
}

pub fn is_known_hook(name: &str) -> bool {
    HOOK_NAMES.contains(&name)
}

/// Plugin ids: a letter, then 1..=63 of `[A-Za-z0-9_.-]`.
pub fn is_valid_id(id: &str) -> bool {
    let mut chars = id.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    if !first.is_ascii_alphabetic() {
        return false;
    }
    let rest = chars.as_str();
    if rest.len() < 1 || rest.len() > 63 {
        return false;
    }
    rest.chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '.' || c == '-')
}

/// Whether the plugin lists a platform this build can run.
pub fn runs_here(manifest: &Manifest) -> bool {
    if manifest.platforms.is_empty() {
        return true;
    }
    manifest.platforms.iter().any(|platform| {
        matches!(
            platform.to_lowercase().as_str(),
            "desktop" | "windows" | "macos" | "linux" | "all" | "any"
        )
    })
}

/// Every reason the client would refuse or skip this plugin, in user-facing
/// words. Empty means the manifest is fine.
pub fn problems(manifest: &Manifest) -> Vec<String> {
    let mut out = Vec::new();
    if !is_valid_id(&manifest.id) {
        out.push("id does not match the app's pattern".to_owned());
    }
    if manifest.name.is_empty() || manifest.name.chars().count() > MAX_NAME_LEN {
        out.push("name must be 1..100 chars".to_owned());
    }
    if manifest.description.chars().count() > MAX_DESCRIPTION_LEN {
        out.push(format!(
            "description longer than {MAX_DESCRIPTION_LEN} chars"
        ));
    }
    if manifest.entry.is_empty() || manifest.entry.contains("..") {
        out.push("entry must be a plain file name".to_owned());
    }
    if manifest.api_version <= 0 {
        out.push("api_version must be a positive number".to_owned());
    } else if manifest.api_version > PLUGIN_API_VERSION {
        out.push(format!(
            "api_version {} is newer than the app's {}: the app will refuse it",
            manifest.api_version, PLUGIN_API_VERSION
        ));
    }
    for hook in &manifest.hooks {
        if !is_known_hook(hook) {
            out.push(format!("unknown hook: {hook}"));
        }
    }
    if !runs_here(manifest) {
        out.push(format!(
            "built for {}, not this platform",
            manifest.platforms.join(", ")
        ));
    }
    out
}

pub fn validate(manifest: &Manifest) -> Result<(), String> {
    match problems(manifest).into_iter().next() {
        Some(problem) => Err(problem),
        None => Ok(()),
    }
}

/// Parses `plugin.json` the same way the C++ host does: missing fields take
/// defaults, strings are truncated, unknown hooks are dropped, then the result
/// is validated.
pub fn parse(json: &str) -> Result<Manifest, String> {
    let manifest = parse_inner(json, true)?;
    validate(&manifest)?;
    Ok(manifest)
}

/// Parses without dropping unknown hooks and without validating, so every
/// problem can be reported at once (what the plugin info box wants) instead of
/// only the first fatal one.
pub fn parse_lenient(json: &str) -> Result<Manifest, String> {
    parse_inner(json, false)
}

fn parse_inner(json: &str, drop_unknown_hooks: bool) -> Result<Manifest, String> {
    let value: Value = serde_json::from_str(json).map_err(|e| truncate_chars(&e.to_string(), 256))?;
    let object = value
        .as_object()
        .ok_or_else(|| "manifest must be a JSON object".to_owned())?;

    let get_string = |key: &str| {
        object
            .get(key)
            .and_then(Value::as_str)
            .map(str::to_owned)
            .unwrap_or_default()
    };
    let optional_string = |key: &str, fallback: &str| {
        object
            .get(key)
            .and_then(Value::as_str)
            .map(str::to_owned)
            .unwrap_or_else(|| fallback.to_owned())
    };
    let string_array = |key: &str| -> Vec<String> {
        object
            .get(key)
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default()
    };

    let api_version = match object.get("api_version") {
        None => PLUGIN_API_VERSION,
        Some(value) => value.as_i64().ok_or_else(|| {
            "api_version must be an integer".to_owned()
        })?,
    };

    let mut manifest = Manifest {
        id: get_string("id"),
        name: truncate_chars(&get_string("name"), MAX_NAME_LEN),
        description: truncate_chars(&get_string("description"), MAX_DESCRIPTION_LEN),
        author: truncate_chars(&get_string("author"), MAX_AUTHOR_LEN),
        version: truncate_chars(&optional_string("version", "1.0"), MAX_VERSION_LEN),
        min_app_version: {
            let explicit = object.get("min_app_version").and_then(Value::as_str);
            match explicit {
                Some(value) => value.to_owned(),
                None => get_string("min_version"),
            }
        },
        api_version,
        entry: optional_string("entry", "main.py"),
        hooks: {
            let hooks = string_array("hooks");
            if drop_unknown_hooks {
                hooks.into_iter().filter(|hook| is_known_hook(hook)).collect()
            } else {
                hooks
            }
        },
        permissions: string_array("permissions"),
        requirements: string_array("requirements"),
        icon: get_string("icon"),
        platforms: Vec::new(),
    };

    let platforms = string_array("platforms");
    if platforms.is_empty() && object.get("desktop_only").and_then(Value::as_bool) == Some(true) {
        manifest.platforms.push("desktop".to_owned());
    } else {
        manifest.platforms = platforms;
    }

    Ok(manifest)
}

/// `permissions: ["ui.*"]` grants `ui.toast`; everything else is exact.
pub fn has_permission(manifest: &Manifest, permission: &str) -> bool {
    manifest.permissions.iter().any(|granted| {
        granted == permission
            || granted
                .strip_suffix(".*")
                .is_some_and(|prefix| permission.starts_with(prefix))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const VALID: &str = r#"{
        "id": "com.example.plugin",
        "name": "Example",
        "description": "Does things",
        "author": "@someone",
        "version": "1.2.3",
        "api_version": 1,
        "entry": "main.py",
        "hooks": ["on_message", "on_message_sending", "on_made_up"],
        "permissions": ["ui.toast", "storage.*"],
        "platforms": ["desktop"]
    }"#;

    #[test]
    fn parses_a_valid_manifest() {
        let m = parse(VALID).expect("valid");
        assert_eq!(m.id, "com.example.plugin");
        assert_eq!(m.version, "1.2.3");
        assert_eq!(m.hooks, ["on_message", "on_message_sending"]);
        assert!(runs_here(&m));
        assert!(problems(&m).is_empty());
    }

    #[test]
    fn lenient_parse_keeps_unknown_hooks_for_reporting() {
        let m = parse_lenient(VALID).expect("parsed");
        assert!(m.hooks.iter().any(|hook| hook == "on_made_up"));
        assert!(
            problems(&m)
                .iter()
                .any(|problem| problem.contains("unknown hook: on_made_up"))
        );
    }

    #[test]
    fn defaults_match_the_cpp_side() {
        let m = parse(r#"{"id": "abc", "name": "N"}"#).expect("valid");
        assert_eq!(m.entry, "main.py");
        assert_eq!(m.version, "1.0");
        assert_eq!(m.api_version, 1);
        assert!(m.platforms.is_empty());
    }

    #[test]
    fn rejects_bad_ids() {
        for id in ["", "1abc", "a", "a b", "a/bb", &"a".repeat(65)] {
            let json = format!(r#"{{"id": "{id}", "name": "N"}}"#);
            assert!(parse(&json).is_err(), "id {id:?} should be rejected");
        }
        for id in ["ab", "a.b-c_1", &"a".repeat(64)] {
            let json = format!(r#"{{"id": "{id}", "name": "N"}}"#);
            assert!(parse(&json).is_ok(), "id {id:?} should be accepted");
        }
    }

    #[test]
    fn truncates_long_fields() {
        let long_name = "n".repeat(500);
        let json = format!(r#"{{"id": "abc", "name": "{long_name}"}}"#);
        let m = parse(&json).expect("valid");
        assert_eq!(m.name.chars().count(), MAX_NAME_LEN);
    }

    #[test]
    fn rejects_unknown_api_and_entry_traversal() {
        assert!(parse(r#"{"id":"abc","name":"N","api_version":2}"#).is_err());
        assert!(parse(r#"{"id":"abc","name":"N","api_version":0}"#).is_err());
        assert!(parse(r#"{"id":"abc","name":"N","entry":"../main.py"}"#).is_err());
        assert!(parse(r#"{"id":"abc","name":"N","entry":""}"#).is_err());
    }

    #[test]
    fn skips_android_only_plugins_here() {
        let m = parse_lenient(r#"{"id":"abc","name":"N","platforms":["android"]}"#).expect("parsed");
        assert!(!runs_here(&m));
        assert!(problems(&m).iter().any(|p| p.contains("not this platform")));
        assert!(parse(r#"{"id":"abc","name":"N","platforms":["android"]}"#).is_err());
    }

    #[test]
    fn desktop_only_legacy_flag() {
        let m = parse(r#"{"id":"abc","name":"N","desktop_only":true}"#).expect("valid");
        assert_eq!(m.platforms, ["desktop"]);
    }

    #[test]
    fn permission_wildcards() {
        let m = parse(VALID).expect("valid");
        assert!(has_permission(&m, "ui.toast"));
        assert!(has_permission(&m, "storage.read"));
        assert!(!has_permission(&m, "ui.alert"));
        assert!(!has_permission(&m, "network"));
    }

    #[test]
    fn malformed_json_is_an_error() {
        assert!(parse("{not json").is_err());
        assert!(parse("[]").is_err());
    }
}
