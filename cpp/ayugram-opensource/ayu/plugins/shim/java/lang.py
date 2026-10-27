"""`java.lang` on Desktop."""
from __future__ import annotations

import sys as _sys
import time as _time

from java import _LIST, _MAP, JavaException, JavaClass, jclass
from ayugram_compat.mobile_stubs import _StubObject

__all__ = [
	"String",
	"Object",
	"Class",
	"Throwable",
	"Exception",
	"RuntimeException",
	"IllegalArgumentException",
	"IllegalStateException",
	"NullPointerException",
	"System",
	"Math",
	"Integer",
	"Long",
	"Float",
	"Double",
	"Boolean",
	"Character",
	"Byte",
	"Short",
	"Thread",
	"Runnable",
	"Comparable",
	"Iterable",
	"Number",
	"StringBuilder",
]


class String(str):
	@staticmethod
	def valueOf(value):
		return String("" if value is None else str(value))

	@staticmethod
	def format(pattern, *args):
		try:
			return String(str(pattern).format(*args))
		except Exception:
			return String(str(pattern))

	def equalsIgnoreCase(self, other):
		return str(self).lower() == str(other).lower()

	def isEmpty(self):
		return len(self) == 0

	def length(self):
		return len(self)

	def trim(self):
		return String(str(self).strip())

	def substring(self, start, end=None):
		return String(str(self)[int(start):] if end is None
					  else str(self)[int(start):int(end)])

	def startsWith(self, prefix):
		return str(self).startswith(str(prefix))

	def endsWith(self, suffix):
		return str(self).endswith(str(suffix))

	def contains(self, part):
		return str(part) in str(self)

	def toLowerCase(self):
		return String(str(self).lower())

	def toUpperCase(self):
		return String(str(self).upper())

	def split(self, pattern):
		return _LIST(str(self).split(str(pattern)))

	def replace(self, old, new):
		return String(str(self).replace(str(old), str(new)))

	def indexOf(self, part):
		return str(self).find(str(part))

	def matches(self, pattern):
		import re

		try:
			return bool(re.fullmatch(str(pattern), str(self)))
		except Exception:
			return False

	def charAt(self, index):
		try:
			return str(self)[int(index)]
		except Exception:
			return ""


class StringBuilder(list):
	def __init__(self, value=""):
		super().__init__()
		self._text = str(value)

	def append(self, value):
		self._text += "" if value is None else str(value)
		return self

	def toString(self):
		return self._text

	# Plugins log or concatenate a builder directly, so str() must work too.
	def __str__(self):
		return self._text

	def __repr__(self):
		return f"StringBuilder({self._text!r})"

	def length(self):
		return len(self._text)

	def insert(self, index, value):
		self._text = self._text[:index] + str(value) + self._text[index:]
		return self

	def deleteCharAt(self, index):
		try:
			self._text = self._text[:index] + self._text[index + 1:]
		except Exception:
			pass
		return self

	def reverse(self):
		self._text = self._text[::-1]
		return self


class _PrintStream:
	"""System.out / System.err: writes through Python's print machinery."""

	def __init__(self, stream) -> None:
		self._stream = stream

	def write(self, text) -> None:
		try:
			self._stream.write(str(text))
		except Exception:
			pass
		return None

	def print(self, *args) -> None:
		self.write(" ".join(str(a) for a in args) + "\n")

	def println(self, *args) -> None:
		self.print(*args)

	def flush(self) -> None:
		try:
			self._stream.flush()
		except Exception:
			pass


class _System:
	@staticmethod
	def currentTimeMillis():
		return int(_time.time() * 1000)

	@staticmethod
	def nanoTime():
		return int(_time.monotonic() * 1e9)

	@staticmethod
	def exit(code=0):
		return None

	out = _PrintStream(_sys.stdout)
	err = _PrintStream(_sys.stderr)

	@staticmethod
	def getProperty(name, default=""):
		import os

		return os.environ.get(str(name), default)

	@staticmethod
	def getenv(name):
		import os

		return os.environ.get(str(name))

	@staticmethod
	def loadLibrary(name):
		return None

	@staticmethod
	def gc():
		return None


