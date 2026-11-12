"""Keep AyuGram running: restart it when it crashes or stops responding.

A plugin can still take the process down, and a hung window is worse than a
crashed one because there is nothing to click. This runs the app as a child and
watches it, so recovering is automatic and needs nobody at the keyboard.

Two failure modes are handled:

* It exits. If the exit code says it was terminated rather than closed, it is
  started again. A clean exit is left alone - quitting on purpose must not turn
  into a loop.
* It stops answering. A window that has not replied to a trivial message for
  several seconds is not busy, it is stuck, so it is killed and started again.
  Windows itself will not do this: a hung window stays on screen and keeps its
  message queue.

There is a restart budget on purpose. If the app dies the moment it starts, that
is not something to paper over by retrying forever - the budget stops the loop
and writes down what happened, so the cause is one log away instead of being
hidden behind the tenth attempt.

Usage:
    python watchdog.py [--exe PATH] [--folder PATH] [--no-restart-on-hang]

The defaults run the AyuGram.exe sitting next to this script, which is the copy
in the application folder.

AyuGram plugin host for Desktop, (c) @CE0fbi
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent

# How long the window may ignore a null message before it counts as hung.
#
# This used to be five seconds, on the reasoning that the interface answers
# faster than that on its own. It does not. Opening a large chat, switching
# between accounts, laying out an album, a database migration, a dialog that
# has to be built before it pumps messages, a plugin handler doing real work -
# all of those have been measured stopping the window for longer than that while
# the application was perfectly healthy and finished a moment later.
#
# The cost of being wrong is not symmetric. A restart loses the window, the
# draft, the scroll position and whatever was half-typed, and the user sees an
# application that "falls over every few minutes". Waiting a little longer on an
# application that is genuinely stuck costs almost nothing by comparison: it is
# already not usable.
#
# So this now asks a question that matches "stuck" rather than "slow": the
# window has to stay silent, continuously, long enough that nobody is waiting
# on it any more.
#
# It was 45 seconds, which turned a twenty second freeze into a twenty five
# second one: the restart is not the complaint, the waiting is. Twenty is a
# compromise - long enough that opening a large chat, sending an album or
# building a dialog are not mistaken for a crash, short enough that a real
# freeze costs half as much waiting. And when it does fire, the process is
# dumped first, so the next one is explained rather than guessed at.
HUNG_SECONDS = 20.0
# Consecutive hung checks before killing, on top of the time above. Three polls
# of a second each means the stall has to persist across several checks, so one
# unlucky sample cannot end it.
HUNG_STRIKES = 3
# How often to look.
POLL_SECONDS = 1.0
# Restarts allowed inside the budget window. A crash that survives this many
# attempts is a bug to fix, not a condition to retry - but see COOLDOWN_START
# for what happens next, which is not "stop".
BUDGET_STARTS = 3
BUDGET_SECONDS = 300.0
# What to do once the budget is spent: wait this long before trying again, and
# double it each time up to the maximum. Unbounded retries in a tight loop would
# hide a crash-on-startup behind a busy process; unbounded retries with a growing
# wait still bring the application back when the cause was transient, and a
# messenger that never comes back on its own is worse than a slow one.
COOLDOWN_START = 60.0
COOLDOWN_MAX = 900.0
# How long an app may be up before a stuck window counts as a hang. Starting up
# is busy work and its window can go unanswered while it happens.
STARTUP_GRACE = 90.0
# Pause between a crash and the next start, so a failure that is slow to appear
# - a missing file, a locked database - is not turned into a tight loop.
RESTART_PAUSE = 5.0
# Memory above which the application is started again.
#
# It grows steadily - about five megabytes a minute while a chat sits open -
# and it was measured at 1.9 GB after three hours. Left alone that ends in an
# allocation failure, which is a crash the watchdog cannot prevent: by the time
# the window stops answering, memory is the least of the problems. Restarting
# while it is still working is the cheaper outcome, and the state that matters
# lives in Telegram's servers, not in this process.
#
# The limit is high on purpose. A messenger holding two and a half gigabytes is
# untidy, not broken, and nobody wants their application restarted for tidiness.
MEMORY_LIMIT_MB = 3072.0
# ...but not restarted for one spike either.
MEMORY_LIMIT_SECONDS = 120.0

# Returned by _watch_until_it_stops when this watchdog ended the process
# itself, because it had stopped answering. Distinct from any exit status,
# because an exit status cannot be trusted for a process that is already gone.
KILLED_FOR_HANG = "killed for hang"
# Ended on purpose while it still worked: not a failure, and not a quit.
RESTARTED_FOR_MEMORY = "restarted for memory"
# Ended on purpose while it still worked: not a failure, and not a quit.

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_NULL = 0x0000
SMTO_ABORTIFHUNG = 0x0002

user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

PROCESS_QUERY_LIMITED = 0x1000
PROCESS_TERMINATE = 0x0001


def top_level_windows(pid: int) -> list:
    """The visible top-level windows belonging to `pid`."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _visit(hwnd, _param):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(_visit, 0)
    return found


