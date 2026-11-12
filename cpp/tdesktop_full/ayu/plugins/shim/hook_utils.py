"""Top-level mobile import: `from hook_utils import ...`."""
from ayugram_compat.hook_utils import *  # noqa: F401,F403
from ayugram_compat.hook_utils import (  # noqa: F401
	# Not part of the mobile surface, but the base class needs them to map a
	# hook name onto a Desktop event, and the star import above does not carry
	# underscore names. Without these two every self.hook(...) registration
	# failed with an ImportError that was only visible in the plugin's log.
	_desktop_hook_name,
	_resolve_instance,
	add_timer,
	call_method,
	cancel_all_timers,
	command,
	find_class,
	get_method,
	get_private_field,
	get_static_private_field,
	hook,
	interval,
	on_hook,
	on_start,
	on_stop,
	remove_timer,
	set_private_field,
	set_static_private_field,
)
