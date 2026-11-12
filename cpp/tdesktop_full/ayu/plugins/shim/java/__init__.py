"""Desktop stand-in for the Chaquopy `java` bridge.

Importing this module never fails, so a mobile plugin that does
`from java import jclass` at module level keeps loading on Desktop. Every
Java type resolves to a chainable no-op object, so class lookups, method
calls and field access survive instead of aborting the import.

What this buys you: all non-UI plugin logic (settings, schedulers, hooks,
message parsing, request building) runs normally. What it cannot do:
anything that depends on a real Java runtime stays inert instead of
raising. Guard the parts you actually need with `hasattr` / `try ... except`
when a Desktop fallback exists.
"""
from __future__ import annotations

import time

from ayugram_compat.mobile_stubs import _StubObject

__all__ = [
	"jclass",
	"jarray",
	"jcast",
	"cast",
	"jvoid",
	"jvalue",
	"jboolean",
	"jbyte",
	"jchar",
	"jshort",
	"jint",
	"jlong",
	"jfloat",
	"jdouble",
	"jstring",
	"jobject",
	"JNI",
	"JavaException",
	"ExceptionInInitializerError",
	"detach",
	"attach",
	"get_env",
	"dynamic_proxy",
	"PythonJavaClass",
	"JavaClass",
]

JVM = _StubObject("java.JVM")


class _JavaClass(_StubObject):
	def __init__(self, name: str):
		super().__init__(name)

	def getClass(self):
		return self

	def getName(self):
		return self.__name__

	def getMethod(self, *args, **kwargs):
		return _StubObject(f"{self.__name__}.method")

	def getDeclaredMethod(self, *args, **kwargs):
		return _StubObject(f"{self.__name__}.method")

	def getDeclaredField(self, *args, **kwargs):
		return _StubObject(f"{self.__name__}.field")

	def getField(self, *args, **kwargs):
		return _StubObject(f"{self.__name__}.field")

	def newInstance(self, *args, **kwargs):
		return _StubObject(f"{self.__name__}()")

	def cast(self, obj):
		return obj

	def isInstance(self, obj):
		return False

	def isAssignableFrom(self, cls):
		return False

	def getClassLoader(self):
		return _StubObject(f"{self.__name__}.classloader")

	def forName(self, name):
		return _JavaClass(str(name))


class _JavaType(_StubObject):
	"""A Java type reference: usable as a base class and as a factory."""

	def __init__(self, name: str):
		super().__init__(name)

	def __call__(self, *args, **kwargs):
		return _StubObject(f"{self.__name__}()")

	def getClass(self):
		return _JavaClass(self.__name__)

	def class_():
		return _JavaClass("java.lang.Object")


class _JavaList(list):
	"""List with the Java accessor names mobile plugins actually call."""

	def size(self):
		return len(self)

	def add(self, item):
		self.append(item)
		return True

	def get(self, index):
		try:
			return self[int(index)]
		except Exception:
			return None

	def isEmpty(self):
		return len(self) == 0

	def contains(self, item):
		return list.__contains__(self, item)

	def addAll(self, items):
		self.extend(list(items or ()))
		return True

	def toArray(self):
		return list(self)

	def iterator(self):
		return iter(self)

	def clear(self):
		del self[:]

	def set(self, index, value):
		try:
			self[int(index)] = value
		except Exception:
			pass

	def removeAt(self, index):
		try:
			del self[int(index)]
		except Exception:
			pass


class _JavaMap(dict):
	"""Map with get/put/containsKey and a size() accessor."""

	def put(self, key, value):
		self[key] = value
		return None

	def getOrDefault(self, key, default=None):
		return self.get(key, default)

	def containsKey(self, key):
		return key in self

	def containsValue(self, value):
		return value in self.values()

	def keySet(self):
		return _JavaSet(self.keys())

	def values(self):
		return _JavaList(self.values())

	def entrySet(self):
		return _JavaList(list(self.items()))

	def size(self):
		return len(self)

	def isEmpty(self):
		return len(self) == 0

	def remove(self, key):
		return dict.pop(self, key, None)


class _JavaSet(set):
	def size(self):
		return len(self)

	def isEmpty(self):
		return len(self) == 0

	def toArray(self):
		return list(self)

	def iterator(self):
		return iter(self)


JavaClass = _JavaType
PythonJavaClass = JavaClass
ExceptionInInitializerError = _JavaType("java.lang.ExceptionInInitializerError")
JavaException = _JavaType("java.lang.Throwable")


def jclass(name: str = "java.lang.Object", *args, **kwargs) -> _JavaClass:
	"""`jclass("org.telegram.ui.Cells.Foo")` -> chainable class stub."""
	return _JavaClass(str(name))


def jarray(name, size=None, *args, **kwargs):
	return _StubObject(f"jarray:{name}")


def jcast(obj, *args, **kwargs):
	return obj


def cast(obj, *args, **kwargs):
	return obj


def dynamic_proxy(interface, handler=None, *args, **kwargs):
	"""`dynamic_proxy(Listener, handler)` - a base class meant to be inherited.

	It has to be a class. The idiom it exists for is subclassing:

	    class PressAnim(dynamic_proxy(View.OnTouchListener)):
	        def onTouch(self, view, event):
	            ...

	Handing back an object raised TypeError on that line, and one plugin uses the
	idiom seven times - for every listener it installs into Telegram, so it
	failed on the first one and none of its integration worked. Dispatch is
	forwards to `handler` when one is given, so the shape of the plugin's code
	survives even though there is no Java runtime here to call it on.
	"""
	from org._stubs import Placeholder, note_use

	label = getattr(interface, "__name__", None) or str(interface)

	def _init(self, *a, **k):
		self._path = f"proxy:{label}"
		self._log = False

	def _getattr(self, name: str):
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		if handler is not None:
			entry = getattr(handler, name, None)
			if callable(entry):
				return entry
		note_use(f"proxy:{label}.{name}")
		return Placeholder(f"proxy:{label}.{name}")

	def _setattr(self, name: str, value) -> None:
		if name in ("_path", "_log"):
			object.__setattr__(self, name, value)
			return
		note_use(f"proxy:{label}.{name} =")

	def _repr(self) -> str:
		return f"<android stub proxy {label}>"

	return type("dynamic_proxy", (Placeholder,), {
		"__init__": _init,
		"__getattr__": _getattr,
		"__setattr__": _setattr,
		"__repr__": _repr,
		"__slots__": ("_path", "_log"),
		"__doc__": f"Stand-in for the Java interface {label}.",
	})


def detach():
	return None


def attach():
	return None


def get_env(*args, **kwargs):
	return None


JNI = _JavaType("java.JNI")
jvoid = _JavaType("void")
jvalue = _JavaType("value")
jboolean = _JavaType("boolean")
jbyte = _JavaType("byte")
jchar = _JavaType("char")
jshort = _JavaType("short")
jint = _JavaType("int")
jlong = _JavaType("long")
jfloat = _JavaType("float")
jdouble = _JavaType("double")
jstring = _JavaType("java.lang.String")
jobject = _JavaType("java.lang.Object")

_LIST = _JavaList
_MAP = _JavaMap
_SET = _JavaSet
