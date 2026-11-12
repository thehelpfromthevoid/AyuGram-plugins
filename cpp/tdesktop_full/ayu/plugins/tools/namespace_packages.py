"""Make the namespace packages degrade instead of refusing the import.

These `__init__.py` files hold nothing. `from org.telegram.ui import
ChatActivity` therefore raised ImportError, and that is the single worst
outcome for a compatibility layer: the plugin does not load, so none of its
features run - not even the ones that would have worked. A class it cannot have
should cost that one feature.

Appended rather than rewritten, so whatever a file already declared is kept.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "shim"

MARKER = "android_module(__name__)"

TAIL = '''

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
'''

TARGETS = [
	"org/__init__.py",
	"org/telegram/__init__.py",
	"org/telegram/ui/__init__.py",
]


def main() -> int:
	for rel in TARGETS:
		path = ROOT / rel
		text = path.read_text(encoding="utf-8")
		if MARKER in text:
			print(f"  уже настроен {rel}")
			continue
		path.write_text(text.rstrip("\n") + TAIL, encoding="utf-8")
		print(f"  дополнен {rel}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
