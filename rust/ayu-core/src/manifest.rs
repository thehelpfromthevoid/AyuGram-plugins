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

/// nlohmann's `type_name()` names, so a refusal reads the same in both hosts.
/// Verified against the real header: `7` throws "cannot use value() with
/// number" and `true` throws "... with boolean".
fn json_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

/// The double→int narrowing the client's MSVC x64 build performs.
///
/// `cvttsd2si` truncates toward zero and, when the truncated value does not fit
/// in 32 bits, yields `0x80000000` instead of saturating. So `1e10`, `1e300`
/// and `2147483648.0` all load as `-2147483648`, while `2147483647.5`
/// truncates to `2147483647` and is kept. Rust's own `as` saturates, which is
/// why this exists. Observed from the compiled header (canon_parse.txt); the
/// C++ operation is formally undefined for these inputs, so this matches the
/// shipped compiler rather than the language standard.
fn float_to_c_int(value: f64) -> i32 {
    if !value.is_finite() {
        return i32::MIN;
    }
    let truncated = value.trunc();
    if truncated < i32::MIN as f64 || truncated > i32::MAX as f64 {
        return i32::MIN;
    }
    truncated as i32
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
    if rest.is_empty() || rest.len() > 63 {
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
    // `ParseManifestJson` calls `j.contains(key)` (false for a non-object) and
    // then, unconditionally, `j.value("api_version", kPluginApiVersion)`.
    // nlohmann's `value()` throws `type_error.306` when the document is not an
    // object, and the host catches that and refuses the manifest. A document
    // like `[]` is therefore *not* answered with a bad-id problem; it is
    // answered with that exception, and the wording is reproduced here so both
    // implementations say the same thing. Verified by compiling the real header
    // (see `canon_notobject.txt`).
    let object = value.as_object().ok_or_else(|| {
        format!(
            "[json.exception.type_error.306] cannot use value() with {}",
            json_type_name(&value)
        )
    })?;

    let get_string = |key: &str| {
        // `getStr` on the C++ side requires `is_string()`: a number, boolean or
        // null is replaced by the default rather than coerced, so a manifest
        // with `"name": 123` has an empty name and is refused for that, not for
        // holding the string "123".
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

    // `api_version` is an `int` in the C++ manifest and nlohmann's
    // `get<int>()` narrows rather than widening. The narrowing differs by which
    // storage nlohmann picked for the JSON number, and all three behaviours
    // below were observed from the compiled header (canon_parse.txt):
    //   * an integer that fits int64/uint64 wraps with a truncating cast -
    //     `5000000000` loads as `705032704`, `2147483648` as `-2147483648`;
    //   * a double (a fractional literal, or an integer too large for uint64)
    //     goes through the platform conversion, which on the MSVC x64 build the
    //     client ships does *not* saturate - `1e10` and `1e300` both load as
    //     INT_MIN. Rust's own `as` saturates, hence `float_to_c_int` below.
    let api_version = match object.get("api_version") {
        None => PLUGIN_API_VERSION as i32,
        Some(Value::Number(number)) => {
            if let Some(integer) = number.as_i64() {
                integer as i32
            } else if let Some(unsigned) = number.as_u64() {
                unsigned as i32
            } else if let Some(float) = number.as_f64() {
                float_to_c_int(float)
            } else {
                return Err("api_version must be a number".to_owned());
            }
        }
        // nlohmann's `get<int>()` casts a boolean; a string, null, array or
        // object throws with `type_error.302`, which is the wording the host
        // reports.
        Some(Value::Bool(value)) => i32::from(*value),
        Some(other) => {
            return Err(format!(
                "[json.exception.type_error.302] type must be number, but is {}",
                json_type_name(other)
            ))
        }
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
        api_version: i64::from(api_version),
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

    // The condition is `platforms.is_array()`, not "platforms produced entries".
    // `"platforms": []` is an array, so the `else` branch - and therefore
    // `desktop_only` - is never reached, while `"platforms": "desktop"` is not
    // an array and falls through to the boolean. And `desktop_only` is read
    // with `value()`, i.e. `get<bool>()`, which *throws* `type_error.302` for a
    // non-boolean; the host reports that refusal. Both behaviours are read off
    // a compiled transcription of ParseManifestJson, not inferred.
    if let Some(array) = object.get("platforms").and_then(Value::as_array) {
        manifest.platforms = array
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect();
    } else {
        match object.get("desktop_only") {
            None => {}
            Some(Value::Bool(true)) => manifest.platforms.push("desktop".to_owned()),
            Some(Value::Bool(false)) => {}
            Some(other) => {
                return Err(format!(
                    "[json.exception.type_error.302] type must be boolean, but is {}",
                    json_type_name(other)
                ))
            }
        }
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
    fn an_empty_platforms_array_suppresses_desktop_only() {
        // The C++ branch is `contains("platforms") && is_array()`, so an *empty*
        // array still counts as present: the `else if (desktop_only)` is never
        // reached. Treating "produced no entries" as "absent" would give this
        // manifest `["desktop"]` where the client gives `[]`. Read off a
        // compiled transcription of ParseManifestJson (canon_parse.txt).
        let m = parse_lenient(r#"{"id":"abc","name":"N","platforms":[],"desktop_only":true}"#)
            .expect("parses");
        assert!(m.platforms.is_empty(), "{:?}", m.platforms);

        // A non-array `platforms` is not present as an array, so it falls
        // through and desktop_only does apply.
        let m = parse_lenient(r#"{"id":"abc","name":"N","platforms":"desktop","desktop_only":true}"#)
            .expect("parses");
        assert_eq!(m.platforms, ["desktop"]);

        // ...but the desktop_only read itself is `value(..., false)`, i.e.
        // get<bool>(), which throws for a non-boolean. The client refuses it.
        let error = parse_lenient(
            r#"{"id":"abc","name":"N","platforms":"desktop","desktop_only":"yes"}"#,
        )
        .expect_err("must be refused");
        assert!(
            error.contains("type_error.302") && error.contains("boolean"),
            "{error}"
        );
        for document in [
            r#"{"id":"abc","name":"N","desktop_only":"yes"}"#,
            r#"{"id":"abc","name":"N","desktop_only":1}"#,
            r#"{"id":"abc","name":"N","desktop_only":null}"#,
            r#"{"id":"abc","name":"N","desktop_only":[]}"#,
        ] {
            let error = parse_lenient(document).expect_err("must be refused");
            assert!(
                error.contains("type_error.302") && error.contains("boolean"),
                "{document} -> {error}"
            );
        }
    }

    #[test]
    fn api_version_narrows_to_int_like_static_cast() {
        // `apiVersion` is an `int` and nlohmann's `get<int>()` narrows with a
        // plain cast, so out-of-range values wrap instead of being refused:
        // 5000000000 -> 705032704, 2147483648 -> -2147483648, and
        // -2147483649 -> 2147483647. Values observed from the real header.
        for (document, expected) in [
            (r#"{"id":"abc","name":"N","api_version":5000000000}"#, 705032704),
            (r#"{"id":"abc","name":"N","api_version":2147483648}"#, -2147483648),
            (r#"{"id":"abc","name":"N","api_version":-2147483649}"#, 2147483647),
        ] {
            let m = parse_lenient(document).expect("parses");
            assert_eq!(m.api_version, expected, "{document}");
        }
    }

    #[test]
    fn api_version_double_clamps_to_int_min_out_of_range() {
        // A fractional literal - or an integer too large for uint64 - is stored
        // by nlohmann as a double, and the MSVC x64 conversion the client ships
        // clamps to INT_MIN rather than saturating or wrapping: `1e10`, `1e300`
        // and `2147483648.0` all load as -2147483648, and `2147483647.5`
        // truncates to 2147483647. Rust's `as` would saturate to INT_MAX, so
        // this is the case the two implementations used to disagree on (caught
        // by triple_check.py, not by native-vs-python parity alone).
        for (document, expected) in [
            (r#"{"id":"abc","name":"N","api_version":1e10}"#, -2147483648),
            (r#"{"id":"abc","name":"N","api_version":1e300}"#, -2147483648),
            (r#"{"id":"abc","name":"N","api_version":-1e300}"#, -2147483648),
            (r#"{"id":"abc","name":"N","api_version":2147483648.0}"#, -2147483648),
            (r#"{"id":"abc","name":"N","api_version":2147483647.5}"#, 2147483647),
            (r#"{"id":"abc","name":"N","api_version":-0.5}"#, 0),
            (r#"{"id":"abc","name":"N","api_version":100.0}"#, 100),
            (
                r#"{"id":"abc","name":"N","api_version":18446744073709551616}"#,
                -2147483648,
            ),
            (
                r#"{"id":"abc","name":"N","api_version":10000000000000000000000000000000}"#,
                -2147483648,
            ),
        ] {
            let m = parse_lenient(document).expect("parses");
            assert_eq!(m.api_version, expected, "{document}");
        }
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
    }

    #[test]
    fn a_json_document_that_is_not_an_object_is_refused_by_value() {
        // `ParseManifestJson` reaches `j.value("api_version", 1)` even for a
        // document whose fields all take their defaults, and nlohmann's
        // `value()` throws `type_error.306` when the document is not an object.
        // That throw is what the host reports - verified by compiling the real
        // header, not inferred - so the answer is a refusal, not a bad-id
        // problem, and the wording matches nlohmann's.
        for (document, kind) in [
            ("[]", "array"),
            ("\"a string\"", "string"),
            ("null", "null"),
            ("7", "number"),
            ("true", "boolean"),
        ] {
            let error = parse_lenient(document).expect_err("must be refused");
            assert!(
                error.contains("type_error.306") && error.contains(kind),
                "{document} -> {error}"
            );
        }
    }

    #[test]
    fn api_version_casts_numbers_and_booleans_like_nlohmann() {
        // `j.value("api_version", 1)` runs through nlohmann's `get<int>()`,
        // which casts number_float and boolean; only a string/null/array/object
        // throws. Refusing `1.0` would reject a manifest the client accepts.
        // The values below were observed by running the real header.
        assert_eq!(
            parse_lenient(r#"{"id":"abc","name":"N","api_version":1.5}"#)
                .expect("parses")
                .api_version,
            1
        );
        assert_eq!(
            parse_lenient(r#"{"id":"abc","name":"N","api_version":1.0}"#)
                .expect("parses")
                .api_version,
            1
        );
        assert_eq!(
            parse_lenient(r#"{"id":"abc","name":"N","api_version":1.9}"#)
                .expect("parses")
                .api_version,
            1
        );
        assert_eq!(
            parse_lenient(r#"{"id":"abc","name":"N","api_version":true}"#)
                .expect("parses")
                .api_version,
            1
        );
        // false casts to 0 and then fails the positive check, not the type check
        let m = parse_lenient(r#"{"id":"abc","name":"N","api_version":false}"#).expect("parses");
        assert_eq!(m.api_version, 0);
        assert!(problems(&m)
            .iter()
            .any(|p| p == "api_version must be a positive number"));

        // A string, null, array or object is refused with nlohmann's own text.
        for (document, kind) in [
            (r#"{"id":"abc","name":"N","api_version":"1"}"#, "string"),
            (r#"{"id":"abc","name":"N","api_version":null}"#, "null"),
            (r#"{"id":"abc","name":"N","api_version":[1]}"#, "array"),
            (r#"{"id":"abc","name":"N","api_version":{}}"#, "object"),
        ] {
            let error = parse_lenient(document).expect_err("must be refused");
            assert!(
                error.contains("type_error.302") && error.contains(kind),
                "{document} -> {error}"
            );
        }
    }

    #[test]
    fn string_fields_of_the_wrong_type_take_the_default() {
        // `getStr` requires `is_string()`; a number is not stringified.
        let m = parse_lenient(r#"{"id":"abc","name":123}"#).expect("parses");
        assert!(m.name.is_empty());
        assert!(problems(&m).iter().any(|p| p == "name must be 1..100 chars"));

        // ...so a null entry falls back to "main.py" instead of failing.
        let m = parse_lenient(r#"{"id":"abc","name":"N","entry":null}"#).expect("parses");
        assert_eq!(m.entry, "main.py");
        assert!(problems(&m).is_empty());

        // a non-string platform is dropped, not stringified into a bad one
        let m = parse_lenient(r#"{"id":"abc","name":"N","platforms":[1]}"#).expect("parses");
        assert!(m.platforms.is_empty());
        assert!(problems(&m).is_empty());
    }

    #[test]
    fn long_fields_are_truncated_before_the_length_check() {
        // ParseManifestJson left()s name and description first, so an
        // over-long field is never a problem - it is silently shortened.
        let long_name = "n".repeat(500);
        let m = parse_lenient(&format!(r#"{{"id":"abc","name":"{long_name}"}}"#)).expect("parses");
        assert_eq!(m.name.chars().count(), MAX_NAME_LEN);
        assert!(problems(&m).is_empty());

        let long_text = "d".repeat(9000);
        let m = parse_lenient(&format!(
            r#"{{"id":"abc","name":"N","description":"{long_text}"}}"#
        ))
        .expect("parses");
        assert_eq!(m.description.chars().count(), MAX_DESCRIPTION_LEN);
        assert!(problems(&m).is_empty());
    }
}
