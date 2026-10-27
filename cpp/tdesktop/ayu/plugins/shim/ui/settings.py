"""Desktop port of mobile `ui.settings` (exteraGram plugin settings rows).

Real dataclasses with the documented fields, so `create_settings()`
works and the host can seed defaults. No Android UI is built on
Desktop; rows are data only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

try:
    from ayugram_compat.mobile_stubs import stub as _stub
except Exception:  # pragma: no cover
    _stub = None  # type: ignore


@dataclass
class Header:
    text: str = ""


@dataclass
class Divider:
    text: str = ""


@dataclass
class Switch:
    key: str = ""
    text: str = ""
    default: bool = False
    subtext: str = ""
    icon: str = ""
    on_change: Optional[Callable] = None
    on_long_click: Optional[Callable] = None
    link_alias: str = ""


@dataclass
class Selector:
    key: str = ""
    text: str = ""
    default: int = 0
    items: List[str] = field(default_factory=list)
    icon: str = ""
    on_change: Optional[Callable] = None
    on_long_click: Optional[Callable] = None
    link_alias: str = ""


@dataclass
class Input:
    key: str = ""
    text: str = ""
    default: str = ""
    subtext: str = ""
    icon: str = ""
    on_change: Optional[Callable] = None
    on_long_click: Optional[Callable] = None
    link_alias: str = ""


@dataclass
class Text:
    text: str = ""
    subtext: str = ""
    icon: str = ""
    accent: bool = False
    red: bool = False
    on_click: Optional[Callable] = None
    on_long_click: Optional[Callable] = None
    create_sub_fragment: Optional[Callable] = None
    link_alias: str = ""


@dataclass
class EditText:
    key: str = ""
    hint: str = ""
    default: str = ""
    multiline: bool = False
    max_length: int = 0
    mask: str = ""
    on_change: Optional[Callable] = None


@dataclass
class Custom:
    item: Any = None
    view: Any = None
    factory: Any = None
    factory_args: Any = None
    on_click: Optional[Callable] = None
    on_long_click: Optional[Callable] = None
    create_sub_fragment: Optional[Callable] = None
    link_alias: str = ""


class _FactoryInstance:
    """Mimics the Java factory handle (`.instance.java`)."""

    def __init__(self, factory):
        self._factory = factory

    def __getattr__(self, name: str):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        if _stub is None:
            return None
        return _stub("CustomSetting.Factory." + name)


class SimpleSettingFactory:
    """Desktop adaptation: stores callbacks, builds no Android views."""

    def __init__(
            self,
            create_view=None,
            bind_view=None,
            create_item=None,
            on_click=None,
            on_long_click=None,
            attached_view=None,
            equals=None,
            content_equals=None,
            is_clickable=True,
            is_shadow=False):
        self.create_view = create_view
        self.bind_view = bind_view
        self.create_item = create_item
        self.on_click = on_click
        self.on_long_click = on_long_click
        self.attached_view = attached_view
        self.equals = equals
        self.content_equals = content_equals
        self.is_clickable = is_clickable
        self.is_shadow = is_shadow
        self.instance = _FactoryInstance(self)


__all__ = [
    "Header", "Divider", "Switch", "Selector", "Input", "Text",
    "EditText", "Custom", "SimpleSettingFactory",
]
