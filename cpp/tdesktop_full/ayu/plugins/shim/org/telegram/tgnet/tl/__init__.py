"""Android TL namespace for Desktop.

`from org.telegram.tgnet.tl import TL_account` is how mobile plugins reach the
API. Most of those calls have no Desktop equivalent and are answered with
placeholders that record the use, so the plugin keeps running and only the
Android-only request does nothing.

One class of call is different: a request a plugin *builds* and hands to
`send_request` is not inert, because the Desktop bridge can perform some of
them (account.updateProfile among them). For those the bridge needs to read the
method off the request, and it reads it from the request's type name - so the
request has to be an instance of a type actually named `TL_account.updateProfile`
rather than an anonymous placeholder. `tl_request_type` builds exactly that.
"""

from org._stubs import (  # noqa: F401
	Placeholder,
	android_module,
	note_use,
	tl_request_type,
	unused,
)


class _TLAccount:
	"""A namespace of Telegram account requests, named after each method."""

	def __getattr__(self, name):
		if name.startswith("_"):
			raise AttributeError(name)
		note_use(f"TL_account.{name}()")
		return tl_request_type(f"TL_account.{name}")


class _TLClass:
	def __init__(self, label: str):
		self._label = label

	def __getattr__(self, name):
		if name.startswith("_"):
			raise AttributeError(name)
		note_use(f"{self._label}.{name}")
		return tl_request_type(f"{self._label}.{name}")


def _tl(label: str):
	return _TLClass(label)


android_module(
	__name__,
	TL_account=_TLAccount(),
	TL_users=_tl("TL_users"),
	TL_messages=_tl("TL_messages"),
	TL_contacts=_tl("TL_contacts"),
	TL_updates=_tl("TL_updates"),
	TL_photo=_tl("TL_photo"),
	TL_upload=_tl("TL_upload"),
	TL_bot=_tl("TL_bot"),
)
