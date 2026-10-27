"""Name the function at an address inside a dump, using the matching PDB.

A minidump gives addresses; without symbols those are numbers, and
`AyuGram.exe+0x1731190` says nothing about why a thread is stuck there. The
Release PDB is three and a half gigabytes and dbghelp reads it directly, which
turns the address into the name of the function it is in.

The load base comes from the dump, not from the running program, so this works on
a dump taken while the process was frozen - which is the only moment worth
having one from.

Usage: resolve_symbol.py DUMP.dmp 0x1731190 [0x1731190 ...]
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

# dbghelp wants the module recorded with the same base the program had, so the
# addresses it computes line up with the dump's.
SYMOPT_LOAD_LINES = 0x00000010
SYMOPT_UNDNAME = 0x00000002
SYMOPT_DEFERRED_LOADS = 0x00000004

# The Release PDB beside the build output.
DEFAULT_SYMBOLS = os.environ.get("AYUGRAM_SYMBOLS", "")
DEFAULT_IMAGE = os.environ.get("AYUGRAM_EXE", "")


class SYMBOL_INFOW(ctypes.Structure):
	_fields_ = [
		("SizeOfStruct", wintypes.DWORD),
		("TypeIndex", wintypes.DWORD),
		("Reserved", ctypes.c_uint64 * 2),
		("Index", wintypes.DWORD),
		("Name", wintypes.WCHAR * 1),
	]


def load_symbols(module_base: int) -> ctypes.c_void_p:
	dbghelp = ctypes.WinDLL("dbghelp", use_last_error=True)
	dbghelp.SymInitializeW.argtypes = [
		ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_int]
	dbghelp.SymInitializeW.restype = ctypes.c_int
	dbghelp.SymLoadModuleExW.argtypes = [
		ctypes.c_void_p, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR,
		ctypes.c_uint64, wintypes.DWORD, wintypes.LPCWSTR, wintypes.DWORD]
	dbghelp.SymLoadModuleExW.restype = ctypes.c_uint64
	dbghelp.SymCleanup.argtypes = [ctypes.c_void_p]
	dbghelp.SymCleanup.restype = ctypes.c_int

	handle = ctypes.c_void_p()
	# No option flags. Any of them - LOAD_LINES, UNDNAME, DEFERRED_LOADS -
	# makes SymInitialize fail on this PDB with STATUS_HEAP_CORRUPTION, and a
	# function name is worth having even without the file and line.
	if not dbghelp.SymInitializeW(handle, DEFAULT_SYMBOLS, 0):
		raise SystemExit(
			f"SymInitialize failed: {ctypes.get_last_error()}")
	dbghelp.SymLoadModuleExW(
		handle, None, DEFAULT_IMAGE, "AyuGram", module_base, 0, None, 0)
	return handle


def name_at(handle, address: int) -> str:
	dbghelp = ctypes.WinDLL("dbghelp", use_last_error=True)
	dbghelp.SymFromAddrW.argtypes = [
		ctypes.c_void_p, ctypes.c_uint64,
		ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p]
	dbghelp.SymFromAddrW.restype = ctypes.c_int

	# The name is written into the caller's buffer and a C++ name is easily
	# longer than the single wchar the structure declares. Handing dbghelp a
	# structure sized for one character fails with ERROR_INVALID_PARAMETER, and
	# the address stays a number.
	size = 4096
	buffer = ctypes.create_string_buffer(size)
	structure = ctypes.cast(buffer, ctypes.POINTER(SYMBOL_INFOW))
	structure.contents.SizeOfStruct = size
	displacement = ctypes.c_uint64()
	if not dbghelp.SymFromAddrW(
			handle, ctypes.c_uint64(address), ctypes.byref(displacement),
			buffer):
		return f"<no symbol: error {ctypes.get_last_error()}>"
	return f"{structure.contents.Name} +{displacement.value:#x}"


def main() -> int:
	if len(sys.argv) < 3:
		print(__doc__)
		return 2
	sys.path.insert(0, str(Path(__file__).resolve().parent))
	from minidump_summary import Dump

	dump = Dump(Path(sys.argv[1]))
	base = None
	size = 0
	for start, length, module in dump.modules():
		if Path(module).name.lower().startswith("ayugram"):
			base, size = start, length
			break
	if base is None:
		raise SystemExit("the dump has no AyuGram module")

	print(f"AyuGram.exe at {base:#x}, size {size:#x}")
	handle = load_symbols(base)
	for raw in sys.argv[2:]:
		value = int(raw, 0)
		# The summary prints offsets like "AyuGram.exe+0x1731190"; accepting an
		# offset as well as an absolute address saves having to add the load base
		# by hand, which is the sort of mistake that makes a report look wrong.
		address = base + value if value < size else value
		if address < base or address >= base + size:
			print(f"  {raw}: outside the module")
			continue
		print(f"  {raw}  {name_at(handle, address)}")
	ctypes.WinDLL("dbghelp").SymCleanup(handle)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())