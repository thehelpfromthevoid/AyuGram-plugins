"""Desktop compat for mobile Extera/AyuGram hook_utils.

Mobile plugins use either:
  from hook_utils import on_hook, find_class, ...
  from ayugram_compat.hook_utils import hook

Hook names are normalized to the Desktop surface; pure Java/TL hooks
are accepted and ignored with a log line instead of killing the load.
Reflection helpers return chainable no-op stubs so import-time and
load-time code survives without a Java runtime.

Desktop conveniences (also importable from ayu_api):
  @command("/ping") - turn an outgoing message prefix into an action.
  @interval(60)     - run a function every N seconds (daemon thread).
  @on_start / @on_stop - aliases for app lifecycle hooks.

AyuGram plugin host for Desktop, (c) @CE0fbi
"""
from __future__ import annotations

import functools
import sys
import threading
import time
import weakref
from typing import Any, Callable, Optional

from .mobile_stubs import stub as _stub

try:
	from ayu_api import hook as _native_hook
except Exception:  # pragma: no cover - pure-python test mode
	_native_hook = None
	_registry = {}


# Live plugin instances, so a hook declared as a method can be bound to the
# object it belongs to when the app calls it. Weak, so unloading a plugin
# really drops it.
_live_instances: "weakref.WeakSet" = weakref.WeakSet()
_lock = threading.RLock()


def register_instance(instance: Any) -> None:
	"""Called by the plugin loader once a BasePlugin subclass is instantiated."""
	try:
		with _lock:
			_live_instances.add(instance)
	except Exception:
		pass


def unregister_instance(instance: Any) -> None:
	try:
		with _lock:
			_live_instances.discard(instance)
	except Exception:
		pass


def _log(text: str) -> None:
	try:
		from ayu_api import log as _l
		_l(str(text))
	except Exception:
		print(f"[hook_utils] {text}")


def _desktop_hook_name(name: str) -> str:
	raw = str(name or "")
	low = raw.lower().replace("-", "_")
	aliases = {
		"on_message": "on_message",
		"message": "on_message",
		"new_message": "on_message",
		"on_new_message": "on_message",
		"message_received": "on_message",
		"on_message_received": "on_message",
		"on_message_sending": "on_message_sending",
		"on_send_message": "on_message_sending",
		"send_message": "on_message_sending",
		"before_send_message": "on_message_sending",
		"before_send": "on_message_sending",
		"on_message_deleted": "on_message_deleted",
		"message_deleted": "on_message_deleted",
		"on_delete_messages": "on_message_deleted",
		"deleted_messages": "on_message_deleted",
		"delete_messages": "on_message_deleted",
		"on_chat_opened": "on_chat_opened",
		"chat_opened": "on_chat_opened",
		"chat_open": "on_chat_opened",
		"on_open_chat": "on_chat_opened",
		"on_app_start": "on_app_start",
		"app_start": "on_app_start",
		"on_app_stop": "on_app_stop",
		"app_stop": "on_app_stop",
		"on_setting_changed": "on_setting_changed",
		"setting_changed": "on_setting_changed",
		# Mobile plugins use the plural freely; both spellings are common and
		# neither is more correct than the other.
		"on_settings_changed": "on_setting_changed",
		"settings_changed": "on_setting_changed",
		"on_setting_change": "on_setting_changed",
		"on_new_message": "on_message",
		"new_messages": "on_message",
		"incoming_message": "on_message",
		"on_incoming_message": "on_message",
		"on_message_new": "on_message",
		"on_delete_message": "on_message_deleted",
		"on_messages_deleted": "on_message_deleted",
		"message_delete": "on_message_deleted",
		"on_before_send": "on_message_sending",
		"before_message_send": "on_message_sending",
		"on_outgoing_message": "on_message_sending",
		"on_app_launch": "on_app_start",
		"on_app_quit": "on_app_stop",
		"on_quit": "on_app_stop",
		"on_typing": "on_typing",
		"typing": "on_typing",
		"user_typing": "on_typing",
		"send_action": "on_typing",
		"on_send_action": "on_typing",
		"on_media_sending": "on_media_sending",
		"media_sending": "on_media_sending",
		"before_send_media": "on_media_sending",
		"on_send_media": "on_media_sending",
	}
	if low in aliases:
		return aliases[low]
	# Substring rules next, so a name nobody thought of still lands on the right
	# event instead of nowhere.
	if "send" in low and "message" in low:
		return "on_message_sending"
	if "delete" in low and "message" in low:
		return "on_message_deleted"
	if "chat" in low and "open" in low:
		return "on_chat_opened"
	# TL and Java hook names. These used to be dropped outright, which left every
	# plugin that watched Telegram updates doing nothing on Desktop. They are
	# the same events under Telegram's own names, so they are matched on the
	# words: TL_updateNewMessage and TL_updateShortChatMessage are incoming
	# messages, TL_updateDeleteMessages is a deletion.
	if low.startswith("tl_") or "." in raw:
		plain = low[3:] if low.startswith("tl_") else low.replace(".", "_")
		if "typing" in plain:
			return "on_typing"
		if "delete" in plain or "deleted" in plain:
			return "on_message_deleted"
		if "newmessage" in plain.replace("_", "") or (
				"message" in plain and "new" in plain):
			return "on_message"
		if "shortmessage" in plain.replace("_", "") or (
				"shortchatmessage" in plain.replace("_", "")):
			return "on_message"
		return ""
	return raw


