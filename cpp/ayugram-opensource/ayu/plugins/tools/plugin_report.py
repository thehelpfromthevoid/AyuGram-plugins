"""Load every installed plugin and say plainly what each one can and cannot do.

Compatibility work so far proved that a plugin loads and registers hooks. That
is not the same as it working, and the difference is exactly what a person
cannot see: a plugin can be listed as loaded, look enabled, and still never do
anything because the thing it needs is not on this platform. Ozon search is the
example - it works perfectly and then reports that the site refused it.

So this runs each plugin the way the app does, feeds it nothing, and reports:

* whether it loads and makes an instance
* which Desktop events it actually subscribed to
* which Android and Java names it reached for and did not get

The last line is the useful one. It turns "this plugin does nothing" into a list
of what it wanted, which is the difference between a dead end and a decision.

Usage: plugin_report.py [PLUGINS_ROOT]
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _roots() -> list:
	"""The places a shim may live, in the order they should be tried.

	The repository keeps everything under plugins/: the shim in plugins/shim
	and the loader in plugins/standalone. A deployment flattens that - the shim's
	modules sit in the application root next to standalone/ - so a path built
	for one layout does not exist in the other, and this tool is run from both.
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
DEFAULT_ROOT = Path(os.environ["AYU_PLUGINS_DIR"]) if os.environ.get("AYU_PLUGINS_DIR") else (Path(os.environ["APPDATA"]) / "AyuGramDesktop" / "plugins")

# Папки, которые лежат рядом с плагинами, но плагинами не являются:
# мост совместимости, автономный загрузчик, песочница, образцы и
# инструменты. Их файлы - обычный код клиента. Без этого списка отчёт
# писал «не грузится: no entry script» про shim и standalone, то есть
# обвинял в поломке собственные служебные папки AyuGram.
NOT_PLUGINS = {
	"shim",
	"standalone",
	"sandbox",
	"examples",
	"tools",
	"desktop",
	"docs",
	"bridge",
	"ui",
}


class Bridge:
	def __init__(self) -> None:
		self.hooks: dict = {}
		self.sent = 0
		# API, которым плагин доказал, что умеет работать. Закрепление в
		# поиске не подписывается ни на один хук: оно зовёт клиент напрямую,
		# и по подпискам такой плагин выглядел бы мёртвым.
		self.used_api: set = set()

	def deliver(self, hook_name: str, *args) -> int:
		"""Вызывает подписчиков события, как это делает клиент.

		Нужно для проверки доставки: подписаться можно и ни разу не получить
		событие, и без этого отчёт не отличил бы работающий плагин от
		молчащего.
		"""
		called = 0
		for fn in list(self.hooks.get(hook_name, ())):
			try:
				fn(*args)
				called += 1
			except Exception:
				continue
		return called

	def __getattr__(self, name):
		if name == "hook":
			def _hook(hook_name, fn=None, *a, **k):
				# Only a real callback counts. A plugin can declare an event by
				# name alone, and the bridge cannot subscribe a null function, so
				# the application reports no subscription either - counting it
				# here would credit a plugin with working that it is not doing.
				if callable(fn):
					self.hooks.setdefault(hook_name, []).append(fn)
				return fn
			return _hook
		if name in ("send_text", "send_message"):
			return lambda *a, **k: self.sent or 1
		if name in ("search_pin", "search_unpin", "search_pinned",
					"search_is_pinned"):
			# Закрепление в поиске идёт через нативный API клиента, а не
			# через хук. Отмечаем вызов, чтобы плагин не сочли мёртвым
			# только из-за отсутствия подписок.
			self.used_api.add(name)

			# Ответ без разбора аргументов: проверка интересуется фактом
			# вызова, а не тем, что именно плагин закрепил.
			return lambda *a, **k: name == "search_pinned"
		if name == "toast":
			return lambda *a, **k: None
		if name == "run_on_main_thread":
			return lambda fn, *a, **k: fn(*a, **k)
		if name == "settings_emit":
			return lambda *a, **k: None
		if name in ("plugins_dir", "data_dir"):
			return str(ROOT)
		if name in ("send_file", "edit_message"):
			return lambda *a, **k: 1
		if name == "copy_to_clipboard":
			return lambda *a, **k: True

		def _stub(*a, **k):
			return None
		return _stub


