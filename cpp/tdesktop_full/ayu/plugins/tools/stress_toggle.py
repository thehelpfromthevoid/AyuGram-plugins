"""Hammer the two paths that have to be consequence-free, in a real app process.

Switching a plugin off and on used to unload and re-run it, so every toggle
leaked a copy: a second set of hooks, a second thread, a second timer. Saving a
setting used to reach into the interpreter from a worker thread that had never
held the GIL, which faulted inside python314.dll - an access violation the user
saw as "it crashes when I change a setting".

Neither can be checked from a separate Python process: the crash was in the
embedded interpreter inside the app, on a thread the app owns. So this writes a
plugin that does both, on purpose and in a loop, points a test app at it, and
watches whether the process survives.

Usage: stress_toggle.py [SECONDS]
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = Path(os.environ["AYUGRAM_EXE"]) if os.environ.get("AYUGRAM_EXE") else Path(__file__).resolve().parents[4]
PYTHON = Path(os.environ["LOCALAPPDATA"]) / "Python" / "pythoncore-3.14-64" / "python.exe"

STRESS_PLUGIN = '''# Deliberately hostile to the plugin lifecycle. Every part of this existed to
# break something that used to break.
import threading
import time

from base_plugin import BasePlugin, HookResult, HookStrategy
from hook_utils import add_timer


class StressPlugin(BasePlugin):
    def create_settings(self):
        return [
            {"key": "n", "type": "int", "label": "N", "default": 0},
        ]

    def on_plugin_load(self):
        self._stop = threading.Event()
        self._writes = 0
        # Settings from a background thread. This is the call that faulted:
        # it reaches the interpreter from a thread that had never held the GIL.
        self._writer = threading.Thread(
            target=self._write_loop, name="stress-writes", daemon=True)
        self._writer.start()
        # A timer, so the scoped-cancel path has something to stop.
        add_timer(self._tick, 1, 0)
        self.add_on_send_message_hook()
        self.add_on_message_hook()

    def _write_loop(self):
        while not self._stop.is_set():
            for i in range(20):
                if self._stop.is_set():
                    return
                self.set_setting("n", i)
                self._writes += 1
            time.sleep(0.05)

    def _tick(self):
        pass

    def on_send_message_hook(self, account, params):
        return HookResult(HookStrategy.NONE)

    def on_message_hook(self, message):
        pass

    def on_plugin_unload(self):
        self._stop.set()
'''

MANIFEST = {
	"id": "stress_lifecycle",
	# Required. Without a name the manifest is rejected, and the app drops it
	# without a word - which is worth remembering when a plugin the user
	# installed simply never appears in the list.
	"name": "Lifecycle stress",
	"description": "Writes settings from a thread on purpose. For testing only.",
	"version": "1.0",
	"entry": "main.py",
	"api_version": 1,
	"permissions": ["messages", "ui", "storage"],
}


def prepare(root: Path) -> Path:
	plugin = root / "stress_lifecycle"
	plugin.mkdir(parents=True, exist_ok=True)
	(plugin / "main.py").write_text(STRESS_PLUGIN, encoding="utf-8")
	(plugin / "plugin.json").write_text(
		json.dumps(MANIFEST, indent=2), encoding="utf-8")
	state = root / "plugins.json"
	if not state.is_file():
		state.write_text(json.dumps({"stress_lifecycle": True}), encoding="utf-8")
	else:
		values = json.loads(state.read_text(encoding="utf-8"))
		values["stress_lifecycle"] = True
		state.write_text(json.dumps(values, indent=2), encoding="utf-8")
	return plugin


def main() -> int:
	seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 150
	exe = APP / "AyuGram.exe"
	if not exe.is_file():
		print(f"  skip {exe} is not there")
		return 0
	# A scratch profile, so the user's own plugins and state are not touched.
	root = Path(os.environ["LOCALAPPDATA"]) / "Temp" / "ayu-stress-plugins"
	shutil.rmtree(root, ignore_errors=True)
	root.mkdir(parents=True, exist_ok=True)
	prepare(root)

	log = APP / "log.txt"
	log.unlink(missing_ok=True)
	env = dict(os.environ)
	env["AYU_PLUGINS_DIR"] = str(root)
	print(f"  stress plugin in {root}")
	print(f"  running the app for {seconds}s")
	child = subprocess.Popen([str(exe)], cwd=str(APP), env=env)
	failures = 0
	try:
		deadline = time.monotonic() + seconds
		samples = 0
		while time.monotonic() < deadline:
			time.sleep(15)
			samples += 1
			alive = child.poll() is None
			text = ""
			if log.is_file():
				try:
					text = log.read_text(encoding="utf-8", errors="replace")
				except OSError:
					text = ""
			fatal = text.count("FATAL")
			writes = 0
			for line in text.splitlines():
				if "stress" in line.lower():
					writes += 1
			print(f"    t+{(samples * 15):>3}s alive={alive} FATAL={fatal}")
			if not alive:
				print(f"  FAIL the app exited with {child.returncode}")
				failures += 1
				break
			if fatal:
				print("  FAIL a fatal exception was logged")
				failures += 1
				break
		else:
			print("  ok   the app survived the whole run")
	finally:
		if child.poll() is None:
			child.kill()
			try:
				child.wait(timeout=20)
			except subprocess.TimeoutExpired:
				pass
		print(f"  log kept at {log}")
		shutil.rmtree(root, ignore_errors=True)
	print()
	print("FAILURES:", failures)
	return 1 if failures else 0


if __name__ == "__main__":
	raise SystemExit(main())
