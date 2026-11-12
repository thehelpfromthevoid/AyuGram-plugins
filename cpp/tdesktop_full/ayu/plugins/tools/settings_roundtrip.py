"""Exercise the settings path the way the app does, for every installed plugin.

The settings dialog is only the last step. What actually decides whether a
setting works is the whole chain: the plugin declares rows, the bridge reads
them through ayugram_compat.schema, the dialog edits values, the bridge calls
apply_settings, and the values have to come back out of the store unchanged and
of the declared type.

Reading all of that off the live app is slow and needs a GUI, so this drives the
same Python code directly. A plugin that passes here cannot fail in the dialog
for a reason of its own code, which narrows a misbehaving setting down to the
dialog or the bridge.

Usage: settings_roundtrip.py [PLUGINS_ROOT]
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _shim_root() -> Path:
	"""The directory holding base_plugin.py, which identifies the shim.

	The repository keeps the shim under plugins/shim; a deployment puts the same
	files in the application root, and this tool is run from both.
	"""
	for base in (HERE.parent, HERE.parent / "shim", HERE):
		if (base / "base_plugin.py").is_file():
			return base
	raise AssertionError("no shim found")


SHIM = _shim_root()
ROOT = Path(
    sys.argv[1] if len(sys.argv) > 1
    else Path(os.environ.get("APPDATA", ".")) / "AyuGramDesktop" / "plugins"
)


def bootstrap() -> None:
    os.environ["AYU_PLUGINS_DIR"] = str(ROOT)
    for path in (str(SHIM), str(HERE)):
        if path not in sys.path:
            sys.path.insert(0, path)


class FakeBridge:
    """Stands in for the C++ bridge so a plugin can be driven headlessly."""

    def __init__(self) -> None:
        self.sent = []
        self.toasts = []

    def __getattr__(self, name):
        if name == "hook":
            return lambda *a, **k: (lambda f: f)
        if name in ("send_text", "send_message"):
            return lambda *a, **k: self.sent.append((name, a, k))
        if name == "toast":
            return lambda *a, **k: self.toasts.append(str(a[0] if a else ""))
        if name == "run_on_main_thread":
            return lambda fn, *a, **k: fn(*a, **k)
        if name in ("plugins_dir", "data_dir"):
            return str(ROOT)

        def _stub(*a, **k):
            return None

        return _stub


def load_plugin(directory: Path):
    """Import a plugin's main.py and instantiate it the way the app does."""
    import importlib.util

    sys.modules["ayu_api"] = FakeBridge()
    entry = "main.py"
    manifest = directory / "plugin.json"
    if manifest.is_file():
        try:
            entry = json.loads(
                manifest.read_text(encoding="utf-8-sig")).get("entry", entry)
        except Exception:
            pass
    script = directory / entry
    if not script.is_file():
        script = directory / "main.py"
    if not script.is_file():
        return None, None

    name = "ayu_plugin_under_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    # activate_base_plugins is what LoadPlugin calls, and it is also what sets
    # _ayu_plugin_instance. Guessing at the subclass instead would miss the
    # on_plugin_load call that so often is where a plugin breaks.
    instance = getattr(module, "_ayu_plugin_instance", None)
    if instance is None:
        from ayugram_compat.base_plugin import activate_base_plugins
        instance = activate_base_plugins(vars(module))
    return instance, module


def load_error(exc: BaseException) -> str:
    """Tell an expected refusal apart from a real failure."""
    text = f"{exc}"
    if isinstance(exc, ImportError) and (
            "android" in text.lower() or "java" in text.lower()):
        return "android only"
    return f"load error: {text.splitlines()[0][:70]}"


def sample_for(row: dict):
    kind = row.get("type")
    key = row.get("key")
    if not key:
        return None
    if kind == "bool":
        return True
    if kind == "int":
        return 7
    if kind == "choice":
        items = row.get("items") or []
        return items[0] if items else ""
    return "proba"


