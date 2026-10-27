"""Desktop compat for mobile Extera/AyuGram client_utils.

Enough surface for mobile-style plugins to import and run: plain-text
sending is bridged to ayu_api, queues run via threading, and Java
controllers/fragments return chainable no-op stubs (never None) so
`get_foo().bar().baz()` chains survive load instead of dying with
AttributeError.

AyuGram plugin host for Desktop, (c) @CE0fbi
"""
from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable, Optional

from .mobile_stubs import stub as _stub

STAGE_QUEUE = "stageQueue"
GLOBAL_QUEUE = "globalQueue"
CACHE_CLEAR_QUEUE = "cacheClearQueue"
SEARCH_QUEUE = "searchQueue"
PHONE_BOOK_QUEUE = "phoneBookQueue"
THEME_QUEUE = "themeQueue"
EXTERNAL_NETWORK_QUEUE = "externalNetworkQueue"
PLUGINS_QUEUE = "pluginsQueue"

_hook_account: Optional[int] = None
_selected_account = 0


def _log(text: str) -> None:
	try:
		from ayu_api import log as _l
		_l(str(text))
	except Exception:
		print(f"[client_utils] {text}")


def _toast(text: str) -> None:
	try:
		from ayu_api import toast as _t
		_t(str(text))
	except Exception:
		print(f"[toast] {text}")


def get_hook_account() -> Optional[int]:
	return _hook_account


def get_selected_account() -> int:
	return _selected_account


def set_selected_account(account: int) -> None:
	global _selected_account
	_selected_account = int(account or 0)


_queues: dict = {}
_queues_lock = threading.Lock()


class _Queue:
	"""One worker per queue, the way a mobile queue behaves.

	`run_on_queue` used to start a thread per call. A plugin that reschedules
	itself from inside its own work - one polls its name change once a second -
	therefore created a thread every second, each sleeping before doing anything,
	and nothing ever joined or counted them. The work also ran concurrently with
	itself, which is not what a queue means: mobile queues run in order.
	"""

	def __init__(self) -> None:
		self._pending: list = []
		self._wake = threading.Event()
		self._running = False

	def post(self, fn: Callable, delay_ms: int = 0) -> None:
		self._pending.append((fn, max(0, int(delay_ms or 0)) / 1000.0))
		self._wake.set()

	def _serve(self) -> None:
		while True:
			if not self._pending:
				self._wake.clear()
				if self._pending:
					continue
				return
			fn, delay = self._pending.pop(0)
			if delay:
				time.sleep(delay)
			try:
				fn()
			except Exception as e:
				# The traceback matters: a bare message from a queue a dozen
				# plugins share cannot be traced back to the one that failed.
				_log(f"run_on_queue error in {getattr(fn, '__qualname__', fn)}: {e}")
				_log(traceback.format_exc())

	def ensure_running(self) -> None:
		if self._running:
			return
		self._running = True
		threading.Thread(
			target=self._drain, name=f"ayu-queue", daemon=True).start()

	def _drain(self) -> None:
		try:
			self._serve()
		finally:
			self._running = False
			if self._pending:
				# Work arrived as the worker was finishing.
				self.ensure_running()


def run_on_queue(
		fn: Callable,
		queue_name: str = PLUGINS_QUEUE,
		delay_ms: int = 0) -> None:
	"""Put `fn` on a named queue, to run in order on one worker."""
	name = str(queue_name or PLUGINS_QUEUE)
	with _queues_lock:
		queue = _queues.get(name)
		if queue is None:
			queue = _queues[name] = _Queue()
	queue.post(fn, delay_ms)
	queue.ensure_running()


