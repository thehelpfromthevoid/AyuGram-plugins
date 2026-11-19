//! C ABI for the fast layer.
//!
//! The library is loaded by the Python host through `ctypes` today and can be
//! linked or delay-loaded by the C++ client later. Rules:
//!
//! * Every string crossing the boundary is UTF-8.
//! * Returned buffers are `(pointer, length)` and are **not** NUL-terminated.
//!   Release them with `ayu_bytes_free(pointer, length)`.
//! * On failure functions return NULL/0/-1 and `ayu_last_error()` describes the
//!   reason. The pointer is owned by the library, valid until the next failing
//!   call on the same thread.

use std::cell::RefCell;
use std::ffi::{c_char, c_int, CStr, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::PathBuf;
use std::ptr;
use std::slice;
use std::str;

use ayu_core::coerce;
use ayu_core::filter::WordFilter;
use ayu_core::hash;
use ayu_core::hooks;
use ayu_core::manifest;
use ayu_core::settings::{sanitize_id, SettingsStore};
use ayu_core::text;

thread_local! {
    static LAST_ERROR: RefCell<Option<CString>> = const { RefCell::new(None) };
}

fn clear_error() {
    LAST_ERROR.with(|slot| *slot.borrow_mut() = None);
}

fn set_error(message: impl Into<String>) {
    let text: String = message.into().replace('\0', " ");
    LAST_ERROR.with(|slot| *slot.borrow_mut() = CString::new(text).ok());
}

fn guard<T>(fallback: T, body: impl FnOnce() -> Result<T, String>) -> T {
    clear_error();
    match catch_unwind(AssertUnwindSafe(body)) {
        Ok(Ok(value)) => value,
        Ok(Err(error)) => {
            set_error(error);
            fallback
        }
        Err(_) => {
            set_error("panic inside ayu_fast");
            fallback
        }
    }
}

fn into_buffer(text: String) -> (*mut c_char, usize) {
    let mut bytes = text.into_bytes();
    let len = bytes.len();
    let pointer = bytes.as_mut_ptr() as *mut c_char;
    std::mem::forget(bytes);
    (pointer, len)
}

unsafe fn borrowed_str<'a>(
    pointer: *const c_char,
    len: usize,
    what: &str,
) -> Result<&'a str, String> {
    if pointer.is_null() {
        return Err(format!("{what} is null"));
    }
    let bytes = slice::from_raw_parts(pointer as *const u8, len);
    str::from_utf8(bytes).map_err(|_| format!("{what} is not valid UTF-8"))
}

unsafe fn borrowed_path(pointer: *const c_char, len: usize, what: &str) -> Result<PathBuf, String> {
    Ok(PathBuf::from(borrowed_str(pointer, len, what)?))
}

/// Compiled word replacement set.
pub struct AyuFilter {
    inner: WordFilter,
}

/// Builds a filter from NUL-terminated UTF-8 needle/replacement pairs.
///
/// # Safety
/// `needles` and `replacements` must point to `count` valid C strings.
#[no_mangle]
pub unsafe extern "C" fn ayu_filter_new(
    needles: *const *const c_char,
    replacements: *const *const c_char,
    count: usize,
) -> *mut AyuFilter {
    guard(ptr::null_mut(), || {
        let mut pairs: Vec<(String, String)> = Vec::with_capacity(count);
        if count > 0 {
            if needles.is_null() || replacements.is_null() {
                return Err("needle list is null".to_owned());
            }
            let needle_ptrs = slice::from_raw_parts(needles, count);
            let replacement_ptrs = slice::from_raw_parts(replacements, count);
            for index in 0..count {
                let needle = CStr::from_ptr(needle_ptrs[index])
                    .to_str()
                    .map_err(|_| format!("needle {index} is not valid UTF-8"))?;
                let replacement = CStr::from_ptr(replacement_ptrs[index])
                    .to_str()
                    .map_err(|_| format!("replacement {index} is not valid UTF-8"))?;
                pairs.push((needle.to_owned(), replacement.to_owned()));
            }
        }
        let inner = WordFilter::new(&pairs).map_err(|error| error.to_string())?;
        Ok(Box::into_raw(Box::new(AyuFilter { inner })))
    })
}

