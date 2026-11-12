"""Say where a stuck process was, from a minidump, without a debugger.

The watchdog has to kill a frozen application to bring the window back, and
killing throws away everything about what it was doing. Nothing inside the
process can report it either: this one freezes while holding the interpreter
lock, so every in-process reporter needs the lock it does not have, and stays
silent. That left a hang that came back every minute or two with no
explanation at all.

A minidump does not need the interpreter. It holds the thread list, each
thread's register context, and the memory around its stack. Mapping those
addresses back to modules answers the question that matters - is the thread
sitting in a wait, spinning, or buried in the plugin runtime - and naming the
module is enough to go and look, since the frame count from a dump without
symbols is small and the modules are unambiguous.

It is not a debugger and does not pretend to be: no line numbers, no variable
values. Usage: minidump_summary.py HANG-1234-1699999999.dmp

AyuGram plugin host for Desktop, (c) @CE0fbi
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path
from typing import Optional

# MINIDUMP_HEADER
_SIGNATURE = 0x504D444D  # 'MDMP'
_HEADER_SIZE = 32

# Stream directory types this reads.
STREAM_THREAD_LIST = 3
STREAM_MODULE_LIST = 4
STREAM_MEMORY_LIST = 5
STREAM_MEMORY64_LIST = 9
STREAM_THREAD_INFO_LIST = 17
STREAM_SYSTEM_INFO = 7

CONTEXT_SIZE_x64 = 1232
RIP_OFFSET_x64 = 248
RSP_OFFSET_x64 = 152


class Dump:
	def __init__(self, path: Path):
		self.data = path.read_bytes()
		self.streams: dict[int, tuple[int, int]] = {}
		self._read_header()

	def _read_header(self) -> None:
		if len(self.data) < _HEADER_SIZE:
			raise ValueError("file is too short to be a minidump")
		# MINIDUMP_HEADER: signature, version, stream count, stream directory.
		(signature, _version, streams, dir_rva, _checksum, _time,
		 _flags) = struct.unpack_from("<IIIIIIQ", self.data, 0)
		if signature != _SIGNATURE:
			raise ValueError(
				f"not a minidump (signature {signature:#x}, expected "
				f"{_SIGNATURE:#x})")
		self.stream_count = streams
		offset = dir_rva
		for _ in range(streams):
			kind, size, location = struct.unpack_from("<III", self.data, offset)
			self.streams.setdefault(kind, (location, size))
			offset += 12

	def _stream(self, kind: int) -> Optional[bytes]:
		entry = self.streams.get(kind)
		if not entry:
			return None
		rva, size = entry
		return self.data[rva:rva + size]

	def modules(self) -> list[tuple[int, int, str]]:
		"""(base, size, name) for every module in the dump."""
		block = self._stream(STREAM_MODULE_LIST)
		if not block or len(block) < 4:
			return []
		count = struct.unpack_from("<I", block, 0)[0]
		result = []
		offset = 4
		# MINIDUMP_MODULE on x64: BaseOfImage (u64), SizeOfImage (u32),
		# CheckSum, TimeDateStamp, ModuleNameRva (u32) - the name is at 20, not
		# after the version block, which is where reading it from first put every
		# address in "not in any module" and made the whole report useless.
		# MINIDUMP_MODULE is 108 bytes, not the 1088 it looks like when the
		# version block is counted twice. The tell is the arithmetic: 15 modules
		# and a 1624-byte block fit exactly one stride of 108, and the previous
		# one stopped after the first module for that reason.
		for _ in range(count):
			if offset + 108 > len(block):
				break
			base = struct.unpack_from("<Q", block, offset)[0]
			size = struct.unpack_from("<I", block, offset + 8)[0]
			name_rva = struct.unpack_from("<I", block, offset + 20)[0]
			result.append((base, size, self._string(name_rva)))
			offset += 108
		return result

	def _string(self, rva: int) -> str:
		if not rva or rva + 4 > len(self.data):
			return "?"
		length = struct.unpack_from("<I", self.data, rva)[0]
		raw = self.data[rva + 4:rva + 4 + length]
		return raw.decode("utf-16-le", errors="replace")

	def memory_regions(self) -> list[tuple[int, int, int]]:
		"""(start, size, data offset) for every readable range."""
		regions = []
		block = self._stream(STREAM_MEMORY_LIST)
		if block:
			count = struct.unpack_from("<I", block, 0)[0]
			offset = 4
			for _ in range(count):
				start, size, rva = struct.unpack_from("<QII", block, offset)
				regions.append((start, size, rva))
				offset += 16
		block64 = self._stream(STREAM_MEMORY64_LIST)
		if block64:
			count, base_rva = struct.unpack_from("<QQ", block64, 0)
			cursor = base_rva
			offset = 16
			for _ in range(count):
				start, size = struct.unpack_from("<QQ", block64, offset)
				regions.append((start, size, cursor))
				cursor += size
				offset += 16
		return regions

	def threads(self) -> list[tuple[int, int, int, int]]:
		"""(thread id, rip, rsp, context rva) for every thread."""
		block = self._stream(STREAM_THREAD_LIST)
		if not block:
			return []
		count = struct.unpack_from("<I", block, 0)[0]
		result = []
		offset = 4
		# MINIDUMP_THREAD on x64 is 48 bytes: id, suspend count, priority class
		# and priority (u32 each), Teb (u64), then the stack descriptor at 24 and
		# the context descriptor at 40. Reading the descriptors from 20 and 32
		# landed them in the middle of Teb, which is why every thread came back
		# with no stack.
		for _ in range(count):
			if offset + 48 > len(block):
				break
			tid = struct.unpack_from("<I", block, offset)[0]
			ctx_size, ctx_rva = struct.unpack_from("<II", block, offset + 40)
			rip = rsp = 0
			if ctx_rva and ctx_rva + CONTEXT_SIZE_x64 <= len(self.data):
				rip = struct.unpack_from("<Q", self.data, ctx_rva + RIP_OFFSET_x64)[0]
				rsp = struct.unpack_from("<Q", self.data, ctx_rva + RSP_OFFSET_x64)[0]
			result.append((tid, rip, rsp, ctx_rva))
			offset += 48
		return result

	def stack_words(self, rsp: int, limit: int = 24) -> list[int]:
		"""Return-address candidates from the thread's own stack."""
		for start, size, rva in self.memory_regions():
			if start <= rsp < start + size:
				base = rva + (rsp - start)
				words = []
				for i in range(limit):
					at = base + i * 8
					if at + 8 > len(self.data):
						break
					words.append(struct.unpack_from("<Q", self.data, at)[0])
				return words
		return []


