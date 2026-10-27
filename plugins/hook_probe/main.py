__id__ = "hook_probe"
__name__ = "Hook Probe (диагностика)"
__description__ = "Пишет в файл каждый хук, который приложение реально передаёт. Нужен, чтобы понять, какие события работают на ПК."
__version__ = "1.0"
__author__ = "AyuGram Desktop"
__min_version__ = "7.0.0"

import json
import os
import time
import traceback

from base_plugin import BasePlugin
from hook_utils import hook

MAX_LINES = 400


def _log_dir():
	env = os.environ.get("AYU_PLUGINS_DIR")
	if env:
		return env
	base = os.environ.get("APPDATA") or os.path.expanduser("~")
	return os.path.join(base, "AyuGramDesktop", "plugins")


def _log_path():
	return os.path.join(_log_dir(), "hook_probe.log")


def _alt_paths():
	out = []
	appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
	out.append(os.path.join(appdata, "AyuGramDesktop", "plugins", "hook_probe.log"))
	out.append(os.path.join(appdata, "AyuGramDesktop", "hook_probe.log"))
	try:
		out.append(os.path.join(os.getcwd(), "hook_probe.log"))
	except Exception:
		pass
	out.append(os.path.join(os.environ.get("TEMP", "/tmp"), "hook_probe.log"))
	return out


def _warn(message):
	"""Last-resort channel when the plugin's own log file is unusable."""
	try:
		import ayu_api

		ayu_api.log(f"[HookProbe] {message}")
	except Exception:
		try:
			print(f"[HookProbe] {message}")
		except Exception:
			pass


_last_failure = ""


def _record(kind, detail=""):
	line = "%s  %-22s %s" % (
		time.strftime("%Y-%m-%d %H:%M:%S"),
		kind,
		detail,
	)
	written = False
	failure = ""
	for path in dict.fromkeys([_log_path(), *_alt_paths()]):
		try:
			os.makedirs(os.path.dirname(path), exist_ok=True)
			with open(path, "a", encoding="utf-8") as f:
				f.write(line + "\n")
			written = True
		except Exception as e:
			# The sandbox denies writes without the storage.write permission,
			# and the paths above all live outside the plugin folder, so every
			# one of them can fail for that single reason. Say so once instead
			# of leaving a probe that looks like it is not running.
			failure = failure or f"{type(e).__name__}: {e}"
	if not written and failure and failure != _last_failure:
		_last_failure = failure
		_warn(f"cannot write hook_probe.log ({failure}) "
		      f"- add storage.write to the manifest")


def _field(obj, name, default=None):
	"""Read a field from either a pybind11 object or a plain dict."""
	if obj is None:
		return default
	if isinstance(obj, dict):
		return obj.get(name, default)
	try:
		value = getattr(obj, name)
	except AttributeError:
		return default
	return default if value is None else value


_received_state = set()
_generation = 0


def _received():
	return _received_state


def _next_generation():
	"""Claim a generation id; a newer load invalidates older workers."""
	global _generation
	_generation += 1
	_received_state.clear()
	return _generation


def _is_current(generation):
	return generation == _generation


class HookProbePlugin(BasePlugin):
	def on_plugin_load(self):
		_record("plugin_load", "probe started")
		caps = json.dumps(self._capabilities())
		_record("capability", caps)
		try:
			self.log(f"[HookProbe] started; bridge={caps}")
		except Exception as e:
			_record("log_failed", str(e))
		try:
			self._run_selftest_async()
		except Exception as e:
			_record("selftest_start_failed", f"{type(e).__name__}: {e}")

	def _run_selftest_async(self):
		# on_plugin_load runs on the main thread while the app is still
		# starting, and the events we synthesise are queued back onto that
		# same thread. Waiting here would deadlock, so poll from a daemon
		# thread and let on_plugin_load return first.
		import threading

		# A live reload re-runs this module while the previous worker may
		# still be waiting; the generation tells the old one to give up so
		# it cannot report a bogus result for a plugin that no longer exists.
		generation = _next_generation()

		def _safe():
			# The GIL stays held while the app finishes starting, so the
			# first thing this worker does is touch the bridge: that call
			# blocks until the app lets the interpreter go.
			try:
				import ayu_api as _api

				_api.hook_count()
			except Exception:
				pass
			if _is_current(generation):
				try:
					self._run_selftest(generation)
				except BaseException as e:
					_record("selftest_crashed", f"{type(e).__name__}: {e}")

		threading.Thread(target=_safe, daemon=True).start()

	def _run_selftest(self, generation=None):
		"""Dispatch synthetic events and confirm each hook comes back.

		Hooks like on_chat_opened only fire while a real chat is open, so
		this proves the whole C++ -> bridge -> shim -> plugin path without
		needing a signed-in account.
		"""
		import ayu_api

		if not hasattr(ayu_api, "emit_test_hooks"):
			_record("selftest", "emit_test_hooks unavailable (old build)")
			return
		try:
			ayu_api.emit_test_hooks(peer_id=777, msg_id=888, text="probe")
		except Exception as e:
			_record("selftest_failed", f"{type(e).__name__}: {e}")
			return
		deadline = time.time() + 10.0
		while time.time() < deadline and len(_received()) < 4:
			time.sleep(0.05)
		got = sorted(_received())
		if len(got) < 4:
			_record("selftest_incomplete",
					f"got {got}, missing: " + ",".join(
						k for k in ("message", "chat", "deleted", "sending")
						if k not in got))


	@hook("on_message")
	def _on_message(self, msg):
		_record(
			"on_message",
			f"peer={_field(msg, 'peer_id', 0)} "
			f"id={_field(msg, 'msg_id', 0)} "
			f"out={bool(_field(msg, 'out', False))} "
			f"text={_field(msg, 'text', '')!r:.60}",
		)
		_received().add("message")

	@hook("on_chat_opened")
	def _on_chat(self, chat):
		_record(
			"on_chat_opened",
			f"peer={_field(chat, 'peer_id', 0)} "
			f"title={_field(chat, 'title', '')!r}",
		)
		_received().add("chat")

	@hook("on_message_deleted")
	def _on_deleted(self, ctx):
		ids = _field(ctx, "msgIds", None) or _field(ctx, "msg_ids", None) or []
		_record("on_message_deleted", f"ids={list(ids)}")
		_received().add("deleted")

	@hook("on_message_sending")
	def _on_sending(self, ctx):
		msg = _field(ctx, "message", None)
		_record("on_message_sending", f"text={_field(msg, 'text', '')!r:.60}")
		_received().add("sending")

	def _capabilities(self):
		out = {}
		for name in ("toast", "log", "hook", "send_text", "edit_message"):
			try:
				import ayu_api

				out[name] = hasattr(ayu_api, name)
			except Exception as e:
				out[name] = f"error: {e}"
		return out

	@hook("on_app_start")
	def _on_start(self, *args):
		_record("on_app_start", "сработал")

	@hook("on_app_stop")
	def _on_stop(self, *args):
		_record("on_app_stop", "сработал")

	@hook("on_setting_changed")
	def _on_setting(self, key, value=None):
		_record("on_setting_changed", f"{key}={value!r}")

	def on_plugin_unload(self):
		_record("plugin_unload", "probe stopped")

	def create_settings(self):
		return []
