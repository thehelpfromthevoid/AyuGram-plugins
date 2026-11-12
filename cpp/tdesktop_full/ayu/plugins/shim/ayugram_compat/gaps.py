"""Names plugins asked for that Desktop has no implementation of.

Written from Python side: a plugin that does `from client_utils import
_generate_photo_sizes` fails at import time over one missing helper, and the
only trace is an ImportError naming the function. Collecting the names here
turns that into a list the plugin check can show, so the report says which
capability is absent instead of merely that the plugin did not load.

The bridge reads all_gaps() and merges it with the names it recorded itself, so
there is one list no matter which side noticed.
"""

from __future__ import annotations

import threading
from typing import Dict, List

_lock = threading.Lock()
_gaps: Dict[str, str] = {}


def note_gap(full_name: str, reason: str = "") -> None:
	"""Record that a plugin reached for something that does not exist here."""
	with _lock:
		_gaps.setdefault(full_name, reason)


def all_gaps() -> List[str]:
	with _lock:
		return sorted(_gaps)


def clear() -> None:
	with _lock:
		_gaps.clear()


def __len__() -> int:
	with _lock:
		return len(_gaps)