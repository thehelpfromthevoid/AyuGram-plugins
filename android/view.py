"""`android.view` on Desktop - constants resolve, widgets stay inert."""
from __future__ import annotations

from ayugram_compat.mobile_stubs import _StubObject

__all__ = [
	"View",
	"ViewGroup",
	"LayoutInflater",
	"Gravity",
	"WindowManager",
	"MenuItem",
	"Menu",
	"SurfaceView",
	"TextureView",
]


class View:
	VISIBLE = 0
	INVISIBLE = 4
	GONE = 8
	ENABLED = 0
	DISABLED = 1
	CLICKABLE = 1
	SCROLL_AXIS_VERTICAL = 1
	SCROLL_AXIS_HORIZONTAL = 2

	def __init__(self, *args, **kwargs):
		self._ayu_stub = True
		self.visibility = 0
		self.tag = None
		self.id = 0

	def setVisibility(self, value):
		self.visibility = value
		return None

	def getVisibility(self):
		return self.visibility

	def setOnClickListener(self, listener):
		self._click_listener = listener
		return None

	def performClick(self):
		listener = getattr(self, "_click_listener", None)
		if callable(listener):
			try:
				return listener(self)
			except Exception:
				return None
		return None

	def setText(self, value):
		self.text = value
		return None

	def getText(self):
		return getattr(self, "text", "")

	def setColor(self, value):
		self.color = value
		return None

	def invalidate(self):
		return None

	def requestLayout(self):
		return None

	def post(self, fn=None, *args, **kwargs):
		if callable(fn):
			try:
				fn(*args, **kwargs)
			except Exception:
				pass
		return False

	def postDelayed(self, fn=None, delay=0, *args, **kwargs):
		import threading

		if callable(fn):
			threading.Timer(
				max(0.0, float(delay) / 1000.0),
				self.post,
				args=(fn, *args),
			).start()
		return True

	def setTranslationX(self, value):
		self.translation_x = value
		return None

	def setAlpha(self, value):
		self.alpha = value
		return None

	def setScaleX(self, value):
		return None

	def setScaleY(self, value):
		return None

	def startAnimation(self, *args, **kwargs):
		return None

	def cancelAnimation(self):
		return None

	def __getattr__(self, name):
		if name.startswith("__") and name.endswith("__"):
			raise AttributeError(name)
		return _StubObject(f"View.{name}")


class ViewGroup(View):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.children = []

	def addView(self, child, *args, **kwargs):
		self.children.append(child)
		return None

	def removeView(self, child):
		if child in self.children:
			self.children.remove(child)
		return None

	def removeAllViews(self):
		self.children.clear()
		return None

	def getChildCount(self):
		return len(self.children)

	def getChildAt(self, index):
		try:
			return self.children[index]
		except Exception:
			return None

	def setPadding(self, *args):
		return None


class Gravity:
	TOP = 48
	BOTTOM = 80
	LEFT = 3
	RIGHT = 5
	CENTER_HORIZONTAL = 1
	CENTER_VERTICAL = 16
	CENTER = 17
	START = 0x00800003
	END = 0x00800005
	NO_GRAVITY = 0
	FILL = 16
	TOP_BOTTOM = 128


class LayoutInflater:
	@staticmethod
	def from_(context):
		return _StubObject("LayoutInflater")

	def inflate(self, *args, **kwargs):
		return ViewGroup()


WindowManager = _StubObject("android.view.WindowManager")
MenuItem = _StubObject("android.view.MenuItem")
Menu = _StubObject("android.view.Menu")
SurfaceView = type("SurfaceView", (View,), {})
TextureView = type("TextureView", (View,), {})

# Added by tools: this shim module doubles as a package so that
# `android.view.<anything>` resolves. Without __path__ Python treats
# it as a plain module and raises "is not a package" before any
# meta path finder is consulted.
__path__ = []
