__id__ = "anonymous_stickers"
__name__ = "Anonymous Stickers"
__description__ = "Отправляет стикеры без привязки к набору"
__author__ = "перенос на Desktop: @CE0fbi"
__version__ = "1.1.0-desktop"

from typing import Any, List, Optional

import ayu_api
from base_plugin import BasePlugin


class AnonymousStickersPlugin(BasePlugin):
	"""Стикеры уходят без указания набора.

	Оригинал на Android правил объект Document прямо в параметрах отправки:
	снимал с него sticker_set и подставлял копию. В Desktop объекта Document
	у плагина нет, а менять отправляемый файл можно ровно одним способом -
	указать другой путь через replacement_path.

	Приём срабатывает так: клиент берёт указанный файл и загружает его заново,
	а не отправляет уже скачанный документ. У свежей загрузки набора нет, и
	получатель не видит, из какого он взят.
	"""

	def on_plugin_load(self):
		self._logged = False
		# Подписка объявляется явно: метод с таким именем сам по себе
		# хуком не является, и без этой строки плагин загружался и молчал.
		self.add_on_media_sending_hook()
		self.log("анонимные стикеры включены")

	def _anonymize(self, media: Any) -> int:
		"""Один элемент: True, если замена применена."""
		try:
			if not media.sticker:
				return False
			path = getattr(media, "path", "") or ""
			if not path:
				return False
		except Exception:
			return False
		# Тот же файл, но отправлять его придётся заново. Кэшированный
		# документ с набором в отправку не пойдёт.
		try:
			media.replacement_path = path
		except Exception:
			return False
		return True

	def on_media_sending_hook(self, account: int, ctx: Any) -> None:
		"""Каждый элемент отправляемого списка проверяется отдельно.

		Подпись из двух аргументов обязательна: базовый класс зовёт хук как
		(account, контекст). С одним аргументом вызов падал бы, исключение
		уходило бы в лог плагина, и он молчал бы на каждой отправке.

		Список отдаётся живьём, и это важно: подмена пути должна дойти до
		клиента. Копия осталась бы здесь, и отправился бы оригинал.
		"""
		self.on_media_sending(ctx)
		media = getattr(ctx, "media", None)
		if not media:
			return
		replaced = 0
		try:
			for item in list(media):
				if self._anonymize(item):
					replaced += 1
		except Exception as e:
			self.log(f"не удалось обезличить: {e}")
			return
		if replaced and not self._logged:
			self._logged = True
			self.log(f"стикеров отправлено без набора: {replaced}")
		elif not replaced:
			self._logged = False

	def create_settings(self) -> List[Any]:
		from ui.settings import Divider, Header, Text
		return [
			Header(text="Anonymous Stickers"),
			Divider(),
			Text(
				text="Стикер отправляется заново, без привязки к набору.",
				icon="msg_sticker",
			),
			Text(text="Перенос на Desktop: @CE0fbi", icon="msg_help"),
		]