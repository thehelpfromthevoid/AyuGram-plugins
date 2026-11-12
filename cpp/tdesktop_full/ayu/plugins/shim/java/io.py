"""java.io.File on top of os.path.

Plugins written for Android ask File questions constantly: does this exist,
how long is it, what is its name. On Desktop the answer is a filesystem call,
so this answers with one instead of a placeholder that is quietly false.

A placeholder that returns False for exists() is worse than no class at all: a
plugin checks it, concludes the file is missing, and takes the wrong branch
without ever saying why.
"""

import os

__all__ = ["File", "ByteArrayOutputStream", "ByteArrayInputStream",
	"InputStream", "OutputStream", "IOException"]


class IOException(Exception):
	pass


class OutputStream:
	def write(self, data) -> None:
		raise NotImplementedError

	def flush(self) -> None:
		pass

	def close(self) -> None:
		pass


class InputStream:
	def read(self) -> int:
		raise NotImplementedError

	def close(self) -> None:
		pass


class ByteArrayOutputStream(OutputStream):
	"""Collects bytes in memory, as on Android."""

	def __init__(self, size: int = 32):
		self._buf = bytearray()

	def write(self, data) -> None:
		if isinstance(data, int):
			# write(int) writes a single byte, not a 4-byte word.
			self._buf.append(data & 0xFF)
		else:
			self._buf.extend(bytes(data))

	def write_bytes(self, data) -> None:
		self._buf.extend(bytes(data))

	def toByteArray(self) -> bytes:
		return bytes(self._buf)

	def size(self) -> int:
		return len(self._buf)

	def reset(self) -> None:
		self._buf.clear()

	def close(self) -> None:
		pass

	def __len__(self) -> int:
		return len(self._buf)


class ByteArrayInputStream(InputStream):
	def __init__(self, data: bytes):
		self._data = bytes(data)
		self._pos = 0

	def read(self) -> int:
		if self._pos >= len(self._data):
			return -1
		value = self._data[self._pos]
		self._pos += 1
		return value

	def close(self) -> None:
		pass


class File:
	"""The slice of java.io.File that plugins actually use."""

	__slots__ = ("_path",)

	def __init__(self, path=None, *args):
		if args:
			# java.io.File(File parent, String name) also turns up.
			self._path = os.path.join(str(path), str(args[0]))
		elif isinstance(path, File):
			self._path = path._path
		else:
			self._path = str(path) if path is not None else ""

	# --- имя ---
	def getName(self) -> str:
		return os.path.basename(self._path)

	def getAbsolutePath(self) -> str:
		return os.path.abspath(self._path)

	def getPath(self) -> str:
		return self._path

	def getParent(self):
		parent = os.path.dirname(self._path)
		return File(parent) if parent else None

	# --- проверки ---
	def exists(self) -> bool:
		return os.path.exists(self._path)

	def isFile(self) -> bool:
		return os.path.isfile(self._path)

	def isDirectory(self) -> bool:
		return os.path.isdir(self._path)

	def isHidden(self) -> bool:
		name = self.getName()
		return bool(name) and (name.startswith(".") or
			(bool(os.name == "nt") and
			 (os.stat(self._path).st_file_attributes & 2)
			 if os.path.exists(self._path) else False))

	def canRead(self) -> bool:
		return os.access(self._path, os.R_OK)

	def canWrite(self) -> bool:
		return os.access(self._path, os.W_OK)

	# --- размер ---
	def length(self) -> int:
		try:
			return os.path.getsize(self._path)
		except OSError:
			return 0

	# --- содержимое ---
	def read(self) -> bytes:
		with open(self._path, "rb") as handle:
			return handle.read()

	def read_text(self, encoding="utf-8") -> str:
		with open(self._path, "r", encoding=encoding) as handle:
			return handle.read()

	# --- запись ---
	def write(self, data: bytes) -> None:
		with open(self._path, "wb") as handle:
			handle.write(data)

	def mkdirs(self) -> bool:
		os.makedirs(self._path, exist_ok=True)
		return True

	def mkdir(self) -> bool:
		try:
			os.mkdir(self._path)
			return True
		except OSError:
			return False

	def delete(self) -> bool:
		try:
			os.remove(self._path)
			return True
		except OSError:
			return False

	# --- перечисление ---
	def list(self):
		try:
			return sorted(os.listdir(self._path))
		except OSError:
			return None

	def listFiles(self):
		names = self.list()
		if names is None:
			return None
		return [File(os.path.join(self._path, name)) for name in names]

	# --- сравнение ---
	def equals(self, other) -> bool:
		if not isinstance(other, File):
			return False
		return os.path.normcase(os.path.abspath(self._path)) == \
			os.path.normcase(os.path.abspath(other._path))

	def __eq__(self, other) -> bool:
		return self.equals(other)

	def __hash__(self) -> int:
		return hash(os.path.normcase(os.path.abspath(self._path)))

	def __str__(self) -> str:
		return self._path

	def __repr__(self) -> str:
		return f"File({self._path!r})"