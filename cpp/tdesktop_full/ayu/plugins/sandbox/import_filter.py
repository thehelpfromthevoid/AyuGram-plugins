"""Allowlist MetaPathFinder for AyuGram plugins."""
import importlib.abc
import sys

_SAFE = {
    "ayu_api", "ayugram_compat", "audithook", "import_filter",
    "hook_utils", "client_utils", "base_plugin", "android_utils",
    "ui", "org", "com", "java", "android", "androidx",
    "extera_utils",
    "exteragram_utils", "mobile_stubs",
    "sys", "importlib", "_imp", "_frozen_importlib", "_frozen_importlib_external",
    "abc", "codecs", "io", "os", "os.path", "stat", "posixpath", "genericpath",
    "re", "json", "datetime", "time", "math", "typing", "collections",
    "functools", "itertools", "enum", "dataclasses", "pathlib", "threading",
    "queue", "weakref", "copy", "traceback", "hashlib", "base64", "urllib",
}
_NEED_PERM = {
    "socket": "net.http",
    "urllib": "net.http",
    "requests": "net.http",
    "http": "net.http",
    "ssl": "net.http",
    "subprocess": "process.spawn",
    "ctypes": "native.code",
    "multiprocessing": "process.spawn",
}

_granted: set = set()


def configure(granted_permissions=()):
    _granted.clear()
    _granted.update(granted_permissions or [])


def grant(granted_permissions=()):
    """Union extra permissions. Loads are additive; grant never narrows."""
    _granted.update(granted_permissions or [])


class _Filter(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        top = name.split(".")[0]
        if top in _SAFE:
            return None
        need = _NEED_PERM.get(top)
        if need is None:
            return None
        if need not in _granted and need.split(".")[0] not in {g.split(".")[0] for g in _granted}:
            raise PermissionError(f"ayu sandbox: import {name!r} needs permission {need!r}")
        return None


def install():
    if not any(isinstance(f, _Filter) for f in sys.meta_path):
        sys.meta_path.insert(0, _Filter())