/// # Safety
/// `filter` must come from `ayu_filter_new` and not be used afterwards.
#[no_mangle]
pub unsafe extern "C" fn ayu_filter_free(filter: *mut AyuFilter) {
    if filter.is_null() {
        return;
    }
    drop(Box::from_raw(filter));
}

/// Rewrites `text` and returns a newly allocated buffer of `*out_len` bytes.
/// `*changed` is set to 1 when at least one needle matched.
///
/// # Safety
/// `filter` must be valid, `text` must point to `text_len` bytes,
/// `out_len` and `changed` may be null.
#[no_mangle]
pub unsafe extern "C" fn ayu_filter_replace(
    filter: *const AyuFilter,
    text: *const c_char,
    text_len: usize,
    out_len: *mut usize,
    changed: *mut c_int,
) -> *mut c_char {
    guard(ptr::null_mut(), || {
        if !out_len.is_null() {
            *out_len = 0;
        }
        if !changed.is_null() {
            *changed = 0;
        }
        let filter = filter.as_ref().ok_or_else(|| "filter is null".to_owned())?;
        let text = borrowed_str(text, text_len, "text")?;
        let (output, was_changed) = filter.inner.replace(text);
        if !out_len.is_null() {
            *out_len = output.len();
        }
        if !changed.is_null() {
            *changed = c_int::from(was_changed);
        }
        Ok(into_buffer(output).0)
    })
}

/// 1 when a needle occurs, 0 when it does not, -1 on error.
///
/// # Safety
/// `filter` must be valid, `text` must point to `text_len` bytes.
#[no_mangle]
pub unsafe extern "C" fn ayu_filter_is_match(
    filter: *const AyuFilter,
    text: *const c_char,
    text_len: usize,
) -> c_int {
    guard(-1, || {
        let filter = filter.as_ref().ok_or_else(|| "filter is null".to_owned())?;
        let text = borrowed_str(text, text_len, "text")?;
        Ok(c_int::from(filter.inner.is_match(text)))
    })
}

/// Returns a JSON array of manifest problems, `[]` when the manifest is fine.
///
/// # Safety
/// `json` must point to `json_len` bytes.
#[no_mangle]
pub unsafe extern "C" fn ayu_manifest_problems(
    json: *const c_char,
    json_len: usize,
    out_len: *mut usize,
) -> *mut c_char {
    guard(ptr::null_mut(), || {
        if !out_len.is_null() {
            *out_len = 0;
        }
        let text = borrowed_str(json, json_len, "manifest json")?;
        let problems = match manifest::parse_lenient(text) {
            Ok(parsed) => manifest::problems(&parsed),
            Err(error) => vec![error],
        };
        let encoded =
            serde_json::to_string(&problems).map_err(|error| error.to_string())?;
        if !out_len.is_null() {
            *out_len = encoded.len();
        }
        Ok(into_buffer(encoded).0)
    })
}

/// 1 when the manifest parses and validates, 0 otherwise.
///
/// # Safety
/// `json` must point to `json_len` bytes.
#[no_mangle]
pub unsafe extern "C" fn ayu_manifest_is_valid(
    json: *const c_char,
    json_len: usize,
) -> c_int {
    guard(0, || {
        let text = borrowed_str(json, json_len, "manifest json")?;
        Ok(c_int::from(manifest::parse(text).is_ok()))
    })
}

