"""Desktop port of mobile `file_utils` (exteraGram file helpers).

Directory helpers point at real Desktop folders; read/write helpers
are thin safe wrappers; FilesController stores extension handlers and
logs (opening files is host business on Desktop).
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional


def _log(text: str) -> None:
    try:
        from ayu_api import log as _l
        _l(str(text))
    except Exception:
        print(f"[file_utils] {text}")


def _base_dir() -> str:
    return os.environ.get(
        "AYU_PLUGINS_DATA",
        os.path.join(os.path.expanduser("~"), ".ayugram"))


def get_plugins_dir() -> str:
    return os.path.join(_base_dir(), "plugins")


def _media_base() -> str:
    return os.environ.get("AYU_MEDIA_DIR", os.path.join(_base_dir(), "media"))


def get_cache_dir() -> str:
    return os.path.join(tempfile.gettempdir(), "ayu_cache")


def get_files_dir() -> str:
    return os.path.join(_media_base(), "files")


def get_images_dir() -> str:
    return os.path.join(_media_base(), "images")


def get_videos_dir() -> str:
    return os.path.join(_media_base(), "videos")


def get_audios_dir() -> str:
    return os.path.join(_media_base(), "audios")


def get_documents_dir() -> str:
    return os.path.join(_media_base(), "documents")


def ensure_dir_exists(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def list_dir(path: str, recursive: bool = False, include_files: bool = True,
             include_dirs: bool = False,
             extensions: Optional[List[str]] = None) -> List[str]:
    out: List[str] = []
    exts = None
    if extensions:
        exts = {e.lower() if e.startswith(".") else "." + e.lower()
                for e in extensions}
    if recursive:
        for root, dirs, files in os.walk(path):
            if include_dirs:
                out.extend(os.path.join(root, d) for d in sorted(dirs))
            if include_files:
                for name in sorted(files):
                    if exts and os.path.splitext(name)[1].lower() not in exts:
                        continue
                    out.append(os.path.join(root, name))
        return out
    try:
        entries = sorted(os.listdir(path))
    except Exception as e:
        _log(f"list_dir error: {e}")
        return []
    for name in entries:
        full = os.path.join(path, name)
        is_dir = os.path.isdir(full)
        if is_dir and not include_dirs:
            continue
        if not is_dir:
            if not include_files:
                continue
            if exts and os.path.splitext(name)[1].lower() not in exts:
                continue
        out.append(full)
    return out


def write_file(path: str, content: str) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(content))
        return True
    except Exception as e:
        _log(f"write_file error: {e}")
        return False


def read_file(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        _log(f"read_file error: {e}")
        return None


def write_file_bytes(path: str, content: bytes) -> bool:
    try:
        with open(path, "wb") as f:
            f.write(bytes(content))
        return True
    except Exception as e:
        _log(f"write_file_bytes error: {e}")
        return False


def read_file_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception as e:
        _log(f"read_file_bytes error: {e}")
        return None


def delete_file(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except Exception as e:
        _log(f"delete_file error: {e}")
        return False


class FilesController:
    SUPPORT_ICONS = False

    class Place(Enum):
        UNKNOWN = auto()
        ChatActivity = auto()
        FilteredSearchView = auto()
        SharedMediaLayout = auto()
        SearchDownloadsContainer = auto()
        ChannelAdminLogActivity = auto()

    @dataclass
    class FileInfo:
        ext: str = ""
        on_click: Optional[Callable] = None
        whitelist_places: List[Any] = field(default_factory=list)
        blacklist_places: List[Any] = field(default_factory=list)
        get_icon: Optional[Callable] = None

        def __post_init__(self):
            if self.whitelist_places and self.blacklist_places:
                raise ValueError(
                    "whitelist_places and blacklist_places are exclusive")
            self.ext = str(self.ext or "").lower().lstrip(".")

    @dataclass
    class OnClickArgs:
        place: Any = None
        file: Any = None
        file_name: str = ""
        message: Any = None
        activity: Any = None
        parent_fragment: Any = None

    class ExtensionAlreadyRegistered(Exception):
        pass

    class ExtensionNotRegistered(Exception):
        pass

    class SecretInvalid(Exception):
        pass

    _handlers: Dict[str, Any] = {}
    _secrets: Dict[str, str] = {}

    @classmethod
    def register(cls, file_info) -> str:
        import secrets as _secrets_mod
        ext = str(getattr(file_info, "ext", "") or "").lower().lstrip(".")
        if not ext:
            raise ValueError("FileInfo.ext is required")
        if ext in cls._handlers:
            raise cls.ExtensionAlreadyRegistered(ext)
        secret = _secrets_mod.token_hex(8)
        cls._handlers[ext] = file_info
        cls._secrets[ext] = secret
        _log(f"FilesController: handler for .{ext} stored "
             f"(desktop opens files itself)")
        return secret

    @classmethod
    def unregister(cls, ext: str, secret: str) -> None:
        ext = str(ext or "").lower().lstrip(".")
        if ext not in cls._handlers:
            raise cls.ExtensionNotRegistered(ext)
        if cls._secrets.get(ext) != secret:
            raise cls.SecretInvalid(ext)
        del cls._handlers[ext]
        del cls._secrets[ext]


__all__ = [
    "get_plugins_dir", "get_cache_dir", "get_files_dir", "get_images_dir",
    "get_videos_dir", "get_audios_dir", "get_documents_dir",
    "ensure_dir_exists", "list_dir", "write_file", "read_file",
    "write_file_bytes", "read_file_bytes", "delete_file", "FilesController",
]
