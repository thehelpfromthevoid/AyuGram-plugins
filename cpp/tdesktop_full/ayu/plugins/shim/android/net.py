"""`android.net` on Desktop."""
from __future__ import annotations

from ayugram_compat.mobile_stubs import _StubObject

ConnectivityManager = _StubObject("android.net.ConnectivityManager")
Uri = _StubObject("android.net.Uri")


def __getattr__(name):
	if name.startswith("__") and name.endswith("__"):
		raise AttributeError(name)
	return _StubObject(f"android.net.{name}")

# Added by tools: this shim module doubles as a package so that
# `android.net.<anything>` resolves. Without __path__ Python treats
# it as a plain module and raises "is not a package" before any
# meta path finder is consulted.
__path__ = []
