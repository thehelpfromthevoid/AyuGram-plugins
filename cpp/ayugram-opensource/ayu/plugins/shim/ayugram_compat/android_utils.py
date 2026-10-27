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
	"""Hand the work to the main thread instead of running it here.

	It used to call fn inline, so anything a plugin declared as UI work ran on
	whichever plugin thread called it, holding the GIL while it touched the
	interface from a thread that does not own it. That froze the window.

	Arguments are carried across, and a marshal that raises runs the work here
	rather than dropping it: several plugins pass a delay as a second argument
	and it used to be discarded, and a plugin treats this as fire-and-forget, so
	a lost call is a feature that silently never happens.
	"""
	callback = fn
	if args or kwargs:
		def callback(*_ignored):  # noqa: F811 - closure over the real call
			return fn(*args, **kwargs)

	bridge = globals().get("_ayu_bridge")
	marshal = getattr(bridge, "run_on_main_thread", None)
	if marshal is None:
		# No bridge (a bare interpreter, or an older app): run inline rather
		# than drop the call, since Android plugins treat this as
		# fire-and-forget.
		try:
			callback()
		except Exception as e:
			log(f"run_on_ui_thread: {e}")
		return
	try:
		marshal(callback)
		return
	except Exception as e:
		log(f"run_on_ui_thread: {e}; running it here instead")
	try:
		callback()
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
    """Put text on the clipboard, saying whether it worked.

    It used to show a toast with the text and copy nothing at all, and a plugin
    that uses this to hand a price or a link to the user went on to report
    success - and to tell them it had been copied. A function whose name is an
    action must not be a no-op that returns as though it were one.
    """
    body = str(text or "")
    if not body:
        return False
    try:
        from ayu_api import copy_to_clipboard as _copy
        return bool(_copy(body))
    except ImportError:
        pass
    except Exception as e:
        _log(f"copy_to_clipboard failed: {e}")
        return False
    try:
        # Standalone and CLI runs have no clipboard to write to. Say so rather
        # than reporting a copy that did not happen.
        _log(f"clipboard is unavailable here, so {body[:80]!r} was not copied")
    except Exception:
        pass
    return False


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