def enabled_ids(root: Path) -> dict:
	state = root / "plugins.json"
	out = {}
	if state.is_file():
		try:
			out = json.loads(state.read_text(encoding="utf-8-sig"))
		except Exception:
			out = {}
	return out


def switched_off_by_itself(directory: Path) -> bool:
	"""True when the plugin's own settings say it should do nothing.

	`custom_nickname` reads an `enabled` setting of its own and arms nothing when
	it is false. Listed as "no events on Desktop" that reads as a broken plugin
	rather than one somebody turned off, which is the opposite of what happened
	and sends the next person looking in the wrong place.
	"""
	path = directory.with_suffix(".settings.json")
	if not path.is_file():
		return False
	try:
		settings = json.loads(path.read_text(encoding="utf-8-sig"))
	except Exception:
		return False
	values = settings.get("settings")
	if not isinstance(values, dict):
		return False
	for key in ("enabled", "enable", "is_enabled"):
		if key in values:
			return not bool(values[key])
	return False


def _watch_self_driving(record: list):
	"""Notice a plugin that keeps itself running without any event.

	Some plugins are supposed to be event-free: custom_nickname arms a repeating
	queue task and does its work on a timer, and there is nothing for it to hook.
	Counting only subscriptions called that "no events" and listed a working
	plugin among the dead ones, which is the opposite of what this report is for.

	The shim is patched before a plugin is imported, because a plugin does
	`from client_utils import run_on_queue` at module level and would otherwise
	bind the original before anything could see it.
	"""
	import client_utils
	import hook_utils

	if getattr(hook_utils, "_report_wrapped", False):
		return

	originals = {
		"run_on_queue": client_utils.run_on_queue,
		"interval": hook_utils.interval,
		"add_timer": hook_utils.add_timer,
	}

	def _wrap(name, func):
		def _recorded(*args, **kwargs):
			record.append(name)
			return func(*args, **kwargs)
		return _recorded

	for name, func in originals.items():
		recorded = _wrap(name, func)
		if name == "run_on_queue":
			client_utils.run_on_queue = recorded
			hook_utils.run_on_queue = getattr(hook_utils, name, recorded)
		else:
			setattr(hook_utils, name, recorded)

	import threading
	real_thread = threading.Thread.__init__

	def _thread_init(self, *args, **kwargs):
		record.append("thread")
		return real_thread(self, *args, **kwargs)

	threading.Thread.__init__ = _thread_init
	hook_utils._report_wrapped = True


def load(directory: Path, bridge: Bridge):
	entry = "main.py"
	manifest = directory / "plugin.json"
	if manifest.is_file():
		try:
			entry = json.loads(
				manifest.read_text(encoding="utf-8-sig")).get("entry", entry)
		except Exception:
			pass
	script = directory / entry
	if not script.is_file():
		script = directory / "main.py"
	if not script.is_file():
		return None, "no entry script"
	name = f"probe_{directory.name}"
	sys.modules.pop(name, None)
	spec = importlib.util.spec_from_file_location(name, script)
	module = importlib.util.module_from_spec(spec)
	sys.modules[name] = module
	try:
		spec.loader.exec_module(module)
	except Exception as e:
		return None, f"{type(e).__name__}: {str(e)[:90]}"
	from base_plugin import activate_base_plugins
	try:
		instance = activate_base_plugins(vars(module))
	except Exception as e:
		return None, f"on_plugin_load: {type(e).__name__}: {str(e)[:80]}"
	return instance, None


