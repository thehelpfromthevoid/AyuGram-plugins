"""Response and PreparedRequest, with the attributes plugins read."""

import json as _json

from . import exceptions


class CaseInsensitiveDict(dict):
	"""Header dict that ignores case, the way requests' does."""

	def __init__(self, data=None):
		super().__init__()
		if data:
			for key, value in dict(data).items():
				self[key] = value

	def update(self, other=None, **kwargs):
		# dict.update does not go through __setitem__ on a subclass, so without
		# this a header added by Session.headers.update() keeps its original
		# case and a later case-insensitive lookup misses it.
		for key, value in dict(other or {}, **kwargs).items():
			self[key] = value

	def copy(self):
		return CaseInsensitiveDict(self.items())

	@staticmethod
	def _norm(key):
		return key.lower() if isinstance(key, str) else key

	def __setitem__(self, key, value):
		super().__setitem__(self._norm(key), (key, value))

	def __getitem__(self, key):
		return super().__getitem__(self._norm(key))[1]

	def __delitem__(self, key):
		super().__delitem__(self._norm(key))

	def __contains__(self, key):
		return super().__contains__(self._norm(key))

	def get(self, key, default=None):
		try:
			return self[key]
		except KeyError:
			return default

	def items(self):
		return [(k, v) for k, (_, v) in super().items()]

	def values(self):
		return [v for _, (_, v) in super().items()]

	def keys(self):
		return [k for k, _ in super().items()]

	def __iter__(self):
		return iter(self.keys())


class Response:
	def __init__(self):
		self.status_code = None
		self.headers = CaseInsensitiveDict()
		self.url = None
		self.reason = None
		self.encoding = "utf-8"
		self.history = []
		self.request = None
		self._content = b""
		self._raw = None

	# --- body -----------------------------------------------------------
	@property
	def content(self):
		return self._content

	@property
	def text(self):
		return self._content.decode(self.encoding or "utf-8", errors="replace")

	@property
	def ok(self):
		return self.status_code is not None and self.status_code < 400

	@property
	def apparent_encoding(self):
		return self.encoding or "utf-8"

	def json(self, **kwargs):
		try:
			return _json.loads(self.text, **kwargs)
		except ValueError as e:
			raise exceptions.JSONDecodeError(str(e), self.text, 0) from None

	def iter_content(self, chunk_size=1024, decode_unicode=False):
		for start in range(0, len(self._content), max(chunk_size, 1)):
			part = self._content[start:start + max(chunk_size, 1)]
			yield part.decode(self.encoding, errors="replace") if decode_unicode else part

	def iter_lines(self, chunk_size=512, decode_unicode=False):
		for line in self.text.splitlines():
			yield line + "\n"

	def raise_for_status(self):
		if 400 <= (self.status_code or 0) < 500:
			raise exceptions.HTTPError(
				f"{self.status_code} Client Error for url: {self.url}",
				response=self)
		if 500 <= (self.status_code or 0) < 600:
			raise exceptions.HTTPError(
				f"{self.status_code} Server Error for url: {self.url}",
				response=self)

	def close(self):
		self._raw = None

	def __bool__(self):
		return self.ok

	def __repr__(self):
		return f"<Response [{self.status_code}]>"


class PreparedRequest:
	def __init__(self):
		self.method = None
		self.url = None
		self.headers = CaseInsensitiveDict()
		self.body = None

	def __repr__(self):
		return f"<PreparedRequest [{self.method}]>"