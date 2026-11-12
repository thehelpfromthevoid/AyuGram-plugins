"""Top-level mobile import: `from client_utils import ...`."""
from ayugram_compat.client_utils import *  # noqa: F401,F403
from ayugram_compat.client_utils import (  # noqa: F401
	AccountClient,
	CACHE_CLEAR_QUEUE,
	EXTERNAL_NETWORK_QUEUE,
	GLOBAL_QUEUE,
	NotificationCenterDelegate,
	PHONE_BOOK_QUEUE,
	PLUGINS_QUEUE,
	SEARCH_QUEUE,
	STAGE_QUEUE,
	THEME_QUEUE,
	_prepare_document,
	edit_message,
	get_account_instance,
	get_client,
	get_connections_manager,
	get_contacts_controller,
	get_download_controller,
	get_file_loader,
	get_hook_account,
	get_last_fragment,
	get_location_controller,
	get_media_controller,
	get_media_data_controller,
	get_messages_controller,
	get_messages_storage,
	get_notification_center,
	get_notifications_controller,
	get_notifications_settings,
	get_queue_by_name,
	get_secret_chat_helper,
	get_selected_account,
	get_send_messages_helper,
	get_user_config,
	run_on_queue,
	send_audio,
	send_document,
	send_message,
	send_photo,
	send_request,
	send_text,
	send_video,
	set_selected_account,
)


def __getattr__(name):
	"""Answer for a helper Desktop does not have, instead of failing the import.

	`from client_utils import _generate_photo_sizes` is how a plugin reaches a
	helper it expects a phone to provide. Raising AttributeError here kills the
	whole plugin at import over one function, and the only clue in the log is
	an ImportError naming that function - which reads as a broken AyuGram
	rather than as a missing capability.

	So an unknown helper comes back as something callable that does nothing,
	and the name is recorded so the plugin check can list it.
	"""
	if name.startswith("__"):
		raise AttributeError(name)
	from ayugram_compat.gaps import note_gap
	from org._stubs import Placeholder

	note_gap(f"client_utils.{name}")
	stub = Placeholder(f"client_utils.{name}")
	stub.__ayu_stub__ = True
	return stub