class PeerId:
	"""A Telegram peer lookalike.

	Plugins read `update.from_id.user_id` where Desktop only has a plain integer,
	and `update.message.peer_id.user_id` is the same story. Returning the
	integer left the attribute chain broken, and since these handlers are
	wrapped in a bare `except`, the plugin just quietly stopped working.
	"""

	__slots__ = ("user_id", "chat_id", "channel_id")

	def __init__(self, user_id: int = 0, chat_id: int = 0,
				 channel_id: int = 0) -> None:
		self.user_id = int(user_id or 0)
		self.chat_id = int(chat_id or 0)
		self.channel_id = int(channel_id or 0)

	def __int__(self) -> int:
		return self.user_id or self.chat_id or self.channel_id

	def __bool__(self) -> bool:
		return bool(int(self))

	def __eq__(self, other: Any) -> bool:
		if isinstance(other, PeerId):
			return int(self) == int(other)
		if isinstance(other, int):
			return int(self) == other
		return NotImplemented

	def __hash__(self) -> int:
		return hash(int(self))

	def __repr__(self) -> str:
		if self.user_id:
			return f"PeerId(user_id={self.user_id})"
		if self.chat_id:
			return f"PeerId(chat_id={self.chat_id})"
		if self.channel_id:
			return f"PeerId(channel_id={self.channel_id})"
		return "PeerId()"


def peer_like(value: Any) -> Any:
	"""Turn an integer id into something with `.user_id` on it."""
	if value is None or isinstance(value, PeerId):
		return value
	if isinstance(value, bool):
		return value
	if isinstance(value, int):
		return PeerId(user_id=value)
	return value


class _Overridden:
	"""Wraps an object, replacing a few attributes and passing the rest through.

	An incoming message must not be rewritten in place: `on_message` is handed
	to every plugin in turn, and turning one plugin's `peer_id` into a peer
	object under another plugin's feet would be its own bug.
	"""

	def __init__(self, target: Any, **overrides: Any) -> None:
		object.__setattr__(self, "_target", target)
		object.__setattr__(self, "_overrides", overrides)

	def __getattr__(self, item: str) -> Any:
		overrides = object.__getattribute__(self, "_overrides")
		if item in overrides:
			return overrides[item]
		return getattr(object.__getattribute__(self, "_target"), item)

	def __setattr__(self, item: str, value: Any) -> None:
		object.__getattribute__(self, "_overrides")[item] = value

	def __delattr__(self, item: str) -> None:
		object.__getattribute__(self, "_overrides").pop(item, None)

	def __repr__(self) -> str:
		return repr(object.__getattribute__(self, "_target"))


