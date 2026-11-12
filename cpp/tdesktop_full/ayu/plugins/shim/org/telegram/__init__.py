"""org.telegram namespace for Desktop."""
from . import messenger, tgnet, ui  # noqa: F401

# Appended so that a name this package does not have resolves to a stand-in class
# instead of raising ImportError. Appending, not replacing: whatever this file
# already declared has to keep working.
try:
	from org._stubs import android_module as _android_module
except Exception:  # pragma: no cover - the shim is incomplete
	pass
else:
	_android_module(__name__)

# This directory, so that a subpackage which really exists here is still found.
# An empty __path__ would say the opposite - Python would take the package to
# have no subpackages at all, and then nothing inside it could be imported.
import os as _os

__path__ = [_os.path.dirname(_os.path.abspath(__file__))]
