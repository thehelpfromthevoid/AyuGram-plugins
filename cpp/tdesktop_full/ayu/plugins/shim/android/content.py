"""`android.content` on Desktop."""
from __future__ import annotations

from ayugram_compat.mobile_stubs import _StubObject

Context = _StubObject("android.content.Context")
Intent = type("Intent", (), {
	"__init__": lambda self, *a, **k: None,
	"setAction": lambda self, *a, **k: self,
	"setClass": lambda self, *a, **k: self,
	"putExtra": lambda self, *a, **k: self,
	"getStringExtra": lambda self, *a, **k: None,
	"getExtras": lambda self: None,
	"getAction": lambda self: None,
})
IntentFilter = _StubObject("android.content.IntentFilter")
SharedPreferences = dict
BroadcastReceiver = _StubObject("android.content.BroadcastReceiver")

# Added by tools: this shim module doubles as a package so that
# `android.content.<anything>` resolves. Without __path__ Python treats
# it as a plain module and raises "is not a package" before any
# meta path finder is consulted.
__path__ = []
