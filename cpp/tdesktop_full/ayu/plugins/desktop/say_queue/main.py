__id__ = "say_queue"
__name__ = "Say Queue"
__description__ = "Пишет сообщения по команде, с ограничением скорости"
__author__ = "@CE0fbi"
__version__ = "1.0.0"

import threading
import time
from typing import Any, List, Optional

import ayu_api
from base_plugin import BasePlugin

try:
	from ayugram_compat.hook_utils import command
	from ayugram_compat.android_utils import toast
except ImportError:
	from ayu_api import command, toast


class SayQueuePlugin(BasePlugin):
	"""Отправка сообщений по команде с ограничением скорости.

	Telegram ограничивает частоту отправки, и за её превышение аккаунт
	получает блокировку на время, растущее с каждым нарушением. Просто
	отправить пачку сообщений подряд поэтому плохо: аккаунт потом не
	работает.

	Здесь сообщения выстраиваются в очередь, и между отправками выдерживается
	пауза. Если Telegram всё же ответил отказом, очередь встаёт на то время,
	которое он назвал, и ждёт. Такой отказ ловится на стороне клиента и
	передаётся плагину через on_flood_wait.
	"""

	def on_plugin_load(self):
		global _plugin
		self._lock = threading.RLock()
		# Очередь пар (куда, что). Список, а не deque: доступ из двух потоков,
		# и блокировка всё равно нужна.
		self._queue: List[Any] = []
		self._sent_at = 0.0
		self._flood_until = 0.0
		self._timer: Optional[threading.Timer] = None
		self._running = False
		# Обработчики команд живут на уровне модуля и не знают про self,
		# поэтому экземпляр кладётся здесь.
		_plugin = self
		self.log("очередь отправки готова")

	def on_plugin_unload(self):
		self._stop_timer()

	# --- ограничение скорости ---

	def _interval(self) -> float:
		"""Пауза между сообщениями, в секундах.

		Значение настройки округляется и ограничивается снизу: ноль или
		отрицательное значение означали бы отправку без пауз, то есть ровно
		то, от чего этот плагин и защищает.
		"""
		try:
			raw = self.get_setting("interval", 5)
			value = float(raw)
		except Exception:
			value = 5.0
		return max(0.5, min(600.0, value))

	def _wait_left(self) -> float:
		"""Сколько ещё ждать перед следующей отправкой."""
		now = time.monotonic()
		# Отказ Telegram важнее обычной паузы: если он назвал 300 секунд,
		# ждать надо 300, а не 5.
		flood = self._flood_until - now
		regular = (self._sent_at + self._interval()) - now
		return max(0.0, flood, regular)

	def _on_flood_wait(self, seconds: Any = None, *args) -> None:
		"""Telegram отказал в отправке и назвал время ожидания.

		Плагин сам этого отказа не увидит: клиент отправляет сообщение и
		возвращает "принято", а отказ приходит позже и мимо плагинов. Поэтому
		вызывается вручную, а автоматической реакции на отказ пока нет -
		пауза между сообщениями подобрана так, чтобы до него не доходить.
		"""
		try:
			wait = float(seconds) if seconds is not None else 60.0
		except Exception:
			wait = 60.0
		wait = max(1.0, min(3600.0, wait))
		with self._lock:
			self._flood_until = time.monotonic() + wait
		self.log(f"Telegram попросил подождать {int(wait)} с")
		self._schedule()

	# --- очередь ---

	def enqueue(self, peer_id: int, text: str) -> int:
		"""Поставить сообщение в очередь. Вернёт размер очереди."""
		if not text.strip():
			return 0
		with self._lock:
			self._queue.append((int(peer_id), text))
			size = len(self._queue)
		self.log(f"в очереди {size} сообщений")
		self._schedule()
		return size

	def queue_size(self) -> int:
		with self._lock:
			return len(self._queue)

	def clear(self) -> int:
		"""Очистить очередь. Вернёт, сколько сообщений отброшено."""
		with self._lock:
			dropped = len(self._queue)
			self._queue.clear()
		self._stop_timer()
		if dropped:
			self.log(f"очередь очищена, отброшено {dropped}")
		return dropped

	def _schedule(self) -> None:
		"""Запустить отправку, когда подойдёт время."""
		with self._lock:
			if not self._queue or self._running:
				return
			self._running = True
		self._pump()

	def _stop_timer(self) -> None:
		with self._lock:
			if self._timer is not None:
				self._timer.cancel()
				self._timer = None

	def _pump(self) -> None:
		"""Отправить следующее сообщение, если время пришло."""
		with self._lock:
			wait = self._wait_left()
			if not self._queue:
				self._running = False
				return
		if wait > 0:
			# Ещё рано. Планируем следующую попытку и выходим, очередь не
			# трогаем.
			timer = threading.Timer(wait, self._pump)
			timer.daemon = True
			with self._lock:
				self._timer = timer
			timer.start()
			return

		with self._lock:
			if not self._queue:
				self._running = False
				return
			peer_id, text = self._queue.pop(0)
			self._sent_at = time.monotonic()

		ok = False
		try:
			ok = bool(ayu_api.send_text(peer_id, text))
		except Exception as e:
			self.log(f"отправка не удалась: {e}")

		with self._lock:
			more = bool(self._queue)
			# Флаг сбрасывается здесь, а не в _schedule: тот всё ещё видит
			# _running=True и вышел бы молча, очередь осталась бы стоять
			# навсегда. Именно так и пропадало второе сообщение.
			self._running = False
		if ok:
			self.log(f"отправлено в {peer_id}, осталось {self.queue_size()}")
		if more:
			self._schedule()
		else:
			self.log("очередь разобрана")

	# --- команды ---

	def create_settings(self) -> List[Any]:
		from ui.settings import (
			Divider,
			Header,
			Input,
			Text,
		)
		# Ключ interval обязан быть здесь: настройка приходит в плагин из
		# окна, и без объявления строки в плагине она осталась бы в файле
		# настроек, но никогда не дошла бы до get_setting. Раньше пауза
		# была всегда 5 с, и настройка выглядела рабочей, но не влияла ни
		# на что.
		return [
			Header(text="Say Queue"),
			Input(
				key="interval",
				text="Пауза между сообщениями, в секундах",
				subtext="Telegram блокирует аккаунт за частую отправку.",
				default=5,
			),
			Divider(),
			Header(text="Команды"),
			Text(text="/say <текст> - поставить в очередь", icon="msg_send"),
			Text(
				text="/sayhere <текст> - поставить в очередь сюда",
				icon="msg_send",
			),
			Text(text="/sayclear - очистить очередь", icon="msg_clear"),
			Text(text="/saystatus - сколько в очереди", icon="msg_info"),
		]


