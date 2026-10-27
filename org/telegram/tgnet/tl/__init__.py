"""Android TL namespace for Desktop.

`from org.telegram.tgnet.tl import TL_account` is how mobile plugins reach the
API. Those calls have no Desktop equivalent, so they are answered with
placeholders that record the use. The plugin keeps running and its Desktop
path keeps working; only the Android-only request does nothing.
"""

from org._stubs import Placeholder, android_module, note_use, unused  # noqa: F401


class _TLAccount:
	"""Records the Android request a plugin tried to make."""

	def __getattr__(self, name):
		note_use(f"TL_account.{name}()")
		return Placeholder(f"TL_account.{name}()")


class _TLClass:
	def __init__(self, label: str):
		self._label = label

	def __getattr__(self, name):
		if name.startswith("_"):
			raise AttributeError(name)
		note_use(f"{self._label}.{name}")
		return Placeholder(f"{self._label}.{name}")


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