def main() -> int:
	global ROOT
	ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROOT

	for path in (str(SHIM), str(LOADER), str(HERE)):
		if path not in sys.path:
			sys.path.insert(0, path)
	os.environ["AYU_PLUGINS_DIR"] = str(ROOT)

	from org._stubs import unused as stubs_used

	# One bridge for the whole run, with its hook registry cleared between
	# plugins. A fresh bridge per plugin did not work: the hook plumbing holds
	# onto the first `ayu_api` it sees, so every plugin after the first reported
	# no events at all - including ones the application shows as subscribed. A
	# report that says a working plugin is dead is worse than no report.
	bridge = Bridge()
	sys.modules["ayu_api"] = bridge
	self_driven: list = []
	_watch_self_driving(self_driven)

	state = enabled_ids(ROOT)
	# Список читается один раз и до цикла: он нужен и при построении
	# таблицы, и в итогах. Раньше он вычислялся в конце, и таблица
	# обращалась к переменной, которая ещё не была определена - отчёт
	# падал целиком вместо того, чтобы показать строку.
	ANDROID_ONLY = _android_only_ids()
	rows = []
	for directory in sorted(ROOT.iterdir()):
		# Инфраструктура клиента лежит рядом с плагинами, но плагином не
		# является. Без этой проверки отчёт писал «не грузится: no entry
		# script» про shim и standalone - то есть обвинял в поломке
		# собственные служебные папки AyuGram.
		if directory.name in NOT_PLUGINS:
			continue
		if not directory.is_dir() or not list(directory.glob("*.py")):
			continue
		pid = directory.name
		on = bool(state.get(pid, False))
		bridge.hooks.clear()
		bridge.used_api.clear()
		self_driven.clear()
		before = set(stubs_used())
		try:
			instance, error = load(directory, bridge)
		except Exception:
			instance, error = None, traceback.format_exc().splitlines()[-1][:90]
		hooks = sorted(bridge.hooks.keys())
		reached = sorted(set(stubs_used()) - before)
		delivered, detail = _probe_delivery(directory, hooks, bridge)
		if error:
			rows.append((pid, on, "не грузится", error[:70], _trim(reached)))
		elif delivered:
			rows.append((pid, on, "работает", detail, _trim(reached)))
		elif self_driven:
			rows.append((pid, on, "работает сам",
						 "события не нужны, само " + ", ".join(
							 sorted(set(self_driven))),
						 _trim(reached)))
		elif bridge.used_api:
			# Плагин доказал, что работает, нативным API: так делает
			# закрепление в поиске. Без этой строки он попадал в молчащие
			# только потому, что не подписывается ни на один хук.
			rows.append((pid, on, "работает",
						 "через клиентский API: " + ", ".join(
							 sorted(bridge.used_api)),
						 _trim(reached)))
		elif switched_off_by_itself(directory):
			rows.append((pid, on, "выключен",
						 "выключен своими настройками, это не поломка",
						 _trim(reached)))
		elif pid in ANDROID_ONLY:
			# Android-only показываем в таблице тем же вердиктом, что и
			# в итогах. Раньше он попадал в «молчит», и таблица говорила
			# одно, а счётчик внизу - другое: пользователь видел
			# «молчит» рядом со строкой «только для Android, чинить
			# нечем», и это выглядело как противоречие, а не как
			# объяснение.
			rows.append((pid, on, "только Android",
						 "переносу на Desktop не поддаётся",
						 _trim(reached)))
		else:
			rows.append((pid, on, "молчит",
						 "подписан, но ни одно событие не дошло",
						 _trim(reached)))

	width = max((len(r[0]) for r in rows), default=10) + 2
	print(f"{'plugin':<{width}} {'вкл':<5} {'итог':<10} подробности")
	print("-" * (width + 76))
	for pid, on, verdict, detail, reached in rows:
		mark = "да" if on else "нет"
		print(f"{pid:<{width}} {mark:<5} {verdict:<10} {detail}")
		if reached:
			print(f"{'':<{width}} {'':<5} {'':<10}   просило у Android/Java: {reached}")

	working = sum(1 for r in rows if r[2].startswith("работает"))
	silent = [r[0] for r in rows if r[2] == "молчит"]
	off = [r[0] for r in rows if r[2] == "выключен"]
	broken = [r[0] for r in rows if r[2] == "не грузится"]
	# Список берём файлом, а не импортом шима: импорт здесь менял то,
	# как грузятся плагины, и счётчик внизу сбивался.
	# Android-only в таблице больше не попадает в «молчит», поэтому он
	# отделяется здесь напрямую, а не вычитанием из молчащих.
	only_android = [r[0] for r in rows if r[2] == "только Android"]
	fixable = [n for n in silent + broken if n not in ANDROID_ONLY]
	print()
	print(f"{working} из {len(rows)} делают что-то на Desktop")
	if only_android:
		print("только для Android, переносу не поддаются: "
			  + ", ".join(only_android))
	if fixable:
		print("молчат, есть смысл чинить: " + ", ".join(fixable))
	if off:
		print("выключены своими настройками: " + ", ".join(off))
	left = [n for n in broken if n not in ANDROID_ONLY]
	if left:
		print("не грузятся: " + ", ".join(left))
	return 0


