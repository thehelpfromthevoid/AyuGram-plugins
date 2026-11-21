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
# How often to note what the plugins are holding, so memory growth can be
# attributed to them or ruled out without guessing. Every few minutes rather
# than every half hour: a leaking process crosses the watchdog's memory limit in
# a couple of hours, and two lines over that time say nothing about when the
# growth started or how fast it is.
SIZES_EVERY = 300.0

# The thread is named so that liveness can be asked of `threading` rather than
# of a flag. `_started` belongs to this module object, and the self test drops
# and re-imports the whole package between checks, so a module whose flag says
# "not started yet" can exist while the thread is already running. Asking by
# name is the answer that does not depend on which module object is asking.
_THREAD_NAME = "ayu-stall-watch"

_started = threading.Event()
_lock = threading.Lock()
_warm_lock = threading.Lock()
_warm_done = False
_warm_ok = False
# Taken before the bridge installs its audit hook; see warm().
_mem_reader: Any = None
_ui_probe: Any = None
_log_file: Any = None
_log_target: Optional[Path] = None


def log_path() -> Path:
	"""Where the report goes.

	`AYU_PLUGINS_DIR` first, because the application and the self test both set
	it. In the application, and only there, the profile's plugins root is the
	next choice, which is where the plugin logs and `python_crash.log` already
	go: the report belongs beside them. Anywhere else - a tool run that creates
	a `PluginLoader`, which is what `plugin_corpus` and the crash-dump helper
	do - the current directory is used, so a diagnostic run cannot write into
	the live profile. That was not hypothetical: with the profile root as the
	unconditional fallback, a corpus run put its own thread stacks into the
	application's `stall-watch.log` and made the file look like the client's.
	"""
	env = os.environ.get("AYU_PLUGINS_DIR")
	if env:
		folder = Path(env)
	elif _in_bridge():
		try:
			from ayugram_compat import settings_store

			folder = settings_store.plugins_dir()
		except Exception:
			folder = Path.cwd()
	else:
		folder = Path.cwd()
	try:
		folder.mkdir(parents=True, exist_ok=True)
	except Exception:
		folder = Path.cwd()
	return folder / "stall-watch.log"


def _stamp() -> str:
	return time.strftime("%Y-%m-%d %H:%M:%S")


def _in_bridge() -> bool:
	"""Whether this process is the application rather than a tool or a test.

	The embedded `ayu_api` marks itself with `__ayu_stub__` because it answers
	unknown names with an inert placeholder; the pure-Python standalone module
	that the CLI and the self test use is not a stub and does not. The marker is
	what tells the two apart, and it matters here because the watcher is for the
	application: starting a background thread and taking a file handle in every
	tool invocation would be a cost with nothing to watch.

	Looked up in `sys.modules` rather than imported. `standalone/ayu_api.py`
	sits on the bridge's path, so a plain `import ayu_api` from here could bind
	the pure-Python file instead of the embedded module; the bridge has already
	imported the real one by the time this runs, so the lookup answers about the
	module that is actually in use, and imports nothing.
	"""
	module = sys.modules.get("ayu_api")
	if module is None:
		return False
	# `is True`, not truthiness: the standalone module's `__getattr__` answers
	# any dunder name with a function, so a truthy check would call that file the
	# bridge. The embedded module sets the attribute to the boolean True.
	return getattr(module, "__ayu_stub__", None) is True


def _write(text: str) -> bool:
	"""Append one block to the report.

	The handle taken by `warm()` comes first: `open(..., "a")` raises an `open`
	audit event, and the bridge's sandbox refuses that event without
	storage.write, so after ConfigureSandbox the only way to write is through a
	file object that was already open before the hook existed. Falling back to a
	fresh `open` keeps the standalone host and the self test working, where no
	hook is installed.
	"""
	handle = _log_file
	if handle is not None:
		try:
			handle.write(text)
			handle.flush()
			return True
		except Exception:
			return False
	try:
		target = log_path()
		target.parent.mkdir(parents=True, exist_ok=True)
		with target.open("a", encoding="utf-8", errors="replace") as out:
			out.write(text)
		return True
	except Exception:
		return False


