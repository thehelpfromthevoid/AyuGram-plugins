"""Check the hang detector does not mistake a healthy app for a stuck one.

A false positive is the failure that matters here. If a working window is
reported as hung, the watchdog kills and restarts a healthy app over and over,
which is worse than having no watchdog at all.

The healthy fixture is the real application, because that is the only window
worth being sure about. A synthetic window is not: a window created without a
message loop never answers a posted message, so it looks hung to Windows itself
and proves nothing about the detector.

Usage: watchdog_check.py
"""
from __future__ import annotations

import subprocess
import sys
import time
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import watchdog  # noqa: E402

DEFAULT_EXE = Path(os.environ["AYUGRAM_EXE"]) if os.environ.get("AYUGRAM_EXE") else HERE / "AyuGram.exe"


def live_pid(exe: Path) -> int:
	"""The pid of a running AyuGram, starting one if none is up."""
	import ctypes
	from ctypes import wintypes

	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	TH32CS_SNAPPROCESS = 0x00000002
	PROCESS_QUERY_LIMITED = 0x1000

	class _Entry(ctypes.Structure):
		_fields_ = [
			("dwSize", wintypes.DWORD),
			("cntUsage", wintypes.DWORD),
			("th32ProcessID", wintypes.DWORD),
		]

	kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
	snapshot = kernel32.CreateToolhelp32Snapshot(
		TH32CS_SNAPPROCESS, 0)
	if snapshot == -1:
		return 0
	found = 0
	entry = _Entry()
	entry.dwSize = ctypes.sizeof(_Entry)
	if kernel32.Process32First(snapshot, ctypes.byref(entry)):
		while True:
			handle = kernel32.OpenProcess(
				PROCESS_QUERY_LIMITED, False, entry.th32ProcessID)
			if handle:
				name = ctypes.create_unicode_buffer(1024)
				size = wintypes.DWORD(1024)
				if kernel32.QueryFullProcessImageNameW(
						handle, 0, name, ctypes.byref(size)):
					if Path(name.value).name.lower() == exe.name.lower():
						found = entry.th32ProcessID
						kernel32.CloseHandle(handle)
						break
				kernel32.CloseHandle(handle)
			if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
				break
	kernel32.CloseHandle(snapshot)
	return found


def _backoff_case(watchdog):
	"""Watch something that always crashes, and see whether the watchdog stays.

	Returns "alive", a description of what went wrong, or None when there is no
	usable stand-in for a crashing application.
	"""
	import tempfile
	from pathlib import Path

	# find.exe with no arguments prints its usage and exits with 2: it starts and
	# dies immediately, which is the shape of a crash on start-up.
	candidate = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" \
		/ "find.exe"
	if not candidate.is_file():
		return None

	folder = Path(tempfile.mkdtemp(prefix="ayu-wd-budget-"))
	# A copy, because the watchdog keeps its claim file next to itself and the
	# real one is running: started in place this test would be turned away as a
	# duplicate, which is correct behaviour and says nothing about the budget.
	watcher = folder / "watchdog.py"
	watcher.write_bytes(Path(watchdog.__file__).read_bytes())
	child = subprocess.Popen(
		[
			sys.executable, str(watcher),
			"--exe", str(candidate),
			"--folder", str(folder),
			"--log", str(folder / "watchdog.log"),
			"--max-starts", "1",
			"--budget-seconds", "5",
		],
		stdout=subprocess.DEVNULL,
		stderr=subprocess.DEVNULL,
	)
	try:
		deadline = time.monotonic() + 45
		while time.monotonic() < deadline:
			time.sleep(1.0)
			try:
				text = (folder / "watchdog.log").read_text(encoding="utf-8",
														   errors="replace")
			except OSError:
				continue
			if "backing off" in text:
				# It has to still be there: the whole point is that it comes
				# back by itself instead of leaving the user with nothing.
				time.sleep(1.0)
				if child.poll() is None:
					return "alive"
				return (f"it logged the backoff and then exited with "
						f"{child.returncode}")
		return "never reached the backoff"
	finally:
		if child.poll() is None:
			child.kill()
		child.wait(timeout=10)


def main() -> int:
	failures = 0

	# A process with no window is never hung: it is still starting up, and
	# killing there would restart a healthy start.
	proc = subprocess.Popen(
		[sys.executable, "-c", "import time; time.sleep(30)"])
	try:
		time.sleep(1.0)
		if watchdog.is_hung(proc.pid):
			print("  FAIL a windowless process was reported as hung")
			failures += 1
		else:
			print("  ok   a windowless process is left alone")
	finally:
		proc.kill()

	# A responsive real window must never look hung, however long it has been
	# open and however busy it is.
	exe = DEFAULT_EXE
	pid = live_pid(exe)
	started = None
	if not pid and exe.is_file():
		child = subprocess.Popen([str(exe)], cwd=str(exe.parent))
		started = child.pid
		time.sleep(45)
		pid = live_pid(exe) or started
	if not pid:
		print("  skip no running AyuGram to check")
	else:
		windows = watchdog.top_level_windows(pid)
		if not windows:
			print(f"  skip pid={pid} has no visible window yet")
		else:
			for attempt in range(3):
				took = time.monotonic()
				hung = watchdog.is_hung(pid)
				elapsed = time.monotonic() - took
				print(f"  check {attempt + 1}: pid={pid} windows={len(windows)} "
					f"hung={hung} ({elapsed:.2f}s)")
				if hung:
					print("  FAIL a responsive application was reported as hung")
					failures += 1
					break
			else:
				print("  ok   a responsive application is never reported as hung")
			if elapsed > 3.0:
				print(f"  FAIL the check itself took {elapsed:.1f}s; it would "
						f"restart the app before it finished looking")
				failures += 1
		if started:
			watchdog.kill(started)

	# The threshold itself. It used to be five seconds, which meant a healthy
	# application was killed every few minutes for opening a large chat, and the
	# user saw an app that fell over constantly. This pins the number so it
	# cannot quietly shrink back to treating ordinary slowness as a crash.
	if watchdog.HUNG_SECONDS < 15.0:
		print(f"  FAIL a {watchdog.HUNG_SECONDS:g}s stall is treated as a hang; "
				f"the interface legitimately blocks for longer than that")
		failures += 1
	else:
		print(f"  ok   a stall has to last {watchdog.HUNG_SECONDS:g}s to count "
				f"as a hang")
	if watchdog.HUNG_STRIKES < 2:
		print("  FAIL one slow sample is enough to kill the app")
		failures += 1
	else:
		print(f"  ok   {watchdog.HUNG_STRIKES} consecutive checks are required")

	# An application that dies on every start must not end with nothing running.
	#
	# The budget existed to stop a crash on startup being hidden by a tight
	# retry loop, which is right. What it did instead was stop the watchdog, and
	# the user was left with no messenger and nothing running to explain it - so
	# a budget failure now backs off and tries again.
	crashy = _backoff_case(watchdog)
	if crashy is None:
		print("  skip no always-exiting program to test the restart budget")
	elif crashy == "alive":
		print(f"  ok   after the restart budget it waits and tries again "
				f"({watchdog.COOLDOWN_START:g}s), rather than giving up")
	else:
		print(f"  FAIL the watchdog gave up on a crashing application: {crashy}")
		failures += 1

	print()
	print("FAILURES:", failures)
	return 1 if failures else 0


if __name__ == "__main__":
	raise SystemExit(main())