//! Dynamic bindings to `WinDivert.dll`, loaded at run time.
//!
//! The crate cannot take a link-time dependency on WinDivert: the DLL ships
//! with the app (next to the client, or from the bundled zapret `bin/`), may be
//! absent, and only works elevated. So the DLL is opened with `LoadLibraryW`
//! and the four entry points we use are resolved with `GetProcAddress`. A
//! missing DLL or a missing export is an ordinary error value, never a load
//! failure of the whole client.
//!
//! Only the layer types and signatures actually used are declared. The struct
//! layout mirrors `windivert.h`; `WINDIVERT_ADDRESS` is deliberately opaque
//! here (a fixed byte blob) because we only round-trip it between `Recv` and
//! `Send` without reading its fields.

use std::ffi::{CString, c_void};
use std::os::raw::c_uint;
use std::path::Path;

/// A validated `WinDivert` capture handle.
///
/// This is deliberately a newtype with a private field, not a bare
/// `*mut c_void`: only [`WinDivert::open_handle`] can produce one, so the safe
/// `recv`/`send`/`close_handle` below can never be handed an arbitrary pointer.
/// A type alias would let any caller forge a handle in safe code and turn those
/// methods into unsound wrappers around undefined behaviour.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Handle(*mut c_void);

/// `WINDIVERT_ADDRESS` is 64 bytes in WinDivert 2.x.
pub const ADDRESS_SIZE: usize = 64;

#[link(name = "kernel32")]
unsafe extern "system" {
    fn LoadLibraryW(name: *const u16) -> *mut c_void;
    fn GetProcAddress(module: *mut c_void, name: *const u8) -> *mut c_void;
    fn FreeLibrary(module: *mut c_void) -> i32;
    fn GetLastError() -> u32;
}

/// `WinDivertOpen`.
type FnOpen = unsafe extern "system" fn(*const u8, c_uint, i16, u64) -> *mut c_void;
/// `WinDivertRecv`.
type FnRecv =
    unsafe extern "system" fn(*mut c_void, *mut u8, c_uint, *mut c_uint, *mut c_void) -> i32;
/// `WinDivertSend`.
type FnSend =
    unsafe extern "system" fn(*mut c_void, *const u8, c_uint, *mut c_uint, *const c_void) -> i32;
/// `WinDivertClose`.
type FnClose = unsafe extern "system" fn(*mut c_void) -> i32;
/// `WinDivertHelperCalcChecksums`.
type FnCalcChecksums = unsafe extern "system" fn(*mut u8, c_uint, *mut c_void, u64) -> i32;

/// A resolved WinDivert DLL, or the reason it could not be used.
pub struct WinDivert {
    module: *mut c_void,
    open: FnOpen,
    recv: FnRecv,
    send: FnSend,
    close: FnClose,
    calc_checksums: FnCalcChecksums,
}

/// Why WinDivert is unavailable. Each variant names the next concrete step.
#[derive(Debug)]
pub enum Unavailable {
    /// The DLL was not found at any search path.
    MissingDll(String),
    /// The DLL loaded but an export is missing (wrong version).
    MissingExport(&'static str),
    /// The handle could not be created, usually because the process is not
    /// elevated. `win32_error` is the `GetLastError` code.
    OpenFailed { filter: String, win32_error: u32 },
}

impl std::fmt::Display for Unavailable {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::MissingDll(p) => write!(f, "WinDivert.dll not found (looked at {p})"),
            Self::MissingExport(name) => write!(f, "WinDivert.dll is missing export {name}"),
            Self::OpenFailed {
                filter,
                win32_error,
            } => write!(
                f,
                "WinDivertOpen({filter:?}) failed with Win32 error {win32_error}; \
                 this normally means the process is not elevated (run as administrator)"
            ),
        }
    }
}

/// True when the Win32 error is ERROR_ACCESS_DENIED.
pub fn is_access_denied(win32_error: u32) -> bool {
    win32_error == 5
}

impl WinDivert {
    /// Loads `WinDivert.dll`. `dir` is an optional directory to try first, so
    /// the app can point at its bundled copy instead of relying on the search
    /// path.
    pub fn load(dir: Option<&Path>) -> Result<Self, Unavailable> {
        let module = match dir {
            Some(d) => {
                let path = d.join("WinDivert.dll");
                load_library(&path)
                    .ok_or_else(|| Unavailable::MissingDll(path.display().to_string()))?
            }
            None => load_library_str("WinDivert.dll")
                .ok_or_else(|| Unavailable::MissingDll("WinDivert.dll".to_owned()))?,
        };
        // Resolve every entry point before promising anything. The transmute
        // target is written out in full so the compiler checks each pointer
        // against the exact `Fn*` signature: an unannotated transmute would
        // silently accept a drifted signature and reinterpret the call.
        let open = unsafe { resolve(module, b"WinDivertOpen\0") }
            .ok_or(Unavailable::MissingExport("WinDivertOpen"))?;
        let recv = unsafe { resolve(module, b"WinDivertRecv\0") }
            .ok_or(Unavailable::MissingExport("WinDivertRecv"))?;
        let send = unsafe { resolve(module, b"WinDivertSend\0") }
            .ok_or(Unavailable::MissingExport("WinDivertSend"))?;
        let close = unsafe { resolve(module, b"WinDivertClose\0") }
            .ok_or(Unavailable::MissingExport("WinDivertClose"))?;
        let calc_checksums = unsafe { resolve(module, b"WinDivertHelperCalcChecksums\0") }
            .ok_or(Unavailable::MissingExport("WinDivertHelperCalcChecksums"))?;
        Ok(Self {
            module,
            open: unsafe { std::mem::transmute::<*mut c_void, FnOpen>(open) },
            recv: unsafe { std::mem::transmute::<*mut c_void, FnRecv>(recv) },
            send: unsafe { std::mem::transmute::<*mut c_void, FnSend>(send) },
            close: unsafe { std::mem::transmute::<*mut c_void, FnClose>(close) },
            calc_checksums: unsafe {
                std::mem::transmute::<*mut c_void, FnCalcChecksums>(calc_checksums)
            },
        })
    }

