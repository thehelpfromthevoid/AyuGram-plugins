"""Desktop compat layer for mobile exteraGram plugins."""
import sys


def _make_console_safe() -> None:
	"""Stop a plugin's own diagnostics from crashing it.

	Every shim module reports through print(), and on a Windows console whose
	code page is cp1251 or cp866 a single emoji or any Cyrillic letter raises
	UnicodeEncodeError. The traceback that follows is worse than the message it
	replaced, and it lands in the middle of whatever the plugin was doing.
	Replacing unencodable characters keeps the line readable and never raises.
	"""
	for stream in (sys.stdout, sys.stderr):
		try:
			stream.reconfigure(errors="replace")
		except Exception:
			pass


_make_console_safe()

from . import base_plugin, client_utils, file_utils, hook_utils  # noqa: E402,F401
from . import android_only, gaps  # noqa: E402,F401
try:
	from . import android_utils  # noqa: E402,F401
except Exception:
	pass
try:
	from . import mobile_stubs
	mobile_stubs.install()
except Exception:
	pass

__all__ = ["base_plugin", "hook_utils", "client_utils", "file_utils",
	"android_utils", "mobile_stubs", "android_only", "gaps"]
__api_version__ = 2