/// Reads `<root>/<id>.settings.json` and returns its settings object as JSON.
/// A missing file yields `{}` with `*out_status = 0`; a file that exists but
/// cannot be read or parsed yields `{}` with `*out_status = 1` so the caller can
/// log it, exactly like the Python store does.
///
/// # Safety
/// `root` and `plugin_id` must point to their byte lengths.
#[no_mangle]
pub unsafe extern "C" fn ayu_settings_read(
    root: *const c_char,
    root_len: usize,
    plugin_id: *const c_char,
    id_len: usize,
    out_len: *mut usize,
    out_status: *mut c_int,
) -> *mut c_char {
    guard(ptr::null_mut(), || {
        if !out_len.is_null() {
            *out_len = 0;
        }
        if !out_status.is_null() {
            *out_status = 0;
        }
        let root = borrowed_path(root, root_len, "settings root")?;
        let id = borrowed_str(plugin_id, id_len, "plugin id")?;
        let store = SettingsStore::new(root);
        let (values, status) = store.read_reported(id);
        if !out_status.is_null() {
            *out_status = status;
        }
        let encoded =
            serde_json::to_string(&values).map_err(|error| error.to_string())?;
        if !out_len.is_null() {
            *out_len = encoded.len();
        }
        Ok(into_buffer(encoded).0)
    })
}

/// Writes the settings object atomically. The JSON may be a bare object or a
/// full `{ "id", "version", "settings" }` document.
///
/// # Safety
/// All pointers must point to their byte lengths.
#[no_mangle]
pub unsafe extern "C" fn ayu_settings_write(
    root: *const c_char,
    root_len: usize,
    plugin_id: *const c_char,
    id_len: usize,
    json: *const c_char,
    json_len: usize,
) -> c_int {
    guard(-1, || {
        let root = borrowed_path(root, root_len, "settings root")?;
        let id = borrowed_str(plugin_id, id_len, "plugin id")?;
        let text = borrowed_str(json, json_len, "settings json")?;
        let value: serde_json::Value =
            serde_json::from_str(text).map_err(|error| error.to_string())?;
        let object = value
            .get("settings")
            .and_then(serde_json::Value::as_object)
            .or_else(|| value.as_object())
            .ok_or_else(|| "settings must be a JSON object".to_owned())?;
        let values = object
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect();
        let store = SettingsStore::new(root);
        store
            .write(id, &values)
            .map_err(|error| error.to_string())?;
        Ok(0)
    })
}

/// Sanitized plugin id used for file names.
///
/// # Safety
/// `id` must point to `id_len` bytes.
#[no_mangle]
pub unsafe extern "C" fn ayu_sanitize_id(
    id: *const c_char,
    id_len: usize,
    out_len: *mut usize,
) -> *mut c_char {
    guard(ptr::null_mut(), || {
        if !out_len.is_null() {
            *out_len = 0;
        }
        let id = borrowed_str(id, id_len, "plugin id")?;
        let sanitized = sanitize_id(id);
        if !out_len.is_null() {
            *out_len = sanitized.len();
        }
        Ok(into_buffer(sanitized).0)
    })
}

/// FNV-1a of `len` bytes.
///
/// # Safety
/// `data` must point to `len` bytes.
#[no_mangle]
pub unsafe extern "C" fn ayu_fnv1a64(data: *const u8, len: usize) -> u64 {
    guard(0, || {
        if data.is_null() && len > 0 {
            return Err("data is null".to_owned());
        }
        let bytes = if len == 0 {
            &[][..]
        } else {
            slice::from_raw_parts(data, len)
        };
        Ok(hash::fnv1a64(bytes))
    })
}

/// Hashes a file into `*out_hash` and `*out_size`. Returns 0 on success.
///
/// # Safety
/// `path` must point to `path_len` bytes, output pointers may be null.
#[no_mangle]
pub unsafe extern "C" fn ayu_file_fingerprint(
    path: *const c_char,
    path_len: usize,
    out_hash: *mut u64,
    out_size: *mut u64,
) -> c_int {
    guard(-1, || {
        let path = borrowed_path(path, path_len, "path")?;
        let (hash, size) = hash::file_fingerprint(path).map_err(|error| error.to_string())?;
        if !out_hash.is_null() {
            *out_hash = hash;
        }
        if !out_size.is_null() {
            *out_size = size;
        }
        Ok(0)
    })
}

/// Releases a buffer returned by this library.
///
/// # Safety
/// `pointer` must come from this library and `len` must be its reported length.
#[no_mangle]
pub unsafe extern "C" fn ayu_bytes_free(pointer: *mut c_char, len: usize) {
    if pointer.is_null() {
        return;
    }
    drop(Vec::from_raw_parts(pointer as *mut u8, len, len));
}

