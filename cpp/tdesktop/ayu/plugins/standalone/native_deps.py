"""Native dependencies of a PE file, read without any third-party module.

The app loads an embedded CPython, so a plugin that imports `ssl` fails with
"DLL load failed while importing _ssl" when libssl-3.dll is missing next to the
executable. That message names the extension module, not the library it needs,
so the only reliable way to tell the user what to copy is to read the import
table of the module itself.

Only what is needed for the import directory is implemented: DOS header, COFF
header, optional header, section table, then the descriptors and their names.
"""

from __future__ import annotations

import struct
from pathlib import Path


class PEError(Exception):
	pass


def _sections(data: bytes) -> list[tuple[int, int, int, int, int]]:
	"""(name, virtual_address, span, raw_pointer, raw_size) per section."""
	if data[:2] != b"MZ":
		raise PEError("not a PE file")
	pe = struct.unpack_from("<I", data, 0x3C)[0]
	if data[pe:pe + 4] != b"PE\0\0":
		raise PEError("bad PE signature")
	coff = pe + 4
	(count,) = struct.unpack_from("<H", data, coff + 2)
	optional_size = struct.unpack_from("<H", data, coff + 16)[0]
	sections_at = coff + 20 + optional_size

	out = []
	for i in range(count):
		at = sections_at + i * 40
		name = data[at:at + 8].rstrip(b"\0").decode("latin-1")
		virtual_size, virtual_address, raw_size, raw_pointer = (
			struct.unpack_from("<IIII", data, at + 8))
		out.append((name, virtual_address, max(virtual_size, raw_size),
			raw_pointer, raw_size))
	return out


def _rva_to_offset(sections, rva: int) -> int:
	for _name, virtual_address, span, raw_pointer, _raw_size in sections:
		if virtual_address <= rva < virtual_address + span:
			return raw_pointer + (rva - virtual_address)
	raise PEError(f"RVA {rva} is outside every section")


def imported_dlls(path: Path) -> list[str]:
	"""DLL names the PE file imports, deduplicated and sorted."""
	data = Path(path).read_bytes()
	sections = _sections(data)
	pe = struct.unpack_from("<I", data, 0x3C)[0]
	coff = pe + 4
	magic = struct.unpack_from("<H", data, coff + 20)[0]
	if magic == 0x20B:
		# PE32+: the optional header has no BaseOfData and the pointers are
		# 8 bytes wide, so the data directories sit 112 bytes in, not 96.
		# Using the PE32 offset silently reads zeroes and reports no imports,
		# which looks exactly like a module that needs nothing.
		directories = coff + 20 + 112
	elif magic == 0x10B:
		directories = coff + 20 + 96
	else:
		raise PEError(f"unknown optional header magic {magic:#x}")
	import_rva, import_size = struct.unpack_from(
		"<II", data, directories + 8)
	if not import_rva or not import_size:
		return []

	names: set[str] = set()
	table = _rva_to_offset(sections, import_rva)
	step = 20
	pos = table
	while True:
		chunk = data[pos:pos + step]
		if len(chunk) < step or chunk == b"\0" * step:
			break
		name_rva = struct.unpack_from("<I", chunk, 12)[0]
		if not name_rva:
			break
		at = _rva_to_offset(sections, name_rva)
		end = data.index(b"\0", at)
		names.add(data[at:end].decode("latin-1"))
		pos += step
	return sorted(names)

# Provided by Windows itself, so their absence from the app folder is fine.
SYSTEM_DLLS = {
	"advapi32.dll", "bcrypt.dll", "combase.dll", "crypt32.dll", "gdi32.dll",
	"iphlpapi.dll", "kernel32.dll", "msvcrt.dll", "ntdll.dll", "ole32.dll",
	"oleaut32.dll", "powrprof.dll", "psapi.dll", "rpcrt4.dll", "secur32.dll",
	"shell32.dll", "shlwapi.dll", "ucrtbase.dll", "user32.dll", "win32u.dll",
	"ws2_32.dll", "version.dll", "winhttp.dll", "winmm.dll", "winspool.drv",
	"dnsapi.dll", "iphlpapi.dll", "normaliz.dll", "wldap32.dll",
	"propsys.dll", "clbcatq.dll", "difxapi.dll", "devpkey.dll",
	"api-ms-win-crt-runtime-l1-1-0.dll",
	"api-ms-win-crt-heap-l1-1-0.dll", "api-ms-win-crt-stdio-l1-1-0.dll",
	"api-ms-win-crt-string-l1-1-0.dll", "api-ms-win-crt-math-l1-1-0.dll",
	"api-ms-win-crt-locale-l1-1-0.dll", "api-ms-win-crt-convert-l1-1-0.dll",
	"api-ms-win-crt-time-l1-1-0.dll", "api-ms-win-crt-filesystem-l1-1-0.dll",
	"api-ms-win-crt-environment-l1-1-0.dll", "api-ms-win-crt-multibyte-l1-1-0.dll",
	"api-ms-win-crt-process-l1-1-0.dll", "api-ms-win-crt-utility-l1-1-0.dll",
	"api-ms-win-core-synch-l1-1-0.dll", "api-ms-win-core-synch-l1-1-0.dll",
	"api-ms-win-core-processthreads-l1-1-0.dll", "api-ms-win-core-file-l1-1-0.dll",
	"api-ms-win-core-heap-l1-1-0.dll", "api-ms-win-core-interlocked-l1-1-0.dll",
	"api-ms-win-core-string-l1-1-0.dll", "api-ms-win-core-error-l1-1-0.dll",
	"api-ms-win-core-datetime-l1-1-0.dll", "api-ms-win-core-localization-l1-1-0.dll",
	"api-ms-win-core-console-l1-1-0.dll", "api-ms-win-core-rtlsupport-l1-1-0.dll",
	"api-ms-win-crt-conio-l1-1-0.dll", "ext-ms-win-crt-",
	"vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll",
	"msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_codec.dll",
	"msvcp140_atomic_wait.dll", "concrt140.dll", "vccorlib140.dll",
	"hostfxr.dll", "hostpolicy.dll",
}


def missing_dependencies(folder: Path, files=None) -> dict[str, list[str]]:
	"""Map each module in `folder` to the DLLs it needs that are not there.

	`files` limits the scan, otherwise every .pyd and .dll is examined.
	System libraries are excluded: Windows provides them.
	"""
	folder = Path(folder)
	staged = {p.name.lower() for p in folder.glob("*.dll")}
	if files is None:
		candidates = sorted(
			list(folder.glob("*.pyd")) + list(folder.glob("*.dll")))
	else:
		candidates = [folder / name for name in files]

	report: dict[str, list[str]] = {}
	for candidate in candidates:
		if not candidate.is_file():
			continue
		try:
			needed = imported_dlls(candidate)
		except (PEError, OSError, ValueError, IndexError):
			continue
		absent = sorted(
			name for name in needed
			if name.lower() not in staged
			and name.lower() not in SYSTEM_DLLS
			and not name.lower().startswith(("api-ms-win-", "ext-ms-win-"))
		)
		if absent:
			report[candidate.name] = absent
	return report


def format_report(report: dict[str, list[str]]) -> str:
	if not report:
		return "all native dependencies are present"
	lines = []
	for module in sorted(report):
		lines.append(f"  {module} needs {', '.join(report[module])}")
	return "\n".join(lines)
