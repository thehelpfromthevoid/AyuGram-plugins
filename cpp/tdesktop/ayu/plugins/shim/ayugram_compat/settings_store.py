"""Persistent per-plugin settings that the app, the CLI and plugins share.

Mobile plugins expect `get_setting` / `set_setting` to survive a restart and
to be reflected in the settings UI. On Desktop the native bridge did not
expose a settings store, so this module keeps the values in a plain JSON
file next to the plugins - the same file the loader already reads.

Layout, per plugin:  <plugins_root>/<plugin_id>.settings.json
Enabled flags:      <plugins_root>/plugins.json   (written by the loader)

The root is resolved in this order:
  1. `AYU_PLUGINS_DIR` environment variable
  2. `ayu_api.plugins_dir()` when the native bridge exposes it
  3. %APPDATA%/AyuGramDesktop/plugins  (where the app looks)
  4. the directory of this file's package, three levels up

AyuGram plugin host for Desktop, (c) @CE0fbi
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

_LOCK = threading.RLock()
_CACHE: Dict[str, Dict[str, Any]] = {}
# What each settings file said when it was last read, so that a save can tell
# what really changed from what is merely remembered.
_LOADED: Dict[str, Dict[str, Any]] = {}
_WRITE_ERRORS = False


def _appdata() -> Path:
	base = os.environ.get("APPDATA") or os.path.expanduser("~")
	return Path(base) / "AyuGramDesktop" / "plugins"


def plugins_dir() -> Path:
	env = os.environ.get("AYU_PLUGINS_DIR")
	if env:
		return Path(env)
	try:
		import ayu_api

		dir_name = getattr(ayu_api, "plugins_dir", None)
		if callable(dir_name):
			value = dir_name()
			if value:
				return Path(value)
	except Exception:
		pass
	candidate = _appdata()
	if candidate.is_dir():
		return candidate
	# shipped layout: <app>/standalone/ayugram_compat/settings_store.py
	return Path(__file__).resolve().parents[2]


def state_path(plugin_id: str) -> Path:
	return plugins_dir() / f"{sanitize(plugin_id)}.settings.json"


def sanitize(plugin_id: str) -> str:
	clean = "".join(
		c if (c.isalnum() or c in "._-") else "_" for c in str(plugin_id or "")
	).strip("._")
	return clean or "plugin"


def coerce(default: Any, raw: Any) -> Any:
	"""Best-effort cast of a stored or CLI value into the declared default's type.

	The cast has to work in both directions. The string branch below only ever
	ran when the value was already a string, so a number that reached the store
	for a text setting was passed through untouched - and the plugin then called
	strings on it, e.g. ``get_setting("animation_name", "").strip()``, which died
	with 'int' object has no attribute 'strip'. A dialog that wrote the field as
	numeric had stored exactly that. The declared default is the plugin's own
	statement of what the setting holds, so it decides the type on the way out.
	"""
	if isinstance(default, str):
		if isinstance(raw, (int, float)) and not isinstance(raw, bool):
			# Keep the number the user entered, as text. Falling back to the
			# declared default here was wrong: a plugin is free to declare its
			# default as a string and still be given a number by the dialog, and
			# a spam count set to 99 was silently read back as the default.
			return str(raw)
	if not isinstance(raw, str):
		return raw
	if isinstance(default, bool):
		return raw.strip().lower() in ("1", "true", "yes", "on", "да")
	if isinstance(default, int) and not isinstance(default, bool):
		try:
			return int(raw.strip())
		except Exception:
			return default
	if isinstance(default, float):
		try:
			return float(raw.strip())
		except Exception:
			return default
	if isinstance(default, (list, tuple)):
		parts = [p.strip() for p in raw.split(",") if p.strip()]
		return type(default)(parts)
	return raw


_coerce = coerce


def read(plugin_id: str) -> Dict[str, Any]:
	key = sanitize(plugin_id)
	with _LOCK:
		if key in _CACHE:
			return dict(_CACHE[key])
	path = state_path(key)
	data: Dict[str, Any] = {}
	try:
		if path.is_file():
			loaded = json.loads(path.read_text(encoding="utf-8"))
			if isinstance(loaded, dict):
				values = loaded.get("settings", loaded)
				if isinstance(values, dict):
					data = {str(k): v for k, v in values.items()}
	except Exception as e:
		_write(f"read failed: {e}")
	with _LOCK:
		_CACHE[key] = dict(data)
		# What the file said when we last read it. Kept so that saving works out
		# which settings were actually changed, rather than writing the whole
		# remembered set back over whatever is on disk now.
		_LOADED[key] = dict(data)
	return dict(data)


_MISSING = object()


def write(plugin_id: str, values: Dict[str, Any]) -> bool:
	key = sanitize(plugin_id)
	try:
		path = state_path(key)
		path.parent.mkdir(parents=True, exist_ok=True)

		# Only the settings that really changed are written, on top of what the
		# file says now.
		#
		# Writing the remembered set wholesale meant an edit made from outside
		# while the application was running was undone the next time the plugin
		# saved anything at all. That reads exactly like "the settings do not
		# save": a corrected command name silently went back to the junk value it
		# had before, with nothing in the log to say so.
		disk: Dict[str, Any] = {}
		try:
			if path.is_file():
				loaded = json.loads(path.read_text(encoding="utf-8-sig"))
				if isinstance(loaded, dict):
					stored = loaded.get("settings", loaded)
					if isinstance(stored, dict):
						disk = {str(k): v for k, v in stored.items()}
		except Exception:
			pass
		with _LOCK:
			remembered = dict(_LOADED.get(key, {}))
		merged = dict(disk)
		for name, value in values.items():
			if remembered.get(name, _MISSING) != value:
				merged[name] = value

		with _LOCK:
			_CACHE[key] = dict(merged)
			_LOADED[key] = dict(merged)

		payload = {
			"id": key,
			"version": 1,
			"settings": merged,
		}
		tmp = path.with_suffix(".tmp")
		tmp.write_text(
			json.dumps(payload, indent=2, ensure_ascii=False),
			encoding="utf-8",
		)
		os.replace(tmp, path)
		return True
	except Exception as e:
		_write(f"write failed: {e}")
		return False


def _write(text: str) -> None:
	try:
		from ayu_api import log as _l

		_l(f"[settings] {text}")
	except Exception:
		print(f"[settings] {text}")


def get(plugin_id: str, key: str, default: Any = None) -> Any:
	values = read(plugin_id)
	# The default the plugin passed in is what the setting is supposed to hold,
	# so it decides the type. Without this the stored value came back exactly as
	# it was written, which let a number reach a text setting and crash the
	# plugin on the first string method it called.
	return coerce(default, values.get(str(key), default))


def set_value(plugin_id: str, key: str, value: Any) -> bool:
	values = read(plugin_id)
	values[str(key)] = value
	ok = write(plugin_id, values)
	notify(plugin_id, key, value)
	return ok


def defaults(items: Any) -> Dict[str, Any]:
	"""Collect `create_settings()` items into a {key: default} mapping."""
	out: Dict[str, Any] = {}
	for item in items or ():
		try:
			key = getattr(item, "key", "")
		except Exception:
			continue
		if not key:
			continue
		value = getattr(item, "default", None)
		out[str(key)] = "" if value is None else value
	return out


def notify(plugin_id: str, key: str, value: Any) -> None:
	"""Tell plugins a setting changed, so live ones can react.

	Used to *register a hook* as a side effect of every write. Two things were
	wrong with that and both were silent: a subscriber was added on every
	save and never removed, so a plugin that writes a setting from a background
	loop grew the subscription list without bound; and the subscriber it added
	took no arguments, while the bridge calls this event with two, so it raised
	TypeError inside the next event of any plugin.

	The event is emitted instead - `settings_emit` is the bridge's own entry
	point and nothing else was ever calling it - and the local dispatch is kept
	for the path where the bridge is not present.
	"""
	_dispatch(key, value)
	try:
		from ayu_api import settings_emit as _emit
	except Exception:
		return
	try:
		_emit(str(plugin_id), str(key), str(value))
	except Exception:
		# Nothing here can be raised at the caller: a write that succeeded must
		# not turn into a failure because nobody was listening.
		pass


def _dispatch(key: str, value: Any) -> None:
	"""Invoke every registered on_setting_changed handler."""
	try:
		import hook_utils

		for fn in getattr(hook_utils, "_registry", {}).get(
				"on_setting_changed", ()):
			try:
				fn(key, value)
			except TypeError:
				try:
					fn({"key": key, "value": value})
				except Exception:
					pass
			except Exception:
				pass
	except Exception:
		pass


def effective(plugin_id: str, items: Any = None) -> Dict[str, Any]:
	"""Stored values on top of the declared defaults."""
	merged = defaults(items) if items is not None else {}
	merged.update(read(plugin_id))
	return merged


# Aliases under the names plugins reach for. Most call self.get_setting, but
# plenty import a helper directly, and "cannot import name get_setting" is a
# load failure for the whole plugin over a spelling difference.
get_setting = get
set_setting = set_value
read_settings = read
write_settings = write
all_settings = read