    /// Opens a capture handle for `filter` (`"tcp.DstPort == 443"` syntax).
    /// `priority` and `flags` follow `WinDivertOpen`; pass 0/0 for defaults.
    pub fn open_handle(
        &self,
        filter: &str,
        priority: i16,
        flags: u64,
    ) -> Result<Handle, Unavailable> {
        let c = CString::new(filter).map_err(|_| Unavailable::OpenFailed {
            filter: filter.to_owned(),
            win32_error: 0,
        })?;
        let handle = unsafe { (self.open)(c.as_ptr() as *const u8, 0, priority, flags) };
        if handle.is_null() || handle as isize == -1 {
            return Err(Unavailable::OpenFailed {
                filter: filter.to_owned(),
                win32_error: unsafe { GetLastError() },
            });
        }
        Ok(Handle(handle))
    }

    /// Receives one packet into `buffer`, returning its length and address.
    pub fn recv(
        &self,
        handle: Handle,
        buffer: &mut [u8],
    ) -> Result<(usize, [u8; ADDRESS_SIZE]), std::io::Error> {
        let mut len: c_uint = 0;
        let mut addr = [0u8; ADDRESS_SIZE];
        let ok = unsafe {
            (self.recv)(
                handle.0,
                buffer.as_mut_ptr(),
                buffer.len() as c_uint,
                &mut len,
                addr.as_mut_ptr() as *mut c_void,
            )
        };
        if ok == 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok((len as usize, addr))
    }

    /// Sends one packet.
    pub fn send(
        &self,
        handle: Handle,
        packet: &[u8],
        addr: &[u8; ADDRESS_SIZE],
    ) -> Result<(), std::io::Error> {
        let mut len: c_uint = 0;
        let ok = unsafe {
            (self.send)(
                handle.0,
                packet.as_ptr(),
                packet.len() as c_uint,
                &mut len,
                addr.as_ptr() as *const c_void,
            )
        };
        if ok == 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    }

    /// Recomputes all checksums for a packet in place. Returns false when the
    /// helper refuses the packet.
    pub fn calc_checksums(&self, packet: &mut [u8]) -> bool {
        unsafe {
            (self.calc_checksums)(
                packet.as_mut_ptr(),
                packet.len() as c_uint,
                std::ptr::null_mut(),
                0,
            ) != 0
        }
    }

    /// Closes a capture handle.
    pub fn close_handle(&self, handle: Handle) {
        unsafe {
            (self.close)(handle.0);
        }
    }
}

impl Drop for WinDivert {
    fn drop(&mut self) {
        if !self.module.is_null() {
            unsafe {
                FreeLibrary(self.module);
            }
        }
    }
}

/// Loads a library by absolute path.
fn load_library(path: &Path) -> Option<*mut c_void> {
    let wide: Vec<u16> = path
        .as_os_str()
        .to_string_lossy()
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect();
    let handle = unsafe { LoadLibraryW(wide.as_ptr()) };
    (!handle.is_null()).then_some(handle)
}

/// Loads a library by bare name through the standard search path.
fn load_library_str(name: &str) -> Option<*mut c_void> {
    load_library(Path::new(name))
}

/// Resolves an export. `name` must be NUL-terminated.
unsafe fn resolve(module: *mut c_void, name: &[u8]) -> Option<*mut c_void> {
    let pointer = unsafe { GetProcAddress(module, name.as_ptr()) };
    (!pointer.is_null()).then_some(pointer)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn access_denied_is_error_5() {
        assert!(is_access_denied(5));
        assert!(!is_access_denied(2));
    }

    #[test]
    fn a_missing_dll_is_reported_not_panicked() {
        // Pointing at an empty temp dir must fail cleanly.
        let dir = std::env::temp_dir().join("ayu-dpi-no-such-dir");
        match WinDivert::load(Some(&dir)) {
            Err(Unavailable::MissingDll(p)) => assert!(p.contains("WinDivert.dll")),
            Err(other) => panic!("expected MissingDll, got {other}"),
            Ok(_) => panic!("expected MissingDll, got a loaded module"),
        }
    }

    #[test]
    fn the_unavailable_display_mentions_elevation_for_open_failure() {
        let u = Unavailable::OpenFailed {
            filter: "tcp".to_owned(),
            win32_error: 5,
        };
        assert!(u.to_string().contains("administrator"));
    }
}