def get_queue_by_name(name: str):
	"""A queue that really queues.

	It used to answer a placeholder, so the commonest mobile idiom -
	`get_queue_by_name("stageQueue").postRunnable(fn)` - posted nothing, returned
	a truthy chain, and wrote nothing to the log.
	"""
	queue_name = str(name or PLUGINS_QUEUE)

	class _Handle:
		def post(self, fn: Callable, delay_ms: int = 0) -> None:
			run_on_queue(fn, queue_name, delay_ms)

		def postDelayed(self, fn: Callable, delay_ms: int = 0) -> None:
			run_on_queue(fn, queue_name, delay_ms)

		def postRunnable(self, fn: Callable) -> None:
			run_on_queue(fn, queue_name)

		def cancel(self, token: Any = None) -> bool:
			return True

	return _Handle()


class RequestCallback:
	"""Desktop stand-in for the Java `RequestCallback` dynamic proxy.

	Mobile plugins wrap a plain Python callable: `RequestCallback(on_resolve)`.
	The proxy is then handed to `send_request`, which invokes it back with
	`(response, error)`. On Desktop there is no MTProto request to attach to,
	so the callback is kept callable and any `onResponse`/`on_response` entry
	point is dispatched with an error instead of a response.
	"""

	__slots__ = ("callback", "response", "error", "called")

	def __init__(self, callback: Optional[Callable] = None, *args) -> None:
		if callback is not None and not callable(callback) and callable(args[0] if args else None):
			callback, args = args[0], (callback,) + args
		self.callback = callback
		self.response: Any = None
		self.error: Any = None
		self.called = False

	def onResponse(self, response: Any = None, error: Any = None) -> None:
		self.on_response(response, error)

	def on_response(self, response: Any = None, error: Any = None) -> None:
		self.called = True
		self.response = response
		self.error = error
		fn = self.callback
		if fn is None:
			return
		try:
			fn(response, error)
		except TypeError:
			try:
				fn(response)
			except Exception as e:
				_log(f"RequestCallback error: {e}")
		except Exception as e:
			_log(f"RequestCallback error: {e}")

	def onError(self, error: Any = None) -> None:
		self.on_response(None, error)

	def on_error(self, error: Any = None) -> None:
		self.on_response(None, error)

	def __call__(self, response: Any = None, error: Any = None) -> None:
		self.on_response(response, error)


class Request:
	"""Minimal writable request record, mirroring the Java TLRPC field style."""

	__slots__ = ("_fields",)

	def __init__(self, **fields: Any) -> None:
		self._fields = dict(fields)

	def __getattr__(self, name: str) -> Any:
		try:
			return self._fields[name]
		except KeyError:
			raise AttributeError(name)

	def __setattr__(self, name: str, value: Any) -> None:
		if name == "_fields":
			object.__setattr__(self, name, value)
			return
		self._fields[name] = value

	def to_dict(self) -> dict:
		return dict(self._fields)

	def __repr__(self) -> str:
		inner = ", ".join(f"{k}={v!r}" for k, v in self._fields.items())
		return f"Request({inner})"


class RequestError(Exception):
	"""What a failed request hands back.

	Mobile plugins read `error.text` in their on_response, which is how Telegram
	reports a failure. Passing a plain string gave them
	"'str' object has no attribute 'text'", so the handler raised, and the
	reason the request failed was never shown - the one thing the callback was
	called for.
	"""

	def __init__(self, text: Any, code: int = 400, kind: str = "") -> None:
		super().__init__(str(text))
		self.text = str(text)
		self.message = str(text)
		self.code = int(code)
		self.type = kind or str(text)
		self.error_code = int(code)


def _as_error(value: Any) -> Any:
	"""Give an error the shape a plugin expects, without inventing one."""
	if value is None or isinstance(value, RequestError):
		return value
	if isinstance(value, BaseException):
		return RequestError(str(value))
	if isinstance(value, str):
		return RequestError(value)
	if isinstance(value, dict):
		return RequestError(
			value.get("text") or value.get("message") or value.get("description")
			or str(value),
			value.get("code") or 400,
			value.get("type") or "")
	return value


