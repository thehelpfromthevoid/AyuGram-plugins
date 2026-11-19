//! Per-plugin settings stored next to the plugins, in the exact layout the
//! Python `settings_store.py` already writes:
//!
//! ```json
//! { "id": "<plugin id>", "version": 1, "settings": { ... } }
//! ```
//!
//! Writes go through a `.tmp` file in the same directory and an atomic rename,
//! so a crash mid-write cannot leave a truncated settings file behind.

use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde_json::Value;

/// `sanitize` from `settings_store.py`: keep alphanumerics and `._-`, replace
/// everything else with `_`, then strip leading/trailing `.` and `_`.
pub fn sanitize_id(raw: &str) -> String {
    let mapped: String = raw
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || matches!(c, '.' | '_' | '-') {
                c
            } else {
                '_'
            }
        })
        .collect();
    let trimmed = mapped.trim_matches(|c| c == '.' || c == '_');
    if trimmed.is_empty() {
        "plugin".to_owned()
    } else {
        trimmed.to_owned()
    }
}

#[derive(Debug, Clone)]
pub struct SettingsStore {
    root: PathBuf,
}

impl SettingsStore {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn path_for(&self, plugin_id: &str) -> PathBuf {
        self.root
            .join(format!("{}.settings.json", sanitize_id(plugin_id)))
    }

    /// Reads the settings map. A missing or unreadable file is an empty map,
    /// which is what the Python store does: settings must never block startup.
    pub fn read(&self, plugin_id: &str) -> BTreeMap<String, Value> {
        self.read_reported(plugin_id).0
    }

    /// Same as [`read`], plus `1` when the file exists but could not be read or
    /// parsed, so the caller can log it the way the Python store does.
    pub fn read_reported(&self, plugin_id: &str) -> (BTreeMap<String, Value>, i32) {
        let path = self.path_for(plugin_id);
        // The Python store starts with `path.is_file()`, and that is the
        // contract: a missing path is an empty map and *not* an error, and
        // neither is a path that exists but is not a regular file. Reading the
        // directory case as a failure reported an error for a file the Python
        // store opens without complaint.
        if !path.is_file() {
            return (BTreeMap::new(), 0);
        }
        let text = match fs::read_to_string(&path) {
            Ok(text) => text,
            Err(error) => {
                let status = i32::from(error.kind() != io::ErrorKind::NotFound);
                return (BTreeMap::new(), status);
            }
        };
        // Python reads these files with `utf-8-sig`, so a byte order mark left
        // by an editor is not an error there and must not become one here:
        // returning `{}` for a file that holds settings reads to the plugin as
        // "my settings were wiped".
        let text = text.strip_prefix('\u{feff}').unwrap_or(&text);
        let Ok(value) = serde_json::from_str::<Value>(text) else {
            return (BTreeMap::new(), 1);
        };
        let object = match value.get("settings") {
            // A `settings` key that is present but not an object is a broken
            // document, not a legacy top-level map. Falling through to the
            // document would hand the plugin a map containing the document's
            // own `settings` field, and the next save would write that nested
            // map back - one bad file, then a permanently corrupted one.
            Some(inner) => match inner.as_object() {
                Some(inner) => inner,
                None => return (BTreeMap::new(), 1),
            },
            None => match value.as_object() {
                Some(top) => top,
                None => return (BTreeMap::new(), 1),
            },
        };
        (
            object
                .iter()
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect(),
            0,
        )
    }