def check(directory: Path) -> tuple:
    from ayugram_compat import schema

    try:
        instance, module = load_plugin(directory)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return load_error(exc), ""
    if instance is None:
        return "no instance", ""

    rows = schema.rows_for(instance, vars(module) if module else None)
    editable = [
        r for r in rows
        if r.get("key") and r.get("type") not in ("header", "note")
    ]
    if not editable:
        return "no settings", ""

    payload = {}
    for row in editable:
        value = sample_for(row)
        if value is not None:
            payload[row["key"]] = value
    if not payload:
        return "no settings", ""

    if hasattr(instance, "apply_settings"):
        instance.apply_settings(payload)
    else:
        for key, value in payload.items():
            instance.set_setting(key, value)

    if hasattr(instance, "setting_values"):
        read_back = instance.setting_values()
    else:
        read_back = {k: instance.get_setting(k) for k in payload}

    # A setting is only good if the value comes back out as the type the plugin
    # declared. This is the check that catches a text setting stored as a number,
    # which used to reach the plugin as an int and blow up on .strip().
    declared = getattr(instance, "declared_defaults", None)
    wanted = declared() if callable(declared) else {}
    broken = []
    for key, value in payload.items():
        got = read_back.get(key)
        want = wanted.get(key, value)
        if isinstance(want, str):
            ok = isinstance(got, str)
        elif isinstance(want, bool):
            ok = isinstance(got, bool)
        elif isinstance(want, int):
            ok = isinstance(got, int) and not isinstance(got, bool)
        else:
            ok = True
        if not ok:
            broken.append(f"{key}:{type(got).__name__}!=want{type(want).__name__}")

    if broken:
        return "TYPE MISMATCH", ", ".join(broken[:4])
    return "ok", f"{len(editable)} rows, {len(payload)} values"


def sandbox_for(directory: Path) -> Path:
	"""A throwaway copy of one plugin, so the real settings are never touched.

	This check writes into the settings of the plugin it is testing - that is
	how it proves a value survives a save - and it used to write into the user's
	own files. It left them full of its sample values: a command name reading
	"proba", a nickname that was never asked for. The mess looked exactly like a
	settings bug and sent the investigation after the wrong thing twice.

	So the work happens on a copy in a temporary directory, and the real folder
	is only ever read.
	"""
	sandbox = Path(tempfile.mkdtemp(prefix="ayu-settings-"))
	shutil.copytree(directory, sandbox / directory.name,
					ignore=shutil.ignore_patterns("__pycache__"))
	settings = directory.with_suffix(".settings.json")
	if settings.is_file():
		shutil.copy2(settings, (sandbox / directory.name).with_suffix(
			".settings.json"))
	return sandbox


def main() -> int:
    bootstrap()
    total = declared = failed = 0
    report = []
    real_root = ROOT
    for directory in sorted(real_root.iterdir()):
        if not directory.is_dir() or not list(directory.glob("*.py")):
            continue
        total += 1
        sandbox = None
        try:
            sandbox = sandbox_for(directory)
            # Point the settings store at the copy for the length of the check.
            import ayugram_compat.settings_store as store
            store._CACHE.clear()
            if hasattr(store, "_LOADED"):
                store._LOADED.clear()
            previous = os.environ.get("AYU_PLUGINS_DIR")
            os.environ["AYU_PLUGINS_DIR"] = str(sandbox)
            try:
                verdict, detail = check(sandbox / directory.name)
            finally:
                if previous is None:
                    os.environ.pop("AYU_PLUGINS_DIR", None)
                else:
                    os.environ["AYU_PLUGINS_DIR"] = previous
                store._CACHE.clear()
                if hasattr(store, "_LOADED"):
                    store._LOADED.clear()
        except Exception:
            failed += 1
            print(f"  FAIL {directory.name}")
            print("       " + traceback.format_exc().replace(
                "\n", "\n       ")[:900])
            continue
        finally:
            if sandbox is not None:
                shutil.rmtree(sandbox, ignore_errors=True)
        if detail:
            declared += 1
            print(f"  {directory.name:<30} {verdict:<14} {detail}")
        else:
            report.append(f"{directory.name:<30} {verdict}")
    print()
    for line in report:
        print(f"  {line}")
    print(f"\n{total} plugins, {declared} with settings, {failed} errors")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())