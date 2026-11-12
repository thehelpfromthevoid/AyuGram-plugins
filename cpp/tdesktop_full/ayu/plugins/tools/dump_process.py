"""Write a minidump of another process. Run by the watchdog; touches nothing else.

It exists as its own program on purpose. MiniDumpWriteDump was called from
inside the watchdog once and took the watchdog down with it, which left a frozen
application with nothing watching it - the worst of both. Started as a separate
process, a failure here costs one dump and nothing else.

Deliberately without MiniDumpWithFullMemory: the application holds about a
gigabyte, and a dump of all of it fills the disk to answer a question that the
instruction pointer alone can answer. Each thread's register context says which
function it is sitting in, and that is what "why is the window not answering"
actually turns on.

AyuGram plugin host for Desktop, (c) @CE0fbi
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# Access rights: read the process, and ask about it.
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
GENERIC_WRITE = 0x40000000
CREATE_ALWAYS = 2
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

# Enough for: which thread is where, which modules are loaded, and the handles
# it was waiting on.
MiniDumpNormal = 0x00000000
MiniDumpWithDataSegs = 0x00000001
MiniDumpWithHandleData = 0x00000004
MiniDumpWithThreadInfo = 0x00001000
MiniDumpWithUnloadedModules = 0x00000020


def write(pid: int, target: str) -> bool:
	dbghelp = ctypes.WinDLL("dbghelp", use_last_error=True)
	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

	kernel32.OpenProcess.argtypes = [
		wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
	kernel32.OpenProcess.restype = wintypes.HANDLE
	kernel32.CreateFileW.argtypes = [
		wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
		wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
	kernel32.CreateFileW.restype = wintypes.HANDLE
	kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
	dbghelp.MiniDumpWriteDump.argtypes = [
		wintypes.HANDLE, wintypes.DWORD, wintypes.HANDLE, ctypes.c_ulong,
		ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
	dbghelp.MiniDumpWriteDump.restype = wintypes.BOOL

	process = kernel32.OpenProcess(
		PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
	if not process:
		print(f"OpenProcess failed: {ctypes.get_last_error()}", flush=True)
		return False
	try:
		handle = kernel32.CreateFileW(
			target, GENERIC_WRITE, 0, None, CREATE_ALWAYS,
			FILE_ATTRIBUTE_NORMAL, None)
		if not handle or handle == INVALID_HANDLE_VALUE:
			print(f"CreateFile failed: {ctypes.get_last_error()}", flush=True)
			return False
		try:
			flags = (MiniDumpNormal | MiniDumpWithDataSegs
				| MiniDumpWithHandleData | MiniDumpWithThreadInfo
				| MiniDumpWithUnloadedModules)
			ctypes.set_last_error(0)
			ok = dbghelp.MiniDumpWriteDump(
				process, pid, handle, flags, None, None, None)
			if not ok:
				print(
					f"MiniDumpWriteDump failed: "
					f"{ctypes.get_last_error()}", flush=True)
			return bool(ok)
		finally:
			kernel32.CloseHandle(handle)
	finally:
		kernel32.CloseHandle(process)


def main() -> int:
	if len(sys.argv) < 3:
		print("usage: dump_process.py PID OUTPUT.dmp")
		return 2
	return 0 if write(int(sys.argv[1]), sys.argv[2]) else 1


if __name__ == "__main__":
	raise SystemExit(main())