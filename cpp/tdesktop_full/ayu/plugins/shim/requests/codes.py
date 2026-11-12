"""`requests.codes`, kept as names because plugins reference them."""

ok = 200
created = 201
accepted = 202
no_content = 204
partial = 206
multiple_choices = 300
moved_permanently = 301
found = 302
see_other = 303
not_modified = 304
temporary_redirect = 307
permanent_redirect = 308
bad_request = 400
unauthorized = 401
payment_required = 402
forbidden = 403
not_found = 404
method_not_allowed = 405
gone = 410
too_many_requests = 429
internal_server_error = 500
not_implemented = 501
bad_gateway = 502
service_unavailable = 503
gateway_timeout = 504


def _get(name, default=None):
	return globals().get(name, default)


__all__ = [k for k, v in list(globals().items()) if isinstance(v, int)]