def _dispatch(callback: Any, response: Any, error: Any) -> None:
	if callback is None:
		return
	entry = getattr(callback, "on_response", None) or getattr(callback, "onResponse", None)
	try:
		if callable(entry):
			entry(response, _as_error(error))
		elif callable(callback):
			callback(response, _as_error(error))
	except Exception as e:
		_log(f"send_request callback error: {e}")


def send_request(request: Any, callback: Optional[Callable] = None, account: int = 0) -> int:
	"""Perform a request a plugin asked for and hand the result to its callback.

	Two very different things arrive under this name. Mobile plugins use it for
	raw MTProto, which Desktop cannot do, but they also use it for plain HTTP:
	custom_nickname builds a urllib request for the weather and the exchange rate
	and passes it here. That used to be discarded with a log line, so the callback
	fired with None every time and the plugin's weather and currency panels stayed
	permanently empty - the request was never the problem, only being thrown away.

	An HTTP request is therefore actually performed. It is dispatched onto a
	worker thread, because a plugin calls this from its command hook and blocking
	the caller for the length of a network call would stall message handling.

	A Telegram API request goes to the native bridge, which answers the few
	methods Desktop can genuinely perform - account.updateProfile among them,
	which is how a plugin renames itself. That used to be refused as raw MTProto
	even though Desktop had the call, so the plugin loaded and logged a failure
	every second without ever changing anything.
	Returns 1 when the work was started, 0 when the request is not something this
	can perform.
	"""
	fetcher = _http_fetcher(request)
	if fetcher is not None:
		def _work() -> None:
			try:
				_dispatch(callback, fetcher(), None)
			except Exception as e:
				_log(f"send_request failed: {e}")
				_dispatch(callback, None, str(e))

		try:
			threading.Thread(
				target=_work, name="ayu-send-request", daemon=True).start()
		except Exception as e:
			_log(f"send_request could not start: {e}")
			_dispatch(callback, None, str(e))
			return 0
		return 1

	method = _telegram_method_name(request)
	if method:
		if _native_telegram_request(method, request, callback):
			return 1
		_dispatch(
			callback, None,
			f"Desktop bridge: {method} is not something this build performs")
		return 0

	_dispatch(
		callback, None,
		f"Desktop bridge: unsupported request {type(request).__name__}")
	return 0


def _telegram_method_name(request: Any) -> str:
	"""The Telegram method a request object stands for, if it is one."""
	name = type(request).__name__
	if name.startswith("TL_") and "." in name:
		return name
	# Some plugins build the request through a helper that returns a subclass
	# named after the call rather than after its type.
	for attribute in ("__tl_name__", "_tl_name", "method", "__name__"):
		value = getattr(request, attribute, None)
		if isinstance(value, str) and value.startswith("TL_") and "." in value:
			return value
	return ""


def _native_telegram_request(
		method: str, request: Any, callback: Optional[Callable]) -> bool:
	"""Ask the bridge to perform a Telegram call, if it knows this one."""
	try:
		from ayu_api import request_telegram
	except Exception:
		return False

	fields = {}
	for name in ("first_name", "last_name", "about", "username"):
		value = getattr(request, name, None)
		if isinstance(value, (str, int, float)):
			fields[name] = value

	def _done(response: Any, error: Any) -> None:
		try:
			if callback:
				callback(response, error)
		except Exception as e:
			_log(f"request callback failed: {e}")

	try:
		return bool(request_telegram(method, fields, _done))
	except Exception as e:
		_log(f"{method} could not be performed: {e}")
		_dispatch(callback, None, str(e))
		return False


class _HttpResult:
	"""What a performed HTTP request hands back, shaped like requests.Response."""

	def __init__(self, status_code: int, body: bytes, url: str, headers: Any = None):
		self.status_code = int(status_code)
		self.content = body
		self.url = url
		self.headers = dict(headers or {})
		self.ok = 200 <= self.status_code < 300

	def json(self) -> Any:
		import json as _json
		return _json.loads(self.text)

	@property
	def text(self) -> str:
		return self.content.decode("utf-8", "replace")

	def raise_for_status(self) -> "_HttpResult":
		if not self.ok:
			raise RuntimeError(f"HTTP {self.status_code}")
		return self

	def __repr__(self) -> str:
		return f"<Response [{self.status_code}]>"


