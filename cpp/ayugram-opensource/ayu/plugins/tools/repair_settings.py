"""Put back the settings values a diagnostic wrote over the real ones.

The round-trip check writes into a plugin's settings, because that is the only
way to prove a value survives a save. It used to do that in the user's own
files, and its sample value - the string "proba" - was left behind in every
setting of every plugin it visited. It went into numbers and switches as well
as text, so a plugin asking whether it was enabled was handed the word "proba",
and a nickname field was filled with it.

That looks exactly like a settings bug: commands that stop being recognised,
panels that stay empty, a name that reverts. It is not one, and no amount of
fixing the settings code would have touched it.

So this walks the installed plugins, finds the values carrying that marker, and
restores what the plugin itself declares as the default - which is known, and is
the best available answer for a value that was never the user's. Nothing else
is touched, and every change is printed.

Close the application first: it keeps the settings in memory and will write its
copy back over anything changed underneath it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _roots() -> list:
	"""Where the shim may be: the repository layout or a deployment."""
	return [HERE.parent, HERE.parent / "shim", HERE]


def _shim_root() -> Path:
	for base in _roots():
		if (base / "base_plugin.py").is_file():
			return base
	raise SystemExit("no shim found next to this tool")


for path in (str(_shim_root()),):
	if path not in sys.path:
		sys.path.insert(0, path)

LOADER = next(
	(base / "standalone" for base in _roots()
	 if (base / "standalone" / "plugin_loader.py").is_file()),
	_shim_root())
if str(LOADER) not in sys.path:
	sys.path.insert(0, str(LOADER))

ROOT = Path(os.environ.get(
	"AYU_PLUGINS_DIR",
	str(Path(os.environ.get("APPDATA", ".")) / "AyuGramDesktop" / "plugins")))

# The value the round-trip check used. Anything equal to it was written by that
# tool and is not the user's.
MARKER = "proba"


def load_plugin(directory: Path):
	"""Import a plugin from a throwaway copy, so nothing real is touched."""
	entry = "main.py"
	manifest = directory / "plugin.json"
	if manifest.is_file():
		try:
			entry = json.loads(
				manifest.read_text(encoding="utf-8-sig")).get("entry", entry)
		except Exception:
			pass
	script = directory / entry
	if not script.is_file():
		script = directory / "main.py"
	if not script.is_file():
		return None, None

	sandbox = Path(tempfile.mkdtemp(prefix="ayu-repair-"))
	working = sandbox / directory.name
	shutil.copytree(directory, working,
					ignore=shutil.ignore_patterns("__pycache__"))
	name = f"repair_{directory.name.replace('-', '_').replace('.', '_')}"
	sys.modules.pop(name, None)
	spec = importlib.util.spec_from_file_location(name, working / script.name)
	module = importlib.util.module_from_spec(spec)
	sys.modules[name] = module
	try:
		spec.loader.exec_module(module)
		from base_plugin import activate_base_plugins
		instance = activate_base_plugins(vars(module))
		return instance, module
	except Exception:
		return None, None
	finally:
		pass


def declared_defaults(instance, module) -> dict:
	"""The plugin's own defaults, which are what a clobbered value becomes."""
	if instance is None:
		return {}
	getter = getattr(instance, "declared_defaults", None)
	if callable(getter):
		try:
			values = getter()
			if isinstance(values, dict):
				return values
		except Exception:
			pass
	if module is None:
		return {}
	try:
		from ayugram_compat import schema
		rows = schema.rows_for(instance, vars(module))
		out = {}
		for row in rows:
			key, kind = row.get("key"), row.get("type")
			if not key or kind in ("header", "note"):
				continue
			out[key] = row.get("default")
		return out
	except Exception:
		return {}


def main() -> int:
	if "--apply" not in sys.argv:
		print(__doc__)
		print("Run it with --apply to change the files.")
		return 2
	apply = True
	print(f"plugins: {ROOT}")
	changed_files = 0
	changed_keys = 0
	skipped = []

	for directory in sorted(ROOT.iterdir()):
		if not directory.is_dir() or not list(directory.glob("*.py")):
			continue
		path = directory.with_suffix(".settings.json")
		if not path.is_file():
			continue
		try:
			stored = json.loads(path.read_text(encoding="utf-8-sig"))
		except Exception:
			continue
		values = stored.get("settings")
		if not isinstance(values, dict):
			continue
		marked = [k for k, v in values.items() if v == MARKER]
		if not marked:
			continue

		instance, module = load_plugin(directory)
		defaults = declared_defaults(instance, module)

		repaired = dict(values)
		notes = []
		for key in marked:
			if key in defaults and defaults[key] is not None:
				repaired[key] = defaults[key]
				notes.append(f"{key} -> {defaults[key]!r}")
			else:
				repaired.pop(key, None)
				notes.append(f"{key} -> removed (no default)")
		if not notes:
			skipped.append(directory.name)
			continue
		print(f"\n{directory.name}")
		for note in notes:
			print(f"  {note}")
		if apply:
			stored["settings"] = repaired
			path.write_text(
				json.dumps(stored, indent=2, ensure_ascii=False),
				encoding="utf-8")
			changed_files += 1
			changed_keys += len(notes)

	print()
	print(f"repaired {changed_keys} value(s) in {changed_files} file(s)")
	if skipped:
		print("could not work out the default for:", ", ".join(skipped))
	if not changed_files and not skipped:
		print("nothing carried the marker; nothing to do")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())