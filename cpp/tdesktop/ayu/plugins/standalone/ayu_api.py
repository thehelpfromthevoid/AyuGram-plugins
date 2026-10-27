"""Pure-Python ayu_api matching the desktop bridge surface."""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable

# Адресата тоста и стену хуков читают хуки, приходящие из рабочего
# потока, а меняют их при старте и при перезагрузке. Под GIL присваивание
# ссылки атомарно, но пара «проверить наличие - вызвать» состоит из двух
# шагов, и между ними другой поток успевает подменить значение: тост
# уходил в приёмник от прошлой смены либо падал на NoneType - в
# зависимости от того, кто выиграл гонку.
_state_lock = RLock()

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
    with _state_lock:
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
    # Приёмник читается под замком и вызывается уже вне его: вызов чужого
    # кода под нашей блокировкой означал бы, что тот код может вызвать
    # set_sinks и повесить сам себя.
    with _state_lock:
        sink = _toast_sink
    if sink:
        sink(s)
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


def send_text(
		peer_id: Any,
		text: str,
		*args,
		account: int = -1,
		**kwargs) -> int:
	"""Only the native bridge can reach Telegram; say so instead of vanishing.

	This module is what the CLI's compatibility check imports. It used to have
	no `send_text` at all, so a plugin that reports which bridge features it
	found - neural_typing prints exactly that and switches its effect off
	without them - was told the app lacks sending. The app does not lack it, so
	the report sent the search for the fault into the wrong place entirely.

	Номер аккаунта назван явно, а не спрятан в **kwargs: иначе плагин,
	проверяющий подпись, видел бы её без номера и решил, что отправлять
	можно только основным аккаунтом.
	"""
	log(f"send_text is only available inside the app (peer={peer_id}); ignored")
	return 0


def edit_message(
		peer_id: Any,
		msg_id: Any,
		text: str,
		*args,
		account: int = -1,
		**kwargs) -> bool:
	log(f"edit_message is only available inside the app (peer={peer_id}); ignored")
	return False


def send_file(
		peer_id: Any,
		path: Any,
		*args,
		account: int = -1,
		**kwargs) -> int:
	log(f"send_file is only available inside the app (peer={peer_id}); ignored")
	return 0


def edit_message_text(*args, **kwargs) -> bool:
	return edit_message(*args, **kwargs)


def self_peer_id(account: int = -1) -> int:
	"""Собственный peer id аккаунта. Вне клиента он неизвестен.

	Номер аккаунта добавлен необязательным: старый плагин зовёт функцию без
	аргументов и должен получать то же, что и раньше, а не исключение.
	"""
	return 0


def account_count() -> int:
	"""Сколько аккаунтов вошло. Вне клиента это ноль."""
	return 0


def is_valid_account(account: int) -> bool:
	"""Вне клиента ни один аккаунт не считается вошедшим."""
	return False


class _Event:
	"""Общая форма события: номер аккаунта и то, что плагин считывает.

	Раньше эти имена просились через __getattr__ и возвращали функцию,
	которая ничего не делает: плагин получал вместо сообщения
	присваиваемую функцию и молчал. Теперь структура настоящая, и номер
	аккаунта на ней есть, как и в клиенте.
	"""

	__slots__ = ("account",)

	def __init__(self, account: int = 0):
		self.account = account

	def __repr__(self) -> str:
		return f"{type(self).__name__}(account={self.account})"


class Message(_Event):
	__slots__ = ("msg_id", "peer_id", "from_id", "text", "date", "out")

	def __init__(self, account: int = 0):
		super().__init__(account)
		self.msg_id = 0
		self.peer_id = 0
		self.from_id = 0
		self.text = ""
		self.date = 0
		self.out = False


class Chat(_Event):
	__slots__ = ("peer_id", "title")

	def __init__(self, account: int = 0):
		super().__init__(account)
		self.peer_id = 0
		self.title = ""


class Typing(_Event):
	__slots__ = ("peer_id", "user_id", "action", "top_msg_id")

	def __init__(self, account: int = 0):
		super().__init__(account)
		self.peer_id = 0
		self.user_id = 0
		self.action = ""
		self.top_msg_id = 0


_last_chat = None


def get_current_chat():
    """The chat that is open now, or None.

    Answered from the last on_chat_opened delivery so the CLI's compatibility
    check sees the same surface the app offers. Without it here a plugin that
    calls this loads fine in the app but is reported as reaching for something
    Desktop does not have, which sends the search in the wrong direction.
    """
    return _last_chat


def emit_chat_opened(chat) -> None:
    global _last_chat
    _last_chat = chat
    emit("on_chat_opened", chat)


def __getattr__(name: str):
    if name in ("command", "interval", "on_start", "on_stop",
                "cancel_all_timers"):
        from ayugram_compat.hook_utils import (
            command, interval, on_start, on_stop, cancel_all_timers,
        )
        return {"command": command, "interval": interval,
                "on_start": on_start, "on_stop": on_stop,
                "cancel_all_timers": cancel_all_timers}[name]
    if name in ("add_timer", "remove_timer", "hook_utils"):
        import ayugram_compat.hook_utils as _hu
        return getattr(_hu, name, None)
    # Anything else is a mobile feature Desktop does not have. Answering with a
    # no-op keeps the plugin loadable: the alternative is an AttributeError from
    # on_plugin_load, and a plugin that fails to load reports nothing at all.
    # The name is recorded so the compatibility report can list it instead of
    # the plugin just quietly doing less than it was written to.
    #
    # Names in double underscores are skipped: Python itself probes __path__
    # and friends on every module, and those probes are not the plugin
    # reaching for a missing feature.
    if not name.startswith("__"):
        pass
    else:
        def _missing(*args, **kwargs):
            return None

        return _missing
    try:
        # Имя функции именно note_gap: было record, которого в gaps.py нет,
        # и except молча глотал ошибку, поэтому эта половина отчёта не
        # записывалась вообще.
        from ayugram_compat.gaps import note_gap as _record
        _record(f"ayu_api.{name}")
    except Exception:
        pass

    def _missing(*args, **kwargs):
        return None

    return _missing


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
