"""Turning a plugin's declared settings into rows the app's dialog can render.

A plugin describes its settings the Android way: `create_settings()` returns
Switch / EditText / Selector / Header rows. The app has no Android views, so
those rows have to be flattened into plain data.

This lives outside BasePlugin on purpose. A large share of mobile plugins never
inherit from BasePlugin at all - they register hooks through decorators and keep
their own object - and for those the inherited `setting_schema()` simply does not
exist, so the settings dialog reported "no setting_schema" for a plugin that had
in fact declared its settings. `rows_for` takes any object, or a module, and
finds the declaration wherever it is.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

__all__ = [
    "rows_for", "rows_from_items", "values_for", "flatten", "DECLARING_NAMES",
]

# Checked in order: an explicit schema wins, then the Android-style
# declaration, then a plain dict/list some plugins return instead.
DECLARING_NAMES = ("setting_schema", "settings_schema", "create_settings",
                   "get_settings_schema", "settings")


def _jsonable(value: Any) -> Any:
	try:
		json.dumps(value)
		return value
	except (TypeError, ValueError):
		return str(value)


def _looks_numeric(item: Any) -> bool:
	"""True when a declared input row should be edited as a number.

	Android's `Input` is the numeric field, so it used to be mapped to an int row
	unconditionally. Plenty of plugins use it for plain text as well, and a text
	setting stored as a number then broke the plugin on the first string method
	it called. The declared default is the most reliable signal the plugin gives,
	so an explicit `input_type` wins and the default decides the rest.
	"""
	raw = getattr(item, "input_type", None)
	if raw is not None:
		lowered = str(raw).lower()
		if "number" in lowered or "digit" in lowered:
			return True
		if "text" in lowered:
			return False
	default = getattr(item, "default", None)
	if isinstance(default, bool):
		return False
	if isinstance(default, (int, float)):
		return True
	return False


def _row_for(item: Any) -> Optional[Dict[str, Any]]:
	"""One declared row as plain data, or None when it carries no setting."""
	if isinstance(item, dict):
		row = dict(item)
		row.setdefault("type", "text")
		row.setdefault("key", "")
		row.setdefault("label", row.get("text", ""))
		return row

	cls = type(item).__name__
	key = str(getattr(item, "key", "") or "")
	text = str(getattr(item, "text", "") or getattr(item, "hint", "") or "")

	if cls in ("Header", "Divider", "Text"):
		if cls == "Header":
			return {"type": "header", "text": text}
		return {"type": "note", "text": text}
	if cls == "Switch":
		kind = "bool"
	elif cls == "Input":
		kind = "int" if _looks_numeric(item) else "text"
	elif cls == "EditText":
		kind = "int" if _looks_numeric(item) else "text"
	elif cls == "Selector":
		kind = "choice"
	elif cls == "Custom":
		# No Desktop equivalent, so it is shown as a read-only line rather
		# than silently dropped: the plugin did ask for something here.
		return {"type": "note", "text": text}
	else:
		kind = "text"

	row: Dict[str, Any] = {
		"type": kind,
		"key": key,
		"label": text,
		"default": _jsonable(getattr(item, "default", None)),
		"subtext": str(getattr(item, "subtext", "") or ""),
	}
	if kind == "choice":
		row["items"] = [str(x) for x in (getattr(item, "items", None) or [])]
	return row


def flatten(declared: Any) -> List[Dict[str, Any]]:
	out: List[Dict[str, Any]] = []
	for item in (declared or []):
		if isinstance(item, (list, tuple)):
			# Grouped rows, as Android settings screens produce them.
			out.extend(flatten(item))
			continue
		row = _row_for(item)
		if row is not None:
			out.append(row)
	return out


def _call_declarer(obj: Any) -> Any:
	for name in DECLARING_NAMES:
		fn = getattr(obj, name, None)
		if fn is None:
			continue
		if not callable(fn):
			return fn
		try:
			return fn()
		except TypeError:
			# A method that needs arguments is not a declaration.
			continue
		except Exception:
			continue
	return None


def rows_from_items(declared: Any) -> List[Dict[str, Any]]:
    """Turn a plugin's declared rows into plain data.

    The single place the mapping from an Android settings row to a Desktop row
    exists. `rows_for` finds where a plugin declares them and calls this;
    `BasePlugin.setting_schema` calls this too. It must not call `rows_for`,
    because `rows_for` looks for `setting_schema` first and would come straight
    back here.
    """
    return flatten(declared)


def rows_for(plugin: Any, module_globals: Any = None) -> List[Dict[str, Any]]:
    """Rows for whatever this plugin exposes, in either plugin style."""
    for source in (plugin, module_globals):
        if source is None:
            continue
        declared = _call_declarer(source)
        if declared:
            rows = rows_from_items(declared)
            if rows:
                return rows
    return []


def values_for(plugin: Any, module_globals: Any = None) -> Dict[str, Any]:
	"""Current values, merged over the declared defaults."""
	merged: Dict[str, Any] = {}
	for source in (plugin, module_globals):
		if source is None:
			continue
		for name in ("declared_defaults", "setting_defaults", "defaults"):
			fn = getattr(source, name, None)
			if callable(fn):
				try:
					merged.update(fn() or {})
					break
				except Exception:
					continue
		if merged:
			break
		read = getattr(source, "get_setting", None)
		if callable(read):
			# Best effort: a get_setting that takes a default is the plugin's
			# own accessor, so ask it for what it already knows.
			for row in rows_for(plugin, module_globals):
				key = row.get("key")
				if not key:
					continue
				try:
					value = read(key)
				except TypeError:
					continue
				except Exception:
					continue
				if value is not None:
					merged[key] = value
			if merged:
				break
	return {k: _jsonable(v) for k, v in merged.items()}