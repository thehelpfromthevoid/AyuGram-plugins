"""Report which plugins declare settings, and what they read.

Run against a plugins root to see why the settings button is or is not there.
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKS = (
	("create_settings", "create_settings"),
	("setting_schema", "setting_schema"),
	("Switch(", "Switch-строки"),
	("Selector(", "Selector-строки"),
	("EditText(", "EditText-строки"),
	("get_setting", "читает настройки"),
	("on_settings_changed", "реагирует на изменение"),
)


def main() -> int:
	root = Path(sys.argv[1])
	total = with_settings = 0
	for directory in sorted(root.iterdir()):
		if not directory.is_dir():
			continue
		files = list(directory.glob("*.py"))
		if not files:
			continue
		source = "\n".join(
			f.read_text(encoding="utf-8-sig", errors="replace") for f in files)
		if not source.strip():
			continue
		total += 1
		found = [label for needle, label in MARKS if needle in source]
		if found:
			with_settings += 1
		print(f"  {directory.name:<34} {', '.join(found) or 'НЕТ НАСТРОЕК'}")
	print(f"\nитого: {total} плагинов, настройки объявили {with_settings}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())