"""Desktop compat layer for mobile exteraGram plugins."""
from . import base_plugin, client_utils, file_utils, hook_utils  # noqa: F401
try:
	from . import android_utils  # noqa: F401
except Exception:
	pass
try:
	from . import mobile_stubs
	mobile_stubs.install()
except Exception:
	pass

__all__ = ["base_plugin", "hook_utils", "client_utils", "file_utils", "android_utils", "mobile_stubs"]
__api_version__ = 2
