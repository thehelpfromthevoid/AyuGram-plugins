//! Core building blocks of the AyuGram fast layer.
//!
//! Everything here is safe Rust with no Qt, Python or OS dependencies so it can
//! be unit tested and later called from both the C++ client and the Python
//! plugin host through `ayu-ffi`.

pub mod dpi;
pub mod filter;
pub mod hash;
pub mod manifest;
pub mod settings;
