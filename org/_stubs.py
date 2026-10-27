"""Permissive stand-ins for the Android packages mobile plugins import.

A mobile plugin's first line is often `from org.telegram.messenger import
AndroidUtilities`. On Desktop that module does not exist, so the import raises
and the plugin never loads, even though the part of it that runs on Desktop
would have worked fine.

Rather than guess every name an Android API exposes, a module built with
`android_module` answers any attribute that was not defined explicitly. The
result is a placeholder that can be called, indexed and compared, records that
it was used, and returns a further placeholder. Real behaviour is filled in
for the few names that matter on Desktop.

The point is that an unreachable Android API degrades into "does nothing"
instead of "the plugin does not load".
"""

from __future__ import annotations

import functools

_uses: dict[str, int] = {}


def note_use(name: str) -> None:
	_uses[name] = _uses.get(name, 0) + 1
	# Сюда же и в общий отчёт: раньше касание Android-заглушки попадало
	# только в _uses, а отчёт плагина этот список не читал. Из-за этого
	# плагин выглядел исправным, хотя половина его вызовов ничего не делала.
	try:
		from ayugram_compat.gaps import note_gap
		note_gap(name)
	except Exception:
		# Отчёт не должен ломать сам плагин.
		pass


def unused() -> dict[str, int]:
	"""Every placeholder that was actually touched, for the plugin check."""
	return dict(sorted(_uses.items(), key=lambda kv: -kv[1]))


class Placeholder:
	"""Stands in for an Android class, method or constant.

	Anything can be done to it: called, subscripted, iterated, compared. Each
	operation is recorded so the log can say which Android API a plugin actually
	reached for instead of only that it could not import it.
	"""

	# __ayu_stub__ is a slot rather than a class attribute because only the
	# ayu_api fallback should set it: a plugin needs to be able to tell that a
	# bridge call quietly did nothing, apart from the inert Android classes
	# that use the same class.
	__slots__ = ("_path", "_log", "__ayu_stub__")

	def __init__(self, path: str, log: bool = False):
		self._path = path
		self._log = log

	def __repr__(self) -> str:
		return f"<android stub {self._path}>"

	def __getattr__(self, name: str) -> "Placeholder":
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		note_use(f"{self._path}.{name}")
		return Placeholder(f"{self._path}.{name}", self._log)

	def __setattr__(self, name: str, value) -> None:
		if name in Placeholder.__slots__:
			object.__setattr__(self, name, value)
			return
		note_use(f"{self._path}.{name} =")

	def __call__(self, *args, **kwargs):
		note_use(f"{self._path}()")
		return Placeholder(f"{self._path}()")

	def __getitem__(self, key):
		note_use(f"{self._path}[]")
		return Placeholder(f"{self._path}[]")

	def __setitem__(self, key, value) -> None:
		note_use(f"{self._path}[] =")

	def __iter__(self):
		note_use(f"{self._path} iter")
		return iter(())

	def __len__(self) -> int:
		note_use(f"{self._path} len")
		return 0

	def __bool__(self) -> bool:
		note_use(f"{self._path} bool")
		return False

	def __int__(self) -> int:
		note_use(f"{self._path} int")
		return 0

	def __index__(self) -> int:
		note_use(f"{self._path} index")
		return 0

	# Android flags are integers and plugins combine them constantly:
	# `req.flags |= 1`, `msg.flags |= MsgFlags.OUT`. Without these the
	# arithmetic raises TypeError and takes the whole plugin down over a field
	# that has no Desktop equivalent anyway. Returning a placeholder keeps the
	# plugin running through the rest of the statement, which is the whole
	# point of having a stub.
	def __or__(self, other) -> "Placeholder":
		note_use(f"{self._path} | {other!r}")
		return self

	def __ror__(self, other) -> "Placeholder":
		note_use(f"{other!r} | {self._path}")
		return self

	def __and__(self, other) -> "Placeholder":
		note_use(f"{self._path} & {other!r}")
		return self

	def __rand__(self, other) -> "Placeholder":
		note_use(f"{other!r} & {self._path}")
		return self

	def __xor__(self, other) -> "Placeholder":
		note_use(f"{self._path} ^ {other!r}")
		return self

	def __add__(self, other) -> "Placeholder":
		note_use(f"{self._path} + {other!r}")
		return self

	def __radd__(self, other) -> "Placeholder":
		note_use(f"{other!r} + {self._path}")
		return self

	def __sub__(self, other) -> "Placeholder":
		note_use(f"{self._path} - {other!r}")
		return self

	def __rsub__(self, other) -> "Placeholder":
		note_use(f"{other!r} - {self._path}")
		return self

	def __mul__(self, other) -> "Placeholder":
		note_use(f"{self._path} * {other!r}")
		return self

	def __rmul__(self, other) -> "Placeholder":
		note_use(f"{other!r} * {self._path}")
		return self

	def __invert__(self) -> "Placeholder":
		note_use(f"~{self._path}")
		return self

	def __neg__(self) -> "Placeholder":
		note_use(f"-{self._path}")
		return self

	def __str__(self) -> str:
		return ""

	def __enter__(self):
		return self

	def __exit__(self, *exc) -> bool:
		return False

	def __eq__(self, other) -> bool:
		return self is other

	def __hash__(self) -> int:
		return hash(self._path)