_plugin: Optional[SayQueuePlugin] = None


def _instance() -> Optional[SayQueuePlugin]:
	"""Живой экземпляр плагина.

	Обработчик команды вызывается шим, а не плагином, и к self у него нет:
	объект создаётся при загрузке, и ссылка на него кладётся здесь.
	"""
	return _plugin


@command("/say", aliases=("/sayhere",))
def say(peer_id, text, args):
	"""Поставить сообщение в очередь.

	Возвращается текст, которым команда заменится: он уходит в тот же чат и
	показывает, что очередь приняла сообщение, а не что оно уже отправлено.
	Отправка впереди, спустя паузу.
	"""
	plugin = _instance()
	if plugin is None:
		return "плагин ещё не загрузился"
	body = text.strip()
	# Отрезается сама команда, иначе в сообщение уехал бы "/say ".
	head = body.split(None, 1)
	body = head[1].strip() if len(head) > 1 else ""
	if not body:
		return "что сказать-то? /say <текст>"
	size = plugin.enqueue(peer_id, body)
	return f"в очереди {size}, уйдёт с паузой {plugin._interval():g} с"


@command("/sayclear")
def say_clear(peer_id, text, args):
	plugin = _instance()
	if plugin is None:
		return "плагин ещё не загрузился"
	dropped = plugin.clear()
	return f"очередь очищена, отброшено {dropped}" if dropped else "очередь и так пуста"


@command("/saystatus")
def say_status(peer_id, text, args):
	plugin = _instance()
	if plugin is None:
		return "плагин ещё не загрузился"
	return (
		f"в очереди {plugin.queue_size()}, "
		f"пауза {plugin._interval():g} с"
	)