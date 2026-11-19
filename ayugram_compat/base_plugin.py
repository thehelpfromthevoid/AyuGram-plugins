"""Desktop BasePlugin API compatible with mobile exteraGram / AyuGram plugins.

AyuGram plugin host for Desktop, (c) @CE0fbi
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

from .gaps import note_gap


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


def _split_account(mapped: str, args: tuple) -> tuple[int, tuple]:
	"""(account, payload) из аргументов события.

	Сигнатуры расходятся, и раньше мост брал первый аргумент за аккаунт всегда.

	- on_message клиент зовёт как (msg): int(msg) падал на каждом входящем, и
	  ни один плагин на TL-хуках апдейтов не получал.
	- on_typing приходит как (account, event).
	- on_message_deleted приходит как (peerId, msgIds), и первый аргумент -
	  это peer_id, а не аккаунт, поэтому его нельзя отбрасывать.

	Контейнерные подписки отдают (account, message), поэтому там первый
	целый аргумент - это аккаунт.
	"""
	# Форма (account, event) проверяется первой: если номер стоят отдельным
	# первым аргументом, он и есть аккаунт, и в rest его оставлять нельзя.
	# Иначе номер берётся с самого события, потому что клиент присылает
	# структуру с полем account. Раньше здесь всегда был 0, и плагин с двумя
	# аккаунтами был уверен, что все сообщения приходят из его.
	if len(args) >= 2 and _is_account(args[0]):
		return int(args[0]), tuple(args[1:])
	account = _account_of(args)
	if account is not None:
		return account, tuple(args)
	return 0, tuple(args)


def _account_of(args: tuple) -> Optional[int]:
	"""Номер аккаунта из самого события, если оно его знает.

	Событие приходит структурой с полем account, поэтому номер не надо
	угадывать по позиции аргумента: у on_message один аргумент, у on_typing
	их два, у on_message_deleted первый - это вообще peer_id.

	Возвращает None, когда номера на событии нет: это событие рукописное,
	и его разбирать надо по старым правилам.
	"""
	for value in args:
		account = getattr(value, "account", None)
		if isinstance(account, int) and not isinstance(account, bool):
			return account
		# Удалённое сообщение приходит словарём, а не объектом.
		if isinstance(value, dict):
			account = value.get("account")
			if isinstance(account, int) and not isinstance(account, bool):
				return account
	return None


def _is_account(value: Any) -> bool:
	"""True for an account number, False for an event payload.

	Отличает вызов (account, event) от (event). bool - подтип int,
	поэтому он исключён: иначе True попал бы в аккаунт.
	"""
	return isinstance(value, int) and not isinstance(value, bool)


def _is_container(name: str) -> bool:
    """True for Telegram's update containers rather than an update itself."""
    low = str(name or "").lower()
    return low.startswith("tl_updates") or low in ("updates", "updatescombined")


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


def _load_settings(plugin_id: str) -> Dict[str, Any]:
    """Stored values for a plugin, so settings survive an app restart."""
    try:
        from ayugram_compat.settings_store import read

        return read(plugin_id)
    except Exception:
        return {}


def _log(text: str) -> None:
    try:
        from ayu_api import log as _l
        _l(str(text))
    except Exception:
        print(f"[log] {text}")


def _looks_numeric(item: Any) -> bool:
    """True when an EditText's default is a bare number."""
    raw = str(getattr(item, "default", "") or "").strip()
    try:
        int(raw)
        return True
    except Exception:
        return False


