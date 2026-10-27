"""`android.util` on Desktop."""
from __future__ import annotations

from android import DisplayMetrics
from ayugram_compat.mobile_stubs import _StubObject

__all__ = ["DisplayMetrics", "Log"]


class Log:
	VERBOSE = 2
	DEBUG = 3
	INFO = 4
	WARN = 5
	ERROR = 6

	@staticmethod
	def v(tag, msg):
		return 0

	@staticmethod
	def d(tag, msg):
		return 0

	@staticmethod
	def i(tag, msg):
		return 0

	@staticmethod
	def w(tag, msg):
		return 0

	@staticmethod
	def e(tag, msg):
		return 0

# Added by tools: this shim module doubles as a package so that
# `android.util.<anything>` resolves. Without __path__ Python treats
# it as a plain module and raises "is not a package" before any
# meta path finder is consulted.
__path__ = []
