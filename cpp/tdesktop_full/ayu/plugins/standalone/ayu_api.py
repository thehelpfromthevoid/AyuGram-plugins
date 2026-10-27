"""Pure-Python ayu_api matching the desktop bridge surface."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

_hooks: dict[str, list[Callable]] = {}
_toast_sink: Callable[[str], None] | None = None
_log_sink: Callable[[str], None] | None = None
_prefs: dict[str, Any] = {}

KNOWN_HOOKS = {
    "on_message",
    "on_message_sending",
    "on_message_deleted",
    "on_chat_opened",
    "on_app_start",
    "on_app_stop",
    "on_setting_changed",
}


@dataclass
class Message:
    msg_id: int = 0
    peer_id: int = 0
    from_id: int = 0
    text: str = ""
    date: int = 0
    out: bool = False
    post: bool = False


@dataclass
class Chat:
    peer_id: int = 0
    title: str = ""
    type: int = 0


@dataclass
class MessageSendingContext:
    message: Message = field(default_factory=Message)
    cancel: bool = False
    rewrittenText: str = ""
    rewrite: bool = False


@dataclass
class MessageDeletedContext:
    peer_id: int = 0
    msgIds: list[int] = field(default_factory=list)


def set_sinks(
    toast: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> None:
    global _toast_sink, _log_sink
    _toast_sink = toast
    _log_sink = log


def clear_hooks() -> None:
    _hooks.clear()


def _normalize_hook_name(name: str):
    """Map mobile hook spellings to Desktop names; None = unsupported."""
    try:
        from ayugram_compat.hook_utils import _desktop_hook_name as _map
        mapped = _map(name)
        return mapped or None
    except Exception:
        return name if name in KNOWN_HOOKS else None


def hook(name: str, fn: Callable | None = None):
    """Register a hook. Supports @hook('name') and hook('name', fn)."""
    mapped = _normalize_hook_name(name)
    if mapped is None:
        log(f"ignoring unsupported hook '{name}'")
        def _identity(f: Callable):
            return f
        if fn is None:
            return _identity
        return fn
    name = mapped

    def register(f: Callable):
        _hooks.setdefault(name, []).append(f)
        return f

    if fn is None:
        return register
    return register(fn)


def toast(text: str) -> None:
    s = str(text)
    if _toast_sink:
        _toast_sink(s)
    else:
        print(f"[toast] {s}")


def log(text: str) -> None:
    s = str(text)
    if _log_sink:
        _log_sink(s)
    else:
        print(f"[log] {s}")


def get_pref(key: str, default=None):
    return _prefs.get(key, default)


def set_pref(key: str, value) -> None:
    _prefs[key] = value


def __getattr__(name: str):
    if name in ("command", "interval", "on_start", "on_stop",
                "cancel_all_timers"):
        from ayugram_compat.hook_utils import (
            command, interval, on_start, on_stop, cancel_all_timers,
        )
        return {"command": command, "interval": interval,
                "on_start": on_start, "on_stop": on_stop,
                "cancel_all_timers": cancel_all_timers}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def emit(name: str, *args, **kwargs):
    results = []
    for fn in list(_hooks.get(name, [])):
        try:
            results.append(fn(*args, **kwargs))
        except Exception as e:
            log(f"hook {name} error: {e}")
    return results


def emit_message_sending(ctx: MessageSendingContext) -> bool:
    for fn in list(_hooks.get("on_message_sending", [])):
        try:
            fn(ctx)
            if ctx.cancel:
                return False
        except Exception as e:
            log(f"hook on_message_sending error: {e}")
    return True
