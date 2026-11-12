"""`android.widget` on Desktop - text is captured so filters still work."""
from __future__ import annotations

from android.view import View, ViewGroup
from ayugram_compat.mobile_stubs import _StubObject

__all__ = [
	"TextView",
	"EditText",
	"Button",
	"LinearLayout",
	"FrameLayout",
	"RelativeLayout",
	"ScrollView",
	"Toast",
	"ImageView",
	"CheckBox",
	"Switch",
	"CompoundButton",
	"ListView",
	"RecyclerView",
	"PopupWindow",
]


class TextView(View):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.text = ""

	def setText(self, value):
		self.text = "" if value is None else str(value)

	def getText(self):
		return self.text

	def append(self, value):
		self.text += "" if value is None else str(value)

	def setTextIsSelectable(self, value):
		return None


class EditText(TextView):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.text = ""

	def getText(self):
		return self.text


class Button(TextView):
	pass


class ImageView(View):
	pass


class CheckBox(Button):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.checked = False

	def setChecked(self, value):
		self.checked = bool(value)

	def isChecked(self):
		return self.checked


class Switch(CheckBox):
	pass


class CompoundButton(CheckBox):
	pass


class LinearLayout(ViewGroup):
	HORIZONTAL = 0
	VERTICAL = 1

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.orientation = 1


class FrameLayout(ViewGroup):
	pass


class RelativeLayout(ViewGroup):
	pass


class ScrollView(ViewGroup):
	pass


class ListView(ViewGroup):
	pass


class RecyclerView(ViewGroup):
	pass


class PopupWindow(View):
	pass


from android import Toast  # noqa: E402  (re-export)

# Added by tools: this shim module doubles as a package so that
# `android.widget.<anything>` resolves. Without __path__ Python treats
# it as a plain module and raises "is not a package" before any
# meta path finder is consulted.
__path__ = []