def stub_type(path: str) -> type:
	"""A class that can stand in for an Android class in any position.

	A plugin does three things with an Android class, and a plain instance only
	answers one of them:

	    View.OnClickListener        - subclassing it: class X(dynamic_proxy(...))
	    isinstance(x, ChatActivity) - needs a type, or Python raises TypeError
	    View.OnClickListener()      - calling it

	So an imported stub class is a real type here, whose instances are
	placeholders and whose metaclass hands out a fresh instance when called. That
	makes all three work, where an instance made `isinstance` raise and a class
	made the call fail.
	"""
	recorded = path

	def _init(self, *args, **kwargs):
		self._path = f"{recorded}({', '.join(repr(a) for a in args)})"
		self._log = False

	def _getattr(self, name: str) -> "Placeholder":
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		note_use(f"{self._path}.{name}")
		return Placeholder(f"{self._path}.{name}")

	def _setattr(self, name: str, value) -> None:
		if name in ("_path", "_log"):
			object.__setattr__(self, name, value)
			return
		note_use(f"{self._path}.{name} =")

	def _call(self, *args, **kwargs):
		note_use(f"{self._path}()")
		return Placeholder(f"{self._path}()")

	def _repr(self) -> str:
		return f"<android stub {self._path}>"

	namespace = {
		"__init__": _init,
		"__getattr__": _getattr,
		"__setattr__": _setattr,
		"__call__": _call,
		"__repr__": _repr,
		"__slots__": ("_path", "_log"),
		"__module__": "org._stubs",
		"__doc__": f"Stand-in for {path}.",
	}

	class _Meta(type):
		"""Treat a plain placeholder as a member of this stand-in.

		A plugin gets Android objects from two directions: it imports the class
		and it receives an object from a bridge call. The object is a Placeholder
		and the class is one of these, so without this the most natural check -
		isinstance(received, ImportedClass) - would be False for something that
		really did come from that class.
		"""

		def __instancecheck__(cls, instance) -> bool:
			if isinstance(instance, Placeholder):
				return True
			return type.__instancecheck__(cls, instance)

		def __getattr__(cls, name: str) -> "Placeholder":
			# Attribute access on the class itself, not on an instance of it.
			# Android APIs are reached both ways - `AndroidUtilities.dp()` and
			# `SomeClass.SOME_CONSTANT` - and without this the second form raised
			# where the first worked.
			if name.startswith("__") and name.endswith("__"):
				raise AttributeError(name)
			note_use(f"{cls.__name__}.{name}")
			return Placeholder(f"{cls.__name__}.{name}")

		def __repr__(cls) -> str:
			return f"<android stub class {cls.__name__}>"

	return _Meta(path.rsplit(".", 1)[-1], (Placeholder,), namespace)


def _module_getattr(module_name: str, prefix: str):
	"""Answer unknown attributes with a class that behaves like a placeholder."""
	cache: dict[str, type] = {}

	def resolve(name: str) -> type:
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		if name not in cache:
			note_use(f"{prefix}.{name}")
			cache[name] = stub_type(f"{prefix}.{name}")
		return cache[name]

	return resolve


def android_module(name: str, **real):
	"""Build a module whose real members are `real` and the rest resolve to stubs.

	Used as a module-level `__getattr__`, so `from <name> import anything`
	succeeds and yields a stand-in class for what is not implemented.

	Members passed as `real` that look like an inert Android class are turned
	into types rather than left as placeholder instances, so that
	`isinstance(x, SomeClass)` - which a plugin uses to ask "did I get the real
	thing?" - is a question Python can answer instead of a TypeError.
	"""
	import sys

	module = sys.modules[name]
	for key, value in real.items():
		if isinstance(value, Placeholder):
			real[key] = stub_type(f"{name.replace('.', '/')}.{key}")
		setattr(module, key, real[key])

	prefix = name.replace(".", "/")
	resolve = _module_getattr(name, prefix)
	previous = getattr(module, "__getattr__", None)

	def get(attr: str):
		try:
			if previous is not None:
				return previous(attr)
		except AttributeError:
			pass
		return resolve(attr)

	module.__getattr__ = get
	return module


@functools.lru_cache(maxsize=None)
def once(name: str):
	"""Log an unsupported Android call once instead of on every event."""
	def wrap(fn):
		@functools.wraps(fn)
		def inner(*args, **kwargs):
			note_use(name)
			return fn(*args, **kwargs)
		return inner

	return wrap