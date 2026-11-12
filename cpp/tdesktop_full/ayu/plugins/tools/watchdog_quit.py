"""Prove the watchdog does not fight the user, and that the marker works.

Two separate things, and the second is easy to get wrong:

* Closing the window is not quitting. Telegram goes to the tray, the process
  stays up, and the watchdog must leave it alone - killing it there would make
  the app impossible to close.
* A real quit writes `clean-exit.marker` from the application destructor, and
  only that marker stops the restart. The exit code cannot be used: a process
  killed with TerminateProcess is reported as having exited with 0 on this path,
  so trusting it means every crash is taken for a quit.

Usage: watchdog_quit.py
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import watchdog  # noqa: E402

EXE = Path(os.environ["AYUGRAM_EXE"]) if os.environ.get("AYUGRAM_EXE") else HERE / "AyuGram.exe"
FOLDER = EXE.parent
LOG = Path(os.environ["TEMP"]) / "wd_quit.log"
WM_CLOSE = 0x0010

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                               wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL


def check_marker(failures: int) -> int:
	"""The rule that decides restart or not, on its own."""
	marker = Path(str(FOLDER / "clean-exit.marker"))
	marker.unlink(missing_ok=True)
	if watchdog.clean_exit_recently(FOLDER):
		print("  FAIL a missing marker counted as a clean quit")
		failures += 1
	else:
		print("  ok   no marker means a crash, so it restarts")

	marker.write_text(str(int(time.time())), encoding="utf-8")
	if not watchdog.clean_exit_recently(FOLDER):
		print("  FAIL a fresh marker was not recognised")
		failures += 1
	else:
		print("  ok   a fresh marker means a quit, so it stays shut")

	# A marker left behind by an earlier session must not speak for this one.
	old = time.time() - 3600
	marker.write_text(str(int(old)), encoding="utf-8")
	import os
	os.utime(marker, (old, old))
	if watchdog.clean_exit_recently(FOLDER):
		print("  FAIL yesterday's marker was taken for this moment's quit")
		failures += 1
	else:
		print("  ok   a stale marker does not count")
	marker.unlink(missing_ok=True)
	return failures


def main() -> int:
	failures = check_marker(0)
	if not EXE.is_file():
		print(f"  skip {EXE} is not there")
		print()
		print("FAILURES:", failures)
		return 1 if failures else 0

	for pid in watchdog.running_pids(EXE):
		watchdog.kill(pid)
	time.sleep(3)
	(FOLDER / "clean-exit.marker").unlink(missing_ok=True)
	LOG.unlink(missing_ok=True)

	wd = subprocess.Popen(
		[sys.executable, str(HERE / "watchdog.py"), "--exe", str(EXE),
		 "--log", str(LOG)],
		stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
	try:
		pid = 0
		deadline = time.monotonic() + 90
		while time.monotonic() < deadline and not pid:
			pids = watchdog.running_pids(EXE)
			pid = pids[0] if pids else 0
			time.sleep(2)
		if not pid:
			print("  FAIL the app never started")
			return 1
		print(f"  closing the window of pid={pid}")
		sent = sum(
			1 for hwnd in watchdog.top_level_windows(pid)
			if user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))
		print(f"  asked {sent} window(s) to close")
		time.sleep(20)
		pids = watchdog.running_pids(EXE)
		if pid in pids:
			print("  ok   the app is still up (tray), and was left alone")
		else:
			text = LOG.read_text(encoding="utf-8") if LOG.is_file() else ""
			if "not restarting" in text or "cleanly" in text:
				print("  ok   it exited and the watchdog did not reopen it")
			else:
				print("  FAIL it went away and the watchdog restarted it")
				failures += 1
	finally:
		wd.kill()
		time.sleep(2)
		for pid in watchdog.running_pids(EXE):
			watchdog.kill(pid)

	print()
	print("FAILURES:", failures)
	return 1 if failures else 0


if __name__ == "__main__":
	raise SystemExit(main())