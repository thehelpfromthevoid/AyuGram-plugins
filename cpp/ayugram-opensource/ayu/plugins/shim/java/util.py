"""`java.util` on Desktop - real list/map/set with Java accessors."""
from __future__ import annotations

from java import _LIST, _MAP, _SET
from ayugram_compat.mobile_stubs import _StubObject

__all__ = [
	"ArrayList",
	"LinkedList",
	"List",
	"HashMap",
	"LinkedHashMap",
	"TreeMap",
	"Map",
	"HashSet",
	"LinkedHashSet",
	"TreeSet",
	"Set",
	"Collection",
	"Collections",
	"Arrays",
	"Objects",
	"Comparator",
	"Optional",
	"Locale",
	"UUID",
	"Random",
	"Timer",
	"TimerTask",
	"concurrent",
]

ArrayList = _LIST
LinkedList = _LIST
List = _LIST
HashMap = _MAP
LinkedHashMap = _MAP
TreeMap = _MAP
Map = _MAP
HashSet = _SET
LinkedHashSet = _SET
TreeSet = _SET
Set = _SET
Collection = _LIST
Comparator = _StubObject("java.util.Comparator")
Iterator = _StubObject("java.util.Iterator")


class _OptionalMeta(type):
	def of(cls, value):
		return Optional(value)

	def empty(cls):
		return Optional()


class Optional(metaclass=_OptionalMeta):
	_NIL = object()

	def __init__(self, value=_NIL):
		self._value = value

	def isPresent(self):
		return self._value is not Optional._NIL

	def isEmpty(self):
		return self._value is Optional._NIL

	def get(self):
		return self._value

	def orElse(self, other):
		return other if self._value is Optional._NIL else self._value


class _Locale:
	def __init__(self, language="", country=""):
		self.language = language
		self.country = country

	def toString(self):
		return f"{self.language}_{self.country}" if self.country else self.language

	def getLanguage(self):
		return self.language

	def getCountry(self):
		return self.country

	def getDefault(self):
		return _Locale("en", "US")


Locale = _Locale


class UUID(str):
	@staticmethod
	def randomUUID():
		import uuid as _uuid

		return str(_uuid.uuid4())


class Random:
	def __init__(self, seed=None):
		import random as _random

		self._r = _random.Random(seed)

	def nextInt(self, bound=None):
		return self._r.randrange(bound) if bound else self._r.randrange(1 << 31)

	def nextDouble(self):
		return self._r.random()

	def nextBoolean(self):
		return self._r.random() < 0.5

	def nextLong(self):
		return self._r.randrange(1 << 62)


class TimerTask:
	def run(self):
		return None

	def cancel(self):
		return None


class Timer:
	def __init__(self, *args, **kwargs):
		self._tasks = []

	def schedule(self, task, delay=0, *args, **kwargs):
		import threading

		def _run():
			try:
				task.run()
			except Exception:
				pass

		t = threading.Timer(max(0.0, float(delay) / 1000.0), _run)
		t.daemon = True
		self._tasks.append(t)
		t.start()
		return None

	def scheduleAtFixedRate(self, task, delay=0, period=0, *args, **kwargs):
		import threading

		stop = threading.Event()

		def _loop():
			while not stop.wait(max(0.05, float(period) / 1000.0)):
				try:
					task.run()
				except Exception:
					pass

		th = threading.Thread(target=_loop, daemon=True)
		self._tasks.append(th)
		th.start()
		return None

	def cancel(self):
		return None


class _Arrays:
	@staticmethod
	def asList(*items):
		if len(items) == 1 and isinstance(items[0], (list, tuple)):
			return _LIST(items[0])
		return _LIST(items)

	@staticmethod
	def sort(items):
		try:
			items.sort()
		except Exception:
			pass
		return None


class _Collections:
	@staticmethod
	def emptyList():
		return _LIST()

	@staticmethod
	def emptyMap():
		return _MAP()

	@staticmethod
	def emptySet():
		return _SET()

	@staticmethod
	def singletonList(item):
		return _LIST([item])

	@staticmethod
	def unmodifiableList(items):
		return _LIST(items)

	@staticmethod
	def reverse(items):
		try:
			items.reverse()
		except Exception:
			pass
		return None

	@staticmethod
	def sort(items, comparator=None):
		try:
			items.sort()
		except Exception:
			pass
		return None


class _Objects:
	@staticmethod
	def requireNonNull(value, message=None):
		return value

	@staticmethod
	def equals(a, b):
		return a == b

	@staticmethod
	def hashCode(value):
		return hash(value)

	@staticmethod
	def toString(value, default="null"):
		return default if value is None else str(value)


class _Concurrent:
	ScheduledExecutorService = _StubObject("ScheduledExecutorService")
	ExecutorService = _StubObject("ExecutorService")
	TimeUnit = _StubObject("TimeUnit")
	ConcurrentHashMap = _MAP
	CopyOnWriteArrayList = _LIST


class _ConcurrentPackage:
	__path__ = []

	@staticmethod
	def newSingleThreadExecutor():
		return _StubObject("ExecutorService")

	@staticmethod
	def newScheduledThreadPool(size):
		return _StubObject("ScheduledExecutorService")

	def __getattr__(self, name):
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		return _StubObject(f"java.util.concurrent.{name}")


concurrent = _ConcurrentPackage()
_Concurrent = _Concurrent
Arrays = _Arrays
Collections = _Collections
Objects = _Objects

# Declared so that `from java.lang.reflect import Method` and the other
# subpackages resolve. Without it CPython refuses the import before any
# finder is consulted, so a plugin that reached for one of them failed to
# load outright rather than degrading like everything else in the shim.
# The submodules themselves are answered by the stub finder.
__path__ = []