def _jsonable(value: Any) -> Any:
    """Reduce a setting value to bool / int / str so the C++ side can read it."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if float(value).is_integer() else str(value)
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _first_id(*values: Any) -> Any:
    """The first value that is actually there, or None."""
    for value in values:
        if value:
            return value
    return None


def _reply_id_of(ctx: Any) -> Any:
    """The id of the message being sent, which is what a reply points at."""
    message = getattr(ctx, "message", None)
    if message is None:
        return None
    for name in ("msg_id", "id", "message_id"):
        value = getattr(message, name, None)
        if value:
            try:
                return int(value)
            except Exception:
                continue
    return None


def _reply_top_id_of(ctx: Any) -> Any:
    """The forum topic a reply belongs to, when there is one."""
    for name in ("reply_to_top_id", "replyToTopMsg", "topic_root_id"):
        value = getattr(ctx, name, None)
        if value:
            try:
                return int(value)
            except Exception:
                continue
    return None


class SendParams:
    """Outgoing-message params with mobile-style attribute access.

    Mobile plugins read `params.message` / `params.peer` /
    `params.replyToMsg`; dict-style `params.get("message")` keeps working
    too.

    The reply is filled in from the context when it is not passed: a plugin
    answering a command reads `params.replyToMsg` to find out what it is
    answering, and it was always None - so a plugin that then passed that same
    value on to send_message had nothing to thread its answer to.
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
        self.replyToMsg = _first_id(replyToMsg, _reply_id_of(_ctx))
        self.replyToTopMsg = _first_id(replyToTopMsg, _reply_top_id_of(_ctx))
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
        # Telegram name -> the Desktop event it maps to, and which events have
        # already been subscribed. A plugin names four Telegram updates for the
        # same Desktop event, and subscribing each one separately meant one
        # arriving message called the plugin four times, four times with an
        # update shaped for a name that was not the one that happened.
        self._raw_names: Dict[str, List[str]] = {}
        self._raw_containers: List[str] = []
        self._raw_subscribed: set = set()
        self.enabled = True
        try:
            from ayugram_compat import hook_utils as _hu

            _hu.register_instance(self)
        except Exception:
            pass

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
        from ayugram_compat.settings_store import coerce

        key = str(key)
        if default is None:
            default = self.declared_defaults().get(key)
        # Cast to the declared type before handing it over. The settings dialog
        # stores whatever the row's type produced, and a text row that had been
        # treated as numeric came back as an int, so the plugin's own
        # `get_setting("name", "").strip()` died on it. The declared default is
        # the plugin's statement of intent, so it wins over what was written.
        return coerce(default, self._settings.get(key, default))

    def set_setting(
            self,
            key: str,
            value: Any,
            reload_settings: bool = False) -> None:
        self._settings[str(key)] = value
        try:
            from ayugram_compat.settings_store import set_value

            set_value(self.plugin_id(), str(key), value)
        except Exception as e:
            self.log(f"set_setting({key}) not persisted: {e}")
        if reload_settings:
            self._settings = _load_settings(self.plugin_id())

    def plugin_id(self) -> str:
        pid = (
            getattr(self, "__id__", "")
            or getattr(self, "_ayu_id", "")
            or type(self).__name__
        )
        try:
            from ayugram_compat.settings_store import sanitize

            return sanitize(pid)
        except Exception:
            return str(pid)

    def declared_settings(self) -> List[Any]:
        try:
            return list(self.create_settings() or [])
        except Exception:
            return []

    def declared_defaults(self) -> Dict[str, Any]:
        cached = getattr(self, "_defaults_cache", None)
        if cached is None:
            try:
                from ayugram_compat.settings_store import defaults

                cached = defaults(self.declared_settings())
            except Exception:
                cached = {}
            self._defaults_cache = cached
        return cached

    def setting_schema(self) -> List[Dict[str, Any]]:
        """Describe the editable rows for the app's settings UI.

        Delegates to ayugram_compat.schema rather than repeating the mapping.
        This used to be a second copy of it, and the two disagreed: this one
        typed every `Input` row as a number while the other had learned to look
        at the declared default. Since the app reaches the schema through this
        method, the copy won in the end and a plugin's text field was still
        edited as a number and stored as one - the dialog showing "0" where the
        plugin had declared "спам". One implementation cannot drift.
        """
        try:
            from ayugram_compat import schema as _schema
        except Exception:
            return []
        # rows_from_items, not rows_for: rows_for looks for `setting_schema`
        # before anything else, so routing this through it would call straight
        # back into this method for as long as the stack lasted.
        return _schema.rows_from_items(self.declared_settings())

    def setting_values(self) -> Dict[str, Any]:
        """Current values merged over declared defaults, for the UI."""
        from ayugram_compat.settings_store import coerce

        merged = dict(self.declared_defaults())
        # Typed here too, so the dialog is built from values of the declared type
        # and does not offer a numeric box for a field the plugin treats as text.
        merged.update({k: coerce(v, self._settings.get(k, v)) for k, v in merged.items()})
        return {k: _jsonable(v) for k, v in merged.items()}

    def apply_settings(self, values: Dict[str, Any]) -> None:
        """Apply values coming from the app's settings UI."""
        if not isinstance(values, dict):
            return
        declared = self.declared_defaults()
        for key, value in values.items():
            coerced = value
            if key in declared:
                try:
                    from ayugram_compat.settings_store import coerce

                    coerced = coerce(declared[key], value)
                except Exception:
                    pass
            self.set_setting(key, coerced, reload_settings=False)
        try:
            from ayugram_compat import hook_utils as _hu

            # Scoped to this plugin. Unscoped, saving one plugin's settings
            # silently stopped the background loops of all the others - they had
            # done nothing, and just stopped being called.
            _hu.cancel_all_timers(self)
        except Exception:
            pass
        self.on_settings_changed()

    def on_settings_changed(self) -> None:
        """Hook for plugins that must re-read values after an edit."""
        return None

    def export_settings(self) -> Dict[str, Any]:
        merged = dict(self.declared_defaults())
        merged.update(self._settings)
        return merged

    def import_settings(
            self, settings: Dict[str, Any], reload_settings: bool = True) -> None:
        if not isinstance(settings, dict):
            return
        inner = settings.get("settings")
        if isinstance(inner, dict):
            settings = inner
        declared = self.declared_defaults()
        for key, value in settings.items():
            coerced = value
            if key in declared:
                try:
                    from ayugram_compat.settings_store import coerce

                    coerced = coerce(declared[key], value)
                except Exception:
                    pass
            self.set_setting(key, coerced, reload_settings=False)
        if reload_settings:
            self._settings = _load_settings(self.plugin_id())

    def add_menu_item(self, data: MenuItemData) -> str:
        """Register a menu entry, and say clearly when it will not appear.

        It used to return a truthy id for an entry nothing reads, so a plugin
        that registered a menu item believed it had one. The Desktop context
        menu is a different surface from the plugin's own dialog and nothing
        bridges them yet; the honest answer is an id, so a caller can tell
        "registered" from "shown" by asking.
        """
        self._menu_seq += 1
        item_id = data.item_id or f"menu_{self._menu_seq}"
        self._menu_items[item_id] = data
        try:
            from ayugram_compat.gaps import note_gap
            note_gap(f"add_menu_item:{item_id}")
        except Exception:
            pass
        self.log(
            f"Desktop: menu item '{data.text}' is remembered but not shown; "
            f"the plugin menu has no equivalent on Desktop")
        return item_id

    def remove_menu_item(self, item_id: str) -> None:
        self._menu_items.pop(item_id, None)

    def add_hook(
            self,
            name: str,
            match_substring: bool = False,
            priority: int = 0) -> bool:
        """Register a hook under a Telegram or Java name.

        Routed through the same mapping `hook()` uses, and it reports whether
        it landed. It used to append the name to a list and log that it was not
        available, and returned nothing - so a plugin that registered ten hooks
        this way had none of them, and the plugin that owns them simply never
        did anything, with nothing to show for it.
        """
        self._hooks.append(name)
        return self._register_hook(name, None, priority, report=False)

    def hook(self, name: str, fn: Any = None, priority: int = 0) -> Any:
        """Register a callback for a hook by name.

        Mobile plugins register this way instead of through add_hook, and the
        method was simply absent: the plugin raised AttributeError from
        on_plugin_load and never loaded at all. An unmapped name is reported
        rather than refused, because a plugin that registers ten hooks and gets
        nine of them should still run.
        """
        if fn is None:
            def _decorator(target):
                self._register_hook(name, target, priority)
                return target
            return _decorator
        self._register_hook(name, fn, priority)
        return fn

    def _register_hook(
            self,
            name: str,
            fn: Any = None,
            priority: int = 0,
            report: bool = True) -> bool:
        """Map a hook name onto a Desktop event and subscribe to it.

        Answers whether it landed. `fn` may be None, which is what `add_hook`
        passes: there is no Desktop event for a bare Telegram update name, and
        the honest answer is to say so rather than to appear to have registered
        something.
        """
        try:
            from hook_utils import _desktop_hook_name
            from ayu_api import hook as _hook
        except Exception as e:
            self.log(f"hook registration unavailable: {e}")
            return False
        mapped = _desktop_hook_name(name)
        if not mapped:
            # A container is not an event of its own: Desktop has one
            # "a message arrived" where Telegram has a bag of updates. Registering
            # TL_updates therefore used to be reported as unavailable and the
            # plugin heard nothing at all, so it is kept aside and handed every
            # update as a one-element list.
            if _is_container(name):
                self._raw_containers.append(name)
                self._register_container_hook(name)
                return True
            try:
                from ayugram_compat.gaps import note_gap
                note_gap(f"hook:{name}")
            except Exception:
                pass
            if report:
                self.log(
                    f"Desktop has no event for '{name}'; "
                    f"the callback will not be called")
            return False
        try:
            if fn is None:
                if not self._wants_raw_updates():
                    if report:
                        self.log(
                            f"Desktop has no event for '{name}', and this "
                            f"plugin has no on_update_hook to receive it")
                    return False
                return self._register_raw_hook(name, mapped)
            _hook(mapped, fn)
            return True
        except Exception as e:
            self.log(f"could not register '{name}': {e}")
            return False

    def _register_raw_hook(self, name: str, mapped: str) -> bool:
        """Subscribe one callback per Desktop event, however many names map to it."""
        try:
            from ayu_api import hook as _hook
        except Exception as e:
            self.log(f"hook registration unavailable: {e}")
            return False
        names = self._raw_names.setdefault(mapped, [])
        if name not in names:
            names.append(name)
        if mapped in self._raw_subscribed:
            return True
        try:
            _hook(mapped, self._raw_bridge(mapped))
            self._raw_subscribed.add(mapped)
            return True
        except Exception as e:
            self.log(f"could not register '{name}': {e}")
            return False

    def _wants_raw_updates(self) -> bool:
        """True when this plugin implements on_update_hook itself.

        A plugin that hooks Telegram updates by name registers nothing at all:
        `add_hook("TL_updateUserTyping")` carries no callback, and the bridge
        cannot subscribe a null function - so the plugin was loaded, enabled and
        silent. If it defines on_update_hook, that is where it wants the update.
        """
        return type(self).on_update_hook is not BasePlugin.on_update_hook

    def _tl_update_bridge(self, name: str, mapped: str):
        """Kept for callers that want a bridge for one specific name."""
        from hook_utils import build_tl_update

        def _bridge(*args, **kwargs):
            try:
                # Разбор общий с контейнерным мостом.
                account, rest = _split_account(mapped, args)
                update = build_tl_update(name, mapped, rest)
                return self.on_update_hook(name, account, update)
            except Exception as e:
                self.log(f"on_update_hook for '{name}' failed: {e}")
                return None

        return _bridge

    def _raw_bridge(self, mapped: str):
        """One callback per Desktop event, handing over the matching name.

        Desktop has one "a message arrived" event where Telegram has several
        named updates. The name is chosen from what actually arrived, so the
        plugin sees one update with the shape it expects instead of one per
        registered name.
        """
        from hook_utils import build_tl_update, pick_update_name

        def _bridge(*args, **kwargs):
            try:
                # Сигнатуры событий разные, и раньше мост брал первый
                # аргумент за аккаунт всегда. Для on_message клиент зовёт
                # (msg), так что int(msg) падал на каждом входящем и ни один
                # плагин на TL-хуках апдейтов не получал. Для on_typing аккаунт
                # действительно первый, а для on_message_deleted первый аргумент
                # - это peer_id, и отбрасывать его нельзя.
                account, rest = _split_account(mapped, args)
                if not self._account_allowed_number(account):
                    return None
                names = self._raw_names.get(mapped) or []
                name = pick_update_name(names, mapped, rest)
                update = build_tl_update(name, mapped, rest)
                result = self.on_update_hook(name, account, update)
                self._deliver_container(mapped, account, name, update)
                return result
            except Exception as e:
                self.log(f"on_update_hook for '{mapped}' failed: {e}")
                return None

        return _bridge

    def _register_container_hook(self, container: str) -> None:
        """Subscribe a container-only plugin to everything a container carries.

        A plugin that registers just TL_updates names no event of its own, so
        there is nothing for it to be called on and it heard nothing at all.
        These are the events Telegram wraps in updates; on_message_sending is
        left out because an outgoing message is not something Telegram sends
        back inside one.
        """
        from ayu_api import hook as _hook

        fallback = {
            "on_message": "TL_updateNewMessage",
            "on_message_deleted": "TL_updateDeleteMessages",
            "on_chat_opened": "TL_updateChatOpened",
            "on_typing": "TL_updateUserTyping",
        }
        for event, default_name in fallback.items():
            if event in self._raw_subscribed:
                continue

            def _make(event=event, default_name=default_name):
                from hook_utils import build_tl_update, pick_update_name

                def _container_bridge(*args, **kwargs):
                    try:
                        # Разбор общий с обычным мостом: клиент зовёт
                        # on_message как (msg), а контейнерная подписка
                        # ожидает (account, msg).
                        account, rest = _split_account(event, args)
                        if not self._account_allowed_number(account):
                            return None
                        names = self._raw_names.get(event) or []
                        name = pick_update_name(names, event, rest) \
                            or default_name
                        update = build_tl_update(name, event, rest)
                        self.on_updates_hook(container, account, [update])
                    except Exception as e:
                        self.log(f"on_updates_hook for '{container}' failed: {e}")
                return _container_bridge

            try:
                _hook(event, _make())
                self._raw_subscribed.add(event)
            except Exception:
                pass

    def _deliver_container(
            self, mapped: str, account: int, name: str, update: Any) -> None:
        """Hand the same update over as a container, if the plugin wants that.

        Telegram wraps updates in TL_updates and TL_updatesCombined, and a
        plugin written against that API often registers only the container and
        reads them in on_updates_hook. Desktop has no container to give it, so
        it got nothing at all: the same update it is already being handed
        arrives as a one-element list, which is what a container is.
        """
        names = self._raw_names.get(mapped) or ()
        if not self._raw_containers:
            return
        try:
            self.on_updates_hook("TL_updates", account, [update])
        except Exception as e:
            self.log(f"on_updates_hook failed: {e}")


    # The registration helpers mobile plugins call by name. Apart from the
    # send-message one they were all missing, so a plugin that merely listened
    # for incoming messages failed on the first line of on_plugin_load.
    def add_on_message_hook(self, priority: int = 0) -> None:
        self._register_hook("on_message", self.on_message_hook, priority)

    def add_on_message_deleted_hook(self, priority: int = 0) -> None:
        self._register_hook(
            "on_message_deleted", self.on_message_deleted_hook, priority)

    def add_on_chat_opened_hook(self, priority: int = 0) -> None:
        self._register_hook(
            "on_chat_opened", self.on_chat_opened_hook, priority)

    def add_on_app_start_hook(self, priority: int = 0) -> None:
        self._register_hook("on_app_start", self.on_app_start_hook, priority)

    def add_on_app_stop_hook(self, priority: int = 0) -> None:
        self._register_hook("on_app_stop", self.on_app_stop_hook, priority)

    def add_on_setting_changed_hook(self, priority: int = 0) -> None:
        self._register_hook(
            "on_setting_changed", self.on_setting_changed_hook, priority)

    def _accounts_wanted(self) -> Optional[Any]:
        """Аккаунты, на которые этот плагин подписан, или None для всех.

        None означает «слушать всё»: это поведение по умолчанию, каким был
        плагин до того, как в клиенте появилось несколько аккаунтов, и менять
        его молча нельзя.

        Плагин может задать self.account или self.accounts, и тогда события
        чужих аккаунтов до него не дойдут. Номер приходит на самом событии,
        так что фильтр ничего не угадывает.
        """
        wanted = getattr(self, "accounts", None)
        if wanted is None:
            single = getattr(self, "account", None)
            wanted = single if single is not None else None
        if wanted is None:
            return None
        if isinstance(wanted, int) and not isinstance(wanted, bool):
            return {wanted}
        try:
            return {int(x) for x in wanted}
        except Exception:
            return None

    def _account_allowed_number(self, account: Any) -> bool:
        """True, если событие с уже известным номером аккаунта нужно плагину."""
        wanted = self._accounts_wanted()
        if wanted is None:
            return True
        if not isinstance(account, int) or isinstance(account, bool):
            return True
        return account in wanted

    def _account_allowed(self, obj: Any) -> bool:
        """True, если событие obj относится к аккаунту этого плагина."""
        wanted = self._accounts_wanted()
        if wanted is None:
            return True
        account = _account_of((obj,))
        if account is None:
            # Событие без номера аккаунта: пропустить его нельзя, иначе
            # плагин молча перестанет работать.
            return True
        return account in wanted

    def on_message_hook(self, message: Any) -> Any:
        handler = getattr(self, "on_message", None)
        if not callable(handler) or not self._account_allowed(message):
            return None
        return handler(message)

    def on_message_deleted_hook(self, params: Any) -> Any:
        handler = getattr(self, "on_message_deleted", None)
        if not callable(handler) or not self._account_allowed(params):
            return None
        return handler(params)

    def on_chat_opened_hook(self, params: Any) -> Any:
        handler = getattr(self, "on_chat_opened", None)
        if not callable(handler) or not self._account_allowed(params):
            return None
        return handler(params)

    def on_app_start_hook(self, params: Any) -> Any:
        return None

    def on_app_stop_hook(self, params: Any) -> Any:
        return None

    def on_setting_changed_hook(self, key: str, value: Any = None) -> Any:
        return None

    def add_timer(
            self,
            fn: Any,
            interval: float = 1.0,
            repeat: int = 1,
            delay: float = None) -> Any:
        """Run `fn` on a timer, the way mobile plugins schedule delayed work.

        Returns a handle that remove_timer accepts. A plugin that scheduled its
        work this way used to fail to load on the missing method.
        """
        try:
            from hook_utils import add_timer as _add_timer
        except Exception as e:
            self.log(f"add_timer unavailable: {e}")
            return None
        return _add_timer(fn, interval, repeat, delay)

    def remove_timer(self, handle: Any) -> bool:
        """Cancel a timer previously returned by add_timer."""
        try:
            from hook_utils import remove_timer as _remove
        except Exception as e:
            self.log(f"remove_timer unavailable: {e}")
            return False
        return _remove(handle)

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
                result = plugin.on_send_message_hook(getattr(plugin, '_account', 0), params)
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

    def add_on_media_sending_hook(self, priority: int = 0) -> None:
        """Watch outgoing files, before they are uploaded.

        Separate from the text hook on purpose. Handing a file to
        on_send_message_hook meant a plugin written for messages was called with
        an empty one every time anything was sent, and one that rewrites text
        would be asked to rewrite nothing. The context carries what is going
        out: name, path, size, kind, caption.
        """
        try:
            from ayu_api import hook as _hook
        except Exception as e:
            self.log(f"add_on_media_sending_hook unavailable: {e}")
            return

        plugin = self

        def _bridge(account_, context):
            try:
                return plugin.on_media_sending_hook(account_, context)
            except Exception as e:
                plugin.log(f"on_media_sending_hook raised: {e}")
                return HookResult()

        self._register_hook("on_media_sending", _bridge, priority)

    def on_media_sending_hook(
            self, account: int, params: Any) -> HookResult:
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
        # Имя класса берём из первого аргумента: именно по нему видно, что
        # плагину нужно, и без записи в отчёт причина молчания неясна.
        target = args[0] if args else kwargs.get("class_name", "?")
        note_gap(f"hook_method:{target}")
        raise NotImplementedError(
            "Android-only: Xposed/Java method hooking is not available "
            "on Desktop AyuGram")

    def unhook_method(self, *args, **kwargs):
        pass

    def hook_all_methods(self, *args, **kwargs) -> list:
        target = args[0] if args else kwargs.get("class_name", "?")
        note_gap(f"hook_all_methods:{target}")
        self.log(
            "Desktop: bulk Java method hooking is not available; "
            "nothing hooked, plugin keeps running")
        return []

    def hook_all_constructors(self, *args, **kwargs) -> list:
        target = args[0] if args else kwargs.get("class_name", "?")
        note_gap(f"hook_all_constructors:{target}")
        return []

    def client(self, account: int = 0):
        raise NotImplementedError(
            "Android-only: Telegram Java client bridge is not available "
            "on Desktop AyuGram")


def activate_base_plugins(module_globals: Dict[str, Any]) -> Optional[BasePlugin]:
    """Instantiate the first BasePlugin subclass and call on_plugin_load."""
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
    declared_id = str(
        module_globals.get("__id__")
        or module_globals.get("plugin_id")
        or chosen.__name__
    )
    try:
        from ayugram_compat.settings_store import sanitize

        declared_id = sanitize(declared_id)
    except Exception:
        pass
    instance._ayu_id = declared_id
    try:
        instance._settings = _load_settings(instance.plugin_id())
    except Exception:
        instance._settings = {}
    instance.on_plugin_load()
    try:
        items = instance.create_settings() or []
    except Exception:
        items = []
    defaults_map: Dict[str, Any] = {}
    for item in items:
        try:
            key = getattr(item, "key", "")
            default = getattr(item, "default", None)
            if key:
                defaults_map[str(key)] = "" if default is None else default
            if key and str(key) not in instance._settings and default is not None:
                instance._settings[str(key)] = default
        except Exception:
            pass
    instance._defaults_cache = defaults_map
    instance._settings = {
        k: v
        for k, v in instance._settings.items()
        if k not in ("id", "version", "settings", "plugin_id")
    }
    return instance