class PluginUpdate:
	"""A Telegram update rebuilt from a Desktop event.

	A plugin that hooks raw updates expects two things: attributes on the
	update, and the *class name* of its action. `draft_ghost` reads
	`type(action).__name__` to tell typing from recording, so the action is
	built with the Telegram name as its class name and not with a friendlier
	one.
	"""

	def __init__(self, name: str, **fields: Any) -> None:
		self.update_name = name
		self.__tl_name__ = name
		for key, value in fields.items():
			setattr(self, key, value)

	def __getattr__(self, item: str) -> Any:
		# A field this Desktop event cannot supply answers None instead of
		# raising. Plugin handlers swallow exceptions, so a missing attribute
		# would otherwise end the plugin's work without a word.
		if item.startswith("__") and item.endswith("__"):
			raise AttributeError(item)
		return None

	def __repr__(self) -> str:
		return f"<{self.update_name}>"


# The action names Telegram uses, so a plugin comparing against its own strings
# finds them. Built on demand: there are a dozen and no plugin loads all.
_TL_ACTIONS = (
	"TL_sendMessageTypingAction",
	"TL_sendMessageCancelAction",
	"TL_sendMessageRecordVideoAction",
	"TL_sendMessageRecordVoiceAction",
	"TL_sendMessageRecordRoundVideoAction",
	"TL_sendMessageUploadPhotoAction",
	"TL_sendMessageUploadVideoAction",
	"TL_sendMessageUploadDocumentAction",
	"TL_sendMessageUploadGeoAction",
	"TL_sendMessageChooseStickerAction",
)


def tl_action(action_name: str) -> Any:
	"""An action object whose class name is the one the plugin expects."""
	if not action_name:
		return None
	known = action_name if action_name in _TL_ACTIONS else f"TL_sendMessage{action_name}"
	try:
		return type(known, (object,), {})()
	except Exception:
		return None


def pick_update_name(
		names: list, event: str, args: tuple) -> str:
	"""Choose which Telegram name an arriving Desktop event should carry.

	One Desktop event stands for several Telegram updates, and a plugin
	registers all of them. Calling it once per registered name would hand the
	plugin the same event repeatedly, each time under a name that had not
	happened - a channel typing notification delivered as a private one reads
	as a different chat. So the name comes from what actually arrived.
	"""
	if not names:
		return ""
	if event == "on_typing":
		src = args[0] if args else None
		try:
			kind = int(getattr(src, "kind", -1))
		except Exception:
			kind = -1
		# Telegram's three typing updates differ only in which id they carry,
		# so the native side says which one it was.
		want = {
			0: "user",
			1: "chat",
			2: "channel",
		}.get(kind)
		if want:
			for candidate in names:
				if want in candidate.lower():
					return candidate
		for candidate in names:
			if "typing" in candidate.lower():
				return candidate
		return names[0]
	if event == "on_message":
		for wanted in ("TL_updateNewMessage", "TL_updateNewChannelMessage"):
			if wanted in names:
				return wanted
		return names[0]
	if event == "on_message_deleted":
		for candidate in names:
			if "delete" in candidate.lower():
				return candidate
		return names[0]
	return names[0]


def _message_like(message: Any) -> Any:
	if message is None or isinstance(message, _Overridden):
		return message
	overrides = {}
	for field in ("from_id", "peer_id", "peer"):
		try:
			value = getattr(message, field)
		except Exception:
			continue
		peer = peer_like(value)
		if peer is not value:
			overrides[field] = peer
	if not overrides:
		return message
	return _Overridden(message, **overrides)