/// Maps a plugin hook name onto the Desktop event it stands for.
///
/// # Safety
/// `name` must point to `name_len` bytes.
#[no_mangle]
pub unsafe extern "C" fn ayu_hook_name(
    name: *const c_char,
    name_len: usize,
    out_len: *mut usize,
) -> *mut c_char {
    guard(ptr::null_mut(), || {
        if !out_len.is_null() {
            *out_len = 0;
        }
        let name = borrowed_str(name, name_len, "hook name")?;
        let mapped = hooks::desktop_hook_name(name);
        if !out_len.is_null() {
            *out_len = mapped.len();
        }
        Ok(into_buffer(mapped).0)
    })
}

/// 1 when the name is a Telegram update container, 0 otherwise.
///
/// # Safety
/// `name` must point to `name_len` bytes.
#[no_mangle]
pub unsafe extern "C" fn ayu_is_container(name: *const c_char, name_len: usize) -> c_int {
    guard(-1, || {
        let name = borrowed_str(name, name_len, "container name")?;
        Ok(c_int::from(hooks::is_container(name)))
    })
}

/// Picks the Telegram update name an arriving event carries.
///
/// `names_json` is a JSON array of strings; `kind` is 0 user, 1 chat,
/// 2 channel, anything else unknown.
///
/// # Safety
/// Pointers must point to their byte lengths.
#[no_mangle]
pub unsafe extern "C" fn ayu_pick_update_name(
    names_json: *const c_char,
    names_len: usize,
    event: *const c_char,
    event_len: usize,
    kind: i64,
    out_len: *mut usize,
) -> *mut c_char {
    guard(ptr::null_mut(), || {
        if !out_len.is_null() {
            *out_len = 0;
        }
        let names_text = borrowed_str(names_json, names_len, "names json")?;
        let event = borrowed_str(event, event_len, "event name")?;
        let names: Vec<String> =
            serde_json::from_str(names_text).map_err(|error| error.to_string())?;
        let chosen = hooks::pick_update_name(&names, event, kind);
        if !out_len.is_null() {
            *out_len = chosen.len();
        }
        Ok(into_buffer(chosen).0)
    })
}

/// The Telegram method a request object stands for.
///
/// `class_name` is `type(request).__name__`; `values_json` is a JSON array of
/// the string attributes tried in order, with `null` for an absent one.
///
/// # Safety
/// Pointers must point to their byte lengths.
#[no_mangle]
pub unsafe extern "C" fn ayu_telegram_method_name(
    class_name: *const c_char,
    class_len: usize,
    values_json: *const c_char,
    values_len: usize,
    out_len: *mut usize,
) -> *mut c_char {
    guard(ptr::null_mut(), || {
        if !out_len.is_null() {
            *out_len = 0;
        }
        let class_name = borrowed_str(class_name, class_len, "class name")?;
        let values_text = borrowed_str(values_json, values_len, "values json")?;
        let values: Vec<Option<String>> =
            serde_json::from_str(values_text).map_err(|error| error.to_string())?;
        let method = hooks::telegram_method_name(class_name, &values);
        if !out_len.is_null() {
            *out_len = method.len();
        }
        Ok(into_buffer(method).0)
    })
}

/// Coerces a string into a boolean the way `settings_store.coerce` does.
///
/// Returns 1 (true), 0 (false) or 2 when the input is not one this layer can
/// answer for - the caller then runs the Python original, so an input Python
/// alone understands can never be answered wrongly.
///
/// # Safety
/// `raw` must point to `raw_len` bytes.
#[no_mangle]
pub unsafe extern "C" fn ayu_coerce_bool(raw: *const c_char, raw_len: usize) -> c_int {
    guard(-1, || {
        let text = borrowed_str(raw, raw_len, "raw value")?;
        Ok(match coerce::coerce_bool(text) {
            coerce::Coerced::Value(true) => 1,
            coerce::Coerced::Value(false) => 0,
            coerce::Coerced::Decline => 2,
        })
    })
}

