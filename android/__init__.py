"""Desktop stand-in for the `android.*` packages.

Importing never fails, so `import android.view`, `from android.os import
Build` and friends keep a mobile plugin loadable on Desktop. Classes and
constants resolve to chainable no-op objects.

Useful real behaviour is kept where it costs nothing:
  Build.VERSION.SDK_INT -> 0
  Environment.getExternalStorageDirectory() -> the cache dir
  SystemClock.uptimeMillis()/elapsedRealtime() -> monotonic milliseconds
  Looper.getMainLooper().getMainThread() -> a stable sentinel
  Handler(looper) -> object with post/postDelayed/run
  View.VISIBLE/GONE/INVISIBLE, Gravity.*, Build.VERSION_CODES.*
"""
from __future__ import annotations

import time
from pathlib import Path

from ayugram_compat.mobile_stubs import _StubObject

__all__ = [
	"Build",
	"Environment",
	"SystemClock",
	"Looper",
	"Handler",
	"Bundle",
	"Intent",
	"Context",
	"Toast",
	"Uri",
	"DisplayMetrics",
	"Type",
	"Process",
]

_VERBOSE = False


def _enable_stub_logging() -> None:
	global _VERBOSE
	_VERBOSE = True


class _Handler:
	"""Runs the callback through a daemon thread so UI code does not block."""

	def __init__(self, looper=None, *args, **kwargs):
		self.looper = looper

	def post(self, fn=None, *args, **kwargs):
		if callable(fn):
			try:
				fn(*args, **kwargs)
			except Exception as e:
				from ayugram_compat.file_utils import get_cache_dir

				try:
					Path(get_cache_dir()).mkdir(parents=True, exist_ok=True)
				except Exception:
					pass
				if _VERBOSE:
					print(f"[android.Handler] {e}")
		return False

	def postDelayed(self, fn=None, delay=0, *args, **kwargs):
		import threading

		if not callable(fn):
			return False
		threading.Timer(max(0.0, float(delay) / 1000.0), self.post, args=(fn, *args)).start()
		return True

	def postAtTime(self, fn=None, uptime=0, *args, **kwargs):
		return self.post(fn, *args, **kwargs)

	def postDelayedAtTime(self, fn=None, uptime=0, delay=0, *args, **kwargs):
		return self.postDelayed(fn, delay, *args, **kwargs)

	def removeCallbacks(self, *args, **kwargs):
		return None

	def hasCallbacks(self, *args, **kwargs):
		return False

	def __getattr__(self, name):
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		return _StubObject(f"Handler.{name}")


class _Version:
	SDK_INT = 0
	RELEASE = ""

	def __getattr__(self, name):
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		return _StubObject(f"Build.{name}")


class _VersionCodes:
	KITKAT = 19
	LOLLIPOP = 21
	M = 23
	N = 24
	O = 26
	P = 28
	Q = 29
	R = 30
	S = 31
	TIRAMISU = 33
	UPSIDE_DOWN_CAKE = 34


class _Build:
	VERSION = _Version()
	VERSION_CODES = _VersionCodes()
	BRAND = ""
	MANUFACTURER = ""
	MODEL = ""
	DEVICE = ""
	SUPPORTED_ABIS = ()
	TAGS = ""

	def __getattr__(self, name):
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		return _StubObject(f"Build.{name}")


class _Environment:
	MEDIA_MOUNTED = "mounted"

	@staticmethod
	def getExternalStorageDirectory():
		from ayugram_compat.file_utils import get_cache_dir

		return Path(get_cache_dir())

	@staticmethod
	def getExternalStorageState():
		return "mounted"

	@staticmethod
	def getExternalFilesDir(*args, **kwargs):
		from ayugram_compat.file_utils import get_cache_dir

		return Path(get_cache_dir())

	@staticmethod
	def getCacheDir():
		from ayugram_compat.file_utils import get_cache_dir

		return Path(get_cache_dir())

	@staticmethod
	def getDataDir():
		from ayugram_compat.file_utils import get_files_dir

		return Path(get_files_dir())

	@staticmethod
	def getExternalStoragePublicDirectory(*args, **kwargs):
		from ayugram_compat.file_utils import get_files_dir

		return Path(get_files_dir())

	def __getattr__(self, name):
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		return _StubObject(f"Environment.{name}")


class _SystemClock:
	@staticmethod
	def uptimeMillis():
		return int(time.monotonic() * 1000)

	@staticmethod
	def elapsedRealtime():
		return int(time.monotonic() * 1000)

	@staticmethod
	def currentThreadTimeMillis():
		return int(time.monotonic() * 1000)

	@staticmethod
	def sleep(ms):
		time.sleep(max(0.0, float(ms) / 1000.0))


class _MainLooper(_StubObject):
	def __init__(self):
		super().__init__("Looper.getMainLooper")

	def getMainThread(self):
		return _StubObject("android.os.Looper.mainThread")

	def getThread(self):
		return _StubObject("android.os.Looper.thread")

	def quit(self):
		return None


class _Looper:
	@staticmethod
	def getMainLooper():
		if not hasattr(_Looper, "_main"):
			_Looper._main = _MainLooper()
		return _Looper._main

	@staticmethod
	def myLooper():
		return _Looper.getMainLooper()

	@staticmethod
	def prepare():
		return None


class _Toast:
	SHORT = 0
	LENGTH_SHORT = 0
	LENGTH_LONG = 1

	@staticmethod
	def makeText(ctx, text, duration=0):
		return _StubObject("android.widget.Toast.text")

	@staticmethod
	def show(*args, **kwargs):
		return None

	def __getattr__(self, name):
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		return _StubObject(f"Toast.{name}")


class _Process:
	@staticmethod
	def myPid():
		import os

		return os.getpid()

	@staticmethod
	def myUid():
		return 0


class _Type:
	TYPE_APPLICATION = 1
	TYPE_ACTIVITY = 2
	TYPE_SERVICE = 3


class _DisplayMetrics:
	density = 1.0
	densityDpi = 160
	widthPixels = 1920
	heightPixels = 1080
	scaledDensity = 1.0


Build = _Build()
Environment = _Environment()
SystemClock = _SystemClock()
Looper = _Looper()
Handler = _Handler
Toast = _Toast()
Process = _Process()
Type = _Type()
DisplayMetrics = _DisplayMetrics()


def __getattr__(name: str):
	if name.startswith("__") and name.endswith("__"):
		raise AttributeError(name)
	return _StubObject(f"android.{name}")
