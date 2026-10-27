"""End-to-end check of the watchdog: crash recovery and no duplicate spawning.

Restarts and kills are slow, so this drives the real thing rather than mocking
it. Everything it starts, it stops again.

Usage: watchdog_e2e.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import watchdog  # noqa: E402

EXE = Path(os.environ["AYUGRAM_EXE"]) if os.environ.get("AYUGRAM_EXE") else HERE / "AyuGram.exe"
LOG = Path(os.environ["TEMP"]) / "wd_e2e.log"
PYTHON = sys.executable


def wait_for(predicate, timeout, what):
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		value = predicate()
		if value:
			return value
		time.sleep(1.0)
	print(f"  FAIL timed out waiting for {what}")
	return None


def main() -> int:
	failures = 0
	if not EXE.is_file():
		print(f"  skip {EXE} is not there")
		return 0

	# Start from a clean slate so the first launch is really ours.
	for pid in watchdog.running_pids(EXE):
		watchdog.kill(pid)
	time.sleep(3)
	LOG.unlink(missing_ok=True)

	wd = subprocess.Popen(
		[PYTHON, str(HERE / "watchdog.py"), "--exe", str(EXE), "--log", str(LOG)],
		stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
	try:
		first = wait_for(
			lambda: (watchdog.running_pids(EXE) or [None])[0], 90,
			"the app to start")
		if not first:
			failures += 1
		else:
			print(f"  ok   started pid={first}")

			# A second watchdog must not add another copy: Telegram keeps one
			# instance, so the extra process would exit at once and look like a
			# crash, restarting forever.
			second = subprocess.Popen(
				[PYTHON, str(HERE / "watchdog.py"), "--exe", str(EXE),
				 "--log", str(LOG)],
				stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
			try:
				out, _ = second.communicate(timeout=40)
			except subprocess.TimeoutExpired:
				second.kill()
				out = ""
			time.sleep(2)
			pids = watchdog.running_pids(EXE)
			if len(pids) > 1:
				print(f"  FAIL {len(pids)} instances are running: {pids}")
				failures += 1
			elif "already running" in out:
				print("  ok   a second watchdog adopts the running app")
			else:
				print("  ok   still one instance")
				if "already running" not in out:
					print("       note: the second watchdog said nothing about it")

			# Crash recovery.
			watchdog.kill(first)
			revived = wait_for(
				lambda: next(
					(p for p in watchdog.running_pids(EXE) if p != first), None),
				120, "the app to come back after a crash")
			if revived:
				print(f"  ok   restarted after a crash: pid={revived}")
			else:
				print("  FAIL the app did not come back")
				failures += 1

			text = LOG.read_text(encoding="utf-8") if LOG.is_file() else ""
			if "restarting" in text:
				print("  ok   the restart is written down")
			else:
				print("  FAIL the restart was not logged")
				failures += 1
			for pid in watchdog.running_pids(EXE):
				watchdog.kill(pid)
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