/// Coerces a string into an integer. Returns 0 and writes `*out` on success,
/// 1 when the input is declined, -1 on error.
///
/// # Safety
/// `raw` must point to `raw_len` bytes; `out` may not be null.
#[no_mangle]
pub unsafe extern "C" fn ayu_coerce_int(
    raw: *const c_char,
    raw_len: usize,
    out: *mut i64,
) -> c_int {
    guard(-1, || {
        let text = borrowed_str(raw, raw_len, "raw value")?;
        match coerce::coerce_int(text) {
            coerce::Coerced::Value(value) => {
                if !out.is_null() {
                    *out = value;
                }
                Ok(0)
            }
            coerce::Coerced::Decline => Ok(1),
        }
    })
}

/// Coerces a string into a float. Returns 0 and writes `*out` on success,
/// 1 when the input is declined, -1 on error.
///
/// # Safety
/// `raw` must point to `raw_len` bytes; `out` may not be null.
#[no_mangle]
pub unsafe extern "C" fn ayu_coerce_float(
    raw: *const c_char,
    raw_len: usize,
    out: *mut f64,
) -> c_int {
    guard(-1, || {
        let text = borrowed_str(raw, raw_len, "raw value")?;
        match coerce::coerce_float(text) {
            coerce::Coerced::Value(value) => {
                if !out.is_null() {
                    *out = value;
                }
                Ok(0)
            }
            coerce::Coerced::Decline => Ok(1),
        }
    })
}

/// Splits a comma-separated string the way `coerce` does for list defaults.
/// Returns a JSON array, or NULL when the input is declined.
///
/// # Safety
/// Pointers must point to their byte lengths.
#[no_mangle]
pub unsafe extern "C" fn ayu_coerce_list(
    raw: *const c_char,
    raw_len: usize,
    out_len: *mut usize,
) -> *mut c_char {
    guard(ptr::null_mut(), || {
        if !out_len.is_null() {
            *out_len = 0;
        }
        let text = borrowed_str(raw, raw_len, "raw value")?;
        match coerce::coerce_list(text) {
            coerce::Coerced::Value(parts) => {
                let encoded =
                    serde_json::to_string(&parts).map_err(|error| error.to_string())?;
                if !out_len.is_null() {
                    *out_len = encoded.len();
                }
                Ok(into_buffer(encoded).0)
            }
            coerce::Coerced::Decline => Err("declined".to_owned()),
        }
    })
}

/// Parses a command line against a JSON array of prefixes.
///
/// Returns a JSON object `{ "prefix", "raw", "args" }` on a match, the literal
/// `null` when the line is not a command, and NULL with `ayu_last_error()` set
/// to `declined` when the input is not one this layer answers for.
///
/// # Safety
/// Pointers must point to their byte lengths.
#[no_mangle]
pub unsafe extern "C" fn ayu_parse_command(
    text: *const c_char,
    text_len: usize,
    prefixes_json: *const c_char,
    prefixes_len: usize,
    out_len: *mut usize,
) -> *mut c_char {
    guard(ptr::null_mut(), || {
        if !out_len.is_null() {
            *out_len = 0;
        }
        let line = borrowed_str(text, text_len, "command line")?;
        let prefixes_text = borrowed_str(prefixes_json, prefixes_len, "prefixes json")?;
        let prefixes: Vec<String> =
            serde_json::from_str(prefixes_text).map_err(|error| error.to_string())?;
        let encoded = match text::command_parse(line, &prefixes) {
            text::Coerced::Value(None) => "null".to_owned(),
            text::Coerced::Value(Some(cmd)) => serde_json::to_string(&serde_json::json!({
                "prefix": cmd.prefix,
                "raw": cmd.raw,
                "args": cmd.args,
            }))
            .map_err(|error| error.to_string())?,
            text::Coerced::Decline => return Err("declined".to_owned()),
        };
        if !out_len.is_null() {
            *out_len = encoded.len();
        }
        Ok(into_buffer(encoded).0)
    })
}

