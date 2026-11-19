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

use ayu_core::filter::WordFilter;
use ayu_core::hash;
use ayu_core::manifest;
use ayu_core::settings::{sanitize_id, SettingsStore};

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
}
