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

use ayu_core::dpi::{options::parse_line, plan, Desync, Resources, SplitPos, TransportKind};
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

unsafe fn borrowed_bytes<'a>(
    pointer: *const c_char,
    len: usize,
    what: &str,
) -> Result<&'a [u8], String> {
    if pointer.is_null() {
        return Err(format!("{what} is null"));
    }
    if len == 0 {
        return Ok(&[]);
    }
    Ok(slice::from_raw_parts(pointer as *const u8, len))
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
        let encoded = serde_json::to_string(&problems).map_err(|error| error.to_string())?;
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
pub unsafe extern "C" fn ayu_manifest_is_valid(json: *const c_char, json_len: usize) -> c_int {
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
        let encoded = serde_json::to_string(&values).map_err(|error| error.to_string())?;
        if !out_len.is_null() {
            *out_len = encoded.len();
        }
        Ok(into_buffer(encoded).0)
    })
}

/// Writes the settings object atomically. The JSON is the settings map itself,
/// not a `{ "id", "version", "settings" }` document.
///
/// The wrapper is deliberately *not* accepted. It is ambiguous with the map:
/// a plugin whose own settings contain a key called `settings` would otherwise
/// be read as a wrapper, and the file would be rewritten holding only that
/// nested object - every sibling setting silently dropped. The caller passes
/// what it wants stored, and this adds the envelope.
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
        // The caller hands over the settings map itself. Taking a nested
        // `settings` object when one is present would read a plugin's own key
        // of that name as an envelope and drop its siblings on the next save,
        // which is a silent loss of every other setting.
        let object = value
            .as_object()
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

// ---------------------------------------------------------------------------
// DPI desync (zapret) exports.
//
// These expose the transport-free strategy engine to the C++ client and the
// Python host, so both can ask which rule applies to a destination, what a rule
// cannot do on a given transport, and how a payload is cut up - without linking
// `ayu-dpi` (which owns sockets and the WinDivert driver).

/// Parses a zapret line and resolves its `--hostlist`/`--ipset` files against
/// the current directory, so a rule gated on a list file is matched by the file
/// contents rather than being dead. A file that cannot be read leaves its rule
/// non-matching instead of matching every host.
fn parse_dpi_config(text: &str) -> ayu_core::dpi::options::Config {
    let mut config = parse_line(text);
    let base = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    config.resolve_lists(&base);
    config
}

/// Parses a zapret `winws.exe`-style command line and returns how many rules it
/// produced, or -1 on error. `capture_tcp`/`capture_udp` receive the global
/// filter's port ranges as `"lo-hi,lo,lo-hi"` text so the caller need not parse
/// the structure; pass NULL to ignore either.
///
/// # Safety
/// `line` must point to `line_len` bytes; the output pointers may be null.
#[no_mangle]
pub unsafe extern "C" fn ayu_dpi_parse(
    line: *const c_char,
    line_len: usize,
    capture_tcp: *mut c_char,
    capture_tcp_len: usize,
    capture_tcp_out: *mut usize,
    capture_udp: *mut c_char,
    capture_udp_len: usize,
    capture_udp_out: *mut usize,
) -> c_int {
    guard(-1, || {
        if !capture_tcp_out.is_null() {
            *capture_tcp_out = 0;
        }
        if !capture_udp_out.is_null() {
            *capture_udp_out = 0;
        }
        let text = borrowed_str(line, line_len, "config line")?;
        let config = parse_dpi_config(text);
        write_capture(
            config.capture.tcp.iter(),
            capture_tcp,
            capture_tcp_len,
            capture_tcp_out,
        );
        write_capture(
            config.capture.udp.iter(),
            capture_udp,
            capture_udp_len,
            capture_udp_out,
        );
        Ok(config.rules.len() as c_int)
    })
}

