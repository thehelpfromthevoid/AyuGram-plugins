"""Android messenger helpers, mapped onto Desktop where it makes sense."""

from __future__ import annotations

import json
import os
from pathlib import Path

from org._stubs import Placeholder, android_module, note_use  # noqa: F401


def _state_dir() -> Path:
	env = os.environ.get("AYU_PLUGINS_DIR")
	base = Path(env) if env else Path(
		os.environ.get("APPDATA", os.path.expanduser("~")))
	return base / "android"


class _Preferences:
	"""A small JSON-backed stand-in for Android SharedPreferences."""

	def __init__(self, name: str):
		self._name = name
		self._path = _state_dir() / (name.replace("/", "_") + ".json")
		self._values = {}
		self._loaded = False

	def _load(self) -> None:
		if self._loaded:
			return
		self._loaded = True
		try:
			if self._path.is_file():
				self._values = json.loads(
					self._path.read_text(encoding="utf-8"))
		except Exception:
			self._values = {}

	def _save(self) -> None:
		try:
			self._path.parent.mkdir(parents=True, exist_ok=True)
			self._path.write_text(
				json.dumps(self._values, ensure_ascii=False),
				encoding="utf-8")
		except Exception:
			pass

	def edit(self) -> "_Editor":
		return _Editor(self)

	def getBoolean(self, key, default=False) -> bool:
		self._load()
		return bool(self._values.get(key, default))

	def getInt(self, key, default=0) -> int:
		self._load()
		try:
			return int(self._values.get(key, default))
		except Exception:
			return default

	def getString(self, key, default="") -> str:
		self._load()
		return str(self._values.get(key, default))

	def contains(self, key) -> bool:
		self._load()
		return key in self._values

	def registerOnSharedPreferenceChangeListener(self, listener) -> None:
		note_use("AndroidUtilities.registerOnSharedPreferenceChangeListener")

	def unregisterOnSharedPreferenceChangeListener(self, listener) -> None:
		note_use("AndroidUtilities.unregisterOnSharedPreferenceChangeListener")


class _Editor:
	def __init__(self, preferences: _Preferences):
		self._preferences = preferences
		self._changes: dict = {}

	def putBoolean(self, key, value) -> "_Editor":
		self._changes[key] = bool(value)
		return self

	def putInt(self, key, value) -> "_Editor":
		self._changes[key] = int(value)
		return self

	def putLong(self, key, value) -> "_Editor":
		self._changes[key] = int(value)
		return self

	def putString(self, key, value) -> "_Editor":
		self._changes[key] = str(value)
		return self

	def remove(self, key) -> "_Editor":
		self._changes[key] = None
		return self

	def clear() -> "_Editor":
		self._changes.clear()
		return self

	def commit(self) -> bool:
		self._preferences._load()
		for key, value in self._changes.items():
			if value is None:
				self._preferences._values.pop(key, None)
			else:
				self._preferences._values[key] = value
		self._preferences._save()
		self._changes.clear()
		return True

	def apply() -> None:
		self.commit()


class _AndroidUtilities:
	_preferences: dict = {}

	def getSharedPreferences(self, name, mode=0) -> _Preferences:
		if name not in _AndroidUtilities._preferences:
			_AndroidUtilities._preferences[name] = _Preferences(name)
		return _AndroidUtilities._preferences[name]

	def getApplicationContext(self):
		return Placeholder("ApplicationContext")

	def getAppContext(self):
		return Placeholder("ApplicationContext")

	def runOnUiThread(self, action) -> None:
		"""Desktop is single threaded here, so run it and say so once."""
		note_use("AndroidUtilities.runOnUiThread")
		try:
			from ayu_api import hook as _hook  # noqa: F401
			from ayugram_compat.android_utils import run_on_ui_thread
			run_on_ui_thread(action)
		except Exception:
			try:
				action()
			except Exception:
				pass

	def getAppVersion(self) -> str:
		return "desktop"

	def getApiLevel(self) -> int:
		return 0

	def getDeviceModel(self) -> str:
		import platform as _platform

		return _platform.machine() or "desktop"

	def getCurrentAccount(self):
		return Placeholder("Account")

	def getNotificationManager(self):
		return Placeholder("NotificationManager")

	def isUserProhibited(self, flags, value) -> bool:
		return False

	def readBytes(self, bytes_in) -> bytes:
		return bytes(bytes_in or ())

	def __getattr__(self, name: str):
		"""Degrade for anything not spelled out above.

		AndroidUtilities is the most-used class in the mobile plugin surface, and
		it was the one place that did not degrade: it had no __getattr__, so
		`AndroidUtilities.dp(64)`, `.bold()`, `.displaySize` and the rest raised
		AttributeError. One plugin defines dp() by delegating straight to it, so
		every single call it made died on the first line - and it has no idea
		which of its features ran. Answering with a placeholder lets the plugin
		keep going, and the name is recorded so the report can list it.
		"""
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		note_use(f"AndroidUtilities.{name}")
		return Placeholder(f"AndroidUtilities.{name}")

	# The few that have a real answer on Desktop, spelled out because a stub
	# would make the plugin's layout maths wrong rather than merely empty.
	def dp(self, value=None):
		"""Density-independent pixels are a no-op on Desktop: 1:1."""
		return 1 if value is None else value

	def dpf(self, value=None):
		return 1.0 if value is None else float(value)

	def dpf2(self, value=None):
		return 1.0 if value is None else float(value)

	def bold(self, text):
		return text

	def navigationBarHeight(self) -> int:
		return 0

	def isCurrentThemeDark(self) -> bool:
		return False


android_module(
	__name__,
	AndroidUtilities=_AndroidUtilities(),
	UserConfig=Placeholder("UserConfig"),
	MessageObject=Placeholder("MessageObject"),
	FileLoader=Placeholder("FileLoader"),
	GsmPhoneFormat=Placeholder("GsmPhoneFormat"),
	ContactsController=Placeholder("ContactsController"),
	ImageLoader=Placeholder("ImageLoader"),
	MediaController=Placeholder("MediaController"),
	NotificationCenter=Placeholder("NotificationCenter"),
	MessagesController=Placeholder("MessagesController"),
	SendBroadcastHelper=Placeholder("SendBroadcastHelper"),
	BuildVars=Placeholder("BuildVars"),
)