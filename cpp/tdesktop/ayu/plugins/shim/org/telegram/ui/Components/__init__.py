"""UI helpers. LayoutHelper is a density calculator, which is what callers use."""

from org._stubs import Placeholder, android_module, note_use  # noqa: F401


class _LayoutHelper:
	DENSITY = 3.0

	@classmethod
	def dp(cls, value, density=None):
		try:
			return int(float(value) * float(density or cls.DENSITY))
		except (TypeError, ValueError):
			return 0

	@classmethod
	def rl(cls, value, density=None):
		return cls.dp(value, density)

	@classmethod
	def getScales(cls):
		return {}

	@classmethod
	def __getattr__(cls, name):
		note_use(f"LayoutHelper.{name}")
		return Placeholder(f"LayoutHelper.{name}")


android_module(
	__name__,
	LayoutHelper=_LayoutHelper,
	TextShine=Placeholder("TextShine"),
	AlignedTextCell=Placeholder("AlignedTextCell"),
	BackupImageView=Placeholder("BackupImageView"),
	ItemTouchHelper=Placeholder("ItemTouchHelper"),
)

