"""`android.graphics` on Desktop."""
from __future__ import annotations

from ayugram_compat.mobile_stubs import _StubObject

Color = _StubObject("android.graphics.Color")
Paint = _StubObject("android.graphics.Paint")
Canvas = _StubObject("android.graphics.Canvas")
Typeface = _StubObject("android.graphics.Typeface")
Rect = _StubObject("android.graphics.Rect")


def __getattr__(name):
	if name.startswith("__") and name.endswith("__"):
		raise AttributeError(name)
	return _StubObject(f"android.graphics.{name}")

# Added by tools: this shim module doubles as a package so that
# `android.graphics.<anything>` resolves. Without __path__ Python treats
# it as a plain module and raises "is not a package" before any
# meta path finder is consulted.
__path__ = []
