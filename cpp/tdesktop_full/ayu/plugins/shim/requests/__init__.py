"""A small stand-in for `requests`, backed by urllib.

Android and exteraGram plugins reach for `requests` almost reflexively, and a
plugin that cannot import it fails to load at all - which reads as "AyuGram is
broken" rather than "one optional library is missing". Bundling a real
`requests` would mean shipping pip's whole dependency tree, so this provides
the surface plugins actually use, on top of the standard library.

It is deliberately not a complete `requests`: no streaming, no multipart
convenience beyond what urllib gives, no adapters, no cookies beyond a simple
per-session dict. Anything missing raises AttributeError, which is honest -
a silent wrong answer would be worse than a clear absence.
"""

from .exceptions import (  # noqa: F401
    ConnectionError,
    HTTPError,
    RequestException,
    Timeout,
    TooManyRedirects,
    ConnectTimeout,
    ReadTimeout,
)
from .models import Response  # noqa: F401
from .sessions import Session, session  # noqa: F401

__version__ = "2.31.0-ayugram-shim"

# The import guard plugins write around optional dependencies.
__all__ = [
    "get", "post", "put", "patch", "delete", "head", "options", "request",
    "Session", "session", "Response", "exceptions", "codes",
]

from urllib.parse import urlencode  # noqa: E402

from . import exceptions  # noqa: E402,F401
from . import codes  # noqa: E402,F401


def _prepare(url, params=None, data=None, json=None, headers=None, **kwargs):
	"""Fold the keyword arguments urllib wants differently into one shape."""
	if params:
		sep = "&" if "?" in url else "?"
		url = f"{url}{sep}{urlencode(params, doseq=True)}"
	headers = dict(headers or {})
	body = None
	if json is not None:
		# import json lazily: the module is also called json below
		import json as _json
		body = _json.dumps(json).encode("utf-8")
		headers.setdefault("Content-Type", "application/json")
	elif isinstance(data, (dict, list)):
		body = urlencode(data, doseq=True).encode("utf-8")
		if not headers.get("Content-Type"):
			headers["Content-Type"] = "application/x-www-form-urlencoded"
	elif isinstance(data, str):
		body = data.encode("utf-8")
	elif isinstance(data, bytes):
		body = data
	elif data is not None:
		# file-like, as multipart plugins pass
		body = data
		if hasattr(data, "read"):
			body = data.read()
	headers.setdefault("User-Agent", "AyuGram-plugin-shim/1.0")
	return url, headers, body


def request(method, url, **kwargs):
	return session.request(method, url, **kwargs)


def get(url, params=None, **kwargs):
	return session.get(url, params=params, **kwargs)


def post(url, data=None, json=None, **kwargs):
	return session.post(url, data=data, json=json, **kwargs)


def put(url, data=None, **kwargs):
	return session.put(url, data=data, **kwargs)


def patch(url, data=None, **kwargs):
	return session.patch(url, data=data, **kwargs)


def delete(url, **kwargs):
	return session.delete(url, **kwargs)


def head(url, **kwargs):
	return session.head(url, **kwargs)


def options(url, **kwargs):
	return session.options(url, **kwargs)