def window_answers(hwnd) -> bool:
    """True when the window replies to a null message within a moment."""
    result = ctypes.c_size_t()
    ok = user32.SendMessageTimeoutW(
        hwnd, WM_NULL, 0, 0, SMTO_ABORTIFHUNG, 1000, ctypes.byref(result))
    return bool(ok)


def is_hung(pid: int) -> bool:
    """True when the process has a window and none of them answer."""
    windows = top_level_windows(pid)
    if not windows:
        # No window yet: still starting up, or a tray-only session. Neither is
        # a hang, and killing here would restart a healthy start.
        return False
    return not any(window_answers(hwnd) for hwnd in windows)


def _alive(pid: int) -> bool:
	"""Whether a pid is still running."""
	import ctypes
	from ctypes import wintypes

	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	PROCESS_QUERY_LIMITED = 0x1000
	STILL_ACTIVE = 259
	# Stated for the same reason as in running_pids: an untyped call gets its
	# arguments marshalled by guesswork.
	kernel32.OpenProcess.argtypes = [
		wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
	kernel32.OpenProcess.restype = wintypes.HANDLE
	kernel32.GetExitCodeProcess.argtypes = [
		wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
	kernel32.GetExitCodeProcess.restype = wintypes.BOOL
	kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
	handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
	if not handle:
		return False
	try:
		code = wintypes.DWORD()
		if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
			return False
		return code.value == STILL_ACTIVE
	finally:
		kernel32.CloseHandle(handle)


def kill(pid: int) -> bool:
    handle = kernel32.OpenProcess(
        PROCESS_TERMINATE | PROCESS_QUERY_LIMITED, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 0))
    finally:
        kernel32.CloseHandle(handle)


def _prune_dumps(folder: Path, keep: int = 2) -> None:
	"""Keep only the newest few dumps.

	Each one is around a hundred megabytes, because an application holding a
	gigabyte of memory still writes a dump of a hundred. A machine that freezes
	regularly would fill its disk with evidence of it, and a full disk stops the
	application being written to as well - turning a recoverable freeze into an
	unrecoverable one. The newest are kept because they are the ones being
	looked at.
	"""
	try:
		dumps = sorted(
			(folder.glob("hang-*.dmp")),
			key=lambda p: p.stat().st_mtime,
			reverse=True,
		)
	except Exception:
		return
	for stale in dumps[keep:]:
		try:
			stale.unlink()
		except OSError:
			pass


def dump_on_hang(pid: int, folder: Path) -> Optional[str]:
	"""Ask a separate process for a dump of `pid`; say what came of it.

	Returns a line for the log. The dump is small on purpose: the instruction
	pointer of every thread is what answers "why is the window not answering",
	and dumping a gigabyte of memory to get it would fill the disk instead.
	"""
	target = folder / f"hang-{pid}-{int(time.time())}.dmp"
	worker = HERE / "dump_process.py"
	if not worker.is_file():
		return "no dumper beside the watchdog"
	_prune_dumps(folder)
	try:
		# Not waited on: the watchdog's one job is to bring the window back, and
		# the dump is worth nothing if it stands in the way of that.
		subprocess.Popen(
			[sys.executable, str(worker), str(pid), str(target)],
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
		)
	except Exception as e:
		return f"could not start the dumper: {e}"
	for _ in range(60):
		try:
			if target.is_file() and target.stat().st_size:
				return (f"wrote {target.name} "
						f"({target.stat().st_size // 1024} KB)")
		except Exception:
			return "the dumper produced nothing"
		time.sleep(0.5)
	return "the dumper produced nothing"


def working_set_mb(pid: int) -> float:
	"""How much memory the process has committed, in megabytes."""
	try:
		import ctypes
		from ctypes import wintypes

		class _Counters(ctypes.Structure):
			_fields_ = [
				("cb", wintypes.DWORD),
				("PageFaultCount", wintypes.DWORD),
				("PeakWorkingSetSize", ctypes.c_size_t),
				("WorkingSetSize", ctypes.c_size_t),
				("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
				("QuotaPagedPoolUsage", ctypes.c_size_t),
				("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
				("QuotaNonPagedPoolUsage", ctypes.c_size_t),
				("PagefileUsage", ctypes.c_size_t),
				("PeakPagefileUsage", ctypes.c_size_t),
			]

		PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
		kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
		psapi = ctypes.WinDLL("psapi", use_last_error=True)
		kernel32.OpenProcess.restype = wintypes.HANDLE
		kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
		psapi.GetProcessMemoryInfo.argtypes = [
			wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
		psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
		handle = kernel32.OpenProcess(
			PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
		if not handle:
			return 0.0
		try:
			counters = _Counters()
			counters.cb = ctypes.sizeof(_Counters)
			if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters),
										   ctypes.sizeof(_Counters)):
				return 0.0
			return counters.WorkingSetSize / (1024.0 * 1024.0)
		finally:
			kernel32.CloseHandle(handle)
	except Exception:
		return 0.0


def clean_exit_recently(folder: Path, within: float = 120.0) -> bool:
	"""Whether the app shut down on purpose just before it stopped.

	The exit code of a process that has already gone cannot be trusted - a
	terminated one is sometimes reported as having exited cleanly - so the app
	writes a marker on its normal shutdown path and that is what gets read. It is
	dated rather than merely present, because a marker left over from an earlier
	session must not make the next crash look like a quit.
	"""
	marker = folder / "clean-exit.marker"
	try:
		age = time.time() - marker.stat().st_mtime
	except OSError:
		return False
	return 0 <= age < within


def crashed(exit_code: int) -> bool:
	"""Whether an exit code describes a failure rather than a quit.

	Zero is a clean exit. Windows reports an access violation as its own code,
	and a kill as 1 or a large status, so anything else is treated as a crash.
	"""
	return exit_code not in (0,)


def running_pids(exe: Path) -> list:
	"""Pids of the processes running this executable, ours or otherwise."""
	import ctypes
	from ctypes import wintypes

	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	TH32CS_SNAPPROCESS = 0x00000002
	PROCESS_QUERY_LIMITED = 0x1000
	wanted = exe.name.lower()

	class _Entry(ctypes.Structure):
		# The whole PROCESSENTRY32W, not just the leading fields. The snapshot
		# call checks dwSize against what it expects and refuses the whole
		# enumeration when it does not match, which looks like "there are no
		# processes" rather than as a malformed structure.
		_fields_ = [
			("dwSize", wintypes.DWORD),
			("cntUsage", wintypes.DWORD),
			("th32ProcessID", wintypes.DWORD),
			("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
			("th32ModuleID", wintypes.DWORD),
			("cntThreads", wintypes.DWORD),
			("th32ParentProcessID", wintypes.DWORD),
			("pcPriClassBase", wintypes.LONG),
			("dwFlags", wintypes.DWORD),
			("szExeFile", wintypes.WCHAR * 260),
		]

	# Every signature stated. Without them ctypes guesses, and a guessed
	# signature for this call passes the string buffer and the size pointer in
	# the wrong registers: the call appears to work and quietly finds nothing,
	# which showed up as the watchdog insisting no instance was running while
	# one was plainly there.
	kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
	kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
	kernel32.Process32FirstW.argtypes = [
		wintypes.HANDLE, ctypes.POINTER(_Entry)]
	kernel32.Process32FirstW.restype = wintypes.BOOL
	kernel32.Process32NextW.argtypes = [
		wintypes.HANDLE, ctypes.POINTER(_Entry)]
	kernel32.Process32NextW.restype = wintypes.BOOL
	kernel32.OpenProcess.argtypes = [
		wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
	kernel32.OpenProcess.restype = wintypes.HANDLE
	kernel32.QueryFullProcessImageNameW.argtypes = [
		wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
		ctypes.POINTER(wintypes.DWORD)]
	kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
	kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

	snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
	if snapshot in (0, wintypes.HANDLE(-1).value):
		return []
	found: list = []
	entry = _Entry()
	entry.dwSize = ctypes.sizeof(_Entry)
	try:
		if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
			while True:
				handle = kernel32.OpenProcess(
					PROCESS_QUERY_LIMITED, False, entry.th32ProcessID)
				if handle:
					try:
						name = ctypes.create_unicode_buffer(1024)
						size = wintypes.DWORD(1024)
						if kernel32.QueryFullProcessImageNameW(
								handle, 0, name, ctypes.byref(size)):
							if Path(name.value).name.lower() == wanted:
								found.append(entry.th32ProcessID)
					finally:
						kernel32.CloseHandle(handle)
				if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
					break
	finally:
		kernel32.CloseHandle(snapshot)
	return found


def _adopt(pid: int, args, log) -> None:
	"""Watch an instance somebody else started, on the same terms.

	Used when the app is already running. It is not ours to restart on a clean
	exit, but a crash or a hang is still worth recovering from, so the same
	checks run - and a crash still means starting a fresh one.
	"""
	hang_checks = 0
	while True:
		# The watchdog is the only thing standing between a frozen application
		# and a user who has to kill it by hand. An exception escaping here ends
		# that protection and leaves the frozen process behind, which is the one
		# outcome worth spending a little code to rule out: it did happen, on
		# the way to a kill, and the app stayed hung with nothing watching it.
		try:
			if not _alive(pid):
				log(f"pid={pid} is gone; not restarting, it was not started here")
				return
			if not args.no_restart_on_hang and is_hung(pid):
				hang_checks += 1
				if hang_checks >= HUNG_STRIKES:
					log(f"pid={pid} stopped responding, killing it")
					log(f"pid={pid} {dump_on_hang(pid, HERE)}")
					kill(pid)
					hang_checks = 0
			else:
				hang_checks = 0
		except Exception as e:
			log(f"watching pid={pid} raised {type(e).__name__}: {e}; "
				f"carrying on")
			hang_checks = 0
		time.sleep(POLL_SECONDS)


def _watch_until_it_stops(pid: int, started: float, args, log):
	"""Watch one instance until it is gone. Returns how it ended.

	KILLED_FOR_HANG means this watchdog ended it for being stuck; anything else
	is the exit code the process reported.
	"""
	# Nothing is judged a hang until it has been up long enough to have a window
	# that pumps messages. A starting app is busy - connecting, loading the
	# interface - and its window can go unanswered for several seconds, so
	# checking too early restarts an app that was on its way up.
	hung_since = 0.0
	strikes = 0
	full_since = 0.0
	while True:
		time.sleep(POLL_SECONDS)
		if not _alive(pid):
			return _exit_code(pid)
		if args.no_restart_on_hang:
			continue
		if (time.monotonic() - started) < STARTUP_GRACE:
			continue

		# Memory is checked first and on its own: an application this size is
		# going to fail an allocation eventually, and restarting it while it is
		# still working is a smaller interruption than the crash that is coming.
		if not args.no_restart_on_memory:
			mb = working_set_mb(pid)
			if mb > MEMORY_LIMIT_MB:
				if not full_since:
					full_since = time.monotonic()
					log(f"pid={pid} is using {mb:.0f} MB")
				elif time.monotonic() - full_since >= MEMORY_LIMIT_SECONDS:
					log(f"pid={pid} has been over {MEMORY_LIMIT_MB:.0f} MB for "
						f"{int(MEMORY_LIMIT_SECONDS)}s, restarting it while it "
						f"still works")
					kill(pid)
					return RESTARTED_FOR_MEMORY
			else:
				full_since = 0.0

		if not is_hung(pid):
			hung_since = 0.0
			strikes = 0
			continue
		now = time.monotonic()
		if not hung_since:
			hung_since = now
			continue
		strikes += 1
		if now - hung_since < HUNG_SECONDS or strikes < HUNG_STRIKES:
			continue
		log(f"pid={pid} stopped responding, killing it")
		# The evidence goes first, in a process of its own.
		#
		# Nothing inside the application can report this: it freezes with the
		# interpreter lock held, and faulthandler and a plain stack dump both need
		# that lock to format anything, so they stay silent. Calling
		# MiniDumpWriteDump from here took the watchdog down the first time,
		# which left the frozen application with nothing watching it, so the dump
		# is taken by a separate program that can fail without taking this one
		# with it.
		dumped = dump_on_hang(pid, HERE)
		log(f"pid={pid} {dumped}" if dumped
			else f"pid={pid}: no dump was taken")
		kill(pid)
		for _ in range(15):
			if not _alive(pid):
				break
			time.sleep(1.0)
		else:
			log(f"pid={pid} would not die")
		return KILLED_FOR_HANG


def _exit_code(pid: int) -> int:
	"""The exit status of a process that has ended, or -1 if it cannot be read."""
	import ctypes
	from ctypes import wintypes

	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	kernel32.OpenProcess.argtypes = [
		wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
	kernel32.OpenProcess.restype = wintypes.HANDLE
	kernel32.GetExitCodeProcess.argtypes = [
		wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
	kernel32.GetExitCodeProcess.restype = wintypes.BOOL
	kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
	handle = kernel32.OpenProcess(0x1000, False, pid)
	if not handle:
		return -1
	try:
		code = wintypes.DWORD()
		if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
			return -1
		return int(code.value)
	finally:
		kernel32.CloseHandle(handle)


def _read_pid(claim: Path) -> int:
	"""The pid recorded in the claim file, or 0 when it says nothing useful."""
	try:
		return int(claim.read_text(encoding="ascii").strip())
	except Exception:
		return 0


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--exe", help="AyuGram.exe to run")
	parser.add_argument("--folder", help="working directory, defaults to the exe's")
	parser.add_argument("--log", help="log file, defaults to watchdog.log beside this script")
	parser.add_argument("--max-starts", type=int, default=BUDGET_STARTS,
						help="restarts allowed inside the budget window")
	parser.add_argument("--budget-seconds", type=float, default=BUDGET_SECONDS)
	parser.add_argument("--no-restart-on-hang", action="store_true",
						help="restart on crash but leave a hung window alone")
	parser.add_argument("--no-restart-on-memory", action="store_true",
						help="leave a process that grew very large alone")
	args = parser.parse_args()

	exe = Path(args.exe) if args.exe else HERE / "AyuGram.exe"
	folder = Path(args.folder) if args.folder else exe.parent
	log_path = Path(args.log) if args.log else HERE / "watchdog.log"
	if not exe.is_file():
		print(f"AyuGram.exe not found at {exe}", file=sys.stderr)
		return 2

	lines: list = []

	# One watchdog at a time, and a way to ask whether one is running at all.
	#
	# Two of them fight: each restarts the application, the second instance
	# loses the single-instance check and exits, and the pair turns into a loop
	# of short-lived processes that looks exactly like an application which will
	# not stay up. That was seen twice during this work, and each time the cause
	# was a watchdog somebody had started earlier by hand and forgotten.
	claim = HERE / "watchdog.pid"
	existing = _read_pid(claim)
	if existing and existing != os.getpid() and _alive(existing):
		print(
			f"another watchdog is already running as pid {existing}; "
			f"leaving it alone. Stop it first if you meant to replace it.",
			file=sys.stderr,
		)
		return 3
	try:
		claim.write_text(str(os.getpid()), encoding="ascii")
	except Exception:
		claim = None

	def log(message: str) -> None:
		stamp = time.strftime("%H:%M:%S")
		text = f"[{stamp}] {message}"
		print(text, flush=True)
		lines.append(text)
		# Kept short on purpose: this file is what gets read after a crash, and a
		# log nobody can skim is a log nobody reads.
		if len(lines) > 400:
			del lines[:200]
		try:
			log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
		except OSError:
			pass

	log(f"watching {exe}")
	starts: list = []
	cooldowns = 0
	failed_starts = 0
	child = None
	try:
		while True:
			# Telegram keeps one instance: a second copy exits immediately. Started
			# on top of a running app that exit looks exactly like a crash - a
			# non-zero code and no window - so the watchdog would restart it
			# forever, each attempt spawning one more doomed process. If an
			# instance is already up, watch that one instead of adding to it.
			existing = [
				p for p in running_pids(exe)
				if p != (child.pid if child else 0)
			]
			if existing:
				pid = existing[0]
				started = time.monotonic()
				log(f"pid={pid} is already running, watching it instead")
				code = _watch_until_it_stops(pid, started, args, log)
			else:
				child = subprocess.Popen([str(exe)], cwd=str(folder))
				starts.append(time.monotonic())
				started = time.monotonic()
				log(f"started pid={child.pid}")
				code = _watch_until_it_stops(child.pid, started, args, log)

			# A process this watchdog killed is a failure by definition, whatever
			# any marker says. It had already decided the app was stuck; a
			# marker left over from any earlier moment must not talk it out of
			# restarting, which is exactly what happened: a hung app was killed
			# and then not brought back.
			if code == KILLED_FOR_HANG:
				log("it was killed for being stuck, so restarting")
			elif code == RESTARTED_FOR_MEMORY:
				# Ended on purpose while it still worked, so it goes straight
				# back up without being counted as a failure: it would otherwise
				# spend the restart budget on a process that never misbehaved.
				log("restarting")
				child = None
				time.sleep(RESTART_PAUSE)
				continue
			# Otherwise the marker decides whether this was a deliberate close.
			#
			# The exit code cannot say so: a process killed with
			# TerminateProcess is reported as having exited with 0, and so is
			# one that died on the way down. Trusting the code here is how
			# the watchdog walked away from a frozen application - the app
			# ended with 0, nothing was watching it afterwards, and reopening
			# it by hand was the only way left.
			elif clean_exit_recently(folder):
				log("shut down cleanly, not restarting")
				return 0
			else:
				log(f"exited with code {code} and no clean-shutdown marker,"
					 f"so restarting")


			# Counted as failures in a row, not as starts inside a window.
			#
			# The window version missed the case it was written for. An
			# application that dies on start-up takes one second to die and five
			# to pause, so its starts never crowd into the window and the budget
			# never fired: it was restarted for as long as anyone watched. What
			# the budget is really about is an application that will not stay up,
			# and that is a question about consecutive failures.
			uptime = time.monotonic() - started
			if uptime >= args.budget_seconds:
				# It stayed up long enough to count as having started properly,
				# so this was not a crash on launch and the run of failures ends.
				failed_starts = 0
			failed_starts += 1
			if failed_starts > args.max_starts:
				# Backing off, not giving up.
				#
				# Stopping here meant one bad patch, or one crash that happened
				# to happen three times in five minutes, left the user with no
				# messenger at all and nothing running to tell them why - the
				# worst possible outcome of a supervisor whose entire job is
				# that this does not happen.
				#
				# The reason for the budget still holds: a crash on start-up is
				# a bug to fix, and retrying it in a tight loop only hides it
				# and burns the disk. A growing wait keeps that true - the
				# application is not being restarted every few seconds - while
				# still coming back by itself when the cause is transient, which
				# is the common case: a locked file during an update, a disk that
				# was busy, a port still in use.
				cooldown = min(
					COOLDOWN_MAX,
					COOLDOWN_START * (2 ** min(cooldowns, 4)),
				)
				cooldowns += 1
				log("")
				log(f"backing off: {failed_starts} starts in a row all died "
					f"within {int(args.budget_seconds)}s (last code {code})")
				log(f"trying again in {int(cooldown)}s, then slower. "
					f"The cause is above and in AyuGram's own log.txt.")
				failed_starts = 0
				time.sleep(cooldown)
				continue
			log(f"exited with code {code}, restarting")
			child = None
			time.sleep(RESTART_PAUSE)
	finally:
		if child is not None and child.poll() is None:
			kill(child.pid)


if __name__ == "__main__":
	raise SystemExit(main())