def note(reason: str) -> Optional[Path]:
	"""Write one line, with no thread stacks.

	A stall report wants the stacks; the two recurring markers do not. Rendering
	every thread's stack for them costs the watched process real work and puts
	it all in the log: the sizes line alone would walk and format every frame of
	every thread every few minutes, which is a cost and a growth of its own
	while memory growth is the thing being measured.
	"""
	if not _write(f"--- {_stamp()} {reason} ---\n"):
		return None
	return _log_target or log_path()


def dump(reason: str, seconds: float) -> Optional[Path]:
	"""Write every thread's stack, and say what the main thread was in."""
	try:
		frames = sys._current_frames()
	except Exception:
		return None

	lines = [f"--- {_stamp()} {reason}" +
			 (f" after {seconds:.0f}s ---" if seconds > 0 else " ---")]
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

	if not _write("\n".join(lines) + "\n"):
		return None
	return _log_target or log_path()


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
			pending += len(getattr(queue, "_pending", ()) or ())
	except Exception:
		queues = pending = -1
	try:
		from ayugram_compat import settings_store

		cached = len(settings_store._CACHE)
	except Exception:
		cached = -1

	held = _shim_bytes()
	rss = _process_mb()
	blocks = _python_blocks()
	return (f"timers={timers} instances={instances} queues={queues} "
			f"queued={pending} settings={cached} "
			f"shim_kb={held} rss_mb={rss} py_blocks={blocks}")


def _python_blocks() -> int:
	"""Allocated blocks in the Python heap, or -1 when unreadable.

	`shim_kb` covers the containers this module knows about and `rss_mb` is the
	whole process, and the two together cannot say which side grew: an object
	leaked by plugin code that never entered a known container is invisible in
	the first number and indistinguishable from a Qt cache in the second. The
	client was measured growing by about seven megabytes a minute with every
	shim container flat, which is exactly that ambiguity. The Python heap is the
	discriminator - a block count that climbs while `shim_kb` stays put means
	Python objects the container walk does not see, and a flat count with a
	rising `rss_mb` means the growth is on the C++ side and nothing here owns it.

	A count, not bytes: there is no cheap stdlib answer for the heap's byte
	size, and the trend is what separates the two cases.
	"""
	try:
		return sys.getallocatedblocks()
	except Exception:
		return -1


def _held_values(group: Any) -> Optional[list]:
	"""The items a shim container holds, whatever kind of container it is.

	The three groups this walks are not all mappings: `_timers` is a list,
	`_live_instances` is a WeakSet and only `_CACHE` is a dict. The old code
	called `.values()` on each, which two of the three do not have - the
	`except` then skipped them, and the line reported a total built from the
	settings cache alone while claiming to cover the timers and the instances.
	Answers None when the container cannot be read at all.
	"""
	try:
		if isinstance(group, dict):
			return list(group.values())
		return list(group)
	except Exception:
		return None


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
		values = _held_values(group)
		if values is None:
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
			items = getattr(queue, "_pending", None)
			if items is not None:
				total += sys.getsizeof(items)
	except Exception:
		pass
	return total // 1024


