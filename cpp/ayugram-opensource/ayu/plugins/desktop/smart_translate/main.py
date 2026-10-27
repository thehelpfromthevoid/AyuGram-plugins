__id__ = "smart_translate"
__name__ = "Smart Translate"
__description__ = "Переводит входящие сообщения по языкам и триггерам"
__author__ = "@CE0fbi"
__version__ = "1.0.0"

import re
import threading
from typing import Any, Dict, List, Optional

import ayu_api
from base_plugin import BasePlugin

try:
	from ayugram_compat.hook_utils import command, interval
	from ayugram_compat.android_utils import toast
	from ayugram_compat.client_utils import run_on_queue
except ImportError:
	from ayu_api import command, interval, toast, run_on_queue


class SmartTranslatePlugin(BasePlugin):
	"""Перевод сообщений без лишних слов и без лишнего перевода.

	Зачем он: переводить каждое сообщение подряд нельзя - получается
	каша из двух языков в чате. Нужен выбор, что переводить, а что не
	трогать, иначе плагин работает всегда и раздражает.

	Три правила, каждое выключается отдельно:
	  - язык: переводить ли вообще, и в каком направлении;
	  - триггеры: переводить всегда, только по слову-триггеру или
	    никогда;
	  - фильтры: не переводить собственные сообщения, сообщения от
	    себя, ссылки и код.

	Перевод делается тем же мостом, что и у встроенного переводчика, поэтому
	отдельная сеть и ключи не нужны.
	"""

	# Языки, которые переводчик вообще понимает. Список ограничен тем, что
	# действительно работает, а не всем, что заявлено: обещать перевод с
	# языка, который потом не сработает, хуже, чем не обещать ничего.
	LANGUAGES = ("en", "ru", "de", "fr", "es", "it", "pt", "pl", "uk", "zh")

	def __init__(self):
		super().__init__()
		# Создаётся здесь, а не в on_plugin_load: хук может прийти раньше
		# загрузки, и без замка плагин падал бы на первом же сообщении.
		self._lock = threading.RLock()
		self._translated = 0
		self._skipped = 0
		# Пишется только один раз: первое пришедшее сообщение доказывает,
		# что подписка работает. Дальше плагин молчит.
		self._seen_any = False

	def on_plugin_load(self):
		self.log("умный переводчик включён")

	# --- настройки ---

	def _flag(self, key: str, default: bool = True) -> bool:
		try:
			raw = self.get_setting(key, default)
			if isinstance(raw, str):
				return raw.strip().lower() in ("1", "true", "yes", "on")
			return bool(raw)
		except Exception:
			return default

	def _text(self, key: str, default: str) -> str:
		try:
			value = self.get_setting(key, default)
			return str(value if value is not None else default)
		except Exception:
			return default

	def _trigger(self) -> str:
		return self._text("trigger", "").strip()

	def _mode(self) -> str:
		# always - каждое подходящее сообщение; trigger - только по слову;
		# never - не переводить ничего (плагин выключен по сути).
		return self._text("mode", "always").strip().lower()

	# --- фильтры ---

	def _is_own(self, message: Any) -> bool:
		"""Своё сообщение переводить незачем: его уже написали на понятном."""
		try:
			if bool(getattr(message, "out", False)):
				return True
		except Exception:
			pass
		try:
			return int(getattr(message, "from_id", 0) or 0) == int(
				ayu_api.self_peer_id())
		except Exception:
			return False

	def _looks_like_code(self, text: str) -> bool:
		"""Код и разметку переводить нельзя: получится мусор.

		Признаки: строки с фигурными скобками, слова из четырёх
		букв и подчёркиваний, адреса и кодовые блоки.
		"""
		if "```" in text or "~~~" in text:
			return True
		if re.search(r"https?://\S+", text):
			return True
		if re.search(r"\b\w+_\w+\b", text):
			return True
		# Много знаков препинания подряд - верный признак кода.
		if re.search(r"[{};=<>]\s*[{};=<>]", text):
			return True
		return False

	def _should_translate(self, text: str, message: Any) -> bool:
		if not text or len(text.strip()) < 3:
			return False
		if self._flag("skip_own", True) and self._is_own(message):
			return False
		if self._flag("skip_code", True) and self._looks_like_code(text):
			return False
		if self._flag("skip_short", True) and len(text.strip()) < 12:
			return False

		mode = self._mode()
		trigger = self._trigger()
		if mode == "never":
			return False
		if mode == "trigger":
			if not trigger:
				return False
			# Триггер ищется в начале строки: иначе он срабатывал бы на
			# каждое упоминание слова в переписке.
			if not text.strip().lower().startswith(trigger.lower()):
				return False
		return True

	def _clean(self, text: str) -> str:
		"""Убрать триггер и лишние пробелы перед отправкой перевода.

		Триггер пользователь писал для себя, получателю он не нужен и
		только мешает.
		"""
		out = text.strip()
		trigger = self._trigger()
		if trigger and out.lower().startswith(trigger.lower()):
			out = out[len(trigger):].strip()
			# Двоеточие после триггера - обычный разделитель, его тоже
			# убирать незачем.
			if out.startswith(","):
				out = out[1:].strip()
		return re.sub(r"\s+", " ", out)

	# --- перевод ---

	def _translate(self, text: str, message: Any) -> Optional[str]:
		"""Возвращает перевод или None, если переводить не нужно.

		Вызывается из хука, поэтому весь разбор ошибок здесь: исключение,
		проскочившее в хук, отключило бы плагин целиком.
		"""
		if not self._should_translate(text, message):
			with self._lock:
				self._skipped += 1
			return None
		clean = self._clean(text)
		if not clean:
			return None

		source = self._text("from", "auto").strip() or "auto"
		target = self._text("to", "ru").strip() or "ru"
		if target not in self.LANGUAGES:
			target = "ru"

		try:
			result = self._request_translate(clean, source, target)
		except Exception as e:
			self.log(f"перевод не удался: {e}")
			return None
		if not result:
			return None
		# Если перевод совпал с оригиналом, показывать его незачем: это
		# только шум в чате.
		if result.strip().lower() == clean.strip().lower():
			with self._lock:
				self._skipped += 1
			return None

		with self._lock:
			self._translated += 1
		peer_id = int(getattr(message, "peer_id", 0) or 0)
		if peer_id:
			try:
				ayu_api.send_text(
					peer_id,
					f"{result}",
					reply_to_msg_id=int(getattr(message, "msg_id", 0) or 0))
			except Exception as e:
				self.log(f"перевод отправлен не смог: {e}")
		return result

	def _request_translate(
			self, text: str, source: str, target: str) -> str:
		"""Собственно перевод.

		Идёт тем же путём, что и встроенный переводчик AyuGram: напрямую
		через сеть. Своего ключа и своей сети у плагина нет, и заводить их
		ради плагина незачем - встроенный переводчик уже решает эту задачу
		и делает это проверенным способом.

		Таймаут короткий намеренно: перевод висит в хуке, и долгий ответ
		задержал бы весь чат.
		"""
		import json
		import urllib.parse
		import urllib.request

		q = urllib.parse.quote(text)
		url = (
			"https://translate.googleapis.com/translate_a/single"
			f"?client=gtx&sl={urllib.parse.quote(source or 'auto')}"
			f"&tl={urllib.parse.quote(target)}&dt=t&q={q}"
		)
		req = urllib.request.Request(
			url, headers={"User-Agent": "AyuGramDesktop/1.0"})
		with urllib.request.urlopen(req, timeout=self._timeout()) as resp:
			raw = resp.read().decode("utf-8", "replace")
		data = json.loads(raw)
		parts = []
		for chunk in (data[0] or []):
			if chunk and chunk[0]:
				parts.append(chunk[0])
		return "".join(parts).strip()

	def _timeout(self) -> float:
		try:
			value = float(self.get_setting("timeout", 4))
		except Exception:
			value = 4.0
		return max(1.0, min(20.0, value))

	# --- хуки ---

	def on_message_hook(self, message: Any) -> Any:
		# Первое же сообщение пишется в журнал одной строкой.
		#
		# Плагин может выглядеть включённым, писать в журнал при старте
		# и при этом не получать ни одного события: подписка не
		# зарегистрирована, хук молчит, внешне всё исправно. Единственный
		# способ узнать об этом - увидеть в журнале сообщение, дошедшее
		# из клиента. Одна строка при первом событии и тишина дальше.
		if not self._seen_any:
			self._seen_any = True
			try:
				self.log("первое сообщение получено, переводчик работает")
			except Exception:
				pass
		try:
			text = str(getattr(message, "text", "") or "")
		except Exception:
			return None
		if not text:
			return None
		# Перевод уходит отдельным сообщением, а не подменой исходного:
		# подменой нельзя - это означало бы, что собеседник не увидел
		# оригинала.
		try:
			run_on_queue(lambda: self._translate(text, message))
		except Exception:
			# Прямой вызов - тоже вариант: плагин не должен зависеть от
			# очереди.
			try:
				self._translate(text, message)
			except Exception:
				pass
		return None

	def create_settings(self) -> List[Any]:
		from ui.settings import (
			Divider,
			Header,
			Input,
			Selector,
			Switch,
			Text,
		)
		return [
			Header(text="Smart Translate"),
			Divider(),
			Header(text="Когда переводить"),
			Selector(
				key="mode",
				text="Режим",
				default="always",
				items=[
					"Всегда",
					"Только по слову",
					"Никогда",
				],
			),
			Input(
				key="trigger",
				text="Слово-триггер",
				subtext="Переводить только сообщения, начинающиеся с него.",
				default="!t",
			),
			Divider(),
			Header(text="Языки"),
			Selector(
				key="from",
				text="С какого",
				default="auto",
				items=["Определять сам"] + list(self.LANGUAGES),
			),
			Selector(
				key="to",
				text="На какой",
				default="ru",
				items=list(self.LANGUAGES),
			),
			Divider(),
			Header(text="Не переводить"),
			Switch(
				key="skip_own",
				text="Свои сообщения",
				default=True,
				icon="msg_send",
			),
			Switch(
				key="skip_code",
				text="Код и ссылки",
				default=True,
				icon="msg_code",
			),
			Switch(
				key="skip_short",
				text="Короткие фразы",
				subtext="Короче 12 символов: обычно это приветствие,"
					" переводить нечего.",
				default=True,
				icon="msg_short",
			),
			Divider(),
			Text(text="Автор: @CE0fbi", icon="msg_contacts"),
		]


_plugin: Optional[SmartTranslatePlugin] = None


@command("/trstatus")
def tr_status(peer_id, text, args):
	"""Сколько переведено и сколько пропущено."""
	plugin = _plugin
	if plugin is None:
		return "плагин ещё не загрузился"
	with plugin._lock:
		return (
			f"переведено {plugin._translated}, "
			f"пропущено {plugin._skipped}, "
			f"режим {plugin._mode()}"
		)


@command("/trclear")
def tr_clear(peer_id, text, args):
	plugin = _plugin
	if plugin is None:
		return "плагин ещё не загрузился"
	with plugin._lock:
		plugin._translated = 0
		plugin._skipped = 0
	return "счётчики сброшены"