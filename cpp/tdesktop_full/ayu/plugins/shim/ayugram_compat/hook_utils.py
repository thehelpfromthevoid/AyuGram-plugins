"""Desktop compat for mobile Extera/AyuGram hook_utils.

Mobile plugins use either:
  from hook_utils import on_hook, find_class, ...
  from ayugram_compat.hook_utils import hook

Hook names are normalized to the Desktop surface; pure Java/TL hooks
are accepted and ignored with a log line instead of killing the load.
Reflection helpers return chainable no-op stubs so import-time and
load-time code survives without a Java runtime.

Desktop conveniences (also importable from ayu_api):
  @command("/ping") — turn an outgoing message prefix into an action.
  @interval(60)     — run a function every N seconds (daemon thread).
  @on_start / @on_stop — aliases for app lifecycle hooks.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from .mobile_stubs import stub as _stub

try:
	from ayu_api import hook as _native_hook
except Exception:  # pragma: no cover - pure-python test mode
	_native_hook = None
	_registry = {}


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
	}
	if low in aliases:
		return aliases[low]
	if "send" in low and "message" in low:
		return "on_message_sending"
	if "delete" in low and "message" in low:
		return "on_message_deleted"
	if "chat" in low and "open" in low:
		return "on_chat_opened"
	if low.startswith("tl_") or "." in raw:
		return ""
	return raw


def hook(name, fn=None):
	"""Supports @hook('name') and hook('name', fn). Desktop maps to ayu_api.hook."""
	mapped = _desktop_hook_name(name)

	def _register(f):
		if not mapped:
			_log(f"Desktop: unsupported mobile hook '{name}' ignored")
			return f
		if _native_hook is not None:
			_native_hook(mapped, f)
		else:
			_registry.setdefault(mapped, []).append(f)
		return f

	if fn is None:
		return _register
	return _register(fn)


def on_hook(name: str):
	"""Mobile alias: @on_hook('TL_...') — maps known names, ignores pure Java/TL."""
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
			try:
				result = f(peer, raw, args)
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
		_timers.append((f, thread, stop))
		thread.start()
		return f

	if fn is None:
		return _register
	return _register(fn)


def cancel_all_timers() -> int:
	"""Stop every @interval loop started in this process."""
	count = 0
	while _timers:
		_, _, stop = _timers.pop()
		try:
			stop.set()
			count += 1
		except Exception:
			pass
	return count


def find_class(class_name: str):
	"""Android Java reflection — no runtime on Desktop; chainable stub."""
	return _stub(str(class_name or "java.lang.Class"))


def get_private_field(obj: Any, field_name: str):
	try:
		value = getattr(obj, field_name, None)
		if value is not None:
			return value
	except Exception:
		pass
	return _stub(f"{type(obj).__name__}.{field_name}")


def set_private_field(obj: Any, field_name: str, new_value: Any) -> bool:
	try:
		setattr(obj, field_name, new_value)
		return True
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
