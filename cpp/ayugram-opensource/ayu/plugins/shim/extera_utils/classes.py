"""Desktop adaptation of `extera_utils.classes` (mobile class-proxy DSL).

Java subclass generation does not exist on Desktop: `Base` is a plain
Python base, while `java_subclass` / `joverride` are identity decorators
so decorated Python logic keeps running without Java.
"""
from __future__ import annotations

from typing import Any, Callable


class Base:
    """Plain base for proxy-style classes on Desktop."""
    pass


def java_subclass(*args, **kwargs):
    """Identity decorator: keep the Python class usable without Java."""
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]

    def _wrap(cls):
        return cls
    return _wrap


def joverride(*args, **kwargs):
    """Identity decorator for override markers."""
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]

    def _wrap(fn: Callable) -> Callable:
        return fn
    return _wrap


__all__ = ["Base", "java_subclass", "joverride"]