def _http_fetcher(request: Any) -> Optional[Callable[[], Any]]:
	"""Return a callable that performs `request` and yields the body, or None.

	Accepts a urllib Request or a bare URL, which is what plugins that use this
	build. Anything else is a raw MTProto request, and there is nothing Desktop
	can do with it.

	urllib is used rather than the bundled requests shim: that shim only assembles
	an outgoing request and never performs it, so calling it here would have
	"answered" every request with a tuple and no data.
	"""
	import urllib.request as _urllib

	if isinstance(request, _urllib.Request):
		url = request.full_url
	elif isinstance(request, str) and request.startswith(("http://", "https://")):
		url = request
		request = _urllib.Request(url)
	else:
		return None

	def _run() -> _HttpResult:
		with _urllib.urlopen(request, timeout=15) as response:
			return _HttpResult(
				getattr(response, "status", 200) or 200,
				response.read(),
				url,
				getattr(response, "headers", None))

	return _run


def _reply_ids(kwargs: dict) -> tuple:
	"""Read a reply target out of however the plugin spelled it.

	The names differ between callers: a plugin replying through send_message
	uses `replyToMsg`, one calling send_text directly may pass `replyToMsgId` or
	`reply_to_msg_id`, and a context object uses `replyToMsg`. All of them mean
	the same thing, so all of them are accepted - and all of them used to be
	dropped, which turned a reply into a plain message in the chat.
	"""
	top = 0
	for name in ("replyToMsg", "replyToMsgId", "reply_to_msg_id",
				 "reply_to", "replyTo"):
		value = kwargs.get(name)
		if value:
			try:
				top = int(value)
				break
			except Exception:
				continue
	root = 0
	for name in ("replyToTopMsg", "replyToTopMsgId", "reply_to_top_msg_id"):
		value = kwargs.get(name)
		if value:
			try:
				root = int(value)
				break
			except Exception:
				continue
	return top, root


def send_text(
		peer_id: Any,
		text: str,
		replyToMsg: Any = None,
		parse_mode: Optional[str] = None,
		account: int = 0,
		**kwargs) -> bool:
	"""Send a plain-text message. Returns True only if it actually went out.

	A plugin that cancels the original outgoing message must know whether
	the replacement really arrived, otherwise the text is lost.
	"""
	peer = int(peer_id) if peer_id is not None else 0
	body = str(text or "")
	if not peer or not body:
		return False
	if replyToMsg is not None and kwargs.get("replyToMsg") is None:
		kwargs["replyToMsg"] = replyToMsg
	top, root = _reply_ids(kwargs)
	try:
		from ayu_api import send_text as _send
	except Exception as e:
		_log(f"send_text unavailable: {e}")
		return False
	try:
		# The native side answers 1 for "queued" and 0 for "refused" (empty text,
		# shutting down). A missing answer means an older build that returned
		# nothing, and is read as dispatched so those still behave. Reporting
		# success without asking used to hide every refusal, which is how a
		# plugin could report that its reply went out when nothing was sent.
		answered = _send(peer, body, top, root)
		return True if answered is None else bool(answered)
	except TypeError:
		# An older native build takes only the peer and the text.
		try:
			answered = _send(peer, body)
			if top:
				_log("send_text: this build cannot reply, sent as a plain message")
			return True if answered is None else bool(answered)
		except Exception as e:
			_log(f"send_text failed: {e}")
			return False
	except Exception as e:
		_log(f"send_text failed: {e}")
		return False


