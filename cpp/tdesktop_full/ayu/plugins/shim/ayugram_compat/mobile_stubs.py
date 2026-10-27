"""Live stubs for mobile-only packages so mobile plugins import and load.

Stubbed roots (org.*, com.*, androidx.*, ui, dalvik, kotlin*, okhttp3,
javax.*): every attribute is a no-op stub object (callable, chainable,
falsy), so import-time code survives without a Java runtime.

`java` / `android` are NOT stubbed: their real packages raise a clear
Android-only ImportError by design, which the C++ bridge maps to a
friendly refusal (see LoadPlugin). Guard them with try/except ImportError
and provide a desktop fallback.

Real files on sys.path (e.g. a plugin shipping its own `ui/` helper
package) always win over stubs.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import types
from typing import Any, Optional


_INSTALLED = False


class _StubObject:
	def __init__(self, name: str = "Stub"):
		self.__name__ = name
		self.__qualname__ = name

	def __call__(self, *args, **kwargs):
		return _StubObject(self.__name__)

	def __getattr__(self, name: str):
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		return _StubObject(f"{self.__name__}.{name}")

	def __iter__(self):
		return iter(())

	def __len__(self):
		return 0

	def __contains__(self, item):
		return False

	def __bool__(self):
		return False

	def __str__(self):
		return f"<ayu desktop stub {self.__name__}>"

	def __repr__(self):
		return str(self)

	def __int__(self):
		return 0

	def __index__(self):
		return 0


def stub(name: str = "Stub") -> _StubObject:
	"""Public factory: chainable no-op placeholder for a mobile-only API."""
	return _StubObject(name)


class _StubModule(types.ModuleType):
	def __getattr__(self, name: str):
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		obj = _StubObject(f"{self.__name__}.{name}")
		setattr(self, name, obj)
		return obj


class _StubLoader(importlib.abc.Loader):
	def __init__(self, fullname: str):
		self.fullname = fullname

	def create_module(self, spec):
		existing = sys.modules.get(self.fullname)
		if existing is not None:
			return existing
		mod = _StubModule(self.fullname)
		mod.__file__ = f"<ayu_stub:{self.fullname}>"
		mod.__package__ = self.fullname.rpartition(".")[0] or self.fullname
		mod.__path__ = []  # mark as package so sub-imports work
		mod.__loader__ = self
		mod.__spec__ = spec
		sys.modules[self.fullname] = mod
		return mod

	def exec_module(self, module):
		return None


_STUB_ROOTS = frozenset({
	"ui",
	"org",
	"com",
	"java",
	"android",
	"androidx",
	"dalvik",
	"kotlin",
	"kotlinx",
	"okhttp3",
	"okio",
	"javax",
	"javafx",
})


def _should_stub(fullname: str) -> bool:
	return fullname.split(".", 1)[0] in _STUB_ROOTS


def _has_real_module(fullname: str, path: Optional[list] = None) -> bool:
	"""True if a real (non-stub) module file exists for this name."""
	try:
		spec = importlib.machinery.PathFinder.find_spec(fullname, path)
	except Exception:
		return False
	if spec is None:
		return False
	return not isinstance(spec.loader, _StubLoader)


class _StubFinder(importlib.abc.MetaPathFinder):
	def find_spec(
			self,
			fullname: str,
			path: Optional[list] = None,
			target: Any = None):
		if not _should_stub(fullname):
			return None
		if fullname in sys.modules:
			mod = sys.modules[fullname]
			spec = getattr(mod, "__spec__", None)
			if spec is not None:
				return spec
			return None
		if _has_real_module(fullname, path):
			return None
		loader = _StubLoader(fullname)
		return importlib.machinery.ModuleSpec(
			fullname,
			loader,
			is_package=True)


def install() -> None:
	global _INSTALLED
	if not any(isinstance(f, _StubFinder) for f in sys.meta_path):
		sys.meta_path.insert(0, _StubFinder())
	_INSTALLED = True