/// Writes a port list as `"lo-hi,lo"` into a caller buffer; silently truncates
/// if the buffer is too small, always reporting the untruncated length.
unsafe fn write_capture<'a>(
    ranges: impl Iterator<Item = &'a ayu_core::dpi::options::PortRange>,
    buffer: *mut c_char,
    capacity: usize,
    out_len: *mut usize,
) {
    let text = ranges
        .map(|r| {
            if r.lo == r.hi {
                r.lo.to_string()
            } else {
                format!("{}-{}", r.lo, r.hi)
            }
        })
        .collect::<Vec<_>>()
        .join(",");
    let bytes = text.as_bytes();
    if !out_len.is_null() {
        *out_len = bytes.len();
    }
    if buffer.is_null() || capacity == 0 {
        return;
    }
    let n = bytes.len().min(capacity);
    ptr::copy_nonoverlapping(bytes.as_ptr(), buffer as *mut u8, n);
}

/// Returns the rules of `line` as a JSON array, each entry describing its match
/// ports, host patterns and strategy, plus `unsupported` - the reasons the
/// strategy cannot run in full on the transport named by `transport`
/// (`0` = userspace SOCKS5, `1` = WinDivert). `[]` for an empty line.
///
/// # Safety
/// `line` must point to `line_len` bytes.
#[no_mangle]
pub unsafe extern "C" fn ayu_dpi_report(
    line: *const c_char,
    line_len: usize,
    transport: c_int,
    out_len: *mut usize,
) -> *mut c_char {
    guard(ptr::null_mut(), || {
        if !out_len.is_null() {
            *out_len = 0;
        }
        let text = borrowed_str(line, line_len, "config line")?;
        let kind = if transport == 1 {
            TransportKind::WinDivert
        } else {
            TransportKind::Userspace
        };
        let config = parse_dpi_config(text);
        let rules: Vec<serde_json::Value> = config
            .rules
            .iter()
            .map(|rule| {
                let unsupported: Vec<&str> = rule.strategy.unsupported(kind);
                serde_json::json!({
                    "tcp": ports_to_json(&rule.filter.tcp),
                    "udp": ports_to_json(&rule.filter.udp),
                    "portsMalformed": rule.filter.ports_malformed,
                    "hosts": rule.filter.hostlist_domains,
                    "ipsets": rule.filter.ipset,
                    "l7": rule.filter.l7,
                    "desyncs": rule.strategy.desyncs.iter().map(desync_name).collect::<Vec<_>>(),
                    "splitPositions": rule.strategy.split_positions.iter().map(split_pos_name).collect::<Vec<_>>(),
                    "seqovl": rule.strategy.split_seqovl,
                    "repeats": rule.strategy.repeats,
                    "badseq": rule.strategy.fooling_badseq,
                    "badseqIncrement": rule.strategy.badseq_increment,
                    "anyProtocol": rule.strategy.any_protocol,
                    "unsupported": unsupported,
                    "unknown": rule.unknown,
                })
            })
            .collect();
        let encoded = serde_json::to_string(&rules).map_err(|error| error.to_string())?;
        if !out_len.is_null() {
            *out_len = encoded.len();
        }
        Ok(into_buffer(encoded).0)
    })
}

fn ports_to_json(list: &[ayu_core::dpi::options::PortRange]) -> Vec<serde_json::Value> {
    list.iter()
        .map(|r| serde_json::json!([r.lo, r.hi]))
        .collect()
}

fn desync_name(d: &Desync) -> &'static str {
    match d {
        Desync::None => "none",
        Desync::Fake => "fake",
        Desync::Multisplit => "multisplit",
        Desync::Multidisorder => "multidisorder",
        Desync::Syndata => "syndata",
        Desync::FakedSplit => "fakedsplit",
        Desync::HostFakeSplit => "hostfakesplit",
    }
}

fn split_pos_name(p: &SplitPos) -> String {
    match p {
        SplitPos::Absolute(n) => n.to_string(),
        SplitPos::MidSld => "midsld".to_owned(),
        SplitPos::HostEnd => "host".to_owned(),
        SplitPos::HostOffset(d) if *d >= 0 => format!("host+{d}"),
        SplitPos::HostOffset(d) => format!("host{d}"),
    }
}