def edit_message(
		peer_id: Any,
		msg_id: Any,
		text: str,
		keep_webpage: bool = True,
		**kwargs) -> bool:
	"""Replace the text of an already sent message.

	This is the Desktop counterpart of what mobile plugins do by calling
	`applyNewText` on a bound `ChatMessageCell`. There is no cell to patch on
	Desktop, so the text is edited through the server instead.

	`keep_webpage` defaults to True because that is what an edit means. It
	defaulted to False, and the native side treats that as "remove the preview",
	so a plugin that animates a message one step at a time stripped the link
	preview out of it on the first edit - and had no way to tell that was why.
	"""
	peer = int(peer_id) if peer_id is not None else 0
	ident = int(msg_id) if msg_id is not None else 0
	body = str(text or "")
	if not peer or not ident or not body:
		return False
	try:
		from ayu_api import edit_message as _edit
	except Exception as e:
		_log(f"edit_message unavailable: {e}")
		return False
	try:
		# Older builds return nothing here, so a missing answer is treated as
		# "dispatched" rather than as a failure: reporting False on every call
		# made plugins resend the whole message instead of animating it.
		answered = _edit(peer, ident, body, bool(keep_webpage))
		return True if answered is None else bool(answered)
	except Exception as e:
		_log(f"edit_message failed: {e}")
		return False


def send_message(params: Any, parse_mode: Optional[str] = None, account: int = 0, **kwargs) -> bool:
	"""Send a message described the way mobile plugins describe it.

	Returns whether the text was handed over, rather than None. A plugin that
	answers a command checks this, and an answer that always reads as "sent"
	is how a refused send turns into a plugin that looks broken.
	"""
	if isinstance(params, dict):
		peer = params.get("peer") or params.get("peer_id") or 0
		text = params.get("message") or params.get("text") or params.get("caption") or ""
		# The reply is taken from the dict and passed on. It was dropped here,
		# so a plugin that built its message with replyToMsg got an unthreaded
		# message in the chat - one spam plugin sets it on every send and had no
		# way to tell that it was ignored.
		extra = {
			key: value
			for key, value in params.items()
			if key.lower().startswith("reply")
		}
		return send_text(
			peer, text, parse_mode=parse_mode, account=account, **extra)
	text = getattr(params, "text", getattr(params, "message", None))
	if isinstance(text, str):
		peer = getattr(params, "peer_id", getattr(params, "peer", 0)) or 0
		extra = {}
		for name in ("replyToMsg", "replyToMsgId", "reply_to_msg_id",
					 "replyToTopMsg", "replyToTopMsgId"):
			value = getattr(params, name, None)
			if value is not None:
				extra[name] = value
		return send_text(
			peer, text, parse_mode=parse_mode, account=account, **extra)
	_log("send_message: unsupported params type")
	return False


def _send_file(peer_id, path, caption, kind) -> bool:
	"""Hand a file on disk to the native sender.

	These four were stubs that logged "not supported on Desktop" and returned
	False, so every plugin that sent a picture, a sticker or a file did nothing
	and had no way to tell why. The native side reads the file off the calling
	thread and uploads it through the ordinary send path.
	"""
	peer = int(peer_id) if peer_id is not None else 0
	if not peer:
		_log(f"send_{kind}: no peer, nothing sent")
		return False
	if not path:
		_log(f"send_{kind}: no path, nothing sent")
		return False
	try:
		from ayu_api import send_file as _send
	except Exception as e:
		_log(f"send_{kind} unavailable: {e}")
		return False
	try:
		answered = _send(peer, str(path), str(caption or ""), kind)
		return True if answered is None else bool(answered)
	except Exception as e:
		_log(f"send_{kind} failed: {e}")
		return False


def send_photo(peer_id, path, caption="", **kwargs):
	return _send_file(peer_id, path, caption, "photo")


def send_document(peer_id, path, caption="", **kwargs):
	return _send_file(peer_id, path, caption, "document")


def send_video(peer_id, path, caption="", **kwargs):
	return _send_file(peer_id, path, caption, "video")


def send_audio(peer_id, path, caption="", **kwargs):
	return _send_file(peer_id, path, caption, "audio")


