"""Desktop compat for mobile Extera/AyuGram client_utils.

Enough surface for mobile-style plugins to import and run: plain-text
sending is bridged to ayu_api, queues run via threading, and Java
controllers/fragments return chainable no-op stubs (never None) so
`get_foo().bar().baz()` chains survive load instead of dying with
AttributeError.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from .mobile_stubs import stub as _stub

STAGE_QUEUE = "stageQueue"
GLOBAL_QUEUE = "globalQueue"
CACHE_CLEAR_QUEUE = "cacheClearQueue"
SEARCH_QUEUE = "searchQueue"
PHONE_BOOK_QUEUE = "phoneBookQueue"
THEME_QUEUE = "themeQueue"
EXTERNAL_NETWORK_QUEUE = "externalNetworkQueue"
PLUGINS_QUEUE = "pluginsQueue"

_hook_account: Optional[int] = None
_selected_account = 0


def _log(text: str) -> None:
	try:
		from ayu_api import log as _l
		_l(str(text))
	except Exception:
		print(f"[client_utils] {text}")


def _toast(text: str) -> None:
	try:
		from ayu_api import toast as _t
		_t(str(text))
	except Exception:
		print(f"[toast] {text}")


def get_hook_account() -> Optional[int]:
	return _hook_account


def get_selected_account() -> int:
	return _selected_account


def set_selected_account(account: int) -> None:
	global _selected_account
	_selected_account = int(account or 0)


def run_on_queue(
		fn: Callable,
		queue_name: str = PLUGINS_QUEUE,
		delay_ms: int = 0) -> None:
	def _runner():
		if delay_ms and delay_ms > 0:
			time.sleep(delay_ms / 1000.0)
		try:
			fn()
		except Exception as e:
			_log(f"run_on_queue error: {e}")

	threading.Thread(target=_runner, daemon=True).start()


def get_queue_by_name(name: str):
	return _stub(f"queue.{name}")


def send_request(request: Any, callback: Optional[Callable] = None, account: int = 0) -> int:
	_log("send_request is Android-only on Desktop; ignored")
	if callback:
		try:
			callback(None, None)
		except Exception:
			pass
	return 0


def send_text(
		peer_id: Any,
		text: str,
		replyToMsg: Any = None,
		parse_mode: Optional[str] = None,
		account: int = 0,
		**kwargs) -> None:
	peer = int(peer_id) if peer_id is not None else 0
	body = str(text or "")
	try:
		from ayu_api import send_text as _send
		_send(peer, body)
		return
	except Exception as e:
		_log(f"send_text fallback: {e}")
	_toast(f"[send] peer={peer}: {body[:80]}")


def send_message(params: Any, parse_mode: Optional[str] = None, account: int = 0, **kwargs) -> None:
	if isinstance(params, dict):
		peer = params.get("peer") or params.get("peer_id") or 0
		text = params.get("message") or params.get("text") or params.get("caption") or ""
		send_text(peer, text, parse_mode=parse_mode, account=account)
		return
	text = getattr(params, "text", getattr(params, "message", None))
	if isinstance(text, str):
		peer = getattr(params, "peer_id", getattr(params, "peer", 0)) or 0
		send_text(peer, text, parse_mode=parse_mode, account=account)
		return
	_log("send_message: unsupported params type")


def send_photo(peer_id, path, caption="", **kwargs):
	send_text(peer_id, caption or f"[photo] {path}")


def send_document(peer_id, path, caption="", **kwargs):
	send_text(peer_id, caption or f"[document] {path}")


def send_video(peer_id, path, caption="", **kwargs):
	send_text(peer_id, caption or f"[video] {path}")


def send_audio(peer_id, path, caption="", **kwargs):
	send_text(peer_id, caption or f"[audio] {path}")


def edit_message(message_obj, text=None, file_path=None, **kwargs):
	_log("edit_message is limited on Desktop")


def _ctrl_stub(name: str):
	"""Controller getter factory: always returns a chainable stub."""
	def _get(*args, **kwargs):
		return _stub(name)
	return _get


get_last_fragment = _ctrl_stub("fragment.last")
get_account_instance = _ctrl_stub("account.instance")
get_messages_controller = _ctrl_stub("messages.controller")
get_contacts_controller = _ctrl_stub("contacts.controller")
get_media_data_controller = _ctrl_stub("media_data.controller")
get_connections_manager = _ctrl_stub("connections.manager")
get_location_controller = _ctrl_stub("location.controller")
get_notifications_controller = _ctrl_stub("notifications.controller")
get_messages_storage = _ctrl_stub("messages.storage")
get_send_messages_helper = _ctrl_stub("send_messages.helper")
get_file_loader = _ctrl_stub("file.loader")
get_secret_chat_helper = _ctrl_stub("secret_chat.helper")
get_download_controller = _ctrl_stub("download.controller")
get_notifications_settings = _ctrl_stub("notifications.settings")
get_notification_center = _ctrl_stub("notification.center")
get_media_controller = _ctrl_stub("media.controller")
get_user_config = _ctrl_stub("user.config")


class AccountClient:
	def __init__(self, account: int = 0):
		self.account = int(account or 0)

	def send_text(self, peer_id, text, **kwargs):
		send_text(peer_id, text, account=self.account, **kwargs)

	def send_message(self, params, **kwargs):
		send_message(params, account=self.account, **kwargs)

	def get_user_config(self):
		return _stub("user.config")

	def get_messages_controller(self):
		return _stub("messages.controller")

	def get_last_fragment(self):
		return _stub("fragment.last")


def get_client(account: int = 0) -> AccountClient:
	return AccountClient(account)


class NotificationCenterDelegate:
	def didReceivedNotification(self, id: int, account: int, args=None):
		pass


def _prepare_document(*args, **kwargs):
	return _stub("document.prepared")