/// Plans the segments for one outbound `payload` at `stream_pos`, returning a
/// JSON array of segments, each `{"data":"<hex>","seq":<u32|null>,"fake":<bool>}`.
/// Concatenating the non-fake `data` in order reproduces the payload; a `seq`
/// that is set means "send with this absolute TCP sequence number" (badseq),
/// and `fake` marks bytes the peer is expected to discard.
///
/// The FFI call has no loaded template blobs, so a plan that needs a fake
/// template still returns the split structure with the fake omitted.
///
/// A userspace caller should check `unsupported` first and prefer
/// [`ayu_dpi_userspace_chunks`], which honours only what a byte stream can do.
///
/// # Safety
/// All pointers must point to their stated lengths.
#[no_mangle]
pub unsafe extern "C" fn ayu_dpi_plan(
    line: *const c_char,
    line_len: usize,
    host: *const c_char,
    host_len: usize,
    port: u16,
    payload: *const c_char,
    payload_len: usize,
    stream_pos: u32,
    out_len: *mut usize,
) -> *mut c_char {
    guard(ptr::null_mut(), || {
        if !out_len.is_null() {
            *out_len = 0;
        }
        let text = borrowed_str(line, line_len, "config line")?;
        let host = borrowed_str(host, host_len, "host")?;
        let bytes = borrowed_bytes(payload, payload_len, "payload")?;
        let config = parse_dpi_config(text);
        let strategy = config
            .strategy_for(host, port)
            .ok_or_else(|| format!("no rule matches {host}:{port}"))?;
        let resources = Resources::default();
        let segments = plan(strategy, bytes, stream_pos, &resources);
        let encoded: Vec<serde_json::Value> = segments
            .iter()
            .map(|segment| {
                serde_json::json!({
                    "data": hex_encode(&segment.data),
                    "seq": segment.seq_override,
                    "fake": segment.fake,
                })
            })
            .collect();
        let encoded = serde_json::to_string(&encoded).map_err(|error| error.to_string())?;
        if !out_len.is_null() {
            *out_len = encoded.len();
        }
        Ok(into_buffer(encoded).0)
    })
}

