"""`android.app` on Desktop."""
from __future__ import annotations

from ayugram_compat.mobile_stubs import _StubObject

Activity = _StubObject("android.app.Activity")
Application = _StubObject("android.app.Application")
Notification = _StubObject("android.app.Notification")
NotificationManager = _StubObject("android.app.NotificationManager")


def __getattr__(name):
	if name.startswith("__") and name.endswith("__"):
		raise AttributeError(name)
	return _StubObject(f"android.app.{name}")

# Added by tools: this shim module doubles as a package so that
# `android.app.<anything>` resolves. Without __path__ Python treats
# it as a plain module and raises "is not a package" before any
# meta path finder is consulted.
__path__ = []
