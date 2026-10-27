#!/usr/bin/env python3
"""Compatibility report: can Desktop load these plugins?

Usage: python compat_check.py [plugins_dir]

Scans the dir with PluginLoader, tries loading every enabled plugin
in-process (imports, hooks, BasePlugin activation) and prints PASS/FAIL
per plugin plus a summary. Exit 0 when everything loadable loads.
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGINS = HERE.parent.parent  # tools/ayu-plugin-cli -> plugins
for candidate in (HERE, PLUGINS / "standalone", PLUGINS / "shim", PLUGINS / "sandbox"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import ayu_api  # noqa: E402
from plugin_loader import PluginLoader  # noqa: E402

try:
    import ayugram_compat  # noqa: F401,E402
except Exception as e:  # pragma: no cover
    print("warning: ayugram_compat unavailable: %s" % e)


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if target is None:
        staged = HERE / "_stage" / "plugins"
        examples = PLUGINS / "examples"
        target = staged if staged.exists() else examples
    loader = PluginLoader(Path(tempfile.mkdtemp()) / "probe")
    # Scan the real dir without adopting it: point loader at a copy.
    import shutil
    work = Path(tempfile.mkdtemp()) / "plugins"
    shutil.copytree(target, work, ignore=shutil.ignore_patterns("__pycache__"))
    probe = PluginLoader(work)
    infos = probe.scan()
    print("dir: %s (%d found)" % (target, len(infos)))
    fails = 0
    for info in infos:
        ayu_api.clear_hooks()
        label = "%s (%s)" % (info.id, info.entry)
        if info.error:
            print("FAIL %-40s scan: %s" % (label, info.error[:150]))
            fails += 1
            continue
        if not info.enabled:
            print("SKIP %-40s disabled" % label)
            continue
        try:
            probe.load_plugin_code(info)
            hooks = sorted(ayu_api._hooks.keys())
            print("PASS %-40s hooks=%s" % (label, ",".join(hooks) if hooks else "-"))
        except ImportError as e:
            msg = str(e)
            if "Android-only" in msg or "android" in msg.lower() or "'java'" in msg:
                print("PASS %-40s android-only (clean refuse)" % label)
            else:
                print("FAIL %-40s ImportError: %s" % (label, msg[:150]))
                fails += 1
        except Exception:
            print("FAIL %-40s" % label)
            traceback.print_exc(limit=3)
            fails += 1
    print("----\n%s plugins, %d failures" % (len(infos), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