def build_tl_update(name: str, event: str, args: tuple) -> PluginUpdate:
	"""Rebuild the Telegram update a plugin asked for out of a Desktop event.

	`name` is the Telegram name that happened, `event` the Desktop event it maps
	to, `args` the arguments that event was called with.
	"""
	raw = str(name or "")
	low = raw.lower()

	if "typing" in low and event == "on_typing":
		# on_typing arrives as one event object, like Chat and
		# MessageDeletedContext; the account has already been stripped off by
		# the bridge.
		src = args[0] if args else None

		def _field(name: str) -> int:
			try:
				return int(getattr(src, name, 0) or 0)
			except Exception:
				return 0

		peer_id = _field("peer_id")
		user_id = _field("user_id")
		top_msg_id = _field("top_msg_id")
		try:
			action = tl_action(str(getattr(src, "action", "") or ""))
		except Exception:
			action = None
		if "channel" in low:
			update = PluginUpdate(
				raw,
				channel_id=peer_id,
				from_id=PeerId(user_id=user_id),
				top_msg_id=top_msg_id,
				action=action,
			)
		elif "chat" in low:
			update = PluginUpdate(
				raw,
				chat_id=peer_id,
				from_id=PeerId(user_id=user_id),
				action=action,
			)
		else:
			update = PluginUpdate(
				raw,
				user_id=user_id,
				top_msg_id=top_msg_id,
				action=action,
			)
		return update

	if event == "on_message":
		message = args[0] if args else None
		return PluginUpdate(raw, message=_message_like(message))

	if event == "on_message_deleted":
		peer_id = args[0] if args else 0
		message_id = args[1] if len(args) > 1 else 0
		return PluginUpdate(
			raw,
			peer_id=peer_like(peer_id),
			messages=[int(message_id or 0)],
			user_id=0,
			action=None,
		)

	if event == "on_chat_opened":
		chat = args[0] if args else None
		peer_id = getattr(chat, "peer_id", None) if chat is not None else None
		title = getattr(chat, "title", "") if chat is not None else ""
		return PluginUpdate(raw, peer_id=peer_like(peer_id), title=title or "")

	if event == "on_message_sending":
		params = args[0] if args else None
		peer_id = getattr(params, "peer", None) if params is not None else None
		text = getattr(params, "message", "") if params is not None else ""
		return PluginUpdate(
			raw,
			message=_Overridden(
				params,
				peer_id=peer_like(peer_id),
				message=str(text or ""),
			) if params is not None else None,
		)

	# A container such as TL_updates: everything it would have carried is
	# unknown, and saying so is better than inventing it.
	return PluginUpdate(raw)


def _resolve_instance(fn: Callable[..., Any]):
	"""Return the plugin instance a class-body-declared hook belongs to.

	`@hook(...)` runs while the class body is evaluated, so the function is
	still unbound. The app then calls it with no `self`, which raises
	TypeError on every event. Find the live instance whose class defines
	this exact function.
	"""
	try:
		with _lock:
			candidates = list(_live_instances)
	except Exception:
		return None
	for instance in candidates:
		try:
			if getattr(type(instance), fn.__name__, None) is fn:
				return instance
		except Exception:
			continue
	return None


def _bind(fn: Callable[..., Any]) -> Callable[..., Any]:
	@functools.wraps(fn)
	def _invoke(*args, **kwargs):
		instance = _resolve_instance(fn)
		if instance is not None:
			return fn(instance, *args, **kwargs)
		return fn(*args, **kwargs)

	return _invoke


def hook(name, fn=None):
	"""Supports @hook('name') and hook('name', fn). Desktop maps to ayu_api.hook."""
	mapped = _desktop_hook_name(name)

	def _register(f):
		if not mapped:
			_log(f"Desktop: unsupported mobile hook '{name}' ignored")
			return f
		if _native_hook is not None:
			_native_hook(mapped, _bind(f))
		else:
			_registry.setdefault(mapped, []).append(_bind(f))
		return f

	if fn is None:
		return _register
	return _register(fn)


def on_hook(name: str):
	"""Mobile alias: @on_hook('TL_...') - maps known names, ignores pure Java/TL."""
	return hook(name)


def on_start(fn=None):
	"""Alias for the app-start hook. Supports @on_start and on_start(fn)."""
	return hook("on_app_start", fn) if fn is not None else hook("on_app_start")


def on_stop(fn=None):
	"""Alias for the app-stop hook. Supports @on_stop and on_stop(fn)."""
	return hook("on_app_stop", fn) if fn is not None else hook("on_app_stop")