class _Math:
	PI = 3.141592653589793
	E = 2.718281828459045

	@staticmethod
	def abs(v):
		return abs(v)

	@staticmethod
	def max(a, b):
		return max(a, b)

	@staticmethod
	def min(a, b):
		return min(a, b)

	@staticmethod
	def floor(v):
		import math

		return math.floor(v)

	@staticmethod
	def ceil(v):
		import math

		return math.ceil(v)

	@staticmethod
	def round(v):
		import math

		return math.floor(v + 0.5)

	@staticmethod
	def pow(a, b):
		return a ** b

	@staticmethod
	def sqrt(v):
		import math

		return math.sqrt(v)

	@staticmethod
	def random():
		import random as _r

		return _r.random()


class _Number:
	def __init__(self, value=0):
		self._value = value

	def intValue(self):
		return int(self._value)

	def longValue(self):
		return int(self._value)

	def floatValue(self):
		return float(self._value)

	def doubleValue(self):
		return float(self._value)

	def compareTo(self, other):
		return (self._value > other) - (self._value < other)


class _Integer(_Number):
	MAX_VALUE = 2147483647
	MIN_VALUE = -2147483648

	@staticmethod
	def parseInt(value, *args):
		try:
			return int(str(value))
		except Exception:
			return 0

	@staticmethod
	def valueOf(value):
		return int(value)

	@staticmethod
	def toString(value, base=10):
		return str(int(value))


class _Long(_Number):
	MAX_VALUE = 9223372036854775807

	@staticmethod
	def parseLong(value, *args):
		try:
			return int(str(value))
		except Exception:
			return 0


class _Float(_Number):
	@staticmethod
	def parseFloat(value):
		try:
			return float(str(value))
		except Exception:
			return 0.0


class _Double(_Number):
	@staticmethod
	def parseDouble(value):
		try:
			return float(str(value))
		except Exception:
			return 0.0


class _Boolean:
	TRUE = True
	FALSE = False

	@staticmethod
	def parseBoolean(value):
		return str(value).lower() == "true"

	@staticmethod
	def valueOf(value):
		return bool(value)


class _Character:
	@staticmethod
	def isDigit(c):
		return str(c).isdigit()

	@staticmethod
	def isLetter(c):
		return str(c).isalpha()

	@staticmethod
	def toUpperCase(c):
		return str(c).upper()

	@staticmethod
	def toLowerCase(c):
		return str(c).lower()


class _Byte(_Number):
	@staticmethod
	def parseByte(value):
		return int(str(value))


class _Short(_Number):
	@staticmethod
	def parseShort(value):
		return int(str(value))


class _Thread:
	@staticmethod
	def sleep(ms):
		_time.sleep(max(0.0, float(ms) / 1000.0))

	@staticmethod
	def currentThread():
		return _StubObject("Thread.current")

	def getName(self):
		return "main"

	def getId(self):
		return 1

	def interrupt(self):
		return None

	def start(self):
		return None

	def join(self, *args):
		return None


Object = JavaClass("java.lang.Object")
Class = JavaClass("java.lang.Class")
Throwable = JavaException
Exception = JavaClass("java.lang.Exception")
RuntimeException = JavaClass("java.lang.RuntimeException")
IllegalArgumentException = JavaClass("java.lang.IllegalArgumentException")
IllegalStateException = JavaClass("java.lang.IllegalStateException")
NullPointerException = JavaClass("java.lang.NullPointerException")
Runnable = JavaClass("java.lang.Runnable")
Comparable = JavaClass("java.lang.Comparable")
Iterable = JavaClass("java.lang.Iterable")
Number = JavaClass("java.lang.Number")
System = _System
Math = _Math
Integer = _Integer
Long = _Long
Float = _Float
Double = _Double
Boolean = _Boolean
Character = _Character
Byte = _Byte
Short = _Short
Thread = _Thread

# Declared so that `from java.lang.reflect import Method` and the other
# subpackages resolve. Without it CPython refuses the import before any
# finder is consulted, so a plugin that reached for one of them failed to
# load outright rather than degrading like everything else in the shim.
# The submodules themselves are answered by the stub finder.
__path__ = []
