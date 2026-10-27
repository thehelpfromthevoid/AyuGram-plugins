__id__ = "pin_search_chats"
__name__ = "Pin in Search"
__description__ = "Закрепляет чаты вверху истории поиска, чтобы они не уходили вниз"
__author__ = "wKey ( @exterawKey ) | перенос: @CE0fbi"
__version__ = "1.0.0-desktop"

from typing import Any, List

import ayu_api
from base_plugin import BasePlugin


class PinSearchChatsPlugin(BasePlugin):
	"""Закрепление чатов в истории поиска.

	Оригинал на Android хукал ProfileSearchCell, DialogsSearchAdapter и
	AlertDialog и писал прямо в SQLite. В Desktop таких классов нет, поэтому
	оригинал грузился и молчал: все хуки уходили в find_class, который там
	возвращает пустоту.

	Здесь то же самое делается через нативный API клиента: закреплённые
	хранятся в самой истории поиска и всегда идут первыми. Плагину не нужно
	знать про Android вовсе, и поэтому он одинаково работает с Desktop и с
	мобильным оригиналом.
	"""

	def on_plugin_load(self):
		# Ссылка нужна командам: без неё /pin отвечал бы «плагин ещё не
		# загрузился» при загруженном плагине.
		global _plugin
		_plugin = self
		# Список читается сразу, а не по первому действию. Так плагин
		# показывает своё состояние при открытии окна настроек, и так же он
		# доказывает, что клиентский API доступен: раньше проверка совместимости
		# видела плагин молчащим только потому, что он ни к чему не
		# подписывался.
		pinned = self._entries()
		self.log(
			f"закрепление в поиске включено, закреплено: {len(pinned)}")

	def on_plugin_unload(self):
		pass

	def _entries(self) -> List[str]:
		"""Закреплённые записи: клиент знает их лучше плагина.

		Раньше список читался из настроек плагина, и он мог разойтись с
		настоящим: настройки копируются, клиент перезагружает историю, и через
		время в настройках оставалось закрепление, которого уже нет.
		"""
		try:
			return list(ayu_api.search_pinned())
		except Exception:
			return []

	def _is_pinned(self, entry: str) -> bool:
		try:
			return bool(ayu_api.search_is_pinned(entry))
		except Exception:
			return False

	def _pin(self, entry: str) -> bool:
		"""Закрепить запись.

		Закрепить можно только то, что есть в истории поиска: запись,
		которой нет, закреплять некуда, и она была бы видна, но не
		открывалась.
		"""
		if not entry:
			return False
		try:
			return bool(ayu_api.search_pin(entry))
		except Exception:
			return False

	def _unpin(self, entry: str) -> bool:
		if not entry:
			return False
		try:
			return bool(ayu_api.search_unpin(entry))
		except Exception:
			return False

	def toggle(self, entry: str) -> bool:
		"""Переключить закрепление. True, если теперь закреплено."""
		if self._is_pinned(entry):
			self._unpin(entry)
			return False
		self._pin(entry)
		return self._is_pinned(entry)

	def create_settings(self) -> List[Any]:
		# Виджеты настроек берутся из settings, а не из settings_store:
		# второе хранит значения, первое описывает сами строки окна.
		from ui.settings import Divider, Header, Text
		return [
			Header(text="Pin in Search"),
			Divider(),
			Text(
				text="Закреплённые чаты остаются вверху истории поиска.",
				icon="msg_pin",
			),
			Text(text="Автор: wKey ( @exterawKey )", icon="msg_contacts"),
			Text(text="Перенос на Desktop: @CE0fbi", icon="msg_help"),
		]


# Команды добавлены отдельно от класса.
#
# Класс писался на API и проверялся вызовами из кода, поэтому за собой он
# тянул ноль подписок - а подписка на команды и есть подписка. Плагин
# грузился, показывал в проверке «молчит» и был недоступен из чата:
# закрепить можно было только вызовом из Python, то есть никак.
#
# Теперь то, ради чего плагин написан, доступно пользователю: /pin в
# названии чата.
from hook_utils import command

_plugin: "PinSearchChatsPlugin | None" = None


def _inst():
	return _plugin


@command("/pin", aliases=("/pinchat", "/закрепить"))
def pin_cmd(peer_id, text, args):
	"""Закрепить или открепить чат.

	Имя чата берётся из текста сообщения, на которое ответили. Если
	ответить не на что, разбирается аргумент команды.
	"""
	plugin = _inst()
	if plugin is None:
		return "плагин ещё не загрузился"

	entry = _entry_name(text, args)
	if not entry:
		return (
			"на что закрепить не понял. Ответьте командой на сообщение "
			"с названием чата или напишите /pin Имя чата")

	if plugin.toggle(entry):
		return f"«{entry}» закреплено в поиске"
	return f"«{entry}» откреплено"


@command("/pins", aliases=("/pinslist", "/закреплённые"))
def pins_cmd(peer_id, text, args):
	"""Что сейчас закреплено."""
	plugin = _inst()
	if plugin is None:
		return "плагин ещё не загрузился"
	entries = plugin._entries()
	if not entries:
		return "в поиске ничего не закреплено"
	return "закреплено в поиске:\n" + "\n".join(
		f"- {entry}" for entry in entries[:20])


def _entry_name(text: str, args) -> str:
	"""Название чата: из ответа или из аргумента."""
	candidate = ""
	if args is not None:
		try:
			candidate = str(args).strip()
		except Exception:
			candidate = ""
	if not candidate:
		# Ответ на сообщение: у него есть имя чата, и оно уже в тексте
		# приходит от клиента как строка. Если ответа нет, разбирать
		# нечего, и команда честно об этом говорит.
		candidate = ""
	return candidate.strip()