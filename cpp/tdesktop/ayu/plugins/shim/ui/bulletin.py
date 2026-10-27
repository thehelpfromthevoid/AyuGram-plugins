"""Desktop port of mobile `ui.bulletin.BulletinHelper`.

Bulletins are bottom-screen Android notifications; on Desktop every
`show_*` maps to a toast so plugins keep working and stay visible.
"""
from __future__ import annotations

from typing import Any, Callable, Optional


def _toast(text: str) -> None:
    try:
        from ayu_api import toast as _t
        _t(str(text))
    except Exception:
        print(f"[bulletin] {text}")


def _log(text: str) -> None:
    try:
        from ayu_api import log as _l
        _l(str(text))
    except Exception:
        print(f"[bulletin] {text}")


class BulletinHelper:
    DURATION_SHORT = 1500
    DURATION_LONG = 2750
    DURATION_PROLONG = 5000

    @staticmethod
    def show_info(message: str, fragment: Any = None) -> None:
        _toast(str(message))

    @staticmethod
    def show_error(message: str, fragment: Any = None) -> None:
        _toast(str(message))

    @staticmethod
    def show_success(message: str, fragment: Any = None) -> None:
        _toast(str(message))

    @staticmethod
    def show_simple(text: str, icon_res_id: int = 0, fragment: Any = None) -> None:
        _toast(str(text))

    @staticmethod
    def show_two_line(title: str, subtitle: str, icon_res_id: int = 0,
                      fragment: Any = None) -> None:
        _toast(f"{title}: {subtitle}" if subtitle else str(title))

    @staticmethod
    def show_with_button(text: str, icon_res_id: int = 0, button_text: str = "",
                         on_click: Optional[Callable[[], None]] = None,
                         fragment: Any = None,
                         duration: int = DURATION_PROLONG) -> None:
        _toast(str(text))
        if on_click is not None:
            _log("BulletinHelper: desktop has no bulletin buttons; on_click ignored")

    @staticmethod
    def show_undo(text: str, on_undo: Optional[Callable[[], None]] = None,
                  on_action: Optional[Callable[[], None]] = None,
                  subtitle: Optional[str] = None, fragment: Any = None) -> None:
        _toast(f"{text}: {subtitle}" if subtitle else str(text))
        if on_undo is not None or on_action is not None:
            _log("BulletinHelper: desktop has no undo UI; callbacks ignored")

    @staticmethod
    def show_copied_to_clipboard(message: Optional[str] = None,
                                 fragment: Any = None) -> None:
        _toast(str(message) if message else "Text copied to clipboard")

    @staticmethod
    def show_link_copied(is_private_link_info: bool = False,
                         fragment: Any = None) -> None:
        _toast("Link copied")

    @staticmethod
    def show_file_saved_to_gallery(is_video: bool = False, amount: int = 1,
                                   fragment: Any = None) -> None:
        kind = "Video" if is_video else "Photo"
        _toast(f"{kind} saved to gallery" if amount <= 1
               else f"{amount} {kind.lower()}s saved to gallery")

    @staticmethod
    def show_file_saved_to_downloads(file_type_enum_name: str = "UNKNOWN",
                                     amount: int = 1,
                                     fragment: Any = None) -> None:
        _toast(f"File saved to downloads ({file_type_enum_name})")


__all__ = ["BulletinHelper"]
