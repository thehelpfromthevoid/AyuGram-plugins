"""Session, and the module-level default one the helpers delegate to."""

import http.client
import socket
import urllib.error
import urllib.request

from . import exceptions
from .models import CaseInsensitiveDict, PreparedRequest, Response


class Session:
	timeout = None

	def __init__(self):
		self.headers = CaseInsensitiveDict()
		self.params = {}
		self.auth = None
		self.proxies = {}
		self.verify = True
		self.cert = None
		self.trust_env = True
		self.cookies = {}
		self.hooks = {"response": []}
		self.adapters = {"http://": None, "https://": None}

	# --- plumbing -------------------------------------------------------
	def _build(self, method, url, kwargs):
		from . import _prepare
		headers = dict(self.headers)
		headers.update(kwargs.get("headers") or {})
		prepared_url, prepared_headers, body = _prepare(
			url,
			params={**self.params, **(kwargs.get("params") or {})},
			data=kwargs.get("data"),
			json=kwargs.get("json"),
			headers=headers,
		)
		prepared = PreparedRequest()
		prepared.method = method.upper()
		prepared.url = prepared_url
		prepared.headers = CaseInsensitiveDict(prepared_headers)
		prepared.body = body
		timeout = kwargs.get("timeout", self.timeout)
		return prepared, timeout

	def request(self, method, url, **kwargs):
		prepared, timeout = self._build(method, url, kwargs)
		if isinstance(timeout, tuple):
			timeout = timeout[0]
		req = urllib.request.Request(
			prepared.url,
			data=prepared.body,
			headers={k: v for k, v in prepared.headers.items()},
			method=prepared.method,
		)
		response = Response()
		response.url = prepared.url
		response.request = prepared
		try:
			with urllib.request.urlopen(req, timeout=timeout) as raw:
				response.status_code = raw.getcode()
				response.reason = getattr(raw, "reason", None)
				response.headers = CaseInsensitiveDict(raw.headers.items())
				response._content = raw.read()
				response._raw = raw
		except urllib.error.HTTPError as e:
			# A 404 or a 500 is a normal answer, not a failure: requests hands
			# it back as a Response, and plugins branch on status_code.
			response.status_code = e.code
			response.reason = getattr(e, "reason", None)
			response.headers = CaseInsensitiveDict(
				e.headers.items() if e.headers else {})
			try:
				response._content = e.read()
			except Exception:
				response._content = b""
		except urllib.error.URLError as e:
			reason = getattr(e, "reason", e)
			if isinstance(reason, socket.timeout):
				raise exceptions.ReadTimeout(str(e)) from None
			if isinstance(reason, http.client.HTTPException):
				raise exceptions.ConnectionError(str(e)) from None
			raise exceptions.ConnectionError(str(e)) from None
		except socket.timeout as e:
			raise exceptions.ReadTimeout(str(e)) from None
		return response

	# --- verbs ----------------------------------------------------------
	def get(self, url, **kwargs):
		return self.request("GET", url, **kwargs)

	def post(self, url, data=None, json=None, **kwargs):
		return self.request("POST", url, data=data, json=json, **kwargs)

	def put(self, url, data=None, **kwargs):
		return self.request("PUT", url, data=data, **kwargs)

	def patch(self, url, data=None, **kwargs):
		return self.request("PATCH", url, data=data, **kwargs)

	def delete(self, url, **kwargs):
		return self.request("DELETE", url, **kwargs)

	def head(self, url, **kwargs):
		kwargs.setdefault("allow_redirects", False)
		return self.request("HEAD", url, **kwargs)

	def options(self, url, **kwargs):
		return self.request("OPTIONS", url, **kwargs)

	# --- compatibility no-ops -------------------------------------------
	def mount(self, prefix, adapter):
		self.adapters[prefix] = adapter

	def close(self):
		pass

	def send(self, request, **kwargs):
		return self.request(request.method, request.url, **kwargs)

	def merge_environment_settings(self, url, proxies, stream, verify, cert):
		return {}

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		self.close()
		return False

	def __repr__(self):
		return "<requests.Session()>"


# The module-level session the verb helpers delegate to, as requests does.
session = Session()