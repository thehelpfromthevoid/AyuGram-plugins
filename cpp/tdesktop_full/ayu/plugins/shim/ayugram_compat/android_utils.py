"""Desktop adaptation of mobile android_utils."""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

_prefs = {}


try:
    from ayu_api import toast as _toast, log as _log  # type: ignore

    def toast(text):
        _toast(str(text))

    def log(text):
        _log(str(text))

except Exception:  # test mode without native module
    def toast(text):
        print(f"[toast] {text}")

    def log(text):
        print(f"[log] {text}")


def get_pref(key, default=None):
    try:
        from ayu_api import get_pref as _get
        return _get(str(key), default)
    except Exception:
        return _prefs.get(key, default)


def set_pref(key, value):
    try:
        from ayu_api import set_pref as _set
        _set(str(key), value)
    except Exception:
        _prefs[key] = value


def require_android_context(*args, **kwargs):
    return None


def get_context(*args, **kwargs):
    return None


def get_activity(*args, **kwargs):
    return None


def run_on_ui_thread(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Exception as e:
        log(f"run_on_ui_thread: {e}")


def run_on_background_thread(fn, *args, **kwargs):
    def _runner():
        try:
            fn(*args, **kwargs)
        except Exception as e:
            log(f"run_on_background_thread: {e}")

    threading.Thread(target=_runner, daemon=True).start()


def show_dialog(*args, **kwargs):
    title = kwargs.get("title") or (args[0] if args else "")
    message = kwargs.get("message") or (args[1] if len(args) > 1 else "")
    toast(f"{title}: {message}" if title else str(message))


def copy_to_clipboard(text):
    try:
        from ayu_api import toast as _t
        _t(f"[clipboard] {str(text)[:120]}")
    except Exception:
        pass


class OnClickListener:
    def __init__(self, fn=None):
        self._fn = fn

    def onClick(self, view=None):
        if self._fn:
            self._fn(view)


class Handler:
    def __init__(self, fn: Optional[Callable] = None):
        self._fn = fn

    def post(self, fn=None, delay_ms: int = 0):
        target = fn or self._fn
        if target is None:
            return

        def _runner():
            if delay_ms and delay_ms > 0:
                import time
                time.sleep(delay_ms / 1000.0)
            try:
                target()
            except Exception as e:
                log(f"Handler.post: {e}")

        threading.Thread(target=_runner, daemon=True).start()
