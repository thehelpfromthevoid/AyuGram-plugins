"""Desktop BasePlugin API compatible with mobile exteraGram / AyuGram plugins."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional


class AppEvent(Enum):
    START = auto()
    STOP = auto()
    PAUSE = auto()
    RESUME = auto()


class HookStrategy(Enum):
    DEFAULT = auto()
    CANCEL = auto()
    MODIFY = auto()
    MODIFY_FINAL = auto()


@dataclass
class HookResult:
    strategy: HookStrategy = HookStrategy.DEFAULT
    request: Any = None
    response: Any = None
    update: Any = None
    updates: Any = None
    params: Any = None


class MenuItemType(Enum):
    MESSAGE_CONTEXT_MENU = auto()
    DRAWER_MENU = auto()
    MAIN_MENU = auto()
    CHAT_ACTION_MENU = auto()
    PROFILE_ACTION_MENU = auto()


@dataclass
class MenuItemData:
    menu_type: MenuItemType
    text: str
    on_click: Callable[[Dict[str, Any]], None]
    item_id: Optional[str] = None
    icon: Optional[str] = None
    subtext: Optional[str] = None
    condition: Optional[str] = None
    priority: int = 0


class MethodHook:
    def before_hooked_method(self, param):
        pass

    def after_hooked_method(self, param):
        pass


class MethodReplacement:
    def replace_hooked_method(self, param):
        return None


@dataclass
class Plugin:
    id: str = ""
    name: str = ""
    description: str = ""
    author: str = ""
    version: str = "1.0"
    min_version: str = ""
    requirements: list = field(default_factory=list)

    def on_load(self):
        pass

    def on_unload(self):
        pass

    def on_message(self, msg):
        pass


def _toast(text: str) -> None:
    try:
        from ayu_api import toast as _t
        _t(str(text))
    except Exception:
        print(f"[toast] {text}")


def _log(text: str) -> None:
    try:
        from ayu_api import log as _l
        _l(str(text))
    except Exception:
        print(f"[log] {text}")


class SendParams:
    """Outgoing-message params with mobile-style attribute access.

    Mobile plugins read `params.message` / `params.peer` /
    `params.replyToMsg`; dict-style `params.get("message")` keeps working
    too. Desktop has no reply threading: replyToMsg/replyToTopMsg stay None.
    """

    def __init__(
            self,
            message: Any = "",
            peer: Any = 0,
            replyToMsg: Any = None,
            replyToTopMsg: Any = None,
            _ctx: Any = None) -> None:
        text = message if isinstance(message, str) else ""
        self.message = text
        self.text = text
        try:
            self.peer = int(peer or 0)
        except Exception:
            self.peer = 0
        self.peer_id = self.peer
        self.replyToMsg = replyToMsg
        self.replyToTopMsg = replyToTopMsg
        self._ctx = _ctx

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and hasattr(self, key)


class BasePlugin:
    """Mobile-compatible plugin base. Desktop maps send hooks to ayu_api."""

    def __init__(self):
        self._settings: Dict[str, Any] = {}
        self._menu_items: Dict[str, MenuItemData] = {}
        self._menu_seq = 0
        self._hooks: List[str] = []
        self.enabled = True

    def on_plugin_load(self):
        pass

    def on_plugin_unload(self):
        pass

    def on_app_event(self, event_type: AppEvent):
        pass

    def create_settings(self) -> List[Any]:
        return []

    def log(self, text: Any) -> None:
        _log(str(text))

    def toast(self, text: Any) -> None:
        _toast(str(text))

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    def set_setting(
            self,
            key: str,
            value: Any,
            reload_settings: bool = False) -> None:
        self._settings[key] = value

    def export_settings(self) -> Dict[str, Any]:
        return dict(self._settings)

    def import_settings(
            self,
            settings: Dict[str, Any],
            reload_settings: bool = True) -> None:
        if isinstance(settings, dict):
            self._settings.update(settings)

    def add_menu_item(self, data: MenuItemData) -> str:
        self._menu_seq += 1
        item_id = data.item_id or f"menu_{self._menu_seq}"
        self._menu_items[item_id] = data
        self.log(
            f"Desktop: menu item '{data.text}' registered "
            f"(UI menus are limited on Desktop)")
        return item_id

    def remove_menu_item(self, item_id: str) -> None:
        self._menu_items.pop(item_id, None)

    def add_hook(
            self,
            name: str,
            match_substring: bool = False,
            priority: int = 0) -> None:
        self._hooks.append(name)
        self.log(
            f"Desktop: TL/Java hook '{name}' is not available; "
            f"use Desktop ayu_api hooks instead")

    def add_on_send_message_hook(self, priority: int = 0) -> None:
        try:
            from ayu_api import hook as _hook
        except Exception as e:
            self.log(f"add_on_send_message_hook unavailable: {e}")
            return

        plugin = self

        def _extract_text(src):
            if src is None:
                return None
            if isinstance(src, str):
                return src
            if isinstance(src, dict):
                value = src.get("message", src.get("text"))
                if isinstance(value, str):
                    return value
                if isinstance(value, dict):
                    nested_text = value.get("text", value.get("message", ""))
                    if isinstance(nested_text, str):
                        return nested_text
                if value is not None and not isinstance(value, (str, bytes)):
                    nested = getattr(value, "text", None)
                    if isinstance(nested, str):
                        return nested
                return None
            value = getattr(src, "text", None)
            if isinstance(value, str):
                return value
            message = getattr(src, "message", None)
            if isinstance(message, str):
                return message
            if message is not None:
                nested = getattr(message, "text", None)
                if isinstance(nested, str):
                    return nested
            return None

        def _handler(ctx):
            try:
                params = ctx
                if not isinstance(ctx, SendParams):
                    text = _extract_text(ctx)
                    peer = 0
                    msg = ctx.get("message") if isinstance(ctx, dict) else getattr(ctx, "message", None)
                    if msg is not None and not isinstance(msg, str):
                        if isinstance(msg, dict):
                            try:
                                peer = int(msg.get("peer_id", msg.get("peer", 0)) or 0)
                            except Exception:
                                peer = 0
                        else:
                            try:
                                peer = int(getattr(msg, "peer_id", 0) or 0)
                            except Exception:
                                peer = 0
                    elif isinstance(ctx, dict):
                        try:
                            peer = int(ctx.get("peer", ctx.get("peer_id", 0)) or 0)
                        except Exception:
                            peer = 0
                    elif hasattr(ctx, "peer_id"):
                        try:
                            peer = int(getattr(ctx, "peer_id", 0) or 0)
                        except Exception:
                            peer = 0
                    params = SendParams(message=text or "", peer=peer, _ctx=ctx)
                result = plugin.on_send_message_hook(0, params)
            except Exception as e:
                plugin.log(f"on_send_message_hook error: {e}")
                return
            if result is None:
                return
            strategy = getattr(result, "strategy", HookStrategy.DEFAULT)
            out_params = getattr(result, "params", None)
            if strategy == HookStrategy.CANCEL:
                if isinstance(ctx, dict):
                    ctx["cancel"] = True
                elif hasattr(ctx, "cancel"):
                    ctx.cancel = True
                return
            if strategy in (HookStrategy.MODIFY, HookStrategy.MODIFY_FINAL):
                src = out_params if out_params is not None else params
                if isinstance(src, SendParams):
                    text = src.message
                else:
                    text = _extract_text(src)
                if isinstance(text, str):
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

        _hook("on_message_sending", _handler)

    def on_send_message_hook(self, account: int, params: Any) -> HookResult:
        return HookResult()

    def pre_request_hook(
            self,
            request_name: str,
            account: int,
            request: Any) -> HookResult:
        return HookResult()

    def post_request_hook(
            self,
            request_name: str,
            account: int,
            response: Any,
            error: Any) -> HookResult:
        return HookResult()

    def on_update_hook(
            self,
            update_name: str,
            account: int,
            update: Any) -> HookResult:
        return HookResult()

    def on_updates_hook(
            self,
            container_name: str,
            account: int,
            updates: Any) -> HookResult:
        return HookResult()

    def hook_method(self, *args, **kwargs):
        raise NotImplementedError(
            "Android-only: Xposed/Java method hooking is not available "
            "on Desktop AyuGram")

    def unhook_method(self, *args, **kwargs):
        pass

    def hook_all_methods(self, *args, **kwargs) -> list:
        self.log(
            "Desktop: bulk Java method hooking is not available; "
            "nothing hooked, plugin keeps running")
        return []

    def client(self, account: int = 0):
        raise NotImplementedError(
            "Android-only: Telegram Java client bridge is not available "
            "on Desktop AyuGram")


def activate_base_plugins(module_globals: Dict[str, Any]) -> Optional[BasePlugin]:
    """Instantiate the first BasePlugin subclass and call on_plugin_load."""
    existing = module_globals.get("_ayu_plugin_instance")
    if isinstance(existing, BasePlugin):
        return existing
    chosen = None
    for value in module_globals.values():
        if not isinstance(value, type):
            continue
        if value is BasePlugin or not issubclass(value, BasePlugin):
            continue
        chosen = value
        break
    if chosen is None:
        return None
    instance = chosen()
    module_globals["_ayu_plugin_instance"] = instance
    instance.on_plugin_load()
    try:
        items = instance.create_settings() or []
    except Exception:
        items = []
    for item in items:
        try:
            key = getattr(item, "key", "")
            if key and key not in instance._settings:
                default = getattr(item, "default", None)
                if default is not None:
                    instance._settings[key] = default
        except Exception:
            pass
    return instance