def _sending_text(ctx):
	"""Extract (peer_id, text) from an on_message_sending context."""
	if isinstance(ctx, dict):
		msg = ctx.get("message")
		if isinstance(msg, dict):
			text = msg.get("text", msg.get("message", ""))
			peer = msg.get("peer_id", msg.get("peer", 0)) or 0
		else:
			text = getattr(msg, "text", "") if msg is not None else ""
			peer = getattr(msg, "peer_id", 0) if msg is not None else 0
			if not peer:
				peer = ctx.get("peer_id", ctx.get("peer", 0)) or 0
		return peer, text if isinstance(text, str) else ""
	msg = getattr(ctx, "message", None)
	text = getattr(msg, "text", "") if msg is not None else ""
	peer = getattr(msg, "peer_id", 0) if msg is not None else 0
	if not peer:
		peer = getattr(ctx, "peer_id", 0) or 0
	return peer, text if isinstance(text, str) else ""


def _cancel_sending(ctx):
	if isinstance(ctx, dict):
		ctx["cancel"] = True
	elif hasattr(ctx, "cancel"):
		ctx.cancel = True


def _rewrite_sending(ctx, text: str):
	if isinstance(ctx, dict):
		ctx["rewrite"] = True
		ctx["rewritten_text"] = text
		ctx["rewrittenText"] = text
	else:
		ctx.rewrite = True
		if hasattr(ctx, "rewrittenText"):
			ctx.rewrittenText = text
		if hasattr(ctx, "rewritten_text"):
			ctx.rewritten_text = text


def command(prefix, fn=None, *, aliases=(), cancel=True):
	"""Command-style reception: @command("/ping").

	The handler `fn(peer_id, text, args)` runs when an outgoing message
	starts with the prefix (first token, case-insensitive). Extra
	prefixes via aliases=("/p",). If the handler returns a string, the
	outgoing message is rewritten to it; if it returns False, the
	message is cancelled. Otherwise the original message is cancelled
	when cancel=True (default) so only the command result is sent.
	"""
	prefixes = [str(prefix or "").strip().lower()]
	prefixes += [str(a or "").strip().lower() for a in (aliases or ())]
	prefixes = [p for p in prefixes if p]

	def _register(f):
		@hook("on_message_sending")
		def _dispatch(ctx):
			peer, text = _sending_text(ctx)
			raw = text.strip()
			if not raw:
				return
			first = raw.split(None, 1)
			if not first or first[0].lower() not in prefixes:
				return
			args = raw[len(first[0]):].strip().split() if len(first) > 1 else []
			handler = f
			instance = _resolve_instance(f)
			if instance is not None:
				handler = getattr(instance, f.__name__, f)
			try:
				result = handler(peer, raw, args)
			except TypeError:
				try:
					result = f(peer, raw)
				except Exception as e:
					_log(f"command {prefixes[0]} error: {e}")
					return
			except Exception as e:
				_log(f"command {prefixes[0]} error: {e}")
				return
			if isinstance(result, str):
				_rewrite_sending(ctx, result)
			elif result is False:
				_cancel_sending(ctx)
			elif cancel:
				_cancel_sending(ctx)
		return f

	if fn is None:
		return _register
	return _register(fn)


_timers: list = []
# A timer whose owner cannot be identified. Kept as a value rather than None so
# "belongs to no plugin in particular" is distinguishable from "belongs to the
# plugin that asked", and only a global cancel stops these.
_Unowned = object()


class _TimerHandle:
	"""What add_timer returns, so a caller can cancel one timer by name."""

	def __init__(self, stop: threading.Event):
		self.stop = stop
		self.started = None

	def cancel(self) -> bool:
		self.stop.set()
		return True


def interval(seconds, fn=None):
	"""Periodic reception: @interval(60) runs fn() every N seconds.

	Daemon thread; best-effort (keeps running until app restart).
	"""
	try:
		period = max(1.0, float(seconds))
	except Exception:
		period = 60.0

	def _register(f):
		stop = threading.Event()

		def _loop():
			while not stop.wait(period):
				try:
					f()
				except Exception as e:
					_log(f"interval({period:g}s) error: {e}")

		thread = threading.Thread(target=_loop, daemon=True)
		_timers.append((f, thread, stop, _timer_owner(f)))
		thread.start()
		return f

	if fn is None:
		return _register
	return _register(fn)


