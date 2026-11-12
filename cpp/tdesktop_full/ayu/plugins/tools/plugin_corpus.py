"""Generate plugins that look like the ones exteraGram distributes, then load them.

The point is breadth. The plugins a user actually installs arrive as Android
`.plugin` packages written against the mobile API: they import `android.*` and
`java.*`, subclass BasePlugin, register hooks under Telegram's own TL names,
declare settings with the Android UI classes, and start their own threads. Any
one of those can be the thing that stops a plugin from loading, and a user
downloading twenty more will hit the one nobody tested.

So this writes a corpus that covers the shapes rather than the names, loads every
one of them through the same loader the app uses, and reports which fail and
why. It is generated rather than committed as files because the point is to grow
it as new patterns turn up.

Usage: plugin_corpus.py [--keep DIR] [PLUGINS_ROOT]
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _roots() -> list:
	"""The places a shim may live, in the order they should be tried.

	The repository keeps everything under plugins/: the shim in plugins/shim and
	the loader in plugins/standalone. A deployment flattens that - the shim's
	modules sit in the application root next to standalone/ - so a path built for
	one layout does not exist in the other, and these tools are run from both.
	"""
	return [HERE.parent, HERE.parent / "shim", HERE]


def _shim_root() -> Path:
	"""The directory holding base_plugin.py, which identifies the shim."""
	for base in _roots():
		if (base / "base_plugin.py").is_file():
			return base
	raise AssertionError(f"no shim found under {[str(b) for b in _roots()]}")


SHIM = _shim_root()
LOADER = next(
	(base / "standalone" for base in _roots()
	 if (base / "standalone" / "plugin_loader.py").is_file()),
	SHIM)

PLUGIN_HEADER = '''# @plugin_extera
import threading
import time

import ayu_api
from base_plugin import BasePlugin, HookResult, HookStrategy
from hook_utils import hook
import android_utils
'''

CORPUS: dict[str, str] = {}


def _add(name: str, body: str) -> None:
	CORPUS[name] = PLUGIN_HEADER + body


# A plugin that registers its hooks the documented way, with no Android at all.
_add("plain_base", '''
class PlainPlugin(BasePlugin):
    def on_plugin_load(self):
        self.add_on_message_hook()
        self.add_on_send_message_hook()
        self.add_on_message_deleted_hook()

    def on_message_hook(self, message):
        pass

    def on_send_message_hook(self, params):
        pass

    def on_message_deleted_hook(self, params):
        pass
''')

# Hooks declared with Telegram's own names, which is what the mobile packages do.
_add("tl_hook_names", '''
class TlPlugin(BasePlugin):
    def on_plugin_load(self):
        self.hook("TL_updateNewMessage", self._new)
        self.hook("TL_updateDeleteMessages", self._deleted)
        self.hook("TL_updateUserTyping", self._typing)

    def _new(self, update):
        pass

    def _deleted(self, update):
        pass

    def _typing(self, update):
        pass
''')

# Settings declared with the Android UI classes rather than plain values.
_add("android_settings_rows", '''
from ui.settings import Header, Switch, Input, EditText, Selector


class RowsPlugin(BasePlugin):
    def create_settings(self):
        return [
            Header("General"),
            Switch(key="enabled", text="Enabled", default=True),
            Input(key="limit", text="Limit", default=10),
            EditText(key="note", text="Note", default="hello"),
            Selector(key="mode", text="Mode", default=0, items=["a", "b", "c"]),
        ]

    def on_plugin_load(self):
        self.add_on_message_hook()
''')

# A plugin that keeps a background thread, the way typing and ghost plugins do.
_add("background_thread", '''
class ThreadPlugin(BasePlugin):
    def on_plugin_load(self):
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.add_on_message_hook()

    def _loop(self):
        while not self._stop:
            time.sleep(0.5)

    def on_message_hook(self, message):
        pass
''')

# Deep android imports, as the visual-editor family does.
_add("deep_android_imports", '''
from android.view import View
from android.widget import TextView, LinearLayout
from android.graphics.drawable import GradientDrawable
from java.util import ArrayList
from java.lang import String, StringBuilder


class DeepPlugin(BasePlugin):
    def on_plugin_load(self):
        self._items = ArrayList()
        self._items.add(StringBuilder("x").toString())

    def on_message_hook(self, message):
        pass
''')

# A plugin that reads and writes settings from its hooks.
_add("settings_from_hook", '''
class SettingsPlugin(BasePlugin):
    def create_settings(self):
        return [Switch(key="on", text="On", default=False)]

    def on_plugin_load(self):
        self.add_on_send_message_hook()

    def on_send_message_hook(self, params):
        flag = self.get_setting("on", False)
        set_setting("seen", bool(flag))
        if flag:
            return HookResult(HookStrategy.CANCEL)
        return HookResult(HookStrategy.NONE)
''')

# A plugin that sends, which is the path that used to fault hardest.
_add("sends_messages", '''
import ayu_api


class SendPlugin(BasePlugin):
    def create_settings(self):
        return [Input(key="times", text="Times", default=3)]

    def on_plugin_load(self):
        self.add_on_message_hook()

    def on_message_hook(self, message):
        for _ in range(int(self.get_setting("times", 3))):
            answer = ayu_api.send_text(getattr(message, "peer_id", 0), "reply")
            if not answer:
                ayu_api.log("send refused")
''')

# client_utils helpers, including the ones that had no implementation.
_add("client_utils_surface", '''
from client_utils import (
    send_message,
    send_text,
    edit_message,
    send_request,
    get_messages_controller,
    get_account_instance,
)


class UtilsPlugin(BasePlugin):
    def on_plugin_load(self):
        self.add_on_message_hook()

    def on_message_hook(self, message):
        send_message({"peer": 1, "message": "hi"})
        send_text(1, "hi")
        edit_message(1, 2, "hi")
        send_request("http://127.0.0.1:1/", None)
        get_messages_controller()
''')

# Asks where it is instead of waiting to be told, and uses a media helper.
_add("chat_and_media", '''
import ayu_api
from client_utils import send_photo


class ChatMediaPlugin(BasePlugin):
    def on_plugin_load(self):
        self.add_on_chat_opened_hook()
        self.add_on_message_hook()

    def on_chat_opened_hook(self, chat):
        current = ayu_api.get_current_chat()
        send_photo(getattr(current, "peer_id", 0), "C:/tmp/a.jpg")

    def on_message_hook(self, message):
        pass
''')

# The media helpers, which were stubs that logged "not supported" and returned
# False, so a plugin that sends a picture or a file did nothing at all.
_add("sends_media", '''
from client_utils import send_photo, send_document, send_video, send_audio


class MediaPlugin(BasePlugin):
    def on_plugin_load(self):
        self.add_on_message_hook()

    def on_message_hook(self, message):
        peer = getattr(message, "peer_id", 0)
        send_photo(peer, "C:/tmp/photo.jpg", "caption")
        send_document(peer, "C:/tmp/report.pdf")
        send_video(peer, "C:/tmp/clip.mp4")
        send_audio(peer, "C:/tmp/track.mp3")
''')

# run_on_ui_thread and run_on_main_thread, the marshalling helpers.
_add("thread_marshalling", '''
import android_utils
import ayu_api


class MarshallingPlugin(BasePlugin):
    def on_plugin_load(self):
        self.add_on_message_hook()

    def on_message_hook(self, message):
        android_utils.run_on_ui_thread(self._ui)
        android_utils.run_on_main_thread(self._main)
        ayu_api.run_on_main_thread(self._main)

    def _ui(self):
        pass

    def _main(self):
        pass
''')

# A plugin that only uses an unknown ayu_api name, which must not break loading.
_add("unknown_api_surface", '''
import ayu_api


class UnknownApiPlugin(BasePlugin):
    def on_plugin_load(self):
        ayu_api.do_something_mobile_only(1, 2)
        ayu_api.register_java_hook("SomeClass", "someMethod")
        self.add_on_message_hook()

    def on_message_hook(self, message):
        pass
''')

# Timers, which mobile plugins use for delayed work.
_add("timers", '''
import hook_utils


class TimerPlugin(BasePlugin):
    def on_plugin_load(self):
        self.add_timer(self._later, 1, 1)
        self.add_on_message_hook()

    def _later(self):
        pass

    def on_message_hook(self, message):
        hook_utils.cancel_all_timers()
''')

# A plugin with no BasePlugin at all, only module-level hooks.
_add("module_level_hooks", '''
import ayu_api


@ayu_api.hook("on_message")
def _incoming(message):
    pass


@ayu_api.hook("on_message_sending")
def _outgoing(params):
    pass
''')

# A plugin whose settings live only in module globals.
_add("globals_settings", '''
settings_schema = [
    {"key": "alpha", "type": "bool", "label": "Alpha", "default": True},
    {"key": "beta", "type": "int", "label": "Beta", "default": 5},
]


def get_setting(key, default=None):
    return _values.get(key, default)


_values = {"alpha": True, "beta": 5}


@ayu_api.hook("on_message")
def _incoming(message):
    pass
''')


def write_corpus(root: Path) -> int:
	for name, source in CORPUS.items():
		directory = root / name
		directory.mkdir(parents=True, exist_ok=True)
		(directory / "plugin.json").write_text(json.dumps({
			"id": name,
			"entry": "main.py",
			"api_version": 1,
			"permissions": ["messages", "ui"],
		}, indent=2), encoding="utf-8")
		(directory / "main.py").write_text(source, encoding="utf-8")
	return len(CORPUS)


def main() -> int:
	keep = None
	if "--keep" in sys.argv:
		index = sys.argv.index("--keep")
		keep = Path(sys.argv[index + 1]) if index + 1 < len(sys.argv) else None
		del sys.argv[index:index + 2]

	for path in (str(SHIM), str(LOADER), str(HERE)):
		if path not in sys.path:
			sys.path.insert(0, path)

	work = keep or Path(tempfile.mkdtemp(prefix="ayu-corpus-")) / "plugins"
	work.mkdir(parents=True, exist_ok=True)
	count = write_corpus(work)
	print(f"corpus: {count} plugins in {work}")

	import ayu_api  # noqa: E402
	from plugin_loader import PluginLoader  # noqa: E402

	loader = PluginLoader(work)
	infos = loader.scan()
	loaded = failed = 0
	for info in infos:
		ayu_api.clear_hooks()
		label = info.id
		if info.error:
			print(f"FAIL {label:<26} scan: {info.error[:120]}")
			failed += 1
			continue
		if not info.enabled:
			print(f"SKIP {label:<26} disabled")
			continue
		# The app instantiates the plugin itself after the module runs - the
		# native bridge calls activate_base_plugins - and that is what registers
		# the hooks declared with add_on_message_hook and friends. Loading the
		# code alone leaves every one of those unregistered, which would make a
		# working plugin look broken and hide the real failures.
		from base_plugin import activate_base_plugins  # noqa: E402

		real_load = loader.load_plugin_code

		def _load_with_activation(info=info, real=real_load):
			real(info)
			module = sys.modules.get(info.id) or sys.modules.get("__main__")
			try:
				import importlib.util
				source = work / info.id / (info.entry or "main.py")
				spec = importlib.util.spec_from_file_location(
					f"corpus_{info.id}", source)
				fresh = importlib.util.module_from_spec(spec)
				sys.modules[spec.name] = fresh
				spec.loader.exec_module(fresh)
				activate_base_plugins(vars(fresh))
			except Exception as e:  # noqa: BLE001 - reported below
				print(f"FAIL {label:<26} {type(e).__name__}: {str(e)[:120]}")
				raise

		try:
			loader.load_plugin_code = _load_with_activation
			_load_with_activation()
		except Exception:
			failed += 1
			continue
		finally:
			loader.load_plugin_code = real_load
		loaded += 1
		hooks = sorted(ayu_api._hooks.keys())
		print(f"ok   {label:<26} hooks: {', '.join(hooks) or 'none'}")

	print(f"\n{loaded} loaded, {failed} failed, {count} total")
	if not keep:
		shutil.rmtree(work.parent, ignore_errors=True)
	return 1 if failed else 0


if __name__ == "__main__":
	raise SystemExit(main())