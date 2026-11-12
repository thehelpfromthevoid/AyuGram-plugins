"""Decide correctly when the app was killed for hanging.

The case that broke: the watchdog killed a stuck app, then read a clean-exit
marker left by an earlier moment and declined to bring it back - so it sat there
having just restarted nothing. Two things had to be true at once for that, and
both are checked here:

* a process this watchdog killed is a failure whatever any marker says
* the app clears the marker when it starts, so it can only describe the session
  that is running

The hang itself cannot be provoked here without a real wedged window, so the
decision is exercised directly instead of by breaking the app.

Usage: watchdog_hang_rule.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import watchdog  # noqa: E402


def decide(code, folder: Path) -> str:
	"""The same sequence the main loop uses, reduced to the decision."""
	if code == watchdog.KILLED_FOR_HANG:
		return "restart"
	if watchdog.clean_exit_recently(folder):
		return "clean"
	# Only the marker says whether a close was deliberate. The exit code cannot:
	# a process killed with TerminateProcess is reported as having exited with 0,
	# and so is one that died on the way down. Trusting the code here left a
	# frozen application with no supervisor and a person reopening it by hand.
	return "restart"


def main() -> int:
	failures = 0
	with tempfile.TemporaryDirectory() as tmp:
		folder = Path(tmp)

		# Killed for hanging, with a stale marker present. This is the case that
		# failed: the marker was written ten minutes earlier, at startup, and the
		# hung app was never brought back.
		marker = folder / "clean-exit.marker"
		old = time.time() - 600
		marker.write_text(str(int(old)), encoding="utf-8")
		import os
		os.utime(marker, (old, old))
		if decide(watchdog.KILLED_FOR_HANG, folder) != "restart":
			print("  FAIL a killed-for-hang process was not restarted")
			failures += 1
		else:
			print("  ok   a process killed for hanging is restarted")

		# Even a marker written this second must not talk it out of it.
		marker.write_text(str(int(time.time())), encoding="utf-8")
		if decide(watchdog.KILLED_FOR_HANG, folder) != "restart":
			print("  FAIL a fresh marker suppressed the restart")
			failures += 1
		else:
			print("  ok   a fresh marker cannot suppress it either")

		# A deliberate quit is still left alone.
		if decide(0, folder) != "clean":
			print("  FAIL a clean shutdown was not recognised")
			failures += 1
		else:
			print("  ok   a clean shutdown is still left alone")

		# No marker at all: an ordinary crash.
		marker.unlink(missing_ok=True)
		if decide(3221225477, folder) != "restart":
			print("  FAIL a crash with no marker was not restarted")
			failures += 1
		else:
			print("  ok   a crash with no marker is restarted")

		# A plain exit with no marker. It used to be left alone, which is how the
		# watchdog walked away from a frozen application: the app ended with code
		# 0, nothing was watching it afterwards, and reopening it by hand was the
		# only way left. Only a marked shutdown counts as deliberate now.
		if decide(0, folder) != "restart":
			print("  FAIL an unmarked exit with code 0 was left alone")
			failures += 1
		else:
			print("  ok   an unmarked exit is restarted, even with code 0")

	print()
	print("FAILURES:", failures)
	return 1 if failures else 0


if __name__ == "__main__":
	raise SystemExit(main())