def _android_only_ids() -> set:
	"""Идентификаторы плагинов, помеченных как Android-only.

	Читается прямо из файла-реестра: импорт шима здесь ломал загрузку
	плагинов, а список нужен только для подписи в отчёте.
	"""
	path = SHIM / "ayugram_compat" / "android_only.py"
	names: set = set()
	try:
		text = path.read_text(encoding="utf-8")
	except OSError:
		return names
	in_block = False
	for line in text.splitlines():
		stripped = line.strip()
		if stripped.startswith("ANDROID_ONLY"):
			in_block = True
			continue
		if in_block:
			if stripped.startswith("}"):
				break
			if stripped.startswith('"'):
				names.add(stripped.split('"')[1])
	return names


def _probe_delivery(directory: Path, hooks: list, bridge) -> tuple:
	"""Отдаёт плагину настоящее событие и смотрит, вызвали ли его.

	Раньше «работает» означало «хук зарегистрирован». Это враньё: хук можно
	зарегистрировать и ни разу не получить, и именно так выглядит плагин,
	который молчит, потому что событие до него не доходит. Здесь событие
	отправляется по-настоящему, через тот же путь, что и в клиенте.
	"""
	if not hooks:
		return False, ""
	calls: list = []
	catcher = type("Catcher", (), {
		"__call__": lambda self, *a, **k: calls.append((a, k)) or None,
	})()
	keep = dict(bridge.hooks)
	try:
		for name in hooks:
			bridge.hooks[name] = [catcher]
		_synthesise(bridge)
	finally:
		bridge.hooks.clear()
		bridge.hooks.update(keep)
	return bool(calls), "доставлено: " + ", ".join(hooks)


def _synthesise(bridge) -> None:
	"""Отправляет по событию каждого вида, как это делает клиент."""
	class Msg:
		msg_id = 1
		peer_id = 4242
		from_id = 900
		dialog_id = 4242
		date = 1700000000
		out = False
		post = False
		text = message = "probe"

	class Chat:
		peer_id = 4242
		type = 0
		title = "probe"

	class Typing:
		peer_id = 4242
		user_id = 900
		top_msg_id = 0
		action = "TypingAction"
		kind = 0

	bridge.deliver("on_message", 0, Msg())
	bridge.deliver("on_message_sending", 0, Msg())
	bridge.deliver("on_chat_opened", 0, Chat())
	bridge.deliver("on_typing", 0, Typing())
	bridge.deliver("on_message_deleted", 4242, [1])
	# Media sending was never delivered, so a plugin acting on it -
	# replacing a file before it goes out - looked as dead as one
	# that really does nothing.
	class Media:
		size = 10
		kind = 0
		sticker = False
		caption = ""
		name = "probe.webm"
		path = ""
		replacement_path = ""
	
	class Sending:
		cancel = False
		text = ""
		account = 0
		peer_id = 4242
		message = Msg()
		media = []
	
	bridge.deliver("on_media_sending", 0, Sending())


def _trim(names: list, limit: int = 6) -> str:
	if not names:
		return ""
	shown = ", ".join(names[:limit])
	return shown + (f" … +{len(names) - limit}" if len(names) > limit else "")


if __name__ == "__main__":
	raise SystemExit(main())
