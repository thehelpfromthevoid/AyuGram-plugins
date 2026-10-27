"""Action bar bits. Colours and icons come from the client for real.

Раньше здесь стояла заглушка: getColor возвращала пустую строку. Плагин звал
int(Theme.getColor(...)), получал исключение и уходил на запасной цвет,
который в другой теме выглядел не так. Теперь цвет спрашивается у клиента по
имени, и он настоящий - тот же, что рисует само приложение.

Имя на Android выглядит как Theme.key_dialogTextBackground, а у клиента -
dialogTextBackground. Лишний префикс key_ снимается, чтобы плагину не пришлось
знать, в каком пространстве имён живут константы.
"""

import os

from org._stubs import Placeholder, android_module, note_use  # noqa: F401

_COLOUR_PREFIXES = ("key_",)
_COLOUR_FALLBACK = ""


def _clean_colour_name(name):
	"""Theme.key_dialogText -> dialogTextBackground.

	Возвращает None, если это точно не имя цвета, чтобы звать клиент только
	когда есть что спрашивать.
	"""
	if name is None:
		return None
	text = str(name)
	if not text:
		return None
	for prefix in _COLOUR_PREFIXES:
		if text.startswith(prefix):
			text = text[len(prefix):]
			break
	return text or None


class _Theme:
	def _colour(self, name, note):
		try:
			import ayu_api
		except Exception:
			note_use(note)
			return _COLOUR_FALLBACK
		cleaned = _clean_colour_name(name)
		if not cleaned:
			note_use(note)
			return _COLOUR_FALLBACK
		try:
			value = ayu_api.theme_color(cleaned)
		except Exception:
			note_use(note)
			return _COLOUR_FALLBACK
		if not value:
			# Имени нет в палитре клиента. Это не ошибка плагина, но и не
			# повод молчать: имя попадает в отчёт совместимости.
			note_use(f"theme:{cleaned}")
			return _COLOUR_FALLBACK
		return value

	def getColor(self, name):
		return self._colour(name, f"Theme.getColor({name})")

	def getColorName(self, name):
		return self._colour(name, f"Theme.getColorName({name})")

	def getColorOrThrow(self, name):
		value = self._colour(name, f"Theme.getColorOrThrow({name})")
		if not value:
			raise KeyError(name)
		return value

	def isCurrentThemeDark(self):
		"""Тёмная ли тема, по яркости фона.

		Отдельного флага клиент не отдаёт, а фона с именем windowBackgroundBg
		в этой версии нет - цвет берётся цепочкой: берётся первый из
		существующих. Иначе тёмная тема определялась бы как светлая, потому
		что первый же несуществующий цвет даёт пустую строку.
		"""
		for name in ("windowBackgroundBg", "profileBg", "storiesComposeBg",
			"historyEditMediaBg", "countryRowBg", "stickersEmojiPickerBg"):
			value = self.getColor(name)
			if not value:
				continue
			try:
				red = int(value[1:3], 16)
				green = int(value[3:5], 16)
				blue = int(value[5:7], 16)
			except (ValueError, IndexError):
				continue
			# 299 - стандартная яркость (ITU-R BT.601).
			luminance = (299 * red + 587 * green + 114 * blue) // 1000
			return luminance < 128
		note_use("Theme.isCurrentThemeDark")
		return False

	def icon_path(self, name):
		"""Путь к png с иконкой клиента по имени, '' если нет."""
		try:
			import ayu_api
		except Exception:
			note_use(f"Theme.icon({name})")
			return ""
		cleaned = str(name).split("/")[-1]
		if cleaned.endswith(".png"):
			cleaned = cleaned[:-4]
		try:
			path = ayu_api.icon_file(cleaned)
		except Exception:
			note_use(f"Theme.icon({name})")
			return ""
		if not path:
			note_use(f"Theme.icon({cleaned})")
			return ""
		return path

	def colour_names(self):
		"""Все имена цветов, которые понимает клиент."""
		try:
			import ayu_api
			return list(ayu_api.theme_color_names())
		except Exception:
			note_use("Theme.colour_names")
			return []

	def icon_names(self):
		"""Все имена иконок, которые понимает клиент."""
		try:
			import ayu_api
			return list(ayu_api.icon_names())
		except Exception:
			note_use("Theme.icon_names")
			return []

	def createChatResources(self, *args, **kwargs):
		note_use("Theme.createChatResources()")

	def loadWallpaper(self, *args, **kwargs):
		note_use("Theme.loadWallpaper()")

	def __getattr__(self, name):
		# Константы вида Theme.key_dialogTextBackground - это имена, а не
		# вызовы. Их значение заранее неизвестно, поэтому отдаём само имя:
		# getColor его примет и спросит у клиента.
		if name.startswith("key_"):
			return name

		note_use(f"Theme.{name}")
		return Placeholder(f"Theme.{name}")


android_module(
	__name__,
	Theme=_Theme(),
	ActionBar=Placeholder("ActionBar"),
	BaseFragment=Placeholder("BaseFragment"),
	SimpleTextView=Placeholder("SimpleTextView"),
)