def describe(path: Path) -> int:
	dump = Dump(path)
	modules = dump.modules()

	def where(address: int) -> str:
		for base, size, name in modules:
			if base <= address < base + size:
				return f"{Path(name).name}+{address - base:#x}"
		return f"{address:#x} (not in any module)"

	print(f"{path.name}: {path.stat().st_size // (1024 * 1024)} MB, "
		  f"{len(modules)} modules, {dump.stream_count} streams")
	print()
	threads = dump.threads()
	if not threads:
		print("no thread list in the dump")
		return 1

	# The interesting thread is the one the process was created with: it owns
	# the window, so it is the one that was not answering.
	for index, (tid, rip, rsp, _ctx) in enumerate(threads):
		label = "main thread" if index == 0 else f"thread {index}"
		print(f"[{label}] tid={tid}")
		print(f"  at   {where(rip)}")
		frames = [w for w in dump.stack_words(rsp) if w]
		if not frames:
			print("  stack not in the dump")
			continue
		seen = []
		for word in frames:
			entry = where(word)
			if entry not in seen:
				seen.append(entry)
			if len(seen) >= 8:
				break
		print("  return addresses:")
		for entry in seen:
			print(f"    {entry}")
		print()

	# Every thread on one line each. A healthy application's threads sit in a
	# handful of well-known places - the message pump, waits, the disk - so the
	# one sitting somewhere unexpected is the answer, and it is much easier to
	# see in a list than while reading paragraphs.
	print(f"{len(threads)} threads:")
	for index, (tid, rip, _rsp, _ctx) in enumerate(threads):
		mark = " <- window" if index == 0 else ""
		print(f"  [{index:>3}] {where(rip)}{mark}")
	return 0


def main() -> int:
	if len(sys.argv) < 2:
		print(__doc__)
		return 2
	given = Path(sys.argv[1])
	if given.is_file():
		return describe(given)
	found = sorted(given.parent.glob(given.name + "*.dmp"))
	if not found:
		print("no dump matched")
		return 1
	return describe(found[-1])


if __name__ == "__main__":
	raise SystemExit(main())
