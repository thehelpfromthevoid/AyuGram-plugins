//! Core building blocks of the AyuGram fast layer.
//!
//! Everything here is safe Rust with no Qt, Python or OS dependencies so it can
//! be unit tested and later called from both the C++ client and the Python
//! plugin host through `ayu-ffi`.

pub mod coerce;
pub mod filter;
pub mod hash;
pub mod hooks;
pub mod manifest;
pub mod settings;
pub mod text;