/// Last failure on this thread, or NULL.
#[no_mangle]
pub extern "C" fn ayu_last_error() -> *const c_char {
    LAST_ERROR.with(|slot| match slot.borrow().as_ref() {
        Some(error) => error.as_ptr(),
        None => ptr::null(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::CString;

    fn c(value: &str) -> CString {
        CString::new(value).expect("cstring")
    }

    unsafe fn take(pointer: *mut c_char, len: usize) -> String {
        assert!(!pointer.is_null(), "null result");
        let bytes = slice::from_raw_parts(pointer as *const u8, len).to_vec();
        ayu_bytes_free(pointer, len);
        String::from_utf8(bytes).expect("utf8")
    }

    #[test]
    fn filter_roundtrip_through_c_abi() {
        let needles = [c("brb"), c("imo")];
        let replacements = [c("be right back"), c("in my opinion")];
        let needle_ptrs: Vec<*const c_char> = needles.iter().map(|s| s.as_ptr()).collect();
        let replacement_ptrs: Vec<*const c_char> =
            replacements.iter().map(|s| s.as_ptr()).collect();

        let filter =
            unsafe { ayu_filter_new(needle_ptrs.as_ptr(), replacement_ptrs.as_ptr(), 2) };
        assert!(!filter.is_null(), "filter build failed");

        let text = c("brb imo");
        let mut out_len = 0usize;
        let mut changed = 0i32;
        let output = unsafe {
            ayu_filter_replace(filter, text.as_ptr(), 7, &mut out_len, &mut changed)
        };
        let output = unsafe { take(output, out_len) };
        assert_eq!(output, "be right back in my opinion");
        assert_eq!(changed, 1);

        let hit = unsafe { ayu_filter_is_match(filter, text.as_ptr(), 7) };
        assert_eq!(hit, 1);

        unsafe { ayu_filter_free(filter) };
    }

    #[test]
    fn filter_errors_are_reported() {
        let filter = unsafe { ayu_filter_new(ptr::null(), ptr::null(), 1) };
        assert!(filter.is_null());
        let error = unsafe { CStr::from_ptr(ayu_last_error()) };
        assert!(error.to_str().unwrap().contains("null"));
    }

    #[test]
    fn manifest_problems_through_c_abi() {
        let json = c(r#"{"id":"abc","name":"N","hooks":["on_made_up"],"platforms":["android"]}"#);
        let mut out_len = 0usize;
        let pointer =
            unsafe { ayu_manifest_problems(json.as_ptr(), json.as_bytes().len(), &mut out_len) };
        let text = unsafe { take(pointer, out_len) };
        let problems: Vec<String> = serde_json::from_str(&text).expect("json");
        assert!(problems.iter().any(|p| p.contains("unknown hook")));
        assert!(problems.iter().any(|p| p.contains("not this platform")));
    }

    #[test]
    fn settings_roundtrip_through_c_abi() {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = c(dir.path().to_str().unwrap());
        let id = c("demo");
        let payload = c(r#"{"enabled":true,"name":"x"}"#);

        let write = unsafe {
            ayu_settings_write(
                root.as_ptr(),
                root.as_bytes().len(),
                id.as_ptr(),
                id.as_bytes().len(),
                payload.as_ptr(),
                payload.as_bytes().len(),
            )
        };
        assert_eq!(write, 0);

        let mut out_len = 0usize;
        let mut out_status = -1i32;
        let pointer = unsafe {
            ayu_settings_read(
                root.as_ptr(),
                root.as_bytes().len(),
                id.as_ptr(),
                id.as_bytes().len(),
                &mut out_len,
                &mut out_status,
            )
        };
        let text = unsafe { take(pointer, out_len) };
        assert_eq!(out_status, 0);
        let value: serde_json::Value = serde_json::from_str(&text).expect("json");
        assert_eq!(value["enabled"], true);
        assert_eq!(value["name"], "x");
    }

    #[test]
    fn missing_settings_file_is_not_an_error() {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = c(dir.path().to_str().unwrap());
        let id = c("absent");
        let mut out_len = 0usize;
        let mut out_status = -1i32;
        let pointer = unsafe {
            ayu_settings_read(
                root.as_ptr(),
                root.as_bytes().len(),
                id.as_ptr(),
                id.as_bytes().len(),
                &mut out_len,
                &mut out_status,
            )
        };
        let text = unsafe { take(pointer, out_len) };
        assert_eq!(text, "{}");
        assert_eq!(out_status, 0);
    }

    #[test]
    fn broken_settings_file_reports_status() {
        let dir = tempfile::tempdir().expect("tempdir");
        std::fs::write(dir.path().join("broken.settings.json"), "{nope").expect("write");
        let root = c(dir.path().to_str().unwrap());
        let id = c("broken");
        let mut out_len = 0usize;
        let mut out_status = 0i32;
        let pointer = unsafe {
            ayu_settings_read(
                root.as_ptr(),
                root.as_bytes().len(),
                id.as_ptr(),
                id.as_bytes().len(),
                &mut out_len,
                &mut out_status,
            )
        };
        let text = unsafe { take(pointer, out_len) };
        assert_eq!(text, "{}");
        assert_eq!(out_status, 1);
    }

    #[test]
    fn invalid_utf8_is_rejected() {
        let bad = [0xffu8, 0xfe];
        let mut out_len = 0usize;
        let pointer = unsafe {
            ayu_manifest_problems(bad.as_ptr() as *const c_char, bad.len(), &mut out_len)
        };
        assert!(pointer.is_null());
        let error = unsafe { CStr::from_ptr(ayu_last_error()) };
        assert!(error.to_str().unwrap().contains("UTF-8"));
    }

    #[test]
    fn hook_name_through_c_abi() {
        let name = c("TL_updateNewMessage");
        let mut out_len = 0usize;
        let pointer =
            unsafe { ayu_hook_name(name.as_ptr(), name.as_bytes().len(), &mut out_len) };
        let mapped = unsafe { take(pointer, out_len) };
        assert_eq!(mapped, "on_message");

        let unknown = c("TL_updateSomethingElse");
        let mut out_len = 0usize;
        let pointer =
            unsafe { ayu_hook_name(unknown.as_ptr(), unknown.as_bytes().len(), &mut out_len) };
        let mapped = unsafe { take(pointer, out_len) };
        assert_eq!(mapped, "");

        let container = c("TL_updates");
        let hit = unsafe { ayu_is_container(container.as_ptr(), container.as_bytes().len()) };
        assert_eq!(hit, 1);
        let not = c("on_message");
        let hit = unsafe { ayu_is_container(not.as_ptr(), not.as_bytes().len()) };
        assert_eq!(hit, 0);
    }

    #[test]
    fn pick_update_name_through_c_abi() {
        let names = c(r#"["TL_updateUserTyping","TL_updateChannelUserTyping"]"#);
        let event = c("on_typing");
        let mut out_len = 0usize;
        let pointer = unsafe {
            ayu_pick_update_name(
                names.as_ptr(),
                names.as_bytes().len(),
                event.as_ptr(),
                event.as_bytes().len(),
                2,
                &mut out_len,
            )
        };
        let chosen = unsafe { take(pointer, out_len) };
        assert_eq!(chosen, "TL_updateChannelUserTyping");
    }

    #[test]
    fn telegram_method_through_c_abi() {
        let class_name = c("Request");
        let values = c(r#"[null,"TL_account.updateProfile"]"#);
        let mut out_len = 0usize;
        let pointer = unsafe {
            ayu_telegram_method_name(
                class_name.as_ptr(),
                class_name.as_bytes().len(),
                values.as_ptr(),
                values.as_bytes().len(),
                &mut out_len,
            )
        };
        let method = unsafe { take(pointer, out_len) };
        assert_eq!(method, "TL_account.updateProfile");

        let broken = c("{not json");
        let mut out_len = 0usize;
        let pointer = unsafe {
            ayu_telegram_method_name(
                class_name.as_ptr(),
                class_name.as_bytes().len(),
                broken.as_ptr(),
                broken.as_bytes().len(),
                &mut out_len,
            )
        };
        assert!(pointer.is_null());
    }

    #[test]
    fn coerce_bool_through_c_abi() {
        let yes = c(" true ");
        assert_eq!(unsafe { ayu_coerce_bool(yes.as_ptr(), yes.as_bytes().len()) }, 1);
        let no = c("off");
        assert_eq!(unsafe { ayu_coerce_bool(no.as_ptr(), no.as_bytes().len()) }, 0);
        // Non-ASCII is declined so the Python path decides it.
        let cyrillic = c("да");
        assert_eq!(
            unsafe { ayu_coerce_bool(cyrillic.as_ptr(), cyrillic.as_bytes().len()) },
            2
        );
    }

    #[test]
    fn coerce_int_and_float_through_c_abi() {
        let digits = c(" -42 ");
        let mut int_out = 0i64;
        assert_eq!(
            unsafe { ayu_coerce_int(digits.as_ptr(), digits.as_bytes().len(), &mut int_out) },
            0
        );
        assert_eq!(int_out, -42);

        // Python-only spelling: declined, never answered wrongly.
        let underscored = c("1_0");
        let mut int_out = 0i64;
        assert_eq!(
            unsafe {
                ayu_coerce_int(underscored.as_ptr(), underscored.as_bytes().len(), &mut int_out)
            },
            1
        );

        let ratio = c("1.5E-2");
        let mut float_out = 0f64;
        assert_eq!(
            unsafe {
                ayu_coerce_float(ratio.as_ptr(), ratio.as_bytes().len(), &mut float_out)
            },
            0
        );
        assert!((float_out - 0.015).abs() < 1e-12);

        let nan = c("nan");
        let mut float_out = 0f64;
        assert_eq!(
            unsafe { ayu_coerce_float(nan.as_ptr(), nan.as_bytes().len(), &mut float_out) },
            1
        );
    }

    #[test]
    fn coerce_list_through_c_abi() {
        let raw = c("a, b ,c");
        let mut out_len = 0usize;
        let pointer =
            unsafe { ayu_coerce_list(raw.as_ptr(), raw.as_bytes().len(), &mut out_len) };
        let text = unsafe { take(pointer, out_len) };
        let parts: Vec<String> = serde_json::from_str(&text).expect("json");
        assert_eq!(parts, vec!["a", "b", "c"]);

        let declined = c("да, x");
        let mut out_len = 0usize;
        let pointer = unsafe {
            ayu_coerce_list(declined.as_ptr(), declined.as_bytes().len(), &mut out_len)
        };
        assert!(pointer.is_null());
        let error = unsafe { CStr::from_ptr(ayu_last_error()) };
        assert!(error.to_str().unwrap().contains("declined"));
    }

    #[test]
    fn parse_command_through_c_abi() {
        let line = c("  /echo   one two ");
        let prefixes = c(r#"["/echo"]"#);
        let mut out_len = 0usize;
        let pointer = unsafe {
            ayu_parse_command(
                line.as_ptr(),
                line.as_bytes().len(),
                prefixes.as_ptr(),
                prefixes.as_bytes().len(),
                &mut out_len,
            )
        };
        let text = unsafe { take(pointer, out_len) };
        let value: serde_json::Value = serde_json::from_str(&text).expect("json");
        assert_eq!(value["prefix"], "/echo");
        assert_eq!(value["args"][0], "one");
        assert_eq!(value["args"][1], "two");

        // Not a command: literal null, not a failure.
        let plain = c("hello there");
        let mut out_len = 0usize;
        let pointer = unsafe {
            ayu_parse_command(
                plain.as_ptr(),
                plain.as_bytes().len(),
                prefixes.as_ptr(),
                prefixes.as_bytes().len(),
                &mut out_len,
            )
        };
        let text = unsafe { take(pointer, out_len) };
        assert_eq!(text, "null");

        // Declined: NULL plus the marker.
        let non_ascii = c("/пинг");
        let mut out_len = 0usize;
        let pointer = unsafe {
            ayu_parse_command(
                non_ascii.as_ptr(),
                non_ascii.as_bytes().len(),
                prefixes.as_ptr(),
                prefixes.as_bytes().len(),
                &mut out_len,
            )
        };
        assert!(pointer.is_null());
        let error = unsafe { CStr::from_ptr(ayu_last_error()) };
        assert!(error.to_str().unwrap().contains("declined"));
    }
}
