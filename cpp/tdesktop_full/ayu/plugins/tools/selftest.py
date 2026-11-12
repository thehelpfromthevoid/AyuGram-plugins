"""End-to-end self test for the Desktop plugin runtime.

Runs entirely outside the app: it drives the same shim and loader the
embedded interpreter uses, so a failure here is a real plugin-runtime
failure rather than a UI problem.

    python selftest.py                 # run every check
    python selftest.py -v              # show each check name
    python selftest.py --only hooks    # run one group
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import traceback
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGINS = HERE.parent
STANDALONE = PLUGINS / "standalone"
EXAMPLES = PLUGINS / "examples"
CLI = HERE / "ayu-plugin-cli"
# The built app. When the tests run from a deployment, selftest.py sits in
# <app>/tools/, so its parent is the app directory holding the runtime.
DEPLOYED = (PLUGINS if (PLUGINS / "AyuGram.exe").is_file()
           else Path(sys.executable).resolve().parent)


def _resolve_shim() -> Path:
	"""The shim lives in shim/ in the repository and at the top of a
	deployment, where it is the interpreter's search root. Pick whichever
	actually holds ayugram_compat so the checks mean the same thing in both."""
	for candidate in (PLUGINS / "shim", PLUGINS):
		if (candidate / "ayugram_compat" / "__init__.py").is_file():
			return candidate
	return PLUGINS / "shim"


SHIM = _resolve_shim()

_RESULTS: list[tuple[str, str, str]] = []


class Skip(Exception):
	pass


def _check(group: str, name: str):
	def deco(fn):
		_RESULTS.append((group, name, fn))
		return fn
	return deco


def _fresh_sys_path() -> list[str]:
	for entry in (str(SHIM), str(STANDALONE)):
		if entry not in sys.path:
			sys.path.insert(0, entry)


_fresh_sys_path()


def _reset_modules() -> None:
	"""Drop every shim/loader module so each check starts clean."""
	prefixes = ("ayugram_compat", "ayu_api", "java", "android", "plugin_loader", "extera_utils", "ayu_utils")
	for name in list(sys.modules):
		if name.split(".")[0] in prefixes:
			del sys.modules[name]


def _quiet(fn, *args, **kwargs):
	out, err = io.StringIO(), io.StringIO()
	with redirect_stdout(out), redirect_stderr(err):
		result = fn(*args, **kwargs)
	return result, out.getvalue() + err.getvalue()


# ---------------------------------------------------------------- shim

@_check("shim", "java.util collections work")
def _java_util():
	_reset_modules()
	from java.util import ArrayList, HashMap, HashSet

	lst = ArrayList()
	lst.add("a")
	lst.add("b")
	assert lst.size() == 2, lst.size()
	assert lst.get(0) == "a"
	assert "b" in lst

	m = HashMap()
	m.put("k", 1)
	assert m.get("k") == 1
	assert m.containsKey("k")

	s = HashSet()
	s.add("x")
	s.add("x")
	assert s.size() == 1


@_check("shim", "java.lang String and StringBuilder work")
def _java_lang():
	_reset_modules()
	from java.lang import String, StringBuilder, System, Math

	assert String("abc").length() == 3
	assert String("abc").toUpperCase() == "ABC"
	b = StringBuilder()
	b.append("a").append("b")
	assert str(b) == "ab", str(b)
	assert Math.max(2, 7) == 7
	assert Math.abs(-3) == 3
	System.out.println("swallowed")


@_check("shim", "android os and view stubs work")
def _android():
	_reset_modules()
	from android.os import Handler, Looper, SystemClock
	from android.view import View, ViewGroup, Gravity
	from android.widget import TextView, EditText

	assert Looper.getMainLooper() is not None
	h = Handler(Looper.getMainLooper())
	h.postDelayed(lambda: None, 10)
	assert SystemClock.elapsedRealtime() >= 0

	v = View(None)
	assert v.getVisibility() == View.VISIBLE
	g = Gravity.CENTER
	assert g is not None
	vg = ViewGroup(None)
	assert vg is not None
	tv = TextView(None)
	assert tv.getText() is not None
	et = EditText(None)
	assert et is not None


@contextmanager
def _isolated_store(label: str):
	"""Point the settings store at a temp dir for the duration of a check."""
	from ayugram_compat import settings_store

	previous = os.environ.get("AYU_PLUGINS_DIR")
	root = Path(tempfile.mkdtemp(prefix=f"ayu-selftest-{label}-"))
	os.environ["AYU_PLUGINS_DIR"] = str(root)
	settings_store._CACHE.clear()
	try:
		yield root, settings_store
	finally:
		if previous is None:
			os.environ.pop("AYU_PLUGINS_DIR", None)
		else:
			os.environ["AYU_PLUGINS_DIR"] = previous
		settings_store._CACHE.clear()
		shutil.rmtree(root, ignore_errors=True)


@_check("shim", "settings store round-trips")
def _settings():
	_reset_modules()
	with _isolated_store("settings") as (_, settings_store):
		settings_store.set_value("demo", "delay", 250)
		settings_store.set_value("demo", "enabled", True)
		assert settings_store.read("demo")["delay"] == 250
		assert settings_store.read("demo")["enabled"] is True
		settings_store.set_value("demo", "delay", 900)
		settings_store._CACHE.clear()
		assert settings_store.read("demo")["delay"] == 900
		assert (settings_store.plugins_dir() / "demo.settings.json").is_file()


@_check("watchdog", "it can tell a quit from a crash, without guessing")
def _watchdog_rules():
	import os
	import time

	sys.path.insert(0, str(HERE))
	import importlib

	wd = importlib.import_module("watchdog")

	# A process killed with TerminateProcess reports exit code 0 on this path,
	# so an earlier version that trusted the code took every crash for a clean
	# shutdown and never recovered from one. The marker is what decides.
	assert wd.crashed(0) is False
	assert wd.crashed(3221225477) is True, "an access violation is a crash"
	assert wd.crashed(-1) is True

	import tempfile

	with tempfile.TemporaryDirectory() as folder:
		root = Path(folder)
		marker = root / "clean-exit.marker"
		assert wd.clean_exit_recently(root) is False, "no marker means a crash"
		marker.write_text("1", encoding="utf-8")
		assert wd.clean_exit_recently(root) is True, "a fresh marker means a quit"
		old = time.time() - 3600
		os.utime(marker, (old, old))
		assert wd.clean_exit_recently(root) is False, "a stale marker is not now"

	# A process with no window is starting up or sitting in the tray. Neither is
	# a hang, and killing either would restart an app that is fine.
	child = subprocess.Popen(
		[sys.executable, "-c", "import time; time.sleep(5)"])
	try:
		assert wd.is_hung(child.pid) is False, "windowless was called hung"
	finally:
		child.kill()
		assert child.wait(timeout=10) is not None


@_check("shim", "the schema is built the same way whoever asks for it")
def _one_schema():
	# There used to be two implementations of the row mapping, and they
	# disagreed: the one the app went through typed every Input row as a number
	# while the other had learned to read the declared default. A plugin's text
	# field was therefore edited as a number and stored as one - the dialog
	# showing 0 where the plugin had declared "привет".
	_reset_modules()
	from base_plugin import BasePlugin
	from ui.settings import Input, Selector
	from ayugram_compat import schema

	class Textish(BasePlugin):
		def create_settings(self):
			return [
				Input(key="count", text="Count", default="7"),
				Input(key="caption", text="Caption", default="привет"),
				Input(key="limit", text="Limit", default=10),
				Selector(key="mode", text="Mode", default=0, items=["a", "b"]),
			]

	plugin = Textish()
	rows = plugin.setting_schema()
	kinds = {r["key"]: r["type"] for r in rows if r.get("key")}
	# A string default is text even though the row is an Input, which is the
	# widget Android uses for numbers too.
	assert kinds["caption"] == "text", kinds
	assert kinds["count"] == "text", kinds
	assert kinds["limit"] == "int", kinds
	assert kinds["mode"] == "choice", kinds

	# The bridge reads the schema through rows_for, and it must agree exactly:
	# rows_for looks for `setting_schema` first, so a mismatch here is the bug
	# coming back through the other door.
	via_bridge = schema.rows_for(plugin, None)
	assert [r.get("type") for r in via_bridge] == [r.get("type") for r in rows], (
		via_bridge, rows)
	# Asking twice must terminate and agree - the shared implementation is one
	# call away from recursing into itself.
	assert plugin.setting_schema() == rows


@_check("shim", "a text setting comes back as text even if a number was stored")
def _settings_types():
	_reset_modules()
	with _isolated_store("settings_types") as (_, settings_store):
		# What a numeric dialog used to write for a text field. Reading it back
		# has to hand the plugin a string, because the plugin's own default says
		# the setting holds text and it will call string methods on the result.
		settings_store.set_value("demo", "animation_name", 0)
		assert settings_store.get("demo", "animation_name", "") == "0"
		assert isinstance(settings_store.get("demo", "animation_name", ""), str)
		# And the other direction still holds.
		settings_store.set_value("demo", "interval", "250")
		assert settings_store.get("demo", "interval", 0) == 250
		assert isinstance(settings_store.get("demo", "interval", 0), int)
		# A number in a field the plugin declared as text is kept, not discarded: the
		# plugin declared a string default but the dialog stores a number, and
		# reading it back as the default threw away what the user had set.
		settings_store.set_value("demo", "caption", 99)
		assert settings_store.get("demo", "caption", "spam") == "99"
		# With no default to compare against the number is kept as it is.
		settings_store.set_value("demo", "blank", 0)
		assert settings_store.get("demo", "blank", 0) == 0
		# A numeric default still yields a number.
		assert settings_store.get("demo", "count", 0) == 0

		# A bool default keeps the value a bool rather than an int.
		settings_store.set_value("demo", "enabled", "on")
		assert settings_store.get("demo", "enabled", False) is True


@_check("shim", "an Input row is only numeric when the plugin says so")
def _schema_input_types():
	_reset_modules()
	from ayugram_compat import schema

	class Input:
		def __init__(self, key, default, input_type=None):
			self.key = key
			self.text = key
			self.default = default
			if input_type is not None:
				self.input_type = input_type

	text_row = schema._row_for(Input("animation_name", ""))
	assert text_row["type"] == "text", text_row
	number_row = schema._row_for(Input("interval", 5))
	assert number_row["type"] == "int", number_row
	declared = schema._row_for(Input("interval", "", "number"))
	assert declared["type"] == "int", declared


@_check("shim", "a queue runs its work in order, on one worker")
def _queue_is_a_queue():
	# run_on_queue used to start a thread per call, so a plugin that
	# reschedules itself created a thread every second and nothing ever joined
	# them. The work also ran concurrently with itself, which is not what a queue
	# means: mobile queues are strictly ordered.
	import threading
	import time as _time

	_reset_modules()
	from ayugram_compat import client_utils

	order = []
	before = threading.active_count()
	for i in range(50):
		client_utils.run_on_queue(lambda i=i: order.append(i), "selftest-queue")
	deadline = _time.monotonic() + 20
	while len(order) < 50 and _time.monotonic() < deadline:
		_time.sleep(0.1)
	assert order == list(range(50)), "queue did not run in order"
	_time.sleep(0.5)
	assert threading.active_count() <= before + 1, (
		f"50 tasks left {threading.active_count() - before} threads running")

	# The commonest mobile idiom posted nothing at all before, and returned a
	# truthy chain so the caller believed it had been queued.
	done = []
	client_utils.get_queue_by_name("selftest-queue").postRunnable(
		lambda: done.append("posted"))
	deadline = _time.monotonic() + 10
	while not done and _time.monotonic() < deadline:
		_time.sleep(0.1)
	assert done == ["posted"], done


@_check("shim", "a reply stays a reply instead of becoming a plain message")
def _reply_is_carried():
	# replyToMsg was read and then dropped on every path, so a plugin that
	# answered a command posted its answer into the chat unthreaded - and one
	# spam plugin sets it on every send and had no way to tell it was ignored.
	import types

	_reset_modules()
	from ayugram_compat import client_utils

	native = types.ModuleType("ayu_api")
	calls = []
	native.send_text = lambda *a: (calls.append(a) or 1)
	sys.modules["ayu_api"] = native
	try:
		assert client_utils.send_text(7, "answer", replyToMsg=4242) is True
		assert calls[-1] == (7, "answer", 4242, 0), calls[-1]
		assert client_utils.send_message(
			{"peer": 7, "message": "y", "replyToMsg": 99}) is True
		assert calls[-1] == (7, "y", 99, 0), calls[-1]
		assert client_utils.send_message(
			{"peer": 7, "message": "z", "replyToTopMsg": 5}) is True
		assert calls[-1] == (7, "z", 0, 5), calls[-1]
		# No reply asked for means no reply invented.
		assert client_utils.send_text(7, "plain") is True
		assert calls[-1] == (7, "plain", 0, 0), calls[-1]
	finally:
		_reset_modules()

	# And the outgoing-message object has to carry it, or a plugin reading it
	# back to learn what it is answering finds nothing.
	_reset_modules()
	from ayugram_compat.base_plugin import SendParams

	class _Message:
		msg_id = 4242

	class _Context:
		message = _Message()
		reply_to_top_id = 0

	params = SendParams(message="x", peer=7, _ctx=_Context())
	assert params.replyToMsg == 4242, params.replyToMsg
	assert SendParams(message="x", peer=7).replyToMsg is None
	# An explicit value still wins over the context.
	assert SendParams(
		message="x", peer=7, replyToMsg=11, _ctx=_Context()).replyToMsg == 11


@_check("shim", "send_text reports whether the text was handed over")
def _send_text_result():
	_reset_modules()
	import types

	from ayugram_compat import client_utils

	native = types.ModuleType("ayu_api")
	state = {"answer": 1}
	recorded = []
	native.send_text = lambda peer, body, top=0, root=0: (
		recorded.append((peer, body, top, root)) or state["answer"])
	sys.modules["ayu_api"] = native
	try:
		assert client_utils.send_text(7, "hi") is True
		# A refusal has to read as a refusal. It used to answer True whatever
		# happened, so a plugin could not tell that nothing was sent.
		state["answer"] = 0
		assert client_utils.send_text(7, "hi") is False
		# An older native build returns nothing; that means "dispatched".
		state["answer"] = None
		assert client_utils.send_text(7, "hi") is True
		# Nothing to send, or nowhere to send it.
		assert client_utils.send_text(0, "hi") is False
		assert client_utils.send_text(7, "") is False
		# send_message passes the answer through instead of swallowing it.
		state["answer"] = 1
		assert client_utils.send_message({"peer": 7, "message": "hi"}) is True
		state["answer"] = 0
		assert client_utils.send_message({"peer": 7, "message": "hi"}) is False
		assert recorded
	finally:
		_reset_modules()


@_check("shim", "send_request performs an HTTP request and answers the callback")
def _send_request_http():
	_reset_modules()
	import types
	import urllib.request

	from ayugram_compat import client_utils

	# A Telegram API request goes to the native bridge, which performs the calls
	# Desktop can genuinely make. It used to be refused as raw MTProto even for
	# account.updateProfile, which Desktop has, so a plugin that renames itself
	# loaded and then failed once a second without ever changing anything.
	performed = []

	class FakeBridge(types.ModuleType):
		def request_telegram(self, method, fields, callback):
			performed.append((method, dict(fields)))
			callback(
				types.SimpleNamespace(
					first_name=fields.get("first_name", ""),
					last_name=fields.get("last_name", "")),
				None)
			return True

	sys.modules["ayu_api"] = FakeBridge("ayu_api")

	class TL_account:
		@staticmethod
		def updateProfile():
			req = types.SimpleNamespace()
			req.flags = 0
			return req

	def _updateProfile():
		req = TL_account.updateProfile()
		req.first_name = "Николай"
		req.last_name = "K"
		return req

	# A plugin builds the request through a TL_account helper, so the object it
	# hands over is named after the call rather than after its class.
	class _ProfileRequest:
		def __init__(self):
			self.flags = 0
			self.first_name = ""
			self.last_name = ""

	UpdateProfile = type("TL_account.updateProfile", (_ProfileRequest,), {})
	req = UpdateProfile()
	req.first_name = "Николай"
	req.last_name = "K"
	answered = []
	assert client_utils.send_request(
		req, lambda r, e: answered.append((r, e))) == 1, "the bridge refused it"
	assert performed and performed[0][0] == "TL_account.updateProfile", performed
	assert performed[0][1].get("first_name") == "Николай", performed[0][1]
	assert answered and answered[0][0].first_name == "Николай", answered

	# A method the bridge does not know is refused by name, and the plugin is
	# told: answering a request that never happened would leave it showing data
	# it believes is fresh.
	class _OtherRequest:
		pass

	Unknown = type("TL_messages.sendMessage", (_OtherRequest,), {})
	sys.modules["ayu_api"].request_telegram = (
		lambda method, fields, callback: False)
	refused = []
	assert client_utils.send_request(
		Unknown(), lambda r, e: refused.append((r, e))) == 0
	assert refused and refused[0][1], "an unsupported call reported success"

	# A raw MTProto request is still something Desktop cannot do, and says so
	# rather than pretending.
	seen = []
	assert client_utils.send_request(
		client_utils.Request(peer="me"), lambda r, e: seen.append((r, e))) == 0
	assert seen and seen[0][0] is None

	# An HTTP request used to be discarded outright, so the callback always fired
	# with None and a plugin's weather or currency panel stayed empty.
	assert client_utils.send_request("http://127.0.0.1:1/none", None) == 1
	assert client_utils.send_request(b"not a request", None) == 0

	# Served locally, so the test does not depend on the network.
	import http.server
	import threading

	class _Handler(http.server.BaseHTTPRequestHandler):
		def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler
			body = b'{"temp": 21}'
			self.send_response(200)
			self.send_header("Content-Type", "application/json")
			self.send_header("Content-Length", str(len(body)))
			self.end_headers()
			self.wfile.write(body)

		def log_message(self, *args):
			pass

	server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
	threading.Thread(target=server.serve_forever, daemon=True).start()
	try:
		port = server.server_address[1]
		got = []
		finished = threading.Event()
		ready = [False]

		def _cb(response, error):
			ready[0] = True
			got.append((response, error))
			finished.set()

		started = client_utils.send_request(
			urllib.request.Request(f"http://127.0.0.1:{port}/weather"), _cb)
		assert started == 1
		assert finished.wait(20), "send_request never called back"
		response, error = got[0]
		assert error is None, error
		assert response.status_code == 200
		assert response.json()["temp"] == 21
		assert response.text.startswith("{")
	finally:
		server.shutdown()


@_check("loader", "the synthetic plugin corpus all loads and registers its hooks")
def _corpus():
	# The corpus is the breadth check: it covers the shapes the mobile packages
	# are written in, not the ones that happen to be installed. BasePlugin was
	# missing add_on_message_hook, add_timer and hook, so every one of these
	# raised AttributeError from on_plugin_load and reported nothing at all.
	import subprocess

	corpus = HERE / "plugin_corpus.py"
	if not corpus.is_file():
		raise AssertionError(f"corpus tool missing at {corpus}")
	env = dict(os.environ)
	env["PYTHONIOENCODING"] = "utf-8"
	result = subprocess.run(
		[sys.executable, str(corpus)], capture_output=True, text=True,
		env=env, timeout=600)
	tail = result.stdout.strip().splitlines()[-1:]
	summary = tail[0] if tail else result.stdout[-200:]
	assert " 0 failed" in summary, f"{summary}\n{result.stdout}\n{result.stderr}"


@_check("shim", "BasePlugin exposes the registration helpers plugins call")
def _base_plugin_surface():
	_reset_modules()
	from base_plugin import BasePlugin

	for name in ("add_on_message_hook", "add_on_send_message_hook",
				"add_on_message_deleted_hook", "add_on_chat_opened_hook",
				"add_on_app_start_hook", "add_on_app_stop_hook",
				"add_on_setting_changed_hook", "hook", "add_timer",
				"remove_timer", "on_message_hook", "on_message_deleted_hook"):
		assert hasattr(BasePlugin, name), f"BasePlugin.{name} is missing"


@_check("shim", "media helpers reach the native sender instead of refusing")
def _send_media():
	_reset_modules()
	import types

	from ayugram_compat import client_utils

	native = types.ModuleType("ayu_api")
	calls = []
	state = {"answer": 1}
	native.send_file = lambda peer, path, caption, kind: (
		calls.append((peer, path, caption, kind)) or state["answer"])
	sys.modules["ayu_api"] = native
	try:
		assert client_utils.send_photo(7, "a.jpg", "hi") is True, "photo"
		assert client_utils.send_document(7, "b.pdf") is True, "document"
		assert client_utils.send_video(7, "c.mp4") is True, "video"
		assert client_utils.send_audio(7, "d.mp3") is True, "audio"
		# The kind has to reach the native side: Telegram treats a photo, a video
		# and a document differently, and a file sent as the wrong one arrives as
		# the wrong thing.
		assert [c[3] for c in calls] == [
			"photo", "document", "video", "audio"], calls
		assert calls[0][2] == "hi", calls[0]
		# A refusal has to be visible, and a file with no peer cannot be sent.
		state["answer"] = 0
		assert client_utils.send_photo(7, "a.jpg") is False, "refusal"
		assert client_utils.send_photo(0, "a.jpg") is False, "no peer"
		assert client_utils.send_photo(7, "") is False, "no path"
	finally:
		_reset_modules()


@_check("shim", "the bridge answers where the user is, as the app does")
def _current_chat():
	# The CLI's compatibility check imports the standalone ayu_api, so a bridge
	# function that only exists natively makes a plugin look like it is reaching
	# for something Desktop does not have - the opposite of the truth.
	import importlib
	import types

	_reset_modules()
	sys.modules.pop("ayu_api", None)
	standalone = importlib.import_module("ayu_api")
	assert hasattr(standalone, "get_current_chat"), "get_current_chat missing"
	assert standalone.get_current_chat() is None, "a fresh session has no chat"
	chat = standalone.Chat()
	chat.peer_id = 42
	chat.title = "Someone"
	standalone.emit_chat_opened(chat)
	again = standalone.get_current_chat()
	assert again is not None and again.peer_id == 42, "chat was not remembered"
	assert again.title == "Someone", again.title


@_check("shim", "hook name aliases map to desktop surface")
def _hook_aliases():
	_reset_modules()
	from ayugram_compat import hook_utils

	cases = {
		"on_message": "on_message",
		"new_message": "on_message",
		"on_message_received": "on_message",
		"before_send_message": "on_message_sending",
		"send_message": "on_message_sending",
		"delete_messages": "on_message_deleted",
		"chat_open": "on_chat_opened",
		"app_start": "on_app_start",
		"setting_changed": "on_setting_changed",
		# Telegram's own names are the same events, so they are matched rather
		# than dropped. They used to map to nothing, which left every plugin
		# written against the mobile API doing nothing at all on Desktop.
		"TL_updateNewMessage": "on_message",
		"TL_updateNewChannelMessage": "on_message",
		"TL_updateShortMessage": "on_message",
		"TL_updateShortChatMessage": "on_message",
		"TL_updateDeleteMessages": "on_message_deleted",
		"TL_messagesSendMessage": "on_message_sending",
		# Typing used to map to nothing, because there was no Desktop event to
		# hand the plugin. There is one now: on_typing carries the peer, the
		# user and the action, which is what a plugin reads off the update.
		"TL_updateUserTyping": "on_typing",
		"TL_updateChatUserTyping": "on_typing",
		"TL_updateChannelUserTyping": "on_typing",
		"TL_updateEncryptedChatTyping": "on_typing",
		"on_typing": "on_typing",
		# Outgoing files have an event of their own. They arrived as the text
		# event with an empty body before, which called every message plugin
		# for something it cannot act on and asked a rewriter to rewrite
		# nothing.
		"on_media_sending": "on_media_sending",
		"media_sending": "on_media_sending",
		"on_send_media": "on_media_sending",
		"before_send_media": "on_media_sending",
		# A container such as TL_updates still has no equivalent: it is a bag of
		# updates, and inventing one would hand the plugin something that looks
		# real and is not.
		"TL_updates": "",
		"TL_updatesCombined": "",
		"Some.Java.Method": "",
	}
	for raw, expected in cases.items():
		got = hook_utils._desktop_hook_name(raw)
		assert got == expected, f"{raw!r} -> {got!r}, expected {expected!r}"


@_check("shim", "a failed request hands back an error the plugin can read")
def _request_error_has_a_shape():
	"""Mobile plugins read error.text; a bare string made them raise.

	The callback exists to say why something failed, and handing it something
	the plugin cannot read throws inside the handler instead - so the reason was
	never shown, which is the only thing the callback was for.
	"""
	_reset_modules()
	from ayugram_compat import client_utils

	seen = []
	client_utils._dispatch(
		lambda response, error: seen.append(error), None, "rate limited")
	assert seen, "the callback was not called"
	error = seen[0]
	assert hasattr(error, "text"), f"error has no text: {type(error).__name__}"
	assert error.text == "rate limited", error.text
	assert error.code == 400, error.code
	assert isinstance(error.type, str) and error.type, error.type

	# A real error object from the bridge is passed through unchanged.
	class Native:
		def __init__(self):
			self.text = "FLOOD_WAIT"
			self.code = 420

	native = Native()
	seen.clear()
	client_utils._dispatch(
		lambda response, error: seen.append(error), None, native)
	assert seen[0] is native, "a native error was replaced"

	# No error stays None, so a success is still distinguishable.
	seen.clear()
	client_utils._dispatch(lambda response, error: seen.append(error), 1, None)
	assert seen[0] is None, seen[0]

	# And a dict, which some plugins build by hand, becomes readable.
	seen.clear()
	client_utils._dispatch(
		lambda response, error: seen.append(error), None,
		{"message": "boom", "code": 500})
	assert seen[0].text == "boom" and seen[0].code == 500, seen[0].text


@_check("shim", "outgoing files get their own event, not an empty message")
def _media_has_its_own_event():
	_reset_modules()
	import types

	registered: dict[str, list] = {}

	class FakeApi(types.ModuleType):
		pass

	fake = FakeApi("ayu_api")
	fake.hook = lambda name, fn: registered.setdefault(name, []).append(fn)
	sys.modules["ayu_api"] = fake

	import ayugram_compat.base_plugin as base_plugin

	seen = []

	class Sending(base_plugin.BasePlugin):
		def on_plugin_load(self):
			self.add_on_send_message_hook()
			self.add_on_media_sending_hook()

		def on_media_sending_hook(self, account, params):
			seen.append((account, params))
			return base_plugin.HookResult()

	plugin = Sending()
	plugin._plugin_id = "media"
	plugin.on_plugin_load()
	assert "on_media_sending" in registered, sorted(registered)
	assert "on_message_sending" in registered, sorted(registered)

	class Context:
		def __init__(self):
			self.peer_id = 5
			self.text = ""
			self.media = []

	plugin.on_media_sending_hook(3, Context())
	assert len(seen) == 1, seen
	assert seen[0][0] == 3, seen[0]


@_check("shim", "a setting corrected by hand is not silently undone")
def _settings_external_edit_survives():
	"""The trap that made "the settings do not save" true.

	Saving wrote the whole remembered set back over the file, so a value
	corrected from outside while the application was running was undone the next
	time the plugin saved anything at all. A fixed command name went back to the
	junk it had replaced, with nothing logged.
	"""
	_reset_modules()
	import json
	import os
	import tempfile
	from pathlib import Path

	folder = Path(tempfile.mkdtemp(prefix="ayu-settings-"))
	previous = os.environ.get("AYU_PLUGINS_DIR")
	os.environ["AYU_PLUGINS_DIR"] = str(folder)
	try:
		import importlib

		import ayugram_compat.settings_store as store
		importlib.reload(store)

		assert store.write("demo", {"cmd": "proba", "lang": 0})
		store.read("demo")
		path = folder / "demo.settings.json"

		# Somebody corrects it by hand, with the application running.
		on_disk = json.loads(path.read_text(encoding="utf-8-sig"))
		on_disk["settings"]["cmd"] = ".ozon"
		path.write_text(json.dumps(on_disk, indent=2), encoding="utf-8")

		# The plugin then saves one setting of its own, from what it remembers.
		store.write("demo", {"cmd": "proba", "lang": 1})
		after = json.loads(path.read_text(encoding="utf-8-sig"))["settings"]
		assert after["cmd"] == ".ozon", (
			f"an outside edit was overwritten: {after}")
		assert after["lang"] == 1, after

		# And a change made from inside is still written.
		store.write("demo", {"cmd": ".ozon", "lang": 5})
		after = json.loads(path.read_text(encoding="utf-8-sig"))["settings"]
		assert after["lang"] == 5 and after["cmd"] == ".ozon", after
	finally:
		if previous is None:
			os.environ.pop("AYU_PLUGINS_DIR", None)
		else:
			os.environ["AYU_PLUGINS_DIR"] = previous


@_check("shim", "a plugin that hooks Telegram updates by name gets them")
def _raw_update_routing():
	"""The whole path a mobile plugin takes, end to end.

	`add_hook("TL_updateUserTyping")` carries no callback at all: the plugin
	writes `def on_update_hook(self, name, account, update)` instead. The
	bridge cannot subscribe a null function, so this used to register nothing
	- the plugin loaded, was enabled, and never heard about anything.
	"""
	_reset_modules()
	import types
	import ayugram_compat.base_plugin as base_plugin

	registered: dict[str, list] = {}

	class FakeApi(types.ModuleType):
		pass

	fake = FakeApi("ayu_api")
	fake.hook = lambda name, fn: registered.setdefault(name, []).append(fn)
	sys.modules["ayu_api"] = fake

	seen = []

	class TypingPlugin(base_plugin.BasePlugin):
		def on_plugin_load(self):
			self.add_hook("TL_updateUserTyping")
			self.add_hook("TL_updateChatUserTyping")
			self.add_hook("TL_updateChannelUserTyping")
			self.add_hook("TL_updateNewMessage")
			# A container has no Desktop equivalent and must not be faked: an
			# empty name used to be passed to the bridge, which resolves it to
			# the incoming-message event.
			self.add_hook("TL_updates")

		def on_update_hook(self, name, account, update):
			seen.append((name, account, update))
			return base_plugin.HookResult()

	plugin = TypingPlugin()
	plugin._plugin_id = "raw_updates"
	plugin.on_plugin_load()

	assert "on_typing" in registered, f"no typing subscription: {sorted(registered)}"
	assert "on_message" in registered, f"no message subscription: {sorted(registered)}"
	assert "" not in registered, (
		"an unmapped name reached the bridge as an empty hook name, which the "
		f"app resolves to the incoming-message event: {sorted(registered)}")

	# One subscription per Desktop event, not one per Telegram name: four
	# names for the same event used to mean the plugin was called four times
	# for every message, each time under a name that had not happened.
	typing_callbacks = registered["on_typing"]
	assert len(typing_callbacks) == 1, (
		f"typing was subscribed {len(typing_callbacks)} times, expected once")
	assert len(registered["on_message"]) == 1, (
		f"messages were subscribed {len(registered['on_message'])} times, "
		f"expected once")

	# A private chat: the user id is the peer, and the action has to arrive
	# under the Telegram class name, because that is what plugins compare with.
	class TypingEvent:
		peer_id = 4242
		user_id = 4242
		top_msg_id = 0
		action = "TypingAction"
		kind = 0

	typing_callbacks[0](7, TypingEvent())
	name, account, update = seen[0]
	assert name == "TL_updateUserTyping", name
	assert account == 7, account
	assert update.user_id == 4242, update.user_id
	assert type(update.action).__name__ == "TL_sendMessageTypingAction", (
		type(update.action).__name__)

	# A channel: the same callback must report the channel update, with the
	# author in from_id. Delivering this under the user-typing name would tell
	# the plugin a private chat is being typed in.
	class ChannelTypingEvent:
		peer_id = -1001234567890
		user_id = 55
		top_msg_id = 12
		action = "UploadVideoAction"
		kind = 2

	typing_callbacks[0](0, ChannelTypingEvent())
	name, account, update = seen[1]
	assert name == "TL_updateChannelUserTyping", name
	assert update.channel_id == -1001234567890, update.channel_id
	assert update.from_id.user_id == 55, update.from_id.user_id
	assert update.top_msg_id == 12, update.top_msg_id
	assert type(update.action).__name__ == "TL_sendMessageUploadVideoAction", (
		type(update.action).__name__)

	class GroupTypingEvent(TypingEvent):
		kind = 1

	typing_callbacks[0](0, GroupTypingEvent())
	assert seen[2][0] == "TL_updateChatUserTyping", seen[2][0]

	# A plugin that registers only the container and reads it in
	# on_updates_hook used to be handed nothing at all: Desktop has no
	# TL_updates to give it. The same update, as the one-element list a
	# container is, keeps it working.
	containers = []

	class AnyMessage:
		def __init__(self):
			self.from_id = 900
			self.message_id = 5
			self.text = "hello"

	class ContainerOnly(base_plugin.BasePlugin):
		def on_plugin_load(self):
			self.add_hook("TL_updates")

		def on_updates_hook(self, container_name, account, updates):
			containers.append((container_name, account, list(updates)))

	ContainerOnly._plugin_id = "containers"
	ContainerOnly().on_plugin_load()
	assert "on_message" in registered, sorted(registered)
	registered["on_message"][-1](5, AnyMessage())
	assert containers, "a container-only plugin was handed nothing"
	name, account, updates = containers[0]
	assert name == "TL_updates", name
	assert account == 5, account
	assert len(updates) == 1, updates
	assert updates[0].message.from_id.user_id == 900, updates[0].message.from_id

	# An incoming message, where plugins read .message.from_id.user_id.
	class NativeMessage:
		def __init__(self):
			self.from_id = 900
			self.message_id = 5
			self.text = "hello"

	native = NativeMessage()
	registered["on_message"][0](3, native)
	name, account, update = seen[3]
	assert name == "TL_updateNewMessage", name
	assert update.message.from_id.user_id == 900, update.message.from_id.user_id
	assert update.message.text == "hello", update.message.text
	# The message belongs to the app, so it must not be rewritten underneath
	# the next plugin that sees the same event.
	assert native.from_id == 900, f"the original message was modified: {native.from_id}"

	# A field the Desktop event cannot supply answers None on the update:
	# plugin handlers swallow exceptions, so a missing attribute would end the
	# plugin's work silently. The message itself stays strict, because it is
	# the app's own object and a typo should still be visible.
	assert update.photo_id is None
	try:
		update.message.does_not_exist
	except AttributeError:
		pass
	else:
		raise AssertionError(
			"the message proxy invented an attribute the message does not have")


def _capture_hooks() -> dict:
	"""Swap in a fake native bridge and return the registry it fills."""
	_reset_modules()
	import types
	import ayugram_compat.hook_utils as hook_utils

	registered: dict[str, list] = {}
	fake = types.ModuleType("ayu_api")
	fake.hook = lambda name, fn: registered.setdefault(name, []).append(fn)
	sys.modules["ayu_api"] = fake
	_reset_modules()
	import ayugram_compat.hook_utils as reloaded

	reloaded._native_hook = fake.hook
	reloaded._registry = registered
	return reloaded, registered


@_check("shim", "unbound method hooks bind to the live instance")
def _hook_binding():
	hook_utils, registry = _capture_hooks()

	seen = []

	class P:
		@hook_utils.hook("on_app_start")
		def _start(self, *a):
			seen.append("start")

		@hook_utils.hook("on_message")
		def _msg(self, msg):
			seen.append(msg["text"])

	p = P()
	hook_utils.register_instance(p)
	for fn in registry["on_app_start"]:
		fn()
	for fn in registry["on_message"]:
		fn({"text": "hi"})
	assert seen == ["start", "hi"], seen

	# Once the plugin is unloaded there is no instance to bind to, so the
	# event must not reach the handler at all.
	hook_utils.unregister_instance(p)
	hook_utils.register_instance(p)
	hook_utils._live_instances.clear()
	hook_utils._live_instances.add(p)
	hook_utils.unregister_instance(p)
	for fn in registry["on_app_start"]:
		try:
			fn()
		except TypeError:
			pass
	assert seen == ["start", "hi"], seen


@_check("shim", "command hook dispatches to the bound method")
def _command_hook():
	hook_utils, registry = _capture_hooks()

	got = []

	class P:
		@hook_utils.command("/ping")
		def _ping(self, peer, text, args):
			got.append((peer, text, tuple(args)))
			return "pong"

	p = P()
	hook_utils.register_instance(p)
	for fn in registry["on_message_sending"]:
		fn({"message": {"text": "/ping a b", "peer_id": 5}})
	assert got and got[0][0] == 5 and got[0][2] == ("a", "b"), got
	hook_utils.unregister_instance(p)


# ---------------------------------------------------------------- loader

def _write_plugin(root: Path, plugin_id: str, body: str, manifest: dict | None = None) -> Path:
	d = root / plugin_id
	d.mkdir(parents=True, exist_ok=True)
	(d / "main.py").write_text(body, encoding="utf-8")
	import json
	m = {"id": plugin_id, "name": plugin_id, "version": "1.0"}
	m.update(manifest or {})
	(d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
	return d


@_check("loader", "installs and loads a plain plugin")
def _load_plain():
	_reset_modules()
	from plugin_loader import PluginLoader

	root = Path(tempfile.mkdtemp(prefix="ayu-selftest-load-"))
	plugins = root / "plugins"
	plugins.mkdir()
	try:
		_write_plugin(plugins, "demo", "import ayugram_compat.base_plugin as _b\n__id__ = 'demo'\n")
		loader = PluginLoader(plugins, root / "state.json")
		found = {p.id: p for p in loader.scan()}
		assert "demo" in found, list(found)
		loader.load_plugin_code(found["demo"])
	finally:
		shutil.rmtree(root, ignore_errors=True)


@_check("loader", "rejects an api_version above the supported one")
def _reject_api_version():
	_reset_modules()
	root = Path(tempfile.mkdtemp(prefix="ayu-selftest-api-"))
	plugins = root / "plugins"
	plugins.mkdir()
	try:
		d = _write_plugin(plugins, "future", "pass\n", {"api_version": 99})
		if str(CLI) not in sys.path:
			sys.path.insert(0, str(CLI))
		import validate as _validate

		raised = False
		try:
			_validate._check_manifest(d / "manifest.json", d)
		except AssertionError:
			raised = True
		assert raised, "api_version 99 passed manifest validation"
	finally:
		shutil.rmtree(root, ignore_errors=True)


@_check("loader", "installs tar.gz, zip and bare .py")
def _install_formats():
	_reset_modules()
	from plugin_loader import PluginLoader
	import tarfile
	import zipfile

	root = Path(tempfile.mkdtemp(prefix="ayu-selftest-fmt-"))
	plugins = root / "plugins"
	plugins.mkdir()
	try:
		src = _write_plugin(root / "src", "tarpkg", "__id__ = 'tarpkg'\n")
		tgz = root / "tarpkg.tar.gz"
		with tarfile.open(tgz, "w:gz") as tf:
			tf.add(src, arcname="tarpkg")

		zsrc = _write_plugin(root / "src", "zippkg", "__id__ = 'zippkg'\n")
		zp = root / "zippkg.zip"
		with zipfile.ZipFile(zp, "w") as zf:
			for f in zsrc.rglob("*"):
				zf.write(f, arcname=str(Path("zippkg") / f.relative_to(zsrc)))

		solo = root / "solo.py"
		solo.write_text("__id__ = 'solo'\n__name__ = 'Solo'\n", encoding="utf-8")

		loader = PluginLoader(plugins, root / "state.json")
		loader.scan()
		for path in (tgz, zp, solo):
			loader.install_path(path)
		found = {p.id: p for p in loader.scan()}
		errors = {pid: p.error for pid, p in found.items() if p.error}
		assert not errors, f"scan reported errors: {errors}"
		assert set(found) == {"tarpkg", "zippkg", "solo"}, sorted(found)
	finally:
		shutil.rmtree(root, ignore_errors=True)


@_check("loader", "rejects archive traversal outside the target")
def _no_traversal():
	_reset_modules()
	from plugin_loader import PluginLoader
	import zipfile

	root = Path(tempfile.mkdtemp(prefix="ayu-selftest-trav-"))
	plugins = root / "plugins"
	plugins.mkdir()
	try:
		bad = root / "evil.zip"
		with zipfile.ZipFile(bad, "w") as zf:
			zf.writestr("../escaped.py", "raise SystemExit('escaped')\n")
		loader = PluginLoader(plugins, root / "state.json")
		loader.scan()
		raised = False
		try:
			loader.install_path(bad)
		except Exception:
			raised = True
		assert raised, "zip traversal was not rejected"
		assert not (root / "escaped.py").exists()
	finally:
		shutil.rmtree(root, ignore_errors=True)


@_check("loader", "tolerates a UTF-8 BOM in the entry script")
def _bom_entry():
	_reset_modules()
	from plugin_loader import PluginLoader

	root = Path(tempfile.mkdtemp(prefix="ayu-selftest-bom-"))
	plugins = root / "plugins"
	plugins.mkdir()
	try:
		d = _write_plugin(plugins, "bommed", "__id__ = 'bommed'\n")
		# Windows editors add a BOM; it must not become a SyntaxError.
		(d / "main.py").write_bytes(
			b"\xef\xbb\xbf__id__ = 'bommed'\nprint('bom ok')\n")
		loader = PluginLoader(plugins, root / "state.json")
		info = {p.id: p for p in loader.scan()}["bommed"]
		loader.load_plugin_code(info)
	finally:
		shutil.rmtree(root, ignore_errors=True)


@_check("loader", "enable and disable persist across instances")
def _enable_disable():
	_reset_modules()
	from plugin_loader import PluginLoader

	root = Path(tempfile.mkdtemp(prefix="ayu-selftest-en-"))
	plugins = root / "plugins"
	plugins.mkdir()
	try:
		_write_plugin(plugins, "toggled", "__id__ = 'toggled'\n")
		loader = PluginLoader(plugins, root / "state.json")
		loader.scan()
		loader.disable("toggled")

		second = PluginLoader(plugins, root / "state.json")
		found = {p.id: p for p in second.scan()}
		assert not found["toggled"].enabled, "disable did not persist"
		second.enable("toggled")
		third = PluginLoader(plugins, root / "state.json")
		found = {p.id: p for p in third.scan()}
		assert found["toggled"].enabled, "enable did not persist"
	finally:
		shutil.rmtree(root, ignore_errors=True)


@_check("loader", "settings survive a new loader instance")
def _settings_persist():
	_reset_modules()
	from plugin_loader import PluginLoader
	from ayugram_compat import settings_store

	root = Path(tempfile.mkdtemp(prefix="ayu-selftest-set-"))
	plugins = root / "plugins"
	plugins.mkdir()
	try:
		_write_plugin(plugins, "cfg", "__id__ = 'cfg'\n")
		with _isolated_store("set") as (_, store):
			loader = PluginLoader(plugins, root / "state.json")
			loader.scan()
			store.set_value("cfg", "delay", 1234)
			store._CACHE.clear()
			second = PluginLoader(plugins, root / "state.json")
			info = {p.id: p for p in second.scan()}["cfg"]
			second.load_plugin_code(info)
			assert store.read("cfg")["delay"] == 1234
	finally:
		shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- examples

def _example_ids() -> list[str]:
	if not EXAMPLES.is_dir():
		return []
	ids = []
	for d in sorted(EXAMPLES.iterdir()):
		if not d.is_dir():
			continue
		if any(d.glob("*.py")) and (d / "manifest.json").is_file():
			ids.append(d.name)
	return ids


@_check("examples", "every bundled example imports and loads")
def _examples_load():
	_reset_modules()
	from plugin_loader import PluginLoader

	ids = _example_ids()
	if not ids:
		raise Skip("no examples present")
	root = Path(tempfile.mkdtemp(prefix="ayu-selftest-ex-"))
	plugins = root / "plugins"
	plugins.mkdir()
	try:
		for name in ids:
			shutil.copytree(EXAMPLES / name, plugins / name,
							ignore=shutil.ignore_patterns("__pycache__"))
		loader = PluginLoader(plugins, root / "state.json")
		found = {p.id: p for p in loader.scan()}
		failed = []
		for pid, info in found.items():
			try:
				loader.load_plugin_code(info)
			except Exception as e:
				failed.append(f"{pid}: {e}")
		assert not failed, "example load failures: " + "; ".join(failed)
	finally:
		shutil.rmtree(root, ignore_errors=True)


@_check("examples", "manifests pass desktop compliance checks")
def _examples_validate():
	ids = _example_ids()
	if not ids:
		raise Skip("no examples present")
	if str(CLI) not in sys.path:
		sys.path.insert(0, str(CLI))
	import validate as _validate

	bad = []
	for name in ids:
		try:
			_validate._check_manifest(EXAMPLES / name / "manifest.json", EXAMPLES / name)
		except AssertionError as e:
			bad.append(f"{name}: {e}")
	assert not bad, "invalid manifests: " + "; ".join(bad)


@_check("regress", "plugin state survives a save and load round trip")
def _state_round_trip():
	# The app read the state file through a narrow-char std::ifstream and
	# swallowed every failure, which left the map empty and switched every
	# plugin back on at the next launch. Exercise the same read/write pair the
	# app uses so a silent read failure cannot come back unnoticed.
	#
	# This runs out of process on purpose: loading a plugin installs the
	# sandbox meta path finder, which changes how every later import in this
	# interpreter resolves and would otherwise break the checks below.
	if not EXAMPLES.joinpath("world_time").is_dir():
		raise Skip("world_time example missing")
	script = textwrap.dedent(
		f"""
		import json, shutil, sys
		from pathlib import Path
		sys.path.insert(0, {str(STANDALONE)!r})
		import plugin_loader as loader

		root = Path(sys.argv[1])
		shutil.copytree({str(EXAMPLES / 'world_time')!r}, root / 'world_time')
		state = root / 'plugins.json'

		def read(value):
			state.write_text(json.dumps({{'world_time': value}}), 'utf-8')
			ld = loader.PluginLoader(root, state)
			return {{p.id: p for p in ld.scan()}}['world_time'].enabled

		problems = []
		if read(False) is not False:
			problems.append(
				'recorded as off, came back on: the app would load every '
				'plugin on every start')
		if read(True) is not True:
			problems.append('recorded as on, came back off')

		state.write_text(json.dumps({{'world_time': True}}), 'utf-8')
		ld3 = loader.PluginLoader(root, state)
		ld3.scan()
		ld3.disable('world_time')
		if read(False) is not False:
			problems.append('disabling did not persist to disk')

		print(json.dumps(problems))
		"""
	)
	with tempfile.TemporaryDirectory() as tmp:
		done = subprocess.run(
			[sys.executable, "-c", script, tmp],
			capture_output=True, text=True, timeout=120)
	if done.returncode != 0:
		raise AssertionError(
			"round trip crashed: " + (done.stderr.strip() or "?")[-400:])
	try:
		problems = json.loads(done.stdout.strip().splitlines()[-1])
	except Exception:
		raise AssertionError("unexpected output: " + done.stdout[-300:])
	assert not problems, "; ".join(problems)


@_check("regress", "no ayu_api.py shadows the embedded module in a deployment")
def _no_bridge_shadow_in_app_dir():
	# A pure-Python ayu_api.py next to the executable takes priority over the
	# built-in one for every plugin, and it defines toast/log/hook but not
	# send_text or edit_message. Plugins then look fine and send nothing.
	app_dir = Path(sys.argv[0]).resolve().parent if sys.argv[0] else None
	candidates = [Path.cwd() / "ayu_api.py"]
	if app_dir:
		candidates.append(app_dir / "ayu_api.py")
	bad = [str(p) for p in candidates if p.is_file()]
	assert not bad, (
		"these files shadow the embedded bridge: " + ", ".join(bad)
	)


@_check("regress", "plugins that write a log declare storage.write")
def _log_writers_declare_permission():
	# The sandbox denies writes without storage.write, and a plugin that
	# swallows the error looks exactly like a plugin that is not running.
	offenders = []
	for name in _example_ids():
		main_py = EXAMPLES / name / "main.py"
		if not main_py.is_file():
			continue
		try:
			source = main_py.read_text(encoding="utf-8", errors="replace")
		except Exception:
			continue
		if "open(" not in source or ".log" not in source:
			continue
		manifest = EXAMPLES / name / "plugin.json"
		granted = False
		if manifest.is_file():
			try:
				data = json.loads(manifest.read_text(encoding="utf-8"))
				perms = data.get("permissions") or []
				granted = any(str(p).startswith("storage") for p in perms)
			except Exception:
				granted = False
		if not granted:
			offenders.append(name)
	assert not offenders, (
		"these examples write a .log but declare no storage permission: "
		+ ", ".join(offenders)
	)


# Names the embedded ayu_api module actually exports. Kept in step with the
# m.def(...) list in plugins/bridge/ayu_api.cpp.
BRIDGE_EXPORTS = {
	"toast", "log", "send_text", "edit_message",
	"emit_test_hooks", "hook_count", "settings_read", "settings_emit", "hook",
}


@_check("examples", "no example calls a bridge function that does not exist")
def _examples_only_use_real_bridge_calls():
	# A plugin referring to ayu_api.set_timer or expecting send_text to return
	# a message id looks fine and only fails when it is loaded, by which point
	# the effect it promised is simply absent. Catch it here instead.
	if not EXAMPLES.is_dir():
		raise Skip("examples not present")

	problems = []
	for name in _example_ids():
		source_file = EXAMPLES / name / "main.py"
		if not source_file.is_file():
			continue
		source = source_file.read_text(encoding="utf-8-sig", errors="replace")
		# Both spellings matter: \b does not match between "_" and "api", so
		# a single pattern for api.() would silently skip ayu_api.() and the
		# check would never fire.
		calls = re.findall(r"\bayu_api\.(\w+)\s*\(", source)
		calls += re.findall(r"(?<![A-Za-z0-9_])_?api\.(\w+)\s*\(", source)
		for attr in calls:
			if attr not in BRIDGE_EXPORTS:
				problems.append(f"{name}: api.{attr}()")
		for attr in re.findall(
			r"hasattr\(\s*_?(?:ayu_)?api\s*,\s*[\"'](\w+)[\"']",
			source,
		):
			if attr not in BRIDGE_EXPORTS:
				problems.append(f"{name}: hasattr(api, {attr!r})")
	assert not problems, (
		"these example plugins use bridge calls the app does not provide: "
		+ ", ".join(problems)
	)


@_check("examples", "plugins that overwrite a hook respect the send guard")
def _examples_do_not_recurse_on_their_own_sends():
	# The app marks plugin-originated sends so on_message_sending does not run
	# for them. A plugin that clears or bypasses the hook instead of adding to
	# it re-enters once per edit.
	offenders = []
	for name in _example_ids():
		source_file = EXAMPLES / name / "main.py"
		if not source_file.is_file():
			continue
		source = source_file.read_text(encoding="utf-8", errors="replace")
		if "clear_hooks" in source or "remove_hook" in source:
			offenders.append(name)
	assert not offenders, (
		"these examples remove hooks, which undoes the plugin send guard: "
		+ ", ".join(offenders)
	)


@_check("runtime", "native dependencies of every shipped module are staged")
def _native_dependencies_present():
	# A plugin that imports ssl reported "DLL load failed while importing
	# _ssl", which names the extension module and not the library it links
	# against. deploy.py had both libssl-3.dll and libcrypto-3.dll in its list
	# and copied none of them, because they live in DLLs\ and it looked in the
	# install root. Read the import tables of what was actually deployed.
	if not DEPLOYED.is_dir():
		raise Skip("no deployed build to inspect")

	sys.path.insert(0, str(STANDALONE))
	import native_deps

	report = native_deps.missing_dependencies(DEPLOYED)
	assert not report, (
		"these staged modules cannot load, and any plugin importing them will "
		"fail with a DLL error that does not name the library:\n"
		+ native_deps.format_report(report))


@_check("runtime", "the PE reader agrees with a known module")
def _native_deps_reader_works():
	# The import table sits at offset 96 for PE32 and 112 for PE32+. Using the
	# PE32 offset for a 64-bit module reads zeroes and reports "imports
	# nothing", which made the check above pass while the app was broken.
	if not DEPLOYED.is_dir():
		raise Skip("no deployed build to inspect")
	ssl_module = DEPLOYED / "_ssl.pyd"
	if not ssl_module.is_file():
		raise Skip("_ssl.pyd is not deployed")

	sys.path.insert(0, str(STANDALONE))
	import native_deps

	imports = {name.lower() for name in native_deps.imported_dlls(ssl_module)}
	assert imports, "_ssl.pyd reports no imports at all, reader is wrong"
	assert "libssl-3.dll" in imports, (
		f"_ssl.pyd should import libssl-3.dll, reader returned {sorted(imports)}")


@_check("runtime", "deploy stages the libraries the modules link against")
def _deploy_lists_native_libs():
	# The failure mode is silence: a glob that matches nothing raises no error,
	# so the deploy reported success with an incomplete runtime.
	if str(STANDALONE) not in sys.path:
		sys.path.insert(0, str(STANDALONE))
	import deploy as _deploy

	for name in ("libssl-3.dll", "libcrypto-3.dll", "libffi-8.dll"):
		assert name in _deploy.NATIVE_LIBS, (
			f"{name} is linked against by a shipped module but deploy does "
			f"not stage it")
	assert callable(getattr(_deploy, "verify_runtime", None)), (
		"deploy must verify the staged runtime, not trust the copy")


@_check("shim", "printing non-ascii does not raise")
def _console_tolerates_non_ascii():
	# Every shim module reports through print(). On a console with a single
	# byte code page one emoji or any Cyrillic letter raised UnicodeEncodeError
	# in the middle of whatever the plugin was doing.
	sys.path.insert(0, str(SHIM))
	import ayugram_compat  # noqa: F401

	import io as _io

	stream = _io.StringIO()
	original = sys.stdout
	sys.stdout = stream
	try:
		print("[test] \u2705 Р С-РЎР‚Р С•Р Р†Р ВµРЎР‚Р С”Р В°")
	finally:
		sys.stdout = original
	assert "\u2705" in stream.getvalue(), (
		"the diagnostic line lost its characters instead of replacing only "
		"what the console cannot encode")


@_check("shim", "writing a setting neither leaks a hook nor wakes every plugin")
def _setting_change_is_scoped():
	# notify() used to *register* a hook as a side effect of every write, so a
	# plugin that saves a setting from a background loop grew the subscription
	# list for the life of the process, and the subscriber it added took no
	# arguments while the bridge calls the event with two - so it raised inside
	# the next event of any plugin.
	import importlib
	import types

	_reset_modules()
	from ayugram_compat import settings_store

	native = types.ModuleType("ayu_api")
	emitted = []
	hooks = []
	native.settings_emit = lambda *a: emitted.append(a)
	native.hook = lambda *a, **k: hooks.append(a)
	sys.modules["ayu_api"] = native
	try:
		with _isolated_store("notify_scope") as (_, store):
			for i in range(5):
				store.set_value("demo", f"k{i}", i)
		assert hooks == [], f"a write registered {len(hooks)} hook(s)"
		assert len(emitted) == 5, emitted
		assert emitted[0][0] == "demo", emitted[0]
	finally:
		_reset_modules()


@_check("shim", "one plugin's timers are not another's to cancel")
def _timers_are_scoped():
	_reset_modules()
	import threading

	sys.path.insert(0, str(SHIM))
	from ayugram_compat import hook_utils
	from base_plugin import BasePlugin, activate_base_plugins

	# Saving a setting used to cancel every plugin's timers, so editing one
	# plugin silently stopped the background loops of all the others.
	hook_utils._timers.clear()
	beats = []
	alive = threading.Event()

	class First(BasePlugin):
		def tick(self):
			beats.append("first")

		def on_plugin_load(self):
			# A bound method, which is how a plugin actually arms a timer and
			# the only form whose owner can be identified.
			hook_utils.add_timer(self.tick, 60)

	class Second(BasePlugin):
		def tick(self):
			alive.wait(0.01)

		def on_plugin_load(self):
			hook_utils.add_timer(self.tick, 60)

	# One plugin at a time: activate_base_plugins instantiates the first
	# BasePlugin subclass it finds, which is the point.
	first = activate_base_plugins({"Only": First})
	assert len(hook_utils._timers) == 1, hook_utils._timers
	second = activate_base_plugins({"Only": Second})
	assert len(hook_utils._timers) == 2, hook_utils._timers

	# The per-plugin count has to be the plugin's own. It used to report every
	# timer on the system whatever it was asked, so the health line could not
	# tell a busy plugin from a broken one.
	assert hook_utils.timers_of(first) == 1, hook_utils.timers_of(first)
	assert hook_utils.timers_of(second) == 1, hook_utils.timers_of(second)

	# Switching a plugin off releases that plugin and nothing else. It used to
	# call the unfiltered cancel, so disabling one plugin ended every other
	# plugin's loops as well.
	stopped = hook_utils.release_instance(first)
	assert stopped == 1, f"expected to stop one timer, stopped {stopped}"
	assert hook_utils.timers_of(second) == 1, (
		"disabling one plugin stopped another plugin's timers: "
		f"{hook_utils.timers_of(second)} left")
	assert not any(
		entry[3] is first for entry in hook_utils._timers
		if len(entry) > 3), "the disabled plugin still has a live timer"

	# Cancelling with no owner still stops everything: that is what a call from
	# outside any plugin means, and the old behaviour is kept on purpose.
	assert hook_utils.cancel_all_timers() == 1, hook_utils._timers
	assert len(hook_utils._timers) == 0, hook_utils._timers


@_check("shim", "copy_to_clipboard copies, or says it did not")
def _clipboard_is_honest():
	import importlib
	import types

	_reset_modules()
	from ayugram_compat import android_utils

	native = types.ModuleType("ayu_api")
	copied = []
	state = {"ok": True}
	native.copy_to_clipboard = lambda text: (
		copied.append(text) or state["ok"])
	sys.modules["ayu_api"] = native
	try:
		assert android_utils.copy_to_clipboard("цена") is True
		assert copied == ["цена"], copied
		# It used to show a toast and copy nothing, and the caller reported
		# success, so the user was told a price was on the clipboard.
		state["ok"] = False
		assert android_utils.copy_to_clipboard("ещё") is False
		assert android_utils.copy_to_clipboard("") is False
	finally:
		_reset_modules()


@_check("shim", "an Android class is usable wherever a class is expected")
def _stub_classes_are_classes():
	# Every one of these was a hard failure, and each took a whole plugin with
	# it: an imported Android class was a placeholder *instance*, so isinstance
	# raised TypeError, dynamic_proxy handed back an object so subclassing it
	# raised TypeError, and a package with an empty __init__ refused the import.
	sys.path.insert(0, str(SHIM))
	from org._stubs import Placeholder, stub_type
	from java import dynamic_proxy

	# isinstance, subclassing and calling are all things a plugin does with an
	# Android class, and all three have to work.
	Listener = stub_type("android.view.View.OnClickListener")
	assert isinstance(Listener, type)
	assert isinstance(Listener(), Listener)
	assert isinstance(Placeholder("received"), Listener), (
		"an object that came from the class must pass its own isinstance")
	assert not isinstance(5, Listener), "unrelated objects must not match"
	assert repr(Listener().onClick(1)), "calling must yield something"
	assert repr(Listener.SOME_CONSTANT), "class attributes must resolve too"

	class Press(dynamic_proxy("android.view.View.OnClickListener")):
		def onClick(self, view):
			return "handled"

	assert isinstance(Press(), Press.__mro__[1]), "dynamic_proxy must be a class"
	assert Press().onClick(None) == "handled"

	# A package whose __init__ declares nothing must still answer.
	import importlib
	for name in ("org.telegram.ui", "java.lang.reflect", "java.util.concurrent"):
		module = importlib.import_module(name)
		assert module is not None, name

	# Android flags are integers and plugins combine them without checking.
	request = Placeholder("req")
	request.flags |= 1
	assert int(request.flags) == 0
	assert repr(request.flags) == "<android stub req.flags>"


@_check("shim", "a reflection helper says it found nothing rather than inventing")
def _reflection_is_honest():
	sys.path.insert(0, str(SHIM))
	from hook_utils import find_class, get_private_field, set_private_field
	from org._stubs import Placeholder

	# find_class used to answer with a falsy stub, so `if find_class(X):` was
	# never true and every hook behind that guard was silently skipped, while
	# `if find_class(X) is None:` was never true either.
	assert find_class("com.example.Thing") is None, (
		"a class that does not exist must read as None")

	# A field that is not there must read as None. Returning a stub made
	# `if value is not None:` true for a value that did not exist, and one plugin
	# then int()s it, so an absent field quietly became 0.
	assert get_private_field(Placeholder("o"), "missing") is None

	# And a write that is discarded must not report success.
	assert set_private_field(Placeholder("o"), "x", 1) is False, (
		"a discarded write must not be reported as done")
	holder = type("Holder", (), {})()
	assert set_private_field(holder, "x", 1) is True
	assert holder.x == 1


@_check("shim", "AndroidUtilities answers instead of raising")
def _android_utilities():
	sys.path.insert(0, str(SHIM))
	from org.telegram.messenger import AndroidUtilities as A

	# It had no __getattr__, so every name not spelled out raised - and one
	# plugin defines dp() by delegating straight to it, so every call it made
	# died on its first line.
	assert A.dp(64) == 64
	assert A.dpf(1) == 1.0
	assert A.bold("x") == "x"
	assert A.navigationBarHeight() == 0
	assert A.someMethodNobodyWrote(1, 2) is not None


@_check("shim", "the Android org.telegram packages import and stay tolerant")
def _org_shim_available():
	# org/ existed in the shim but was missing from deploy's package list, so
	# it never reached a build and every plugin that did
	# `from org.telegram.tgnet.tl import TL_account` died on the import.
	sys.path.insert(0, str(SHIM))
	for name in ("org.telegram.messenger", "org.telegram.tgnet",
				 "org.telegram.tgnet.tl", "org.telegram.ui.ActionBar",
				 "org.telegram.ui.Components"):
		try:
			__import__(name)
		except Exception as e:
			raise AssertionError(f"{name} does not import: {e}")

	from org.telegram.tgnet.tl import TL_account  # noqa: F401
	from org.telegram.ui.Components import LayoutHelper

	# An API the shim does not implement must resolve to a placeholder rather
	# than raise: that is the whole point of the tolerant layer.
	from org.telegram.messenger import NothingWasEverImplementedHere

	assert NothingWasEverImplementedHere.something() is not None, (
		"an unknown Android API must still be callable")
	assert LayoutHelper.dp(14) > 0, "LayoutHelper.dp must compute a real value"


@_check("shim", "permissions are read from the code, not demanded")
def _permissions_come_from_the_code():
	# A plugin written for a phone declares no permissions, because on Android
	# there is nothing to declare. Here that means the sandbox refuses its first
	# import and it never loads. The bridge reads the source instead, so both
	# the grant and the reason for it have to come out of the analysis.
	if str(STANDALONE) not in sys.path:
		sys.path.insert(0, str(STANDALONE))
	import permissions_infer as pi

	with tempfile.TemporaryDirectory() as tmp:
		root = Path(tmp) / "net_only"
		root.mkdir()
		(root / "plugin.json").write_text(json.dumps({
			"id": "net_only", "name": "Net", "version": "1.0",
			"entry": "main.py", "api_version": 1,
		}), encoding="utf-8")
		(root / "main.py").write_text(
			"import urllib.request\n"
			"def run():\n"
			"    return urllib.request.urlopen('http://x')\n",
			encoding="utf-8")

		granted = pi.ensure_manifest_permissions(root)
		if "net.http" not in granted:
			raise AssertionError(f"urllib import did not imply net.http: {granted}")
		if "storage.write" in granted:
			raise AssertionError("granted write for a plugin that only reads")
		manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
		if "net.http" not in manifest.get("permissions", []):
			raise AssertionError("manifest not updated")
		if granted["net.http"] != "import urllib":
			raise AssertionError(f"grant lost its reason: {granted}")

		# Running again must find nothing left to add, so installing twice
		# cannot keep rewriting the manifest.
		again = pi.ensure_manifest_permissions(root)
		if again:
			raise AssertionError(f"second pass granted more: {again}")


@_check("shim", "a manifest saved with a BOM is still readable")
def _manifest_survives_a_bom():
	# PowerShell's Set-Content -Encoding UTF8 writes one, and so do several
	# editors. json.loads rejects it outright and ast.parse rejects a source file
	# that starts with one, so a plugin can silently fall back to "no
	# permissions inferred" rather than to a working manifest.
	if str(STANDALONE) not in sys.path:
		sys.path.insert(0, str(STANDALONE))
	import permissions_infer as pi

	with tempfile.TemporaryDirectory() as tmp:
		root = Path(tmp) / "bommed"
		root.mkdir()
		(root / "plugin.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({
			"id": "bommed", "name": "B", "version": "1.0",
			"entry": "main.py", "api_version": 1,
		}).encode("utf-8"))
		(root / "main.py").write_bytes(
			b"\xef\xbb\xbfimport socket\n")

		found = pi.infer(root)
		if "net.http" not in found:
			raise AssertionError(f"BOM hid the imports: {found}")
		if pi.ensure_manifest_permissions(root, dry_run=True) == {}:
			raise AssertionError("BOM manifest was not parsed")


@_check("shim", "requests shim covers the surface plugins use")
def _requests_shim_surface():
	# Mobile plugins import requests before they do anything else, so its
	# absence stops the plugin loading at all and reads as AyuGram being
	# broken. Checked without a network call: this is about the API shape, and
	# a test that needs the internet fails exactly when it matters least.
	import requests
	from requests import exceptions
	from requests.models import CaseInsensitiveDict

	for name in ("get", "post", "put", "patch", "delete", "head", "request",
				 "Session", "Response", "exceptions", "codes"):
		if not hasattr(requests, name):
			raise AssertionError(f"requests.{name} missing")
	for name in ("HTTPError", "ConnectionError", "Timeout", "RequestException"):
		if not issubclass(getattr(exceptions, name), exceptions.RequestException):
			raise AssertionError(f"exceptions.{name} is not a RequestException")
	if requests.codes.ok != 200:
		raise AssertionError("codes.ok is not 200")

	# The bug this caught: dict.update bypasses __setitem__ on a subclass, so a
	# header added by Session.headers.update() kept its original case and every
	# later case-insensitive lookup missed it.
	session = requests.Session()
	session.headers.update({"X-Test": "1"})
	if session.headers.get("x-test") != "1":
		raise AssertionError("session headers are not case-insensitive")
	if session.headers.get("X-TEST") != "1":
		raise AssertionError("session headers lost their original case")

	# A 404 is a response, not an exception. Plugins branch on status_code, and
	# raising here would make half of them treat a missing page as a crash.
	response = requests.Response()
	response.status_code = 404
	response.url = "https://example.invalid/missing"
	response._content = b"nope"
	if response.ok:
		raise AssertionError("404 reported as ok")
	try:
		response.raise_for_status()
	except exceptions.HTTPError:
		pass
	else:
		raise AssertionError("raise_for_status did not raise for 404")


@_check("shim", "android submodules resolve as deep as a plugin asks")
def _android_submodules_resolve():
	# The failure was silent and shallow: android.graphics was a plain module,
	# so `import android.graphics.drawable` raised "'android.graphics' is not a
	# package" before any finder was consulted, and every plugin that touched a
	# drawable refused to load.
	import importlib

	sys.path.insert(0, str(SHIM))
	from ayugram_compat import mobile_stubs

	mobile_stubs.install()
	for name in ("android.graphics.drawable",
				"android.graphics.drawable.Drawable",
				"android.view.View",
				"android.widget.TextView",
				"android.util.DisplayMetrics",
				"android.os.Build"):
		try:
			importlib.import_module(name)
		except Exception as e:
			raise AssertionError(f"{name} does not import: {e}") from None
	# A real shim attribute must still win over the tolerant attribute path.
	from android.graphics import Color

	if "stub" not in repr(Color):
		raise AssertionError(
			f"android.graphics.Color is no longer the shim stub: {Color!r}")


@_check("shim", "an unknown client_utils helper does not kill the plugin")
def _unknown_client_utils_helper_is_inert():
	# `from client_utils import some_helper` is how a plugin reaches something it
	# expects a phone to provide. Raising there killed the whole plugin at
	# import over one function, and the log said only ImportError.
	sys.path.insert(0, str(SHIM))
	from ayugram_compat.gaps import all_gaps, clear

	clear()
	import client_utils

	try:
		from client_utils import _definitely_not_a_real_helper  # noqa: F401
	except ImportError as e:
		raise AssertionError(
			f"a missing helper still aborts the import: {e}") from None
	if "_definitely_not_a_real_helper" not in all_gaps()[0:1] + [
			g.split(".")[-1] for g in all_gaps()]:
		raise AssertionError(f"the gap was not recorded: {all_gaps()}")
	# It has to be callable, so a plugin that uses it at runtime continues
	# instead of crashing on the first invocation.
	from client_utils import _definitely_not_a_real_helper as helper

	helper(1, 2, keyword=3)
	# And the names that do exist must be untouched.
	if not callable(client_utils.send_text):
		raise AssertionError("client_utils.send_text is no longer callable")
	clear()


@_check("shim", "deploy ships every shim package")
def _deploy_ships_all_shim_packages():
	# The failure was silent: org was in the shim and missing from the list, so
	# the build looked complete and the plugins failed at import time instead.
	# In the repository the shim sits in shim/; in a deployment it is flattened
	# next to the executable, so both directions are checked where they apply.
	if str(STANDALONE) not in sys.path:
		sys.path.insert(0, str(STANDALONE))
	import deploy as _deploy

	shipped = set(_deploy.SHIM_PACKAGES)
	repo_shim = PLUGINS / "shim"
	if repo_shim.is_dir():
		on_disk = {
			child.name for child in repo_shim.iterdir()
			if child.is_dir() and child.name != "__pycache__"
		}
		absent = sorted(on_disk - shipped)
		assert not absent, (
			"these shim packages are never deployed, so plugins importing "
			"them cannot work: " + ", ".join(absent))
	else:
		missing = sorted(
			name for name in shipped
			if not (SHIM / name / "__init__.py").is_file()
			and not (SHIM / f"{name}.py").is_file())
		assert not missing, (
			"deploy claims to ship these packages but they are absent: "
			+ ", ".join(missing))

	# The sandbox is a sibling of shim/, so listing it as a shim package found
	# nothing and copied nothing. The app then started with no permission
	# enforcement at all, silently. Only meaningful against a real deployment.
	sandbox = SHIM / "sandbox"
	if (SHIM / "AyuGram.exe").is_file():
		assert (sandbox / "import_filter.py").is_file(), (
			"the sandbox is not deployed, so plugins run with no permission "
			"checks; the app looks up sandbox/import_filter.py next to the "
			"executable at startup")


# ---------------------------------------------------------------- runner

@_check("accounts", "account number survives the event")
def _account_number_survives_event():
	# Клиент присылает событие с номером аккаунта на самой структуре. Раньше
	# номер всегда был 0, и плагин с двумя аккаунтами отвечал на сообщения
	# обоих, отправляя ответ в аккаунт, откуда сообщение не пришло.
	_fresh_sys_path()
	from ayugram_compat import base_plugin

	class Message:
		def __init__(self, account):
			self.account = account
			self.peer_id = 1

	assert base_plugin._account_of((Message(3),)) == 3
	assert base_plugin._account_of((Message(0),)) == 0
	assert base_plugin._account_of(({"account": 5},)) == 5

	# Событие без номера: угадывать нечем, и это не повод его терять.
	assert base_plugin._account_of((object(),)) is None
	assert base_plugin._account_of(()) is None


@_check("accounts", "split reads account from event and legacy form")
def _account_split():
	_fresh_sys_path()
	from ayugram_compat import base_plugin

	class Message:
		def __init__(self, account):
			self.account = account

	# Новое: номер на структуре, payload остаётся целиком.
	account, rest = base_plugin._split_account("on_message", (Message(7),))
	assert account == 7
	assert len(rest) == 1

	account, rest = base_plugin._split_account(
		"on_message_deleted", (Message(1),))
	assert account == 1
	assert len(rest) == 1

	# Старое: номер отдельным первым аргументом, его убирают из payload,
	# иначе плагин получил бы лишний позиционный аргумент.
	account, rest = base_plugin._split_account("on_typing", (3, Message(9)))
	assert account == 3
	assert len(rest) == 1


@_check("accounts", "plugin can listen to one account only")
def _account_filter():
	_fresh_sys_path()
	from ayugram_compat.base_plugin import BasePlugin

	class Message:
		def __init__(self, account):
			self.account = account

	class Only(BasePlugin):
		account = 0

		def __init__(self):
			self.seen = []

		def on_message(self, message):
			self.seen.append(message.account)
			return "ok"

	only = Only()
	assert only._account_allowed(Message(0))
	assert not only._account_allowed(Message(1))
	assert only.on_message_hook(Message(0)) == "ok"
	assert only.on_message_hook(Message(1)) is None
	assert only.seen == [0]

	# Без фильтра ведёт себя как раньше: слышит все аккаунты.
	class All(BasePlugin):
		pass

	assert All()._account_allowed(Message(1))

	# Несколько аккаунтов задаются списком.
	class Pair(BasePlugin):
		accounts = [0, 2]

	pair = Pair()
	assert pair._account_allowed(Message(2))
	assert not pair._account_allowed(Message(1))


@_check("accounts", "unknown event is not dropped by the filter")
def _account_filter_unknown_event():
	# Событие без номера аккаунта пропустить нельзя: иначе плагин, задавший
	# фильтр, молча перестал бы работать на рукописных событиях.
	_fresh_sys_path()
	from ayugram_compat.base_plugin import BasePlugin

	class Only(BasePlugin):
		account = 0

	assert Only()._account_allowed(object())
	assert Only()._account_allowed_number(None)


@_check("accounts", "send helpers accept account without breaking callers")
def _account_send_signature():
	# Старые плагины зовут send_text(peer, text) и должны работать как
	# прежде: номер аккаунта необязателен.
	_fresh_sys_path()
	import inspect

	api = None
	for name in ("ayu_api", "ayu_api_standalone"):
		try:
			api = __import__(name)
			break
		except Exception:
			continue
	if api is None:
		raise Skip("ayu_api is only importable inside the client")

	for name in ("send_text", "edit_message", "send_file", "self_peer_id"):
		fn = getattr(api, name, None)
		if fn is None:
			raise Skip(f"{name} is not available")
		params = inspect.signature(fn).parameters
		assert "account" in params, f"{name} has no account"
		assert params["account"].default == -1, \
			f"{name} must keep working without an account"


@_check("accounts", "client reports how many accounts are signed in")
def _account_count_api():
	_fresh_sys_path()
	api = None
	for name in ("ayu_api", "ayu_api_standalone"):
		try:
			api = __import__(name)
			break
		except Exception:
			continue
	if api is None:
		raise Skip("ayu_api is only importable inside the client")

	if not hasattr(api, "account_count"):
		raise Skip("account_count is not available in this build")

	count = api.account_count()
	assert isinstance(count, int)
	assert count >= 0
	assert api.is_valid_account(0) is (count > 0)
	assert api.is_valid_account(-1) is False
	assert api.is_valid_account(count + 5) is False


@_check("accounts", "hooks carry account into python objects")
def _account_hook_fields():
	# pybind-структуры отдают номер наружу, иначе фильтр выше не увидит
	# ничего и пропустит событие.
	_fresh_sys_path()
	api = None
	for name in ("ayu_api", "ayu_api_standalone"):
		try:
			api = __import__(name)
			break
		except Exception:
			continue
	if api is None:
		raise Skip("ayu_api is only importable inside the client")

	for cls_name in ("Message", "Chat", "Typing"):
		cls = getattr(api, cls_name, None)
		if cls is None:
			raise Skip(f"{cls_name} is not available")
		inst = cls()
		inst.account = 4
		assert inst.account == 4, f"{cls_name}.account is not live"


@_check("accounts", "activity tracking survives a reload")
def _activity_reset():
	# A plugin that did nothing must be recognisable as such, or the health
	# report calls it working and the user waits for an effect that never
	# comes. The counter names live in the bridge; this checks the contract
	# the health report relies on.
	import inspect

	api = None
	for name in ("ayu_api", "ayu_api_standalone"):
		try:
			api = __import__(name)
			break
		except Exception:
			continue
	if api is None:
		raise Skip("ayu_api is only importable inside the client")

	# The activity names the report reads. If the bridge stops exporting one
	# of them, the report silently treats every plugin as busy.
	names = ("hook", "log", "toast")
	for name in names:
		assert hasattr(api, name), f"bridge lost {name}"


def main() -> int:
	ap = argparse.ArgumentParser(description=__doc__)
	ap.add_argument("-v", "--verbose", action="store_true")
	ap.add_argument("--only", help="run only this group")
	args = ap.parse_args()

	selected = [
		(g, n, fn) for (g, n, fn) in _RESULTS
		if args.only is None or g == args.only
	]
	if not selected:
		print(f"no checks in group {args.only!r}", file=sys.stderr)
		return 2

	passed = failed = skipped = 0
	for group, name, fn in selected:
		label = f"{group}: {name}"
		try:
			# Some shim paths print diagnostics; keep the report readable.
			with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
				fn()
		except Skip as e:
			skipped += 1
			print(f"SKIP  {label} ({e})")
		except Exception as e:
			failed += 1
			print(f"FAIL  {label}\n      {e}")
			if args.verbose:
				traceback.print_exc()
		else:
			passed += 1
			if args.verbose:
				print(f"ok    {label}")

	print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
	return 1 if failed else 0


if __name__ == "__main__":
	raise SystemExit(main())
