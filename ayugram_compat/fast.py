"""Optional native fast path for the hot helpers.

The Rust library `ayu_fast.dll` (see `rust/`) implements the same contracts as
the pure-Python code below: word replacement, manifest validation, settings
I/O, fingerprints. It is loaded once through `ctypes`; if the DLL is missing or
refuses to load, every function falls back to the Python implementation here so
that a missing native build can never break a plugin.

Nothing in this module is required at import time: `available()` reports whether
the native path is live, and every caller works either way. `ctypes` itself is
imported lazily too, because the repository root ships the embedded CPython
3.14 runtime next to the sources: a plain `import ctypes` with that directory on
`sys.path` picks up a `_ctypes.pyd` built for another interpreter version and
fails, and the shim must not die because of that.

AyuGram plugin host for Desktop, (c) @CE0fbi
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

_DLL_NAMES = ("ayu_fast.dll", "libayu_fast.so", "libayu_fast.dylib")

_ctypes_module: Any = None
_ctypes_error: Optional[str] = None

_lib: Any = None
_load_error: Optional[str] = None


def _ctypes() -> Any:
	"""`ctypes` when the running interpreter can load it, else None.

	The application directory doubles as the CPython search root in a
	deployment and ships `_ctypes.pyd` next to the sources. When the shim is
	driven by a *different* interpreter (the self test does exactly that), a
	plain `import ctypes` finds that foreign binary first and dies with
	"Module use of python314.dll conflicts with this version of Python". The
	import is therefore retried once with the directories that ship a foreign
	`_ctypes` removed from `sys.path`; if that also fails, the native path
	stays off and the Python fallbacks run, which is the whole point of
	making this optional.
	"""
	global _ctypes_module, _ctypes_error
	if _ctypes_module is not None:
		return _ctypes_module
	if _ctypes_error is not None:
		return None
	module, error = _import_ctypes()
	if module is None:
		_ctypes_error = error
		return None
	_ctypes_module = module
	return _ctypes_module


def _import_ctypes() -> tuple[Any, Optional[str]]:
	try:
		import ctypes as module

		return module, None
	except Exception as first:
		first_error = f"{type(first).__name__}: {first}"
	for name in [name for name in sys.modules if name == "ctypes" or name.startswith("ctypes.") or name == "_ctypes"]:
		del sys.modules[name]
	saved = list(sys.path)
	try:
		sys.path[:] = [entry for entry in saved if not _ships_foreign_ctypes(entry)]
		import ctypes as module

		return module, None
	except Exception as second:
		return None, f"{second} (first attempt: {first_error})"
	finally:
		sys.path[:] = saved


def _ships_foreign_ctypes(entry: str) -> bool:
	"""Whether `entry` is a directory holding a `_ctypes` binary extension."""
	try:
		directory = Path(entry or os.getcwd())
	except Exception:
		return False
	if not directory.is_dir():
		return False
	try:
		return any(directory.glob("_ctypes*.pyd")) or any(directory.glob("_ctypes*.so"))
	except Exception:
		return False


def _candidate_dirs() -> Iterable[Path]:
	seen: set[Path] = set()
	env = os.environ.get("AYU_FAST_DIR")
	if env:
		yield Path(env)
	here = Path(__file__).resolve()
	# rust/target/release next to the repository root, then the shipped layout.
	for base in (here.parents[1] / "rust" / "target" / "release", here.parent / "native", here.parent):
		if base not in seen:
			seen.add(base)
			yield base
	if getattr(sys, "frozen", False):
		yield Path(sys.executable).parent


def _bind(lib: Any, ctypes: Any) -> None:
	c_char_p = ctypes.c_char_p
	c_void_p = ctypes.c_void_p
	c_size_t = ctypes.c_size_t
	c_int = ctypes.c_int
	c_uint64 = ctypes.c_uint64

	lib.ayu_last_error.restype = c_char_p
	lib.ayu_last_error.argtypes = ()

	lib.ayu_bytes_free.restype = None
	lib.ayu_bytes_free.argtypes = [c_void_p, c_size_t]

	lib.ayu_filter_new.restype = c_void_p
	lib.ayu_filter_new.argtypes = [c_void_p, c_void_p, c_size_t]

	lib.ayu_filter_free.restype = None
	lib.ayu_filter_free.argtypes = [c_void_p]

	lib.ayu_filter_replace.restype = c_void_p
	lib.ayu_filter_replace.argtypes = [
		c_void_p,
		c_char_p,
		c_size_t,
		ctypes.POINTER(c_size_t),
		ctypes.POINTER(c_int),
	]

	lib.ayu_filter_is_match.restype = c_int
	lib.ayu_filter_is_match.argtypes = [c_void_p, c_char_p, c_size_t]

	lib.ayu_manifest_problems.restype = c_void_p
	lib.ayu_manifest_problems.argtypes = [c_char_p, c_size_t, ctypes.POINTER(c_size_t)]

	lib.ayu_manifest_is_valid.restype = c_int
	lib.ayu_manifest_is_valid.argtypes = [c_char_p, c_size_t]

	lib.ayu_settings_read.restype = c_void_p
	lib.ayu_settings_read.argtypes = [
		c_char_p,
		c_size_t,
		c_char_p,
		c_size_t,
		ctypes.POINTER(c_size_t),
		ctypes.POINTER(c_int),
	]

	lib.ayu_settings_write.restype = c_int
	lib.ayu_settings_write.argtypes = [
		c_char_p,
		c_size_t,
		c_char_p,
		c_size_t,
		c_char_p,
		c_size_t,
	]

	lib.ayu_sanitize_id.restype = c_void_p
	lib.ayu_sanitize_id.argtypes = [c_char_p, c_size_t, ctypes.POINTER(c_size_t)]

	lib.ayu_fnv1a64.restype = c_uint64
	lib.ayu_fnv1a64.argtypes = [c_char_p, c_size_t]

	lib.ayu_file_fingerprint.restype = c_int
	lib.ayu_file_fingerprint.argtypes = [
		c_char_p,
		c_size_t,
		ctypes.POINTER(c_uint64),
		ctypes.POINTER(c_uint64),
	]


def _load() -> Any:
	global _lib, _load_error
	if _lib is not None:
		return _lib
	ctypes = _ctypes()
	if ctypes is None:
		_load_error = f"ctypes unavailable: {_ctypes_error}"
		return None
	if _load_error is not None:
		return None
	errors: list[str] = []
	for directory in _candidate_dirs():
		for name in _DLL_NAMES:
			path = directory / name
			if not path.is_file():
				continue
			try:
				lib = ctypes.CDLL(str(path))
				_bind(lib, ctypes)
			except (OSError, AttributeError) as exc:
				errors.append(f"{path}: {exc}")
				continue
			_lib = lib
			return _lib
	_load_error = "; ".join(errors) if errors else "ayu_fast library not found"
	return None


def available() -> bool:
	return _load() is not None


def load_error() -> str:
	if _lib is None:
		_load()
	return "" if _lib is not None else (_load_error or "")


def _last_error(fallback: str) -> str:
	lib = _load()
	if lib is None:
		return fallback
	raw = lib.ayu_last_error()
	if not raw:
		return fallback
	try:
		return raw.decode("utf-8", "replace")
	except Exception:
		return fallback


class NativeError(RuntimeError):
	"""Raised when the native library answers with an error instead of a value."""


def _take(lib: Any, pointer: Any, length: int) -> str:
	if not pointer:
		raise NativeError(_last_error("native call failed"))
	try:
		ctypes = _ctypes()
		return ctypes.string_at(pointer, length).decode("utf-8")
	finally:
		lib.ayu_bytes_free(pointer, length)


def _as_bytes(text: str) -> tuple[bytes, int]:
	data = text.encode("utf-8")
	return data, len(data)


class WordFilter:
	"""Compiled literal replacement set.

	`pairs` are `(needle, replacement)`; empty needles and duplicates are
	dropped and the first replacement wins.

	The contract is `str.replace` applied once per pair, in order - which is
	what the plugins do today, so handing the work to the native engine cannot
	change a plugin's output. Replacements cascade exactly as the loop does:
	`("a", "b"), ("b", "c")` turns `"a"` into `"c"`.

	A single leftmost-longest pass is faster but *not* equivalent, and no
	property of the pair set predicts when it differs: a replacement can join
	the text around it (`("ab"->"c"), ("cd"->"X")` turn `"abd"` into `"cd"` in
	one pass, `"X"` in the loop) and a deletion can join its neighbours. The
	native side therefore implements the loop, and so does the fallback below.
	"""

	def __init__(self, pairs: Iterable[tuple[str, str]]):
		self._pairs: list[tuple[str, str]] = []
		seen: set[str] = set()
		for needle, replacement in pairs:
			needle = str(needle)
			if not needle or needle in seen:
				continue
			seen.add(needle)
			self._pairs.append((needle, str(replacement)))
		self._handle: Any = None
		lib = _load()
		if lib is not None:
			self._handle = self._build(lib)
		self._translation = (
			[(needle.encode("utf-8"), replacement.encode("utf-8"))
			 for needle, replacement in self._pairs]
			if self._handle is None
			else []
		)

	def _build(self, lib: Any) -> Any:
		"""Keeps the UTF-8 buffers alive for the call.

		The C side copies every pair during `ayu_filter_new`, so the temporary
		arrays only have to outlive that one call.
		"""
		ctypes = _ctypes()
		count = len(self._pairs)
		if count == 0:
			return None
		needle_buffers = [ctypes.c_char_p(n.encode("utf-8")) for n, _ in self._pairs]
		replacement_buffers = [ctypes.c_char_p(r.encode("utf-8")) for _, r in self._pairs]
		needles = (ctypes.c_char_p * count)(*needle_buffers)
		replacements = (ctypes.c_char_p * count)(*replacement_buffers)
		handle = lib.ayu_filter_new(needles, replacements, count)
		del needle_buffers, replacement_buffers
		if not handle:
			raise NativeError(_last_error("filter build failed"))
		return handle

	def __len__(self) -> int:
		return len(self._pairs)

	def __enter__(self) -> "WordFilter":
		return self

	def __exit__(self, *exc_info) -> None:
		self.close()

	def close(self) -> None:
		if self._handle:
			lib = _load()
			if lib is not None:
				lib.ayu_filter_free(self._handle)
			self._handle = None

	def __del__(self) -> None:
		try:
			self.close()
		except Exception:
			pass

	def replace(self, text: str) -> tuple[str, bool]:
		if not self._pairs:
			return text, False
		data, length = _as_bytes(text)
		if self._handle:
			lib = _load()
			ctypes = _ctypes()
			if lib is not None and ctypes is not None:
				out_len = ctypes.c_size_t(0)
				changed = ctypes.c_int(0)
				pointer = lib.ayu_filter_replace(
					self._handle, data, length, ctypes.byref(out_len), ctypes.byref(changed)
				)
				result = _take(lib, pointer, out_len.value)
				return result, bool(changed.value)
		return _sequential_replace(text, self._pairs)

	def is_match(self, text: str) -> bool:
		if not self._pairs:
			return False
		data, length = _as_bytes(text)
		if self._handle:
			lib = _load()
			if lib is not None:
				answer = lib.ayu_filter_is_match(self._handle, data, length)
				if answer < 0:
					raise NativeError(_last_error("is_match failed"))
				return answer == 1
		return any(needle in data for needle, _ in self._translation)


def _sequential_replace(text: str, pairs: list[tuple[str, str]]) -> tuple[str, bool]:
	"""`str.replace` in order, which is what the plugins already do."""
	out = text
	for needle, replacement in pairs:
		if needle:
			out = out.replace(needle, replacement)
	return out, out != text


_KNOWN_HOOKS = (
	"on_message",
	"on_message_sending",
	"on_message_deleted",
	"on_chat_opened",
	"on_app_start",
	"on_app_stop",
	"on_setting_changed",
	"on_typing",
	"on_media_sending",
)
_PLUGIN_API_VERSION = 1
_MAX_DESCRIPTION = 8000


def _valid_id(plugin_id: str) -> bool:
	if not plugin_id or not plugin_id[0].isalpha() or not plugin_id[0].isascii():
		return False
	rest = plugin_id[1:]
	if not 1 <= len(rest.encode("utf-8")) <= 63:
		return False
	return all(c.isascii() and (c.isalnum() or c in "_.-") for c in rest)


def _python_manifest_problems(document: str) -> list[str]:
	"""Python mirror of `ManifestProblems` in `plugin_manifest.cpp`."""
	try:
		m = json.loads(document)
	except Exception as exc:
		return [str(exc)[:256]]
	if not isinstance(m, dict):
		return ["manifest must be a JSON object"]
	out: list[str] = []
	plugin_id = str(m.get("id") or "")
	name = str(m.get("name") or "")
	description = str(m.get("description") or "")
	entry = str(m.get("entry", "main.py") or "")
	try:
		api_version = int(m.get("api_version", _PLUGIN_API_VERSION))
	except Exception:
		api_version = -1
	if not _valid_id(plugin_id):
		out.append("id does not match the app's pattern")
	if not name or len(name) > 100:
		out.append("name must be 1..100 chars")
	if len(description) > _MAX_DESCRIPTION:
		out.append(f"description longer than {_MAX_DESCRIPTION} chars")
	if not entry or ".." in entry:
		out.append("entry must be a plain file name")
	if api_version <= 0:
		out.append("api_version must be a positive number")
	elif api_version > _PLUGIN_API_VERSION:
		out.append(
			f"api_version {api_version} is newer than the app's "
			f"{_PLUGIN_API_VERSION}: the app will refuse it"
		)
	hooks = m.get("hooks") or []
	if isinstance(hooks, list):
		for hook in hooks:
			if isinstance(hook, str) and hook not in _KNOWN_HOOKS:
				out.append(f"unknown hook: {hook}")
	platforms = m.get("platforms")
	if platforms is None and m.get("desktop_only"):
		platforms = ["desktop"]
	if isinstance(platforms, list) and platforms:
		lower = {str(p).lower() for p in platforms}
		if not lower & {"desktop", "windows", "macos", "linux", "all", "any"}:
			out.append(f"built for {', '.join(str(p) for p in platforms)}, not this platform")
	return out


def manifest_problems(document: str) -> list[str]:
	"""Every reason the client would refuse this manifest; empty when fine."""
	lib = _load()
	if lib is None:
		return _python_manifest_problems(document)
	ctypes = _ctypes()
	data, length = _as_bytes(document)
	out_len = ctypes.c_size_t(0)
	pointer = lib.ayu_manifest_problems(data, length, ctypes.byref(out_len))
	return json.loads(_take(lib, pointer, out_len.value))


def settings_read(root: str, plugin_id: str) -> tuple[Dict[str, Any], bool]:
	"""Returns `(settings, had_error)`; a missing file is not an error."""
	lib = _load()
	if lib is None:
		return _python_settings_read(root, plugin_id)
	ctypes = _ctypes()
	root_bytes, root_len = _as_bytes(str(root))
	id_bytes, id_len = _as_bytes(str(plugin_id))
	out_len = ctypes.c_size_t(0)
	status = ctypes.c_int(0)
	pointer = lib.ayu_settings_read(
		root_bytes, root_len, id_bytes, id_len, ctypes.byref(out_len), ctypes.byref(status)
	)
	values = json.loads(_take(lib, pointer, out_len.value) or "{}")
	return values, bool(status.value)


def _python_settings_read(root: str, plugin_id: str) -> tuple[Dict[str, Any], bool]:
	path = Path(root) / f"{sanitize_id(plugin_id)}.settings.json"
	if not path.is_file():
		return {}, False
	try:
		loaded = json.loads(path.read_text(encoding="utf-8-sig"))
	except Exception:
		return {}, True
	if not isinstance(loaded, dict):
		return {}, True
	stored = loaded.get("settings", loaded)
	if not isinstance(stored, dict):
		return {}, True
	return {str(k): v for k, v in stored.items()}, False


def settings_write(root: str, plugin_id: str, values: Dict[str, Any]) -> bool:
	lib = _load()
	if lib is not None:
		ctypes = _ctypes()
		root_bytes, root_len = _as_bytes(str(root))
		id_bytes, id_len = _as_bytes(str(plugin_id))
		payload, payload_len = _as_bytes(json.dumps(values, ensure_ascii=False))
		if lib.ayu_settings_write(
			root_bytes, root_len, id_bytes, id_len, payload, payload_len
		) == 0:
			return True
	return _python_settings_write(root, plugin_id, values)


def _python_settings_write(root: str, plugin_id: str, values: Dict[str, Any]) -> bool:
	try:
		directory = Path(root)
		directory.mkdir(parents=True, exist_ok=True)
		path = directory / f"{sanitize_id(plugin_id)}.settings.json"
		payload = {"id": sanitize_id(plugin_id), "version": 1, "settings": values}
		tmp = path.with_suffix(".tmp")
		tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
		os.replace(tmp, path)
		return True
	except Exception:
		return False


def sanitize_id(plugin_id: str) -> str:
	lib = _load()
	if lib is None:
		return _python_sanitize(plugin_id)
	ctypes = _ctypes()
	data, length = _as_bytes(str(plugin_id or ""))
	out_len = ctypes.c_size_t(0)
	pointer = lib.ayu_sanitize_id(data, length, ctypes.byref(out_len))
	return _take(lib, pointer, out_len.value)


def _python_sanitize(plugin_id: str) -> str:
	clean = "".join(
		c if (c.isalnum() or c in "._-") else "_" for c in str(plugin_id or "")
	).strip("._")
	return clean or "plugin"


def fnv1a64(data: bytes) -> int:
	lib = _load()
	if lib is None:
		value = 0xCBF29CE484222325
		for byte in data:
			value ^= byte
			value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
		return value
	return lib.ayu_fnv1a64(data, len(data))


def file_fingerprint(path: str | Path) -> Optional[tuple[int, int]]:
	"""Returns `(hash, size)` or None when the file cannot be read."""
	lib = _load()
	text = str(path)
	if lib is None:
		try:
			payload = Path(text).read_bytes()
		except OSError:
			return None
		return fnv1a64(payload), len(payload)
	ctypes = _ctypes()
	data, length = _as_bytes(text)
	out_hash = ctypes.c_uint64(0)
	out_size = ctypes.c_uint64(0)
	if lib.ayu_file_fingerprint(data, length, ctypes.byref(out_hash), ctypes.byref(out_size)) != 0:
		return None
	return out_hash.value, out_size.value