def add_timer(fn, interval_s: float = 1.0, repeat: int = 1, delay_s=None):
	"""Call `fn` after a delay, optionally repeating. Returns a cancel handle.

	Mobile plugins schedule delayed work with add_timer rather than @interval,
	and the function did not exist, so any plugin that used it raised
	AttributeError while loading and never ran. Repeats default to once, which is
	what the mobile signature means.
	"""
	stop = threading.Event()
	handle = _TimerHandle(stop)

	def _loop():
		if delay_s:
			if stop.wait(max(0.0, float(delay_s))):
				return
		times = 1 if repeat is None else max(1, int(repeat))
		for _ in range(times):
			if stop.is_set():
				return
			try:
				fn()
			except Exception as e:
				_log(f"add_timer({interval_s}) error: {e}")
				return
			if times > 1 and stop.wait(max(0.0, float(interval_s))):
				return

	thread = threading.Thread(target=_loop, daemon=True)
	_timers.append((fn, thread, stop, _timer_owner(fn)))
	thread.start()
	handle.started = thread
	return handle


def _timer_owner(fn: Any) -> Any:
	"""Which plugin a timer belongs to.

	Recorded when the timer is armed, because afterwards there is nothing left
	to tell two plugins' loops apart: they all sit in one list holding plain
	function references, and working it out from the module the function was
	defined in gets it wrong - two plugins loaded from main.py are the same
	module, and a bound method's module is the class's, not the plugin's.
	"""
	self_obj = getattr(fn, "__self__", None)
	if self_obj is not None:
		return self_obj
	# A hook declared in a class body is still unbound when it is registered,
	# so the live instance has to be looked up. Failing that the timer counts
	# as unowned, and only a global cancel stops it.
	try:
		return _resolve_instance(fn)
	except Exception:
		return None


def remove_timer(handle) -> bool:
	"""Cancel a timer previously returned by add_timer."""
	if handle is None:
		return False
	stop = getattr(handle, "stop", None)
	if stop is None:
		return False
	stop.set()
	for entry in list(_timers):
		if entry[2] is stop:
			_timers.remove(entry)
	return True


def _entry_owner(entry, owner: Any) -> Any:
	"""The owner of a timer entry, or a value that matches nothing.

	Entries armed before ownership was recorded fall back to the old best-effort
	match, so a plugin loaded by an older path is still recognised.
	"""
	recorded = entry[3] if len(entry) > 3 else None
	if recorded is not None:
		return recorded
	if owner is None:
		return None
	fn = entry[0]
	if getattr(fn, "__self__", None) is owner:
		return owner
	try:
		if getattr(fn, "__module__", "") == getattr(type(owner), "__module__", ""):
			return owner
	except Exception:
		pass
	return _Unowned


def cancel_all_timers(owner: Any = None) -> int:
	"""Stop the background loops a plugin started.

	`owner` narrows it to one plugin's own timers. Without that argument the
	function stops everything, and that is how editing one plugin's settings
	used to silence every other plugin's background work: saving a setting
	called it, and the timers it stopped belonged to plugins that had done
	nothing. Pass the plugin to keep the blast radius to the plugin that
	actually changed.

	`release_instance` went through the unfiltered version while claiming to
	release only its own plugin, so switching any plugin off stopped every
	other plugin's loops. Ownership is recorded when a timer is armed, so this
	no longer has to be guessed.
	"""
	count = 0
	remaining = []
	for entry in list(_timers):
		if owner is not None and _entry_owner(entry, owner) is not owner:
			remaining.append(entry)
			continue
		try:
			entry[2].set()
			count += 1
		except Exception:
			pass
	_timers[:] = remaining
	return count


def timers_of(owner: Any) -> int:
	"""How many live timers belong to this plugin.

	The count used to be every timer on the system whatever the argument, so a
	plugin with one loop was reported as having as many as all of them put
	together - which made the health line that uses it useless for telling a
	busy plugin from a broken one.
	"""
	if owner is None:
		return len(_timers)
	return sum(
		1 for entry in _timers if _entry_owner(entry, owner) is owner)


