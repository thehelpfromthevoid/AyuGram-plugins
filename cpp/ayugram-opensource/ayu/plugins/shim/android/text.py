"""`android.text` on Desktop."""
from __future__ import annotations

from ayugram_compat.mobile_stubs import _StubObject

Spanned = _StubObject("android.text.Spanned")
SpannableString = type("SpannableString", (), {
	"__init__": lambda self, text="": setattr(self, "_text", str(text)),
	"toString": lambda self: getattr(self, "_text", ""),
})
Html = _StubObject("android.text.Html")


def __getattr__(name):
	if name.startswith("__") and name.endswith("__"):
		raise AttributeError(name)
	return _StubObject(f"android.text.{name}")

# Added by tools: this shim module doubles as a package so that
# `android.text.<anything>` resolves. Without __path__ Python treats
# it as a plain module and raises "is not a package" before any
# meta path finder is consulted.
__path__ = []
