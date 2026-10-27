"""Desktop port of mobile `ui.alert.AlertDialogBuilder`.

Android dialogs do not exist on Desktop: the builder records every
call (chainable, like the original) and `show()` surfaces title and
message as a toast. Button/item listeners are stored, never invoked.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def _toast(text: str) -> None:
    try:
        from ayu_api import toast as _t
        _t(str(text))
    except Exception:
        print(f"[alert] {text}")


class AlertDialogBuilder:
    ALERT_TYPE_MESSAGE = 0
    ALERT_TYPE_LOADING = 1
    ALERT_TYPE_SPINNER = 2

    BUTTON_POSITIVE = -1
    BUTTON_NEGATIVE = -2
    BUTTON_NEUTRAL = -3

    def __init__(self, context: Any = None, progress_style: int = 0,
                 resources_provider: Any = None):
        self.context = context
        self.progress_style = progress_style
        self.resources_provider = resources_provider
        self.title = ""
        self.message = ""
        self.items: List[str] = []
        self.buttons: Dict[int, Any] = {}
        self.cancelable = True
        self.progress = 0
        self._shown = False

    def set_title(self, title: str):
        self.title = str(title)
        return self

    def set_message(self, message: str):
        self.message = str(message)
        return self

    def set_message_text_view_clickable(self, clickable: bool):
        return self

    def set_view(self, view: Any, height: int = -2):
        return self

    def set_items(self, items: List[str],
                  listener: Optional[Callable] = None,
                  icons: Optional[List[int]] = None):
        self.items = list(items or [])
        if listener is not None:
            self.buttons["items_listener"] = listener
        return self

    def set_positive_button(self, text: str, listener: Optional[Callable] = None):
        self.buttons[self.BUTTON_POSITIVE] = (text, listener)
        return self

    def set_negative_button(self, text: str, listener: Optional[Callable] = None):
        self.buttons[self.BUTTON_NEGATIVE] = (text, listener)
        return self

    def set_neutral_button(self, text: str, listener: Optional[Callable] = None):
        self.buttons[self.BUTTON_NEUTRAL] = (text, listener)
        return self

    def make_button_red(self, button_type: int):
        return self

    def set_on_back_button_listener(self, listener: Optional[Callable] = None):
        return self

    def set_on_dismiss_listener(self, listener: Optional[Callable] = None):
        self._on_dismiss = listener
        return self

    def set_on_cancel_listener(self, listener: Optional[Callable] = None):
        return self

    def set_top_image(self, res_id: int, background_color: int):
        return self

    def set_top_drawable(self, drawable: Any, background_color: int):
        return self

    def set_top_animation(self, res_id: int, size: int, auto_repeat: bool,
                          background_color: int,
                          layer_colors: Optional[Dict[str, int]] = None):
        return self

    def set_dim_enabled(self, enabled: bool):
        return self

    def set_dialog_button_color_key(self, theme_key: int):
        return self

    def set_blurred_background(self, blur: bool, blur_behind_if_possible: bool = True):
        return self

    def set_cancelable(self, cancelable: bool):
        self.cancelable = bool(cancelable)
        return self

    def set_canceled_on_touch_outside(self, cancel: bool):
        return self

    def set_progress(self, progress: int):
        try:
            self.progress = int(progress)
        except Exception:
            pass
        return self

    def create(self):
        return self

    def show(self):
        self._shown = True
        body = self.message or (
            ("; ".join(self.items)) if self.items else "")
        _toast(f"{self.title}: {body}" if self.title else body)
        return self

    def dismiss(self):
        self._shown = False
        on_dismiss = getattr(self, "_on_dismiss", None)
        if callable(on_dismiss):
            try:
                on_dismiss(self)
            except Exception:
                pass

    def get_dialog(self):
        return None

    def get_button(self, button_type: int):
        return None


__all__ = ["AlertDialogBuilder"]
