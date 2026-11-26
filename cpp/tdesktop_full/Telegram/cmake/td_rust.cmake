# This file is part of AyuGram Desktop.
#
# Links the Rust "fast layer" (`ayu-ffi`, crate `ayu_fast`) into the client as a
# static library, so the Rust modules (`power`, `gram`, `dpi`, `filter`, ...) run
# inside AyuGram.exe instead of a separate helper.
#
# Two things make this work on Windows/MSVC:
#
# 1. The staticlib is built with `-C target-feature=+crt-static`. The client uses
#    the static CRT (`CMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded`), so a
#    dynamically linked Rust library would put two independent C runtimes into
#    one process and let `malloc`/`free` and locales drift apart. Verified with
#    `dumpbin /dependents`: the result imports no `VCRUNTIME140.dll` and no
#    `api-ms-win-crt-*`.
#
# 2. The native libraries below are exactly what `rustc --print native-static-libs`
#    asks for. `WaitOnAddress`/`WakeByAddressSingle` live in `synchronization.lib`,
#    not in `Kernel32.lib`, so a hand-written list fails to link.
#
# Only the objects actually referenced are pulled out of the archive, so a call
# site in C++ is required for a module to be part of the executable. Call sites
# must be guarded by `AYU_RUST_ENABLED`, which this module exports as a compile
# definition (1 when the layer is linked, 0 when it is not).
#
# The workspace is a sibling checkout, not a subdirectory of this tree, so it is
# located explicitly. Order of preference: the `AYU_RUST_DIR` cache variable, the
# `AYU_RUST_DIR` environment variable, then a short automatic search near this
# directory, the source root and the user profile.
#
# A missing workspace is a hard error by default, because an executable that
# quietly lost its Rust modules is worse than a failed configure. Trees that are
# meant to build without the Rust layer pass `-DAYU_RUST_OPTIONAL=ON`; that path
# also flips `AYU_RUST_ENABLED` to 0, so guarded call sites compile out.

include_guard(GLOBAL)

option(AYU_RUST_OPTIONAL
    "Build without the Rust fast layer instead of failing when it is missing"
    OFF)

set(AYU_RUST_ENABLED FALSE)

# Always defined, so `target_link_libraries(Telegram ... ayu_rust)` is valid on
# every platform and simply contributes nothing where unsupported.
add_library(ayu_rust INTERFACE)

# Reports why the layer is absent. `_args` is passed as separate arguments and
# joined with a single space, because adjacent string literals are concatenated
# only inside `message`, not in a function call.
function(_ayu_rust_missing)
    string(JOIN " " _reason ${ARGN})
    if (AYU_RUST_OPTIONAL)
        message(WARNING
            "AyuGram Rust fast layer is NOT linked: ${_reason} The build "
            "continues without the Rust modules, and AYU_RUST_ENABLED is 0, so "
            "guarded call sites compile out.")
    else()
        message(FATAL_ERROR
            "AyuGram Rust fast layer is required, but ${_reason} Pass "
            "-DAYU_RUST_DIR=<path> or set the AYU_RUST_DIR environment variable "
            "to the directory holding Cargo.toml, for example "
            "-DAYU_RUST_DIR=D:/src/AyuGram/rust. Pass "
            "-DAYU_RUST_OPTIONAL=ON to build without the Rust layer instead.")
    endif()
    target_compile_definitions(ayu_rust INTERFACE "AYU_RUST_ENABLED=0")
endfunction()

if (NOT WIN32)
    # The fast layer is wired into the Windows link only.
    _ayu_rust_missing("the fast layer is currently wired for Windows only.")
    return()
endif()

set(AYU_RUST_DIR "" CACHE PATH
    "Path to the AyuGram Rust workspace (the directory holding Cargo.toml)")

set(_ayu_rust_source "")
if (AYU_RUST_DIR)
    set(_ayu_rust_source "AYU_RUST_DIR")
elseif (DEFINED ENV{AYU_RUST_DIR})
    set(AYU_RUST_DIR "$ENV{AYU_RUST_DIR}")
    set(_ayu_rust_source "the AYU_RUST_DIR environment variable")
