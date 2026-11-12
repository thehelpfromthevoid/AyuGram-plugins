"""`android.graphics.drawable` aliases on Desktop."""
from __future__ import annotations

from android.graphics import _StubObject

Drawable = _StubObject("Drawable")
GradientDrawable = _StubObject("GradientDrawable")
ColorDrawable = _StubObject("ColorDrawable")
InsetDrawable = _StubObject("InsetDrawable")
LayerDrawable = _StubObject("LayerDrawable")

# Added by tools: this shim module doubles as a package so that
# `android.graphics2d.<anything>` resolves. Without __path__ Python treats
# it as a plain module and raises "is not a package" before any
# meta path finder is consulted.
__path__ = []
