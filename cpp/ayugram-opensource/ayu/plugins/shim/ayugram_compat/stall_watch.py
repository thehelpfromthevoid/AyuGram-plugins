"""Notice a stuck application and write down what every thread was doing.

The watchdog outside the process can only say "the window stopped answering"
and then kill it, which loses the one thing worth knowing: where the main
thread was. That happened more than once with no explanation, so this watches
from inside.

It works without the main thread's cooperation, which matters because the
whole problem is that the main thread is not running anything of ours. If the
frame it is executing does not change for a while, the thread is stuck inside a
call it cannot return from, and whatever Python line it entered that call from
is still on the stack.

The poll is cheap and the dump is written only on a stall, with a gap between
dumps so a long freeze produces one report rather than hundreds.

AyuGram plugin host for Desktop, (c) @CE0fbi
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Optional

# Long enough that a busy load or a slow disk is not mistaken for a freeze, and
# short enough to catch it before the watchdog's own timeout kills the process
# and takes the evidence with it.
STALL_SECONDS = 20.0
POLL_SECONDS = 2.0
# One report per stall, not one per poll.
REPORT_GAP = 60.0
# How often to note what the plugins are holding, so memory growth can be
# attributed to them or ruled out without guessing.
SIZES_EVERY = 1800.0

_started = threading.Event()
_lock = threading.Lock()


def log_path() -> Path:
	folder = Path(os.environ.get("AYU_PLUGINS_DIR") or Path.cwd())
	try:
		folder.mkdir(parents=True, exist_ok=True)
	except Exception:
		folder = Path.cwd()
	return folder / "stall-watch.log"


def _stamp() -> str:
	return time.strftime("%Y-%m-%d %H:%M:%S")


def dump(reason: str, seconds: float) -> Optional[Path]:
	"""Write every thread's stack, and say what the main thread was in."""
	target = log_path()
	try:
		frames = sys._current_frames()
	except Exception:
		return None

	lines = [f"--- {_stamp()} {reason} after {seconds:.0f}s ---"]
	for thread in threading.enumerate():
		frame = frames.get(thread.ident)
		if frame is None:
			lines.append(f"\n[{thread.name}] alive={thread.is_alive()} "
						 f"(no frame: it is in native code)")
			continue
		lines.append(f"\n[{thread.name}] alive={thread.is_alive()} "
					 f"daemon={thread.daemon}")
		lines.extend("  " + line for line in
					 traceback.format_stack(frame)[-12:])

	try:
		target.parent.mkdir(parents=True, exist_ok=True)
		with target.open("a", encoding="utf-8", errors="replace") as out:
			out.write("\n".join(lines) + "\n")
		return target
	except Exception:
		return None


def _main_frame_key(frames: dict) -> Any:
	frame = frames.get(threading.main_thread().ident)
	if frame is None:
		return None
	try:
		return (frame.f_code.co_filename, frame.f_lasti)
	except Exception:
		return None


def describe_sizes() -> str:
	"""One line about what the shim is holding.

	Memory grows for all sorts of reasons, and "the application uses more memory
	than it did" is not something anyone can act on. These are the containers
	the plugins own: if one of them climbs while the application sits idle, the
	answer is the plugins. If they stay flat while the process keeps growing,
	the growth is the client's own caches and nothing here will fix it.

	Bytes, not counts: a thousand small timers can matter, and so can five
	hundred megabytes held by one of them, so the line carries both. The
	process weight is here for the same reason - it is what the guard reacts
	to, and without it the two numbers cannot be compared.
	"""
	try:
		from ayugram_compat import hook_utils, settings_store

		timers = len(hook_utils._timers)
		instances = len(hook_utils._live_instances)
	except Exception:
		timers = instances = -1
	try:
		from ayugram_compat import client_utils

		queues = len(client_utils._queues)
		pending = 0
		for queue in list(client_utils._queues.values()):
			pending += len(getattr(queue, "_queue", ()) or ())
	except Exception:
		queues = pending = -1
	try:
		from ayugram_compat import settings_store

		cached = len(settings_store._CACHE)
	except Exception:
		cached = -1

	held = _shim_bytes()
	rss = _process_mb()
	return (f"timers={timers} instances={instances} queues={queues} "
			f"queued={pending} settings={cached} "
			f"shim_kb={held} rss_mb={rss}")


