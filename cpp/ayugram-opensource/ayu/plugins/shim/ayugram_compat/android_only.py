"""Плагины, которые на Desktop перенести нельзя в принципе.

Не «сломанные» и не «ещё не сделано», а именно Android-only. Разница видна
пользователю: первый случай можно починить, второй нельзя, и обещать первое
было бы враньём.

Признак один: плагин дергает живые объекты Android, которых на Desktop нет
физически, - экраны, View, рисование через Canvas. Списка Android API,
которые стоит заменить на вызовы клиента, тут нет: такие плагины заменяются
целиком, поимённо.
"""

from __future__ import annotations

from typing import Dict

# Имя папки плагина -> почему перенос невозможен
ANDROID_ONLY: Dict[str, str] = {
	"md3_navbars_everywhere":
		"вешается на Android-экраны (ChatAttachAlert, GlassTabView, "
		"StarGiftPreviewSheet) и рисует через Canvas; на Desktop их нет",
	"animtou":
		"Android-only, отказывается грузиться сам",
	"visual_editor":
		"Рисует свой интерфейс целиком: Bitmap, Canvas, View, диалоги "
		"и списки Android. Переносить тут нечего: на Desktop другой "
		"инструментарий, и подменять тут нечем",
}


def reason_for(plugin_id: str) -> str | None:
	"""Причина Android-only для плагина либо None, если он переносим."""
	# Идентификаторы бывают и именем папки, и полным id из manifest.
	for known, reason in ANDROID_ONLY.items():
		if plugin_id == known or plugin_id.endswith("." + known) \
				or plugin_id.split(".")[-1] == known:
			return reason
	return None


def is_android_only(plugin_id: str) -> bool:
	return reason_for(plugin_id) is not None