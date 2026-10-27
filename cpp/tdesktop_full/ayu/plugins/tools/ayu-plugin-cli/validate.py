#!/usr/bin/env python3
"""Validate plugin dir or file: python validate.py <path>."""
import json
import re
import sys
from pathlib import Path

ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,63}$")
HOOKS = {"on_message", "on_message_sending", "on_message_deleted",
         "on_chat_opened", "on_app_start", "on_app_stop", "on_setting_changed"}
MANIFESTS = ("plugin.json", "manifest.json")
ENTRIES = ("main.py", "__init__.py", "plugin.py", "index.py", "bot.py")


def _check_manifest(mj: Path, root: Path) -> None:
    m = json.loads(mj.read_text(encoding="utf-8"))
    assert isinstance(m, dict), "manifest must be an object"
    assert ID_RE.match(m.get("id", "")), "bad id"
    assert m.get("name"), "bad name"
    assert m.get("api_version", 1) == 1, "unsupported api_version"
    for h in m.get("hooks", []):
        if h not in HOOKS:
            print(f"warning: unknown hook {h} (will be ignored on Desktop)")
    entry = root / m.get("entry", "main.py")
    if not entry.exists():
        for candidate in ENTRIES:
            if (root / candidate).exists():
                print(f"note: entry {m.get('entry')} missing, "
                      f"would fall back to {candidate}")
                break
        else:
            raise AssertionError(f"missing entry {entry}")
    assert ".." not in m.get("entry", ""), "bad entry"


def _check_single(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if "__id__" in text and "__name__" in text:
        return
    print(f"note: no legacy metadata in {path.name}; "
          f"id will fall back to filename")


def main() -> int:
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    if p.is_file():
        assert p.suffix.lower() in (".plugin", ".py", ".pyw", ".json"), \
            "unsupported file (use .plugin/.py/.pyw/.json)"
        if p.name.lower() in MANIFESTS or p.suffix.lower() == ".json":
            _check_manifest(p, p.parent)
        else:
            _check_single(p)
        print("OK")
        return 0
    for mj in MANIFESTS:
        if (p / mj).exists():
            _check_manifest(p / mj, p)
            print("OK")
            return 0
    for entry in ENTRIES:
        if (p / entry).exists():
            _check_single(p / entry)
            print("OK (entry %s)" % entry)
            return 0
    singles = [f for f in sorted(p.iterdir())
               if f.is_file() and f.suffix.lower() in (".plugin", ".py", ".pyw")]
    assert singles, "need plugin.json/manifest.json or an entry script"
    _check_single(singles[0])
    print("OK (single %s)" % singles[0].name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
