"""Android TL runtime, mapped onto Desktop where possible."""

from . import tl  # noqa: F401
from org._stubs import Placeholder, android_module, note_use  # noqa: F401


class _TLRPC:
	"""The RPC method table. Mobile plugins probe it before calling."""

	def __getattr__(self, name):
		note_use(f"TLRPC.{name}")
		return Placeholder(f"TLRPC.{name}")


android_module(
	__name__,
	TLRPC=_TLRPC(),
	ConnectionsManager=Placeholder("ConnectionsManager"),
	RequestBase=Placeholder("RequestBase"),
	TLRPCRequest=Placeholder("TLRPCRequest"),
)