    /// Writes the settings map atomically. Returns an error only for real I/O
    /// failures; the caller decides whether to log them.
    pub fn write(&self, plugin_id: &str, settings: &BTreeMap<String, Value>) -> io::Result<()> {
        fs::create_dir_all(&self.root)?;
        let path = self.path_for(plugin_id);
        let payload = serde_json::json!({
            "id": sanitize_id(plugin_id),
            "version": 1,
            "settings": settings,
        });
        let text = serde_json::to_string_pretty(&payload)
            .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
        let tmp = path.with_extension("tmp");
        fs::write(&tmp, text)?;
        match fs::rename(&tmp, &path) {
            Ok(()) => Ok(()),
            Err(error) => {
                let _ = fs::remove_file(&tmp);
                Err(error)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (tempfile::TempDir, SettingsStore) {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = SettingsStore::new(dir.path());
        (dir, store)
    }

    #[test]
    fn sanitize_follows_the_python_rules() {
        assert_eq!(sanitize_id("com.example.plugin"), "com.example.plugin");
        assert_eq!(sanitize_id("my plugin"), "my_plugin");
        assert_eq!(sanitize_id("../evil"), "evil");
        assert_eq!(sanitize_id("._."), "plugin");
        assert_eq!(sanitize_id(""), "plugin");
        assert_eq!(sanitize_id("плагин"), "плагин");
    }

    #[test]
    fn roundtrips_settings() {
        let (_dir, store) = store();
        let mut settings = BTreeMap::new();
        settings.insert("enabled".to_owned(), Value::Bool(true));
        settings.insert("name".to_owned(), Value::String("hello".to_owned()));
        settings.insert("count".to_owned(), Value::from(3));

        store.write("demo", &settings).expect("write");
        let read = store.read("demo");
        assert_eq!(read, settings);
    }

    #[test]
    fn writes_the_documented_file_layout() {
        let (_dir, store) = store();
        let mut settings = BTreeMap::new();
        settings.insert("k".to_owned(), Value::from(1));
        store.write("demo", &settings).expect("write");

        let text = fs::read_to_string(store.path_for("demo")).expect("file");
        let value: Value = serde_json::from_str(&text).expect("json");
        assert_eq!(value["id"], "demo");
        assert_eq!(value["version"], 1);
        assert_eq!(value["settings"]["k"], 1);
        assert!(!text.ends_with('\n'));
    }

    #[test]
    fn overwrites_existing_file() {
        let (_dir, store) = store();
        let mut first = BTreeMap::new();
        first.insert("k".to_owned(), Value::from("one"));
        store.write("demo", &first).expect("write");

        let mut second = BTreeMap::new();
        second.insert("k".to_owned(), Value::from("two"));
        store.write("demo", &second).expect("overwrite");

        assert_eq!(store.read("demo"), second);
        assert!(!store.path_for("demo").with_extension("tmp").exists());
    }

    #[test]
    fn missing_and_broken_files_read_as_empty() {
        let (_dir, store) = store();
        assert!(store.read("missing").is_empty());

        let path = store.path_for("broken");
        fs::write(&path, "{not json").expect("write");
        assert!(store.read("broken").is_empty());
    }

    #[test]
    fn reads_legacy_top_level_maps() {
        let (_dir, store) = store();
        let path = store.path_for("legacy");
        fs::write(&path, r#"{"a": 1, "b": "two"}"#).expect("write");
        let settings = store.read("legacy");
        assert_eq!(settings["a"], 1);
        assert_eq!(settings["b"], "two");
    }

    #[test]
    fn tolerates_a_utf8_bom() {
        let (_dir, store) = store();
        let path = store.path_for("bom");
        fs::write(&path, "\u{feff}{\"settings\":{\"a\":1}}").expect("write");
        let (settings, status) = store.read_reported("bom");
        assert_eq!(status, 0);
        assert_eq!(settings["a"], 1);
    }

    #[test]
    fn settings_key_that_is_not_an_object_is_a_failure() {
        // The Python store returns `({}, True)` here, and it gets first refusal
        // in `settings_store._read_file` only when the native library is
        // missing. If the native side answered `({"settings": null}, 0)`
        // instead, the plugin would be handed a map holding the document's own
        // field, and its next save would write `settings.settings` back.
        let (_dir, store) = store();
        for body in [
            r#"{"settings": null}"#,
            r#"{"settings": null, "other": 2}"#,
            r#"{"settings": 5}"#,
            r#"{"settings": []}"#,
        ] {
            fs::write(store.path_for("bad"), body).expect("write");
            let (settings, status) = store.read_reported("bad");
            assert!(settings.is_empty(), "{body} -> {settings:?}");
            assert_eq!(status, 1, "{body} must report an error");
        }
    }

    #[test]
    fn a_directory_is_not_a_readable_settings_file() {
        // `path.is_file()` fails for a directory, and the Python store treats
        // that exactly like a missing file: no settings, no error. Mapping the
        // OS error to status 1 reported a failure for a path Python opens
        // without complaint.
        let (_dir, store) = store();
        fs::create_dir_all(store.path_for("adir")).expect("dir");
        let (settings, status) = store.read_reported("adir");
        assert!(settings.is_empty());
        assert_eq!(status, 0);
    }
}