def release_instance(instance: Any) -> int:
	"""Put a plugin's shim-side state away: its timers and its hooks.

	Called when a plugin is switched off. Without it a disabled plugin kept
	every timer it had armed, because nothing on the way out touched the lists
	they live in - muting stops hooks being called, it does not stop a repeating
	timer from waking up every second to find that it has been muted.

	Only this plugin's own timers are stopped. Stopping all of them is what
	this used to do, so switching one plugin off quietly ended every other
	plugin's background work.
	"""
	stopped = cancel_all_timers(instance)
	try:
		with _lock:
			_live_instances.discard(instance)
	except Exception:
		pass
	try:
		from .gaps import note_gap
		note_gap(f"released:{type(instance).__name__}")
	except Exception:
		pass
	return stopped


def find_class(class_name: str):
	"""Look up an Android class by name.

	Returns None when there is no such class, and records the name. It used to
	return a falsy stub, which is the worst of both answers: `if find_class(X):`
	was never true, so every guarded hook registration was skipped, while
	`if find_class(X) is None:` was never true either, so the plugins that guard
	the other way carried on with a placeholder and logged that they had hooked
	something. One plugin reached this from twenty-five places and hooked nothing
	without ever saying so.
	"""
	name = str(class_name or "")
	try:
		from .gaps import note_gap
		note_gap(f"find_class:{name}")
	except Exception:
		pass
	return None


def _is_inert(value: Any) -> bool:
	"""Whether a value came from a stand-in rather than from real state.

	Reading a field off a stub always succeeds: the placeholder invents an
	attribute for any name asked of it. So `getattr(...) is not None` proves
	nothing here, and a field that does not exist has to be recognised as such
	or the caller is handed a placeholder and believes it read something.
	"""
	if getattr(value, "__ayu_stub__", False):
		return True
	kind = type(value).__name__
	return kind in ("Placeholder", "_StubObject", "_JavaList", "_JavaClass")


def get_private_field(obj: Any, field_name: str):
	"""Read a field, answering None when there is nothing to read.

	A stub was returned instead, which made `if value is not None:` true for a
	value that did not exist - one plugin does exactly that and then int()s the
	result, so an absent field silently became 0 and the plugin carried on as
	though it had read something.
	"""
	try:
		value = getattr(obj, field_name, None)
		if value is not None and not _is_inert(value):
			return value
	except Exception:
		pass
	try:
		from .gaps import note_gap
		note_gap(f"get_private_field:{type(obj).__name__}.{field_name}")
	except Exception:
		pass
	return None


def set_private_field(obj: Any, field_name: str, new_value: Any) -> bool:
	"""Set a field, saying whether it was really set.

	Reported success unconditionally before. On a stub the assignment is
	discarded, so the answer was True for a write that never happened - worse than
	failing, because a plugin that checks the result concludes the value is in
	place and carries on. It is verified by reading the field back.
	"""
	try:
		setattr(obj, field_name, new_value)
	except Exception:
		return False
	try:
		return getattr(obj, field_name, None) == new_value
	except Exception:
		return False


def get_static_private_field(clazz: Any, field_name: str):
	try:
		value = getattr(clazz, field_name, None)
		if value is not None:
			return value
	except Exception:
		pass
	return _stub(f"{getattr(clazz, '__name__', clazz)}.{field_name}")


def set_static_private_field(clazz: Any, field_name: str, new_value: Any) -> bool:
	try:
		setattr(clazz, field_name, new_value)
		return True
	except Exception:
		return False


def call_method(obj: Any, method_name: str, *args, **kwargs):
	try:
		bound = getattr(obj, method_name, None)
		if callable(bound):
			return bound(*args, **kwargs)
	except Exception:
		pass
	return _stub(f"{type(obj).__name__}.{method_name}")


def get_method(obj: Any, method_name: str):
	try:
		bound = getattr(obj, method_name, None)
		if bound is not None:
			return bound
	except Exception:
		pass
	return _stub(f"{type(obj).__name__}.{method_name}")
