"""Top-level alias so `from base_plugin import BasePlugin` works for mobile ports."""
from ayugram_compat.base_plugin import *  # noqa: F401,F403
from ayugram_compat.base_plugin import (  # noqa: F401
    AppEvent,
    BasePlugin,
    HookResult,
    HookStrategy,
    MenuItemData,
    MenuItemType,
    MethodHook,
    MethodReplacement,
    Plugin,
    activate_base_plugins,
)