def _ctrl_stub(name: str):
	"""Controller getter factory: always returns a chainable stub.

	Marked `__ayu_stub__` so a plugin can tell it is holding an inert object
	rather than a controller. The two that have a real Desktop answer -
	`get_user_config` and `get_last_fragment` - are defined below instead.
	"""
	def _get(*args, **kwargs):
		holder = _stub(name)
		try:
			holder.__ayu_stub__ = True
		except Exception:
			pass
		return holder
	return _get


class _UserConfig:
	"""The signed-in user, to the extent a plugin can use it.

	Plugins ask this who they are changing: `get_user_config().getCurrentUser()`
	and then read `first_name` to decide whether the name they are about to set
	needs changing at all. The answer was a falsy stub, so the check read as
	"there is no user" and the plugin returned without doing anything - which is
	why one of them never even built its request, with nothing logged.
	"""

	def getCurrentUser(self):
		return _CurrentUser()

	# The camelCase and snake_case spellings both turn up.
	def get_current_user(self):
		return self.getCurrentUser()

	def isPremium(self) -> bool:
		return False

	def getName(self) -> str:
		return ""


class _CurrentUser:
	"""A user whose real name is not knowable from a plugin."""

	def __init__(self):
		self.id = 0
		# Not empty: a plugin compares the current name against the one it wants
		# and skips the work when they match. An empty value would make every
		# comparison fail and the plugin would try to rename on every tick.
		self.first_name = ""
		self.last_name = ""
		self.username = ""
		self.bot = False
		self.deleted = False

	def __bool__(self) -> bool:
		return True


def get_user_config(*args, **kwargs):
	return _UserConfig()


def get_last_fragment(*args, **kwargs):
	"""The screen the user is looking at.

	Tied to what the app knows about the open chat, because a plugin that
	opens a dialog from this asks whether it is already inside one. The falsy
	answer it used to get meant `if not fragment: return`, so the dialog never
	opened from either entry point.
	"""
	try:
		from ayu_api import get_current_chat as _current
	except Exception:
		return _stub("fragment.last")
	chat = _current()
	if not chat:
		return _stub("fragment.last")
	fragment = _stub("fragment.last")
	try:
		fragment.chat = chat
		fragment.getParentActivity = lambda: fragment
	except Exception:
		pass
	return fragment


get_account_instance = _ctrl_stub("account.instance")
get_messages_controller = _ctrl_stub("messages.controller")
get_contacts_controller = _ctrl_stub("contacts.controller")
get_media_data_controller = _ctrl_stub("media_data.controller")
get_connections_manager = _ctrl_stub("connections.manager")
get_location_controller = _ctrl_stub("location.controller")
get_notifications_controller = _ctrl_stub("notifications.controller")
get_messages_storage = _ctrl_stub("messages.storage")
get_send_messages_helper = _ctrl_stub("send_messages.helper")
get_file_loader = _ctrl_stub("file.loader")
get_secret_chat_helper = _ctrl_stub("secret_chat.helper")
get_download_controller = _ctrl_stub("download.controller")
get_notifications_settings = _ctrl_stub("notifications.settings")
get_notification_center = _ctrl_stub("notification.center")
get_media_controller = _ctrl_stub("media.controller")


class AccountClient:
	def __init__(self, account: int = 0):
		self.account = int(account or 0)

	def send_text(self, peer_id, text, **kwargs):
		send_text(peer_id, text, account=self.account, **kwargs)

	def send_message(self, params, **kwargs):
		send_message(params, account=self.account, **kwargs)

	def get_user_config(self):
		return _stub("user.config")

	def get_messages_controller(self):
		return _stub("messages.controller")

	def get_last_fragment(self):
		return _stub("fragment.last")

	def __getattr__(self, name):
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		return _stub(f"AccountClient.{name}")


def get_client(account: int = 0) -> AccountClient:
	return AccountClient(account)


class NotificationCenterDelegate:
	def didReceivedNotification(self, id: int, account: int, args=None):
		pass


def _prepare_document(*args, **kwargs):
	return _stub("document.prepared")