def _shim_bytes() -> int:
	"""Rough byte total of what the shim holds, in KB.

	Deliberately shallow and one level deep. A full walk would follow the
	whole object graph from every timer and cost more than it explains, and
	what matters is the trend, not a figure anyone would quote as exact.
	"""
	total = 0
	seen = 0
	try:
		from ayugram_compat import client_utils, hook_utils, settings_store
	except Exception:
		return -1

	groups = (
		getattr(hook_utils, "_timers", {}),
		getattr(hook_utils, "_live_instances", {}),
		getattr(settings_store, "_CACHE", {}),
	)
	for group in groups:
		try:
			values = list(group.values())
		except Exception:
			continue
		total += sys.getsizeof(group)
		for value in values:
			seen += 1
			if seen > 4000:
				# Enough to see a trend without stalling the watchdog thread.
				break
			try:
				total += sys.getsizeof(value)
			except Exception:
				continue
	try:
		for queue in list(client_utils._queues.values()):
			total += sys.getsizeof(queue)
			items = getattr(queue, "_queue", None)
			if items is not None:
				total += sys.getsizeof(items)
	except Exception:
		pass
	return total // 1024


def _process_mb() -> int:
	"""Working set of this process in MB, or -1 if it cannot be read.

	Типы аргументов заданы явно: через ctypes.windll обёртка режет HANDLE
	до 32 бит, GetProcessMemoryInfo молча возвращает 0, и строка в отчёте
	всегда была бы -1. Типовой вызов на 64-битном Python без них не работает.
	"""
	try:
		import ctypes
		import ctypes.wintypes as wintypes

		class Counters(ctypes.Structure):
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

		kernel = ctypes.WinDLL("kernel32", use_last_error=True)
		psapi = ctypes.WinDLL("psapi", use_last_error=True)
		psapi.GetProcessMemoryInfo.argtypes = [
			wintypes.HANDLE,
			ctypes.POINTER(Counters),
			wintypes.DWORD,
		]
		psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

		counters = Counters()
		counters.cb = ctypes.sizeof(Counters)
		handle = kernel.GetCurrentProcess()
		if not psapi.GetProcessMemoryInfo(
				handle, ctypes.byref(counters), counters.cb):
			return -1
		return counters.WorkingSetSize // (1024 * 1024)
	except Exception:
		return -1


def _watch() -> None:
	last: Any = None
	still_since = time.monotonic()
	last_report = 0.0
	last_sizes = time.monotonic()

	while True:
		try:
			time.sleep(POLL_SECONDS)
			frames = sys._current_frames()
			key = _main_frame_key(frames)
			now = time.monotonic()
			if key != last:
				last = key
				still_since = now
			elif now - still_since >= STALL_SECONDS:
				if now - last_report >= REPORT_GAP:
					last_report = now
					where = ""
					if key:
						where = f"{os.path.basename(key[0])}"
					else:
						where = "native code (no Python frame)"
					dump(f"main thread stuck in {where}", now - still_since)
			if now - last_sizes >= SIZES_EVERY:
				last_sizes = now
				dump(describe_sizes(), 0)
		except Exception:
			# A watcher that dies on its own is worse than none, so it keeps
			# going and stays quiet about its own failures.
			continue


def start() -> bool:
	"""Start watching. Returns whether this call is the one that started it."""
	with _lock:
		if _started.is_set():
			return False
		_started.set()
	try:
		threading.Thread(
			target=_watch, name="ayu-stall-watch", daemon=True).start()
	except Exception:
		return False
	return True
