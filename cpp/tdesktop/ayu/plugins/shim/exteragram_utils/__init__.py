"""Alias package some Extera plugins import as exteragram_utils.*."""
import sys as _sys
from ayugram_compat import base_plugin as base_plugin  # noqa: F401
from ayugram_compat import hook_utils as hook_utils  # noqa: F401
from ayugram_compat import client_utils as client_utils  # noqa: F401
from ayugram_compat import android_utils as android_utils  # noqa: F401

__all__ = ["base_plugin", "hook_utils", "client_utils", "android_utils"]

# Allow `from exteragram_utils.base_plugin import X` (submodule-style).
_sys.modules.setdefault("exteragram_utils.base_plugin", base_plugin)
_sys.modules.setdefault("exteragram_utils.hook_utils", hook_utils)
_sys.modules.setdefault("exteragram_utils.client_utils", client_utils)
_sys.modules.setdefault("exteragram_utils.android_utils", android_utils)