/// The ordered chunks a userspace SOCKS5 transport should write for the first
/// `payload` of a connection to `host:port`, based on `line`. Concatenating the
/// chunks reproduces the payload exactly; the number of chunks is the number of
/// writes (each with `TCP_NODELAY`) that make the kernel emit separate segments.
///
/// Returns a JSON array of hex strings so binary payloads survive the boundary.
/// An empty array means "no rule applies" or "no split"; the caller then writes
/// the payload whole.
///
/// # Safety
/// All pointers must point to their stated lengths.
#[no_mangle]
pub unsafe extern "C" fn ayu_dpi_userspace_chunks(
    line: *const c_char,
    line_len: usize,
    host: *const c_char,
    host_len: usize,
    port: u16,
    payload: *const c_char,
    payload_len: usize,
    out_len: *mut usize,
) -> *mut c_char {
    guard(ptr::null_mut(), || {
        if !out_len.is_null() {
            *out_len = 0;
        }
        let text = borrowed_str(line, line_len, "config line")?;
        let host = borrowed_str(host, host_len, "host")?;
        let bytes = borrowed_bytes(payload, payload_len, "payload")?;
        let config = parse_dpi_config(text);
        let chunks: Vec<String> = match config.strategy_for(host, port) {
            Some(strategy) if !strategy.split_positions.is_empty() => strategy
                .userspace_chunks(bytes)
                .iter()
                .map(|chunk| hex_encode(chunk))
                .collect(),
            _ => Vec::new(),
        };
        let encoded = serde_json::to_string(&chunks).map_err(|error| error.to_string())?;
        if !out_len.is_null() {
            *out_len = encoded.len();
        }
        Ok(into_buffer(encoded).0)
    })
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        out.push(HEX[(b >> 4) as usize] as char);
        out.push(HEX[(b & 0x0f) as usize] as char);
    }
    out
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

        let filter = unsafe { ayu_filter_new(needle_ptrs.as_ptr(), replacement_ptrs.as_ptr(), 2) };
        assert!(!filter.is_null(), "filter build failed");

        let text = c("brb imo");
        let mut out_len = 0usize;
        let mut changed = 0i32;
        let output =
            unsafe { ayu_filter_replace(filter, text.as_ptr(), 7, &mut out_len, &mut changed) };
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
    fn settings_write_does_not_read_a_settings_key_as_an_envelope() {
        // A plugin's own map may contain a key called `settings`. Treating it
        // as the `{ id, version, settings }` envelope makes the writer store
        // only that nested object: every sibling setting is lost, silently, on
        // the first save after one is added.
        let dir = tempfile::tempdir().expect("tempdir");
        let root = c(dir.path().to_str().unwrap());
        let id = c("demo");
        let payload = c(r#"{"settings":{"a":1},"b":2}"#);

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
        assert_eq!(write, 0, "write failed: {:?}", unsafe {
            CStr::from_ptr(ayu_last_error())
        });

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
        assert_eq!(value["b"], 2, "sibling setting was dropped: {text}");
        assert_eq!(value["settings"]["a"], 1, "nested map lost: {text}");
    }

    #[test]
    fn settings_write_rejects_a_non_object() {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = c(dir.path().to_str().unwrap());
        let id = c("demo");
        let payload = c("[1,2,3]");
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
        assert_eq!(write, -1);
        assert!(!dir.path().join("demo.settings.json").exists());
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

    const DPI_LINE: &str = concat!(
        "--wf-tcp=443,2053 --filter-tcp=443 --hostlist-domains=discord.media ",
        "--dpi-desync=fake,multidisorder --dpi-desync-split-pos=1 --dpi-desync-fooling=badseq ",
        "--dpi-desync-split-seqovl=100 --dpi-desync-repeats=6 ",
        "--new --filter-tcp=2053 --dpi-desync=multisplit --dpi-desync-split-pos=1"
    );

    #[test]
    fn dpi_parse_counts_rules_and_reports_capture_ports() {
        let line = c(DPI_LINE);
        let mut tcp = vec![0u8; 64];
        let mut tcp_len = 0usize;
        let mut udp = vec![0u8; 64];
        let mut udp_len = 0usize;
        let rules = unsafe {
            ayu_dpi_parse(
                line.as_ptr(),
                line.as_bytes().len(),
                tcp.as_mut_ptr() as *mut c_char,
                tcp.len(),
                &mut tcp_len,
                udp.as_mut_ptr() as *mut c_char,
                udp.len(),
                &mut udp_len,
            )
        };
        assert_eq!(rules, 2);
        let tcp_text = std::str::from_utf8(&tcp[..tcp_len]).expect("utf8");
        assert_eq!(tcp_text, "443,2053");
        assert_eq!(udp_len, 0);
    }

    #[test]
    fn dpi_report_names_what_userspace_cannot_do() {
        let line = c(DPI_LINE);
        let mut out_len = 0usize;
        // transport 0 == userspace
        let pointer =
            unsafe { ayu_dpi_report(line.as_ptr(), line.as_bytes().len(), 0, &mut out_len) };
        let text = unsafe { take(pointer, out_len) };
        let rules: Vec<serde_json::Value> = serde_json::from_str(&text).expect("json");
        assert_eq!(rules.len(), 2);
        let first = &rules[0];
        let unsupported = first["unsupported"].as_array().expect("array");
        // fake, badseq, multidisorder, seqovl -> four structural losses
        assert_eq!(unsupported.len(), 4, "{text}");
        assert!(unsupported
            .iter()
            .any(|r| r.as_str().unwrap().contains("multidisorder")));
        // the second rule is a plain split, honourable in full
        assert_eq!(rules[1]["unsupported"].as_array().unwrap().len(), 0);
        assert_eq!(rules[1]["desyncs"][0], "multisplit");
    }

    #[test]
    fn dpi_report_windivert_has_no_structural_losses() {
        let line = c(DPI_LINE);
        let mut out_len = 0usize;
        // transport 1 == WinDivert: it can forge packets and sequences
        let pointer =
            unsafe { ayu_dpi_report(line.as_ptr(), line.as_bytes().len(), 1, &mut out_len) };
        let text = unsafe { take(pointer, out_len) };
        let rules: Vec<serde_json::Value> = serde_json::from_str(&text).expect("json");
        for rule in &rules {
            assert_eq!(
                rule["unsupported"].as_array().unwrap().len(),
                0,
                "WinDivert should honour everything: {text}"
            );
        }
    }

    #[test]
    fn dpi_userspace_chunks_split_and_rejoin_intact() {
        let line = c("--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1,3");
        let host = c("example.com");
        let payload = b"abcdef";
        let mut out_len = 0usize;
        let pointer = unsafe {
            ayu_dpi_userspace_chunks(
                line.as_ptr(),
                line.as_bytes().len(),
                host.as_ptr(),
                host.as_bytes().len(),
                443,
                payload.as_ptr() as *const c_char,
                payload.len(),
                &mut out_len,
            )
        };
        let text = unsafe { take(pointer, out_len) };
        let chunks: Vec<String> = serde_json::from_str(&text).expect("json");
        assert_eq!(chunks, vec!["61", "6263", "646566"], "{text}");
        let rejoined: Vec<u8> = chunks
            .iter()
            .flat_map(|chunk| {
                (0..chunk.len() / 2)
                    .map(|i| u8::from_str_radix(&chunk[i * 2..i * 2 + 2], 16).unwrap())
            })
            .collect();
        assert_eq!(rejoined, payload);
    }

    #[test]
    fn dpi_userspace_chunks_empty_when_no_rule_matches() {
        let line = c("--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=1");
        let host = c("example.com");
        let payload = b"abc";
        let mut out_len = 0usize;
        let pointer = unsafe {
            ayu_dpi_userspace_chunks(
                line.as_ptr(),
                line.as_bytes().len(),
                host.as_ptr(),
                host.as_bytes().len(),
                8443, // no rule for this port
                payload.as_ptr() as *const c_char,
                payload.len(),
                &mut out_len,
            )
        };
        let text = unsafe { take(pointer, out_len) };
        assert_eq!(text, "[]");
    }

    #[test]
    fn dpi_plan_leaves_real_sequences_alone_without_a_fake() {
        let line = c("--filter-tcp=443 --dpi-desync=multisplit --dpi-desync-split-pos=2 --dpi-desync-fooling=badseq");
        let host = c("example.com");
        let payload = b"abcdef";
        let mut out_len = 0usize;
        let pointer = unsafe {
            ayu_dpi_plan(
                line.as_ptr(),
                line.as_bytes().len(),
                host.as_ptr(),
                host.as_bytes().len(),
                443,
                payload.as_ptr() as *const c_char,
                payload.len(),
                1000,
                &mut out_len,
            )
        };
        let text = unsafe { take(pointer, out_len) };
        let segments: Vec<serde_json::Value> = serde_json::from_str(&text).expect("json");
        assert_eq!(segments.len(), 2, "{text}");
        assert_eq!(segments[0]["data"], "6162");
        assert_eq!(segments[1]["data"], "63646566");
        // `badseq` forges a fake packet; a plain split without a fake template
        // has nothing to forge, so the real segments keep the kernel's
        // sequence and no override is emitted.
        assert_eq!(segments[0]["seq"], serde_json::Value::Null);
        assert_eq!(segments[1]["seq"], serde_json::Value::Null);
    }
}
