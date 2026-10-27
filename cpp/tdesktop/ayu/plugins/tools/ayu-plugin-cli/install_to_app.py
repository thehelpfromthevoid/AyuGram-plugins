#!/usr/bin/env python3
"""Install plugins into the live AyuGram Desktop app.

Usage:
  python install_to_app.py <plugin_dir_or_file> [...]   install given ones
  python install_to_app.py --examples <name> [...]      install from repo examples
  python install_to_app.py --list                       show live plugins + state

Copies into %APPDATA%/AyuGramDesktop/plugins/<id>/ and marks enabled in
plugins.json (the same files the C++ Manager scans on startup).
Restart AyuGram afterwards (or Rescan in Settings > AyuGram > Plugins).
"""
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGINS = HERE.parent.parent  # tools/ayu-plugin-cli -> plugins
for candidate in (HERE, PLUGINS / "standalone", PLUGINS / "shim"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from plugin_loader import PluginLoader  # noqa: E402


def live_root() -> Path:
    env = os.environ.get("AYU_PLUGINS_DIR")
    if env:
        return Path(env)
    appdata = os.environ.get("APPDATA", "")
    for name in ("AyuGramDesktop", "AyuGram Desktop"):
        candidate = Path(appdata) / name / "plugins"
        if candidate.is_dir():
            return candidate
    return Path(appdata) / "AyuGramDesktop" / "plugins"


def main() -> int:
    args = sys.argv[1:]
    root = live_root()
    if args[:1] == ["--list"]:
        loader = PluginLoader(root)
        for info in loader.scan():
            state = "ERROR:%s" % info.error[:80] if info.error else (
                "ON" if info.enabled else "off")
            print("[%s] %s - %s (%s)" % (state, info.name, info.id, info.entry))
        return 0
    sources: list[Path] = []
    if args[:1] == ["--examples"]:
        if len(args) < 2:
            print("usage: install_to_app.py --examples <name> [...]")
            return 2
        for name in args[1:]:
            sources.append(PLUGINS / "examples" / name)
    else:
        if not args:
            print(__doc__)
            return 2
        sources = [Path(a) for a in args]
    scratch = PluginLoader(Path(__file__).resolve().parent / ".tmp_probe")
    live = PluginLoader(root)
    live.scan()
    for src in sources:
        if not src.exists():
            print("SKIP missing: %s" % src)
            continue
        try:
            if src.is_dir():
                probe = scratch.parse_manifest(src)
            else:
                probe = None
        except Exception as e:
            print("SKIP %s: %s" % (src, e))
            continue
        dst = root / (probe.id if probe else src.stem)
        if dst.exists():
            shutil.rmtree(dst)
        if src.is_dir():
            shutil.copytree(src, dst,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            info = scratch.install_from_file(src)
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(info.dir_path, dst,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.rmtree(info.dir_path, ignore_errors=True)
        live._enabled[dst.name] = True
        live.save_state()
        print("OK %s -> %s (enabled)" % (src, dst))
    print("Restart AyuGram (or Rescan in Settings > AyuGram > Plugins).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