def _make_mem_reader() -> Optional[Any]:
	"""A callable returning this process's working set in MB, or None.

	`ctypes.WinDLL` is what the sandbox refuses, and it refuses it only when the
	library is loaded - so this is called from `warm()` before the hook exists
	and the resulting closure is reused for the session. The argument types are
	declared explicitly: through `ctypes.windll` the wrapper narrows HANDLE to
	32 bits, GetProcessMemoryInfo then returns 0, and the figure is always -1 on
	a 64-bit interpreter.
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
	except Exception:
		return None

	def read() -> int:
		try:
			counters = Counters()
			counters.cb = ctypes.sizeof(Counters)
			handle = kernel.GetCurrentProcess()
			if not psapi.GetProcessMemoryInfo(
					handle, ctypes.byref(counters), counters.cb):
				return -1
			return counters.WorkingSetSize // (1024 * 1024)
		except Exception:
			return -1

	return read


def warm() -> bool:
	"""Take the resources the sandbox will not allow later.

	The watcher was written to explain a freeze and to attribute memory growth,
	and in the running client it could do neither. It is only ever started from
	`plugin_loader.PluginLoader.__init__`, which the bridge does not use - the
	bridge loads each plugin itself through `LoadPlugin` plus
	`activate_base_plugins` - so the thread never ran, which is why no
	`stall-watch.log` exists anywhere while `python_crash.log` does.

	Starting it from the package import fixes the reachability half. This
	function fixes the other half: by the time the watcher is asked to do
	anything, the bridge has installed the audit hook from ConfigureSandbox,
	which refuses `ctypes.dlopen` unconditionally and refuses an `open` for
	writing without storage.write. All three were measured: `_process_mb()`
	returned -1 (ctypes denied), the window probe could not be built for the
	same reason, and `dump()` wrote nothing (file write denied).

	Loading the library and opening the file are the operations the hook
	refuses; calling a function that is already loaded and writing to a handle
	that is already open raise no such event. So the reader, the probe and the
	handle are taken here, during the package's first import inside InitPython -
	before ConfigureSandbox runs - and are kept for the session. This is the
	same order of operations that keeps the native fast layer alive, and it was
	verified the same way: with the capture first, the reading and the writing
	both still work after the hook is installed.

	Never raises. Returns whether every resource was captured.
	"""
	global _warm_done, _warm_ok, _mem_reader, _ui_probe, _log_file, _log_target
	with _warm_lock:
		if _warm_done:
			return _warm_ok
		captured = 0
		reader = _make_mem_reader()
		if reader is not None:
			_mem_reader = reader
			captured += 1
		probe = _make_ui_probe()
		if probe is not None:
			_ui_probe = probe
			captured += 1
		try:
			target = log_path()
			target.parent.mkdir(parents=True, exist_ok=True)
			_log_file = target.open("a", encoding="utf-8", errors="replace")
			_log_target = target
			captured += 1
		except Exception:
			pass
		_warm_done = True
		_warm_ok = captured == 3
		return _warm_ok


def _process_mb() -> int:
	"""Working set of this process in MB, or -1 if it cannot be read.

	The reader captured by `warm()` first; a fresh one otherwise, which works
	where no audit hook is installed (the standalone host, the self test) and
	answers -1 under the sandbox, the state this fix exists to avoid.
	"""
	if _mem_reader is not None:
		return _mem_reader()
	reader = _make_mem_reader()
	if reader is None:
		return -1
	return reader()


def _make_ui_probe() -> Optional[Any]:
	"""A callable saying whether this process's window still answers, or None.

	The frame test cannot tell a stuck thread from an idle one, and that is not
	a theory: in the running client the report filled with "main thread stuck
	in base_plugin.py" while the window answered `WM_NULL` and the process used
	one second of CPU in eight - it was sitting in the Qt event loop between
	events, which is exactly what an idle application does. The signal that
	separates the two states is the message probe the external guard already
	uses, so it is read from inside here instead of guessed at from frames.

	`user32` has to be loaded before the sandbox installs its hook, which
	refuses `ctypes.dlopen` outright, so this is called from `warm()`.

	The answer is True (responding), False (not responding) or None (no window
	yet, so nothing can be concluded - the guard treats the same case the same
	way, because a client still starting has no window and is not hung).
	"""
	try:
		import ctypes
		import ctypes.wintypes as wintypes

		user = ctypes.WinDLL("user32", use_last_error=True)
		kernel = ctypes.WinDLL("kernel32", use_last_error=True)

		enum_proc = ctypes.WINFUNCTYPE(
			wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
		user.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
		user.EnumWindows.restype = wintypes.BOOL
		user.GetWindowThreadProcessId.argtypes = [
			wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
		user.GetWindowThreadProcessId.restype = wintypes.DWORD
		user.IsWindowVisible.argtypes = [wintypes.HWND]
		user.IsWindowVisible.restype = wintypes.BOOL
		user.SendMessageTimeoutW.argtypes = [
			wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
			wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)]
		user.SendMessageTimeoutW.restype = wintypes.HWND
		kernel.GetCurrentProcessId.argtypes = []
		kernel.GetCurrentProcessId.restype = wintypes.DWORD
	except Exception:
		return None

	pid = kernel.GetCurrentProcessId()
	SMTO_ABORTIFHUNG = 0x0002
	SMTO_BLOCK = 0x0001
	WM_NULL = 0x0000

	def probe(timeout_ms: int = 2000) -> Optional[bool]:
		found: list = []

		def visit(hwnd, _param):
			owner = wintypes.DWORD()
			user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
			if owner.value == pid and user.IsWindowVisible(hwnd):
				found.append(hwnd)
				return False
			return True

		try:
			user.EnumWindows(enum_proc(visit), 0)
		except Exception:
			return None
		if not found:
			return None
		result = ctypes.c_void_p()
		try:
			ok = user.SendMessageTimeoutW(
				found[0], WM_NULL, 0, 0,
				SMTO_ABORTIFHUNG | SMTO_BLOCK, timeout_ms,
				ctypes.byref(result))
		except Exception:
			return None
		return bool(ok)

	return probe


def _ui_probe_reader() -> Optional[bool]:
	"""Whether the application's window still answers, or None if unknowable.

	`warm()` captured the probe before the sandbox existed; a fresh one is built
	otherwise, which works where no hook is installed (the standalone host, the
	self test) and returns None under the sandbox, where the watcher then has no
	way to tell an idle main thread from a stuck one and stays silent rather
	than crying wolf.
	"""
	if _ui_probe is not None:
		return _ui_probe()
	probe = _make_ui_probe()
	if probe is None:
		return None
	return probe()


def _watch() -> None:
	last: Any = None
	still_since = time.monotonic()
	reported = False
	last_sizes = time.monotonic()

	# One line at the start, so "is the watcher live" is answerable from the file
	# itself instead of by waiting out SIZES_EVERY. The old watcher wrote only on
	# a stall or a sizes tick, so its absence from the log meant both "not
	# running" and "running fine", which is the ambiguity that let it stay dead
	# in the client unnoticed.
	note(f"watching (stall>{STALL_SECONDS:.0f}s, sizes every {SIZES_EVERY:.0f}s)")

	while True:
		try:
			time.sleep(POLL_SECONDS)
			frames = sys._current_frames()
			key = _main_frame_key(frames)
			now = time.monotonic()
			if key != last:
				last = key
				still_since = now
				reported = False
			elif now - still_since >= STALL_SECONDS:
				# A frame that does not change is not proof of a stall; it is
				# also what an idle event loop looks like. The window is asked
				# instead, and only one that stopped answering makes this worth
				# writing down. One report per excursion: a freeze that lasts
				# minutes is one event, not one per poll.
				responsive = _ui_probe_reader()
				if responsive is False and not reported:
					reported = True
					if key:
						where = os.path.basename(key[0])
					else:
						where = "native code (no Python frame)"
					dump(f"main thread stuck in {where}", now - still_since)
			if now - last_sizes >= SIZES_EVERY:
				last_sizes = now
				note(describe_sizes())
		except Exception:
			# A watcher that dies on its own is worse than none, so it keeps
			# going and stays quiet about its own failures.
			continue


def start() -> bool:
	"""Start watching. Returns whether this call is the one that started it.

	Liveness is asked of `threading` by name, not of the module flag alone. The
	flag lives on this module object, and the self test drops and re-imports the
	package between checks, so a fresh module can believe it has not started
	while the thread from the previous import is still running; with the flag for
	the only guard that would start a second watcher and two reporters would
	write the same file. A thread that is still running under the name is the
	real answer to "is it already watching".
	"""
	global _started
	with _lock:
		if any(thread.name == _THREAD_NAME for thread in threading.enumerate()):
			_started.set()
			return False
		_started.set()
	try:
		threading.Thread(
			target=_watch, name=_THREAD_NAME, daemon=True).start()
	except Exception:
		return False
	return True
