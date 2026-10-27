"""Drive one installed plugin the way the app does, and print what it does.

The compatibility work so far proved that plugins load and register hooks.
That is not the same as them working: a plugin can load perfectly and still do
nothing, and the only way to find out which is happening is to run it and look.

This loads a plugin, activates it the way the bridge does, feeds a command
through the hook path and reports what came back - including the plugin's own
log, which is written next to it and is otherwise only visible after the fact.

Usage: run_plugin.py [PLUGIN_ID] [--command ".ozon ..."] [--keep]
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

ROOT = Path(os.environ["AYU_PLUGINS_DIR"]) if os.environ.get("AYU_PLUGINS_DIR") else (Path(os.environ["APPDATA"]) / "AyuGramDesktop" / "plugins")
HERE = Path(__file__).resolve().parent


def _roots() -> list:
	"""The places a shim may live, in the order they should be tried.

	The repository keeps the shim under plugins/shim; a deployment flattens it
	and puts the modules in the application root next to standalone/. Building the
	path for one layout means this tool cannot load a single plugin from the
	other, which is what it did from the user's own installation.
	"""
	return [HERE.parent, HERE.parent / "shim", HERE]


def _shim_root() -> Path:
	for base in _roots():
		if (base / "base_plugin.py").is_file():
			return base
	raise SystemExit(
		"no shim found; looked in "
		+ ", ".join(str(b) for b in _roots()))


SHIM = _shim_root()
LOADER = next(
	(base / "standalone" for base in _roots()
	 if (base / "standalone" / "plugin_loader.py").is_file()),
	SHIM)

for path in (str(SHIM), str(LOADER), str(ROOT)):
	if path not in sys.path:
		sys.path.insert(0, path)


class Bridge:
	"""A bridge that records instead of sending, so the run is repeatable."""

	def __init__(self) -> None:
		self.sent = []
		self.toasts = []

	def __getattr__(self, name):
		if name == "hook":
			registry = {}

			def _hook(hook_name, fn=None, *a, **k):
				registry.setdefault(hook_name, []).append(fn)
				return fn
			_hook.registry = registry
			self.hooks = registry
			return _hook
		if name in ("send_text", "send_message"):
			return lambda *a, **k: self.sent.append((name, a, k)) and 1
		if name == "toast":
			return lambda *a, **k: self.toasts.append(str(a[0] if a else ""))
		if name == "run_on_main_thread":
			return lambda fn, *a, **k: fn(*a, **k)
		if name in ("plugins_dir", "data_dir"):
			return str(ROOT)
		if name == "settings_emit":
			return lambda *a, **k: None

		def _stub(*a, **k):
			return None
		return _stub


def main() -> int:
	args = [a for a in sys.argv[1:]]
	keep = "--keep" in args
	if keep:
		args.remove("--keep")
	command = ""
	if "--command" in args:
		index = args.index("--command")
		command = args[index + 1]
		del args[index:index + 2]
	plugin_id = args[0] if args else "ozonsearch_ozon_420"

	directory = ROOT / plugin_id
	if not directory.is_dir():
		print(f"  skip {directory} is not there")
		return 0

	import os
	os.environ["AYU_PLUGINS_DIR"] = str(ROOT)

	log_before = directory.with_suffix(".log")
	log_size = log_before.stat().st_size if log_before.is_file() else 0

	bridge = Bridge()
	sys.modules["ayu_api"] = bridge

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
	spec = importlib.util.spec_from_file_location("under_test", script)
	module = importlib.util.module_from_spec(spec)
	sys.modules["under_test"] = module
	try:
		spec.loader.exec_module(module)
	except Exception as e:
		import traceback
		print(f"  import failed: {type(e).__name__}: {e}")
		traceback.print_exc()
		return 1

	from base_plugin import activate_base_plugins, HookResult, HookStrategy

	instance = activate_base_plugins(vars(module))
	if instance is None:
		print("  no instance was created")
		return 1
	print(f"  instance: {type(instance).__name__}")
	print(f"  hooks registered: {sorted(getattr(bridge, 'hooks', {}).keys())}")

	if command:
		print(f"  feeding: {command!r}")
		# Build the context the way the bridge does for an outgoing message.
		params = types.SimpleNamespace(
			message=command,
			peer=1234567,
			replyToMsg=None,
			replyToTopMsg=None,
		)
		handler = getattr(instance, "on_send_message_hook", None)
		if handler is None:
			print("  the plugin has no on_send_message_hook")
			return 1
		try:
			result = handler(0, params)
			print(f"  hook returned: {result!r}")
		except Exception as e:
			import traceback
			print(f"  the hook raised: {type(e).__name__}: {e}")
			traceback.print_exc()

	print(f"  sends attempted: {len(bridge.sent)}")
	for entry_ in bridge.sent[:6]:
		print(f"    {entry_[0]} {str(entry_[1])[:150]}")
	print(f"  toasts: {bridge.toasts[:6]}")

	# A plugin hands its work to a thread - that is what the search plugin does
	# with `_spawn(self._run_search, ...)` - so answering immediately reports
	# nothing has happened when the real answer is a few seconds away.
	import time

	# A plugin hands its work to a thread - the search plugin does it with
	# `_spawn(self._run_search, ...)` - and warms a session up before searching,
	# so the answer can be a minute away. Waiting stops when something is sent,
	# never on a toast: "searching" is the plugin saying it has started, not
	# that it has finished.
	import time

	deadline = time.monotonic() + 75
	while time.monotonic() < deadline and not bridge.sent:
		time.sleep(0.5)
	print(f"  after waiting: {len(bridge.sent)} send(s), toasts={bridge.toasts[:8]}")
	for entry_ in bridge.sent[:8]:
		print(f"    {entry_[0]} {str(entry_[1])[:220]}")
	if not bridge.sent:
		print("  waited 75s and nothing came back")

	log_after = directory.with_suffix(".log")
	if log_after.is_file():
		text = log_after.read_text(encoding="utf-8", errors="replace")
		fresh = text[log_size:] if len(text) > log_size else text
		print("  --- the plugin's own log ---")
		for line in fresh.strip().splitlines()[-25:]:
			print(f"    {line}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())