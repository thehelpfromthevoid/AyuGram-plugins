"""`android.os` on Desktop - real clock/loop/handler, stubs for the rest."""
from __future__ import annotations

from android import Handler, Looper, Process, SystemClock
from ayugram_compat.mobile_stubs import _StubObject

__all__ = [
	"Build",
	"Environment",
	"Looper",
	"Handler",
	"SystemClock",
	"Process",
	"Bundle",
	"Parcelable",
	"AsyncTask",
	"UserHandle",
]


class Bundle(dict):
	"""Working dict-backed Bundle: mobile plugins do getString/putString."""

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)

	def getString(self, key, default=None):
		return self.get(key, default)

	def putString(self, key, value):
		self[key] = value

	def getInt(self, key, default=0):
		try:
			return int(self.get(key, default))
		except Exception:
			return default

	def putInt(self, key, value):
		self[key] = int(value)

	def getBoolean(self, key, default=False):
		return bool(self.get(key, default))

	def putBoolean(self, key, value):
		self[key] = bool(value)

	def getParcelable(self, key, default=None):
		return self.get(key, default)

	def putParcelable(self, key, value):
		self[key] = value


class AsyncTask:
	"""Runs `doInBackground` synchronously, then `onPostExecute`."""

	def __init__(self, *args, **kwargs):
		self.args = args

	def doInBackground(self, *args, **kwargs):
		return None

	def onPostExecute(self, result=None):
		return None

	def onCancelled(self, result=None):
		return None

	def execute(self, *args):
		import threading

		def _run():
			try:
				result = self.doInBackground(*args)
				self.onPostExecute(result)
			except Exception:
				pass

		threading.Thread(target=_run, daemon=True).start()
		return self


UserHandle = _StubObject("android.os.UserHandle")
Parcelable = _StubObject("android.os.Parcelable")

from android import Build, Environment  # noqa: E402  (re-export)

# Added by tools: this shim module doubles as a package so that
# `android.os.<anything>` resolves. Without __path__ Python treats
# it as a plain module and raises "is not a package" before any
# meta path finder is consulted.
__path__ = []
