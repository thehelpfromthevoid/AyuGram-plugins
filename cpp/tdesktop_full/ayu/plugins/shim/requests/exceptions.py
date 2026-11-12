"""Exception hierarchy mirroring requests.exceptions.

Kept faithful to the real names because plugins catch them by name, and a
plugin that catches HTTPError around a request must still catch ours.
"""

from urllib.error import HTTPError as _UrlHTTPError
from urllib.error import URLError as _UrlURLError


class RequestException(IOError):
	"""Base for everything this shim raises."""


class HTTPError(RequestException):
	def __init__(self, *args, response=None, request=None):
		super().__init__(*args)
		self.response = response
		self.request = request


class ConnectionError(RequestException):
	pass


class ProxyError(ConnectionError):
	pass


class SSLError(ConnectionError):
	pass


class Timeout(RequestException):
	pass


class ConnectTimeout(ConnectionError, Timeout):
	pass


class ReadTimeout(Timeout):
	pass


class TooManyRedirects(RequestException):
	pass


class URLRequired(RequestException):
	pass


class MissingSchema(RequestException, ValueError):
	pass


class InvalidURL(RequestException, ValueError):
	pass


class InvalidJSONError(RequestException):
	pass


class JSONDecodeError(InvalidJSONError, ValueError):
	pass


class ChunkedEncodingError(RequestException):
	pass


class ContentDecodingError(RequestException):
	pass


class StreamConsumedError(RequestException, TypeError):
	pass


class RetryError(RequestException):
	pass


# adapters re-export these; a plugin catching urllib's own errors should keep
# working when it catches the requests ones
RequestException.__module__ = __name__
HTTPError._url_error = _UrlHTTPError
URLError = _UrlURLError