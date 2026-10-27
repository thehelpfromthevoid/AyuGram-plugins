"""Post-link fix: move Qt's static UIAutomation imports into the delay table.

`qwindows.lib` (Qt Windows platform plugin) pulls in the Windows SDK
`UIAutomationCore.h` pragma libraries. Those come from a *default library*
directive, and MSVC's `/DELAYLOAD` cannot convert default-lib imports, so
`UIAutomationCore.dll`, `ext-ms-win-uiacore-l1-1-0/1.dll` and the three
`api-ms-win-core-*` sets end up in the static import table.

That static table is what makes the freshly linked AyuGram.exe fail to start:
the loader reports `python314.dll` missing even though the file sits next to
the exe, while the same DLL loads fine for any other binary in the same
folder. The previously working build has these six in the *delay* import
table instead (54 delay entries vs 47).

This script reproduces that layout: it converts the six descriptors in
place, reusing the writable BSS tail of `.data` for the module-handle slots
and the slack between the delay and import tables for the new descriptors.

Usage:  python3 tools/uia_delayload_fix.py <exe> [--check]
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

TARGETS = {
	"uiautomationcore.dll",
	"ext-ms-win-uiacore-l1-1-0.dll",
	"ext-ms-win-uiacore-l1-1-1.dll",
	"api-ms-win-core-libraryloader-l1-2-0.dll",
	"api-ms-win-core-rtlsupport-l1-1-0.dll",
	"api-ms-win-core-synch-ansi-l1-1-0.dll",
	"python314.dll",
}

DESC = 20
DELAY_DESC = 32
ATTR_RVA = 1


class Pe:
	def __init__(self, path):
		self.path = Path(path)
		self.data = bytearray(self.path.read_bytes())
		self.pe = struct.unpack_from("<I", self.data, 0x3C)[0]
		if bytes(self.data[self.pe:self.pe + 4]) != b"PE\0\0":
			raise ValueError("not a PE file")
		self.nsec = struct.unpack_from("<H", self.data, self.pe + 6)[0]
		self.optsz = struct.unpack_from("<H", self.data, self.pe + 20)[0]
		self.magic = struct.unpack_from("<H", self.data, self.pe + 24)[0]
		self.dd = self.pe + 24 + (112 if self.magic == 0x20B else 96)
		self.sec_off = self.pe + 24 + self.optsz
		self.secs = []
		for i in range(self.nsec):
			o = self.sec_off + i * 40
			self.secs.append({
				"va": struct.unpack_from("<I", self.data, o + 12)[0],
				"vsize": struct.unpack_from("<I", self.data, o + 8)[0],
				"raw": struct.unpack_from("<I", self.data, o + 20)[0],
				"rawsize": struct.unpack_from("<I", self.data, o + 16)[0],
				"chars": struct.unpack_from("<I", self.data, o + 36)[0],
			})

	def rva_to_off(self, rva):
		for s in self.secs:
			span = max(s["vsize"], s["rawsize"])
			if s["va"] <= rva < s["va"] + span:
				return s["raw"] + (rva - s["va"])
		return None

	def read_cstr(self, rva):
		off = self.rva_to_off(rva)
		if off is None:
			return None
		end = self.data.index(b"\x00", off)
		return bytes(self.data[off:end]).decode("latin1")

	def dir_entry(self, index):
		return struct.unpack_from("<II", self.data, self.dd + index * 8)

	def set_dir_entry(self, index, rva, size):
		struct.pack_into("<II", self.data, self.dd + index * 8, rva, size)

	def section_va_end(self, index):
		s = self.secs[index]
		return s["va"] + max(s["vsize"], s["rawsize"])

	def writable_section(self):
		for i, s in enumerate(self.secs):
			if s["chars"] & 0x80000000:
				return i, s
		return None, None


def import_descriptors(pe):
	rva, size = pe.dir_entry(1)
	base = pe.rva_to_off(rva)
	if base is None:
		return []
	out = []
	i = 0
	while True:
		e = base + i * DESC
		if e + DESC > len(pe.data):
			break
		oft, tds, fwd, nm, ft = struct.unpack_from("<IIIII", pe.data, e)
		if nm == 0:
			break
		out.append({"off": e, "oft": oft, "tds": tds, "fwd": fwd, "nm": nm, "ft": ft})
		i += 1
	return out


def delay_descriptors(pe):
	rva, size = pe.dir_entry(13)
	if not rva:
		return [], 0, 0
	base = pe.rva_to_off(rva)
	if base is None:
		return [], 0, 0
	out = []
	i = 0
	while True:
		e = base + i * DELAY_DESC
		if e + DELAY_DESC > len(pe.data):
			break
		attrs, nm = struct.unpack_from("<II", pe.data, e)
		if nm == 0:
			break
		rest = struct.unpack_from("<IIIIII", pe.data, e + 8)
		out.append({"off": e, "attrs": attrs, "nm": nm, "hmod": rest[0],
			"iat": rest[1], "int": rest[2], "bound": rest[3],
			"unload": rest[4], "ts": rest[5]})
		i += 1
	return out, rva, base


def find_slack(pe, start_rva, need, limit_rva):
	"""First run of `need` zero bytes in .rdata between two RVAs."""
	i, s = None, None
	for idx, sec in enumerate(pe.secs):
		if sec["va"] <= start_rva < sec["va"] + sec["rawsize"]:
			i, s = idx, sec
			break
	if i is None:
		return None
	off = pe.rva_to_off(start_rva)
	end = pe.rva_to_off(min(limit_rva, pe.section_va_end(i)))
	if off is None or end is None or end - off < need:
		return None
	run = 0
	for pos in range(off, end):
		if pe.data[pos] == 0:
			run += 1
			if run == need:
				return pos - run + 1
		else:
			run = 0
	return None


def find_bss(pe, need):
	"""Writable RVA range past the raw data (zero-filled at load)."""
	for s in pe.secs:
		if not (s["chars"] & 0x80000000):
			continue
		if s["vsize"] <= s["rawsize"]:
			continue
		slots_rva = s["va"] + s["rawsize"] + 8
		end = s["va"] + s["vsize"] - 16
		if end - slots_rva >= need:
			return slots_rva
	return None


def convert(path, check=False):
	pe = Pe(path)
	imports = import_descriptors(pe)
	delays, delay_rva, delay_off = delay_descriptors(pe)

	names = []
	convert_idx = []
	for idx, d in enumerate(imports):
		n = (pe.read_cstr(d["nm"]) or "").lower()
		names.append(n)
		if n in TARGETS:
			convert_idx.append(idx)

	print("static imports :", len(imports))
	print("delay entries  :", len(delays))
	print("to convert     :", [names[i] for i in convert_idx])

	if check:
		return 0 if not convert_idx else 1
	if not convert_idx:
		print("nothing to do")
		return 0

	need = len(convert_idx) * DELAY_DESC
	slot_need = len(convert_idx) * 4
	slots_rva = find_bss(pe, slot_need)
	if slots_rva is None:
		print("no writable BSS space for module-handle slots", file=sys.stderr)
		return 2

	new_rva = delay_rva + len(delays) * DELAY_DESC
	imp_rva, _ = pe.dir_entry(1)
	limit = imp_rva
	if not delay_rva:
		new_rva = imp_rva
		limit = imp_rva + len(imports) * DESC
		limit = new_rva + need + DELAY_DESC
		limit = pe.section_va_end(0)
	slack_off = find_slack(pe, new_rva, need, limit)
	if slack_off is None:
		print("no slack room between delay and import tables", file=sys.stderr)
		return 2
	slack_rva = new_rva + (slack_off - pe.rva_to_off(new_rva))

	for slot, idx in enumerate(convert_idx):
		d = imports[idx]
		base = slack_off + slot * DELAY_DESC
		struct.pack_into("<IIIIIIII", pe.data, base,
			ATTR_RVA,
			d["nm"],
			slots_rva + slot * 4,
			d["ft"],
			d["oft"] or d["ft"],
			0,
			0,
			0,
		)
	struct.pack_into("<II", pe.data, slack_off + len(convert_idx) * DELAY_DESC, 0, 0)

	keep = [d for i, d in enumerate(imports) if i not in set(convert_idx)]
	base = pe.rva_to_off(imp_rva if not delay_rva else pe.dir_entry(1)[0])
	for i in range(len(keep)):
		d = keep[i]
		struct.pack_into("<IIIII", pe.data, base + i * DESC,
			d["oft"], d["tds"], d["fwd"], d["nm"], d["ft"])
	struct.pack_into("<IIIII", pe.data, base + len(keep) * DESC, 0, 0, 0, 0, 0)

	pe.set_dir_entry(1, imp_rva, (len(keep) + 1) * DESC)
	if delay_rva:
		pe.set_dir_entry(13, delay_rva, (len(delays) + len(convert_idx) + 1) * DELAY_DESC)
	else:
		pe.set_dir_entry(13, slack_rva, (len(convert_idx) + 1) * DELAY_DESC)

	pe.path.write_bytes(bytes(pe.data))
	print("patched:", pe.path)
	print("  static imports ->", len(keep))
	print("  delay entries  ->", len(delays) + len(convert_idx))
	return 0


if __name__ == "__main__":
	args = [a for a in sys.argv[1:] if not a.startswith("--")]
	sys.exit(convert(args[0], check="--check" in sys.argv))