else()
    # Automatic search: near this directory, near the source root, and near the
    # user profile, looking for a Cargo.toml that declares the ayu-ffi member.
    foreach(_base
            "${CMAKE_CURRENT_SOURCE_DIR}"
            "${CMAKE_SOURCE_DIR}"
            "$ENV{USERPROFILE}")
        if (NOT _base OR AYU_RUST_DIR)
            continue()
        endif()
        foreach(_up "" "/.." "/../.." "/../../..")
            set(_candidate "${_base}${_up}/rust")
            if (EXISTS "${_candidate}/Cargo.toml")
                file(STRINGS "${_candidate}/Cargo.toml" _members REGEX "ayu-ffi")
                if (_members)
                    set(AYU_RUST_DIR "${_candidate}")
                    set(_ayu_rust_source "the automatic search")
                    break()
                endif()
            endif()
        endforeach()
    endforeach()
endif()

if (AYU_RUST_DIR)
    # A value taken from the environment keeps backslashes, and a backslash
    # inside a GLOB path makes CMake generate a VerifyGlobs.cmake that dies with
    # "Invalid character escape '\U'". Normalize once, here.
    file(TO_CMAKE_PATH "${AYU_RUST_DIR}" AYU_RUST_DIR)
    get_filename_component(AYU_RUST_DIR "${AYU_RUST_DIR}" ABSOLUTE)
endif()

if (NOT AYU_RUST_DIR)
    _ayu_rust_missing("the Rust workspace was not found.")
    return()
endif()

if (NOT EXISTS "${AYU_RUST_DIR}/Cargo.toml")
    _ayu_rust_missing("${_ayu_rust_source} points at"
        "'${AYU_RUST_DIR}', where there is no Cargo.toml.")
    return()
endif()

find_program(AYU_RUST_CARGO
    NAMES cargo cargo.exe
    HINTS "$ENV{USERPROFILE}/.cargo/bin" "$ENV{CARGO_HOME}/bin"
    DOC "cargo used to build the Rust fast layer")
if (NOT AYU_RUST_CARGO)
    _ayu_rust_missing("cargo was not found on this machine.")
    return()
endif()

set(AYU_RUST_TARGET "x86_64-pc-windows-msvc")
set(AYU_RUST_RELEASE_DIR "${AYU_RUST_DIR}/target/${AYU_RUST_TARGET}/release")
set(AYU_RUST_STATICLIB "${AYU_RUST_RELEASE_DIR}/ayu_fast.lib")

file(GLOB_RECURSE AYU_RUST_SOURCES CONFIGURE_DEPENDS
    "${AYU_RUST_DIR}/ayu-core/src/*.rs"
    "${AYU_RUST_DIR}/ayu-ffi/src/*.rs"
    "${AYU_RUST_DIR}/ayu-wallet/src/*.rs"
    "${AYU_RUST_DIR}/ayu-core/Cargo.toml"
    "${AYU_RUST_DIR}/ayu-ffi/Cargo.toml"
    "${AYU_RUST_DIR}/ayu-wallet/Cargo.toml"
    "${AYU_RUST_DIR}/Cargo.toml"
    "${AYU_RUST_DIR}/Cargo.lock")

get_filename_component(AYU_RUST_CARGO_BIN "${AYU_RUST_CARGO}" DIRECTORY)

add_custom_command(
    OUTPUT "${AYU_RUST_STATICLIB}"
    COMMAND "${CMAKE_COMMAND}" -E env
        "RUSTFLAGS=-C target-feature=+crt-static"
        "PATH=${AYU_RUST_CARGO_BIN};$ENV{PATH}"
        "${AYU_RUST_CARGO}" build -p ayu-ffi --release --target "${AYU_RUST_TARGET}"
    WORKING_DIRECTORY "${AYU_RUST_DIR}"
    DEPENDS ${AYU_RUST_SOURCES}
    COMMENT "Building the Rust fast layer (ayu_fast.lib, static CRT)"
    VERBATIM)

add_custom_target(ayu_rust_build DEPENDS "${AYU_RUST_STATICLIB}")

target_link_libraries(ayu_rust INTERFACE
    "${AYU_RUST_STATICLIB}"
    kernel32.lib
    ntdll.lib
    userenv.lib
    ws2_32.lib
    dbghelp.lib
    synchronization.lib)

# Consumers of the interface library build the archive first, so they never
# race the link step against the Rust build.
add_dependencies(ayu_rust ayu_rust_build)

target_compile_definitions(ayu_rust INTERFACE "AYU_RUST_ENABLED=1")

set(AYU_RUST_ENABLED TRUE)
message(STATUS "AyuGram Rust fast layer: enabled, workspace ${AYU_RUST_DIR} "
    "(from ${_ayu_rust_source})")
