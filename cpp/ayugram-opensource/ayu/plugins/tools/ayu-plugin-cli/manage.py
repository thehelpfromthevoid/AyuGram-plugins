"""ayu-plugin - manage Desktop AyuGram plugins and their settings.

Everything a plugin can be dropped in as is accepted, and every setting a
plugin declares can be read and written from here.

  ayu-plugin list
  ayu-plugin doctor
  ayu-plugin install <path>            folder, .zip/.tar.gz/.tgx/.plugin/.py
  ayu-plugin uninstall <id>
  ayu-plugin enable  <id> [<id> ...]
  ayu-plugin disable <id> [<id> ...]
  ayu-plugin settings <id>             show effective values + defaults
  ayu-plugin get   <id> <key>
  ayu-plugin set   <id> <key> <value>
  ayu-plugin unset <id> <key>
  ayu-plugin export   <id> <file.json>
  ayu-plugin import   <id> <file.json>
  ayu-plugin reset  <id>

The plugins root is %APPDATA%/AyuGramDesktop/plugins by default; override
with AYU_PLUGINS_DIR or --root.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _locate_plugins_pkg() -> Path:
	"""Find the directory that holds `standalone/plugin_loader.py` + `shim/`."""
	env = os.environ.get("AYU_PLUGINS_PKG")
	candidates = []
	if env:
		candidates.append(Path(env))
	candidates.append(HERE)
	candidates.extend(HERE.parents)
	for start in (HERE, *HERE.parents):
		candidates.extend([
			start / "plugins",
			start / "ayu" / "plugins",
		])
	for cand in candidates:
		try:
			if (cand / "standalone" / "plugin_loader.py").is_file():
				return cand
		except Exception:
			continue
	raise SystemExit(
		"cannot locate the plugin package: expected standalone/plugin_loader.py "
		"next to this tool (or set AYU_PLUGINS_PKG)")


PKG = _locate_plugins_pkg()
sys.path.insert(0, str(PKG / "standalone"))
# shim may live in a `shim/` subdir (source tree) or flat next to the loader
for _shim in (PKG / "shim", PKG, PKG / "standalone"):
	if (_shim / "base_plugin.py").is_file() and str(_shim) not in sys.path:
		sys.path.insert(0, str(_shim))
		break
os.environ.setdefault("AYU_PLUGINS_SHIM", str(PKG / "shim")
                      if (PKG / "shim" / "base_plugin.py").is_file()
                      else str(PKG))
os.environ.setdefault("AYU_PLUGINS_PKG", str(PKG))

import plugin_loader as loader  # noqa: E402


def root_of(args) -> Path:
	"""Where the plugins live.

	AYU_PLUGINS_DIR wins over the default profile because the app reads the
	same variable: when a test build runs against its own folder and the CLI
	falls back to %APPDATA%, enable and disable edit a profile the app never
	looks at, and the change appears to do nothing.
	"""
	if getattr(args, "root", None):
		return Path(args.root).expanduser()
	env = os.environ.get("AYU_PLUGINS_DIR")
	if env:
		return Path(env).expanduser()
	base = os.environ.get("APPDATA") or os.path.expanduser("~")
	return Path(base) / "AyuGramDesktop" / "plugins"


def make_loader(args):
	root = root_of(args)
	root.mkdir(parents=True, exist_ok=True)
	state = Path(args.state) if getattr(args, "state", None) else root / "plugins.json"
	os.environ["AYU_PLUGINS_DIR"] = str(root)
	return loader.PluginLoader(root, state), root


def find(infos, plugin_id):
	for info in infos:
		if info.id == plugin_id:
			return info
	raise SystemExit(f"unknown plugin: {plugin_id}")


def settings_file(root: Path, plugin_id: str) -> Path:
	return root / f"{loader.sanitize_id(plugin_id)}.settings.json"


def read_settings(root: Path, plugin_id: str) -> dict:
	fp = settings_file(root, plugin_id)
	if not fp.is_file():
		return {}
	try:
		data = json.loads(fp.read_text(encoding="utf-8-sig"))
	except Exception:
		return {}
	if isinstance(data, dict):
		values = data.get("settings", data)
		if isinstance(values, dict):
			return values
	return {}


def write_settings(root: Path, plugin_id: str, values: dict) -> None:
	fp = settings_file(root, plugin_id)
	fp.parent.mkdir(parents=True, exist_ok=True)
	payload = {"id": loader.sanitize_id(plugin_id), "version": 1, "settings": values}
	fp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def declared(ld, info):
	"""Defaults + labels declared by the plugin's create_settings()."""
	out = {}
	try:
		ld.enable(info.id)
		inst = ld._namespaces.get(info.id, {}).get("_ayu_plugin_instance")
		if inst is None:
			return out
		for item in inst.declared_settings():
			key = getattr(item, "key", "")
			if not key:
				continue
			out[key] = {
				"type": type(item).__name__,
				"default": getattr(item, "default", None),
				"text": getattr(item, "text", ""),
				"subtext": getattr(item, "subtext", ""),
			}
	except Exception as e:
		out["__error__"] = str(e)
	return out


APP_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,63}$")
APP_MAX_NAME = 100
APP_MAX_DESC = 400
APP_API_VERSION = 1  # kPluginApiVersion in plugin_manifest.h


def app_compliance(root: Path, info) -> list:
	"""Reasons the native app would refuse this plugin (ValidateManifest)."""
	problems = []
	manifest = None
	for name in ("plugin.json", "manifest.json"):
		fp = info.dir_path / name if info.dir_path else None
		if fp and fp.is_file():
			try:
				manifest = json.loads(fp.read_text(encoding="utf-8-sig"))
			except Exception as e:
				problems.append(f"{name} is not valid JSON: {e}")
			break
	if manifest is None:
		return problems
	pid = str(manifest.get("id", ""))
	if not APP_ID_RE.match(pid):
		problems.append(f"id {pid!r} does not match the app's id pattern")
	name = str(manifest.get("name", ""))
	if not name or len(name) > APP_MAX_NAME:
		problems.append(f"name must be 1..{APP_MAX_NAME} chars")
	desc = str(manifest.get("description", ""))
	if len(desc) > APP_MAX_DESC:
		problems.append(f"description longer than {APP_MAX_DESC} chars")
	entry = str(manifest.get("entry", "main.py"))
	if not entry or ".." in entry:
		problems.append(f"bad entry {entry!r}")
	api = manifest.get("api_version", APP_API_VERSION)
	try:
		api = int(api)
	except Exception:
		problems.append("api_version must be a number")
		api = APP_API_VERSION
	if api > APP_API_VERSION:
		problems.append(
			f"api_version {api} > app's {APP_API_VERSION}: the app will refuse "
			"this plugin (remove the field so it defaults)")
	for hook in manifest.get("hooks", []) or []:
		if str(hook) not in HOOK_NAMES:
			problems.append(f"unknown hook {hook!r} for the app")
	return problems


HOOK_NAMES = {
	"on_message", "on_message_sending", "on_message_deleted", "on_chat_opened",
	"on_app_start", "on_app_stop", "on_setting_changed",
}


def cmd_list(args):
	ld, root = make_loader(args)
	infos = ld.scan()
	if not infos:
		print("no plugins found in", root)
		return 0
	print("%-28s %-6s %-10s %s" % ("ID", "STATE", "VERSION", "NAME"))
	bad = 0
	for info in sorted(infos, key=lambda i: i.id):
		state = "on" if info.enabled else "off"
		if info.error:
			state = "ERR"
		problems = app_compliance(root, info)
		if problems:
			state = "REJ"
			bad += 1
		print("%-28s %-6s %-10s %s" % (info.id, state, info.version, info.name))
		for p in problems:
			print("      app would reject:", p)
		if info.error and args.verbose:
			print("    ", info.error.splitlines()[0])
	print()
	print("%d plugin(s) in %s" % (len(infos), root))
	if bad:
		print("%d plugin(s) the app will not load - see REJ above" % bad)
	orphans = report_orphans(ld, infos)
	if orphans:
		print()
		print("in the state file but not on disk:")
		for pid in orphans:
			print("  %-28s %-6s" % (
				pid, "on" if ld._enabled.get(pid) else "off"))
	return 0


def manifest_for(info):
	for name in ("plugin.json", "manifest.json"):
		fp = info.dir_path / name if info.dir_path else None
		if fp and fp.is_file():
			try:
				return json.loads(fp.read_text(encoding="utf-8-sig"))
			except Exception:
				return None
	return None


def runs_here(info) -> bool:
	"""Mirror of ManifestRunsHere in plugin_manifest.cpp."""
	m = manifest_for(info)
	if not m:
		return True
	platforms = m.get("platforms")
	if not isinstance(platforms, list) or not platforms:
		return True
	for p in platforms:
		if str(p).lower() in ("desktop", "windows", "macos", "linux",
							  "all", "any"):
			return True
	return False


def cmd_doctor(args):
	ld, root = make_loader(args)
	print("plugins root :", root)
	print("state file   :", ld.state_path)
	infos = ld.scan()
	print("plugins      :", len(infos))

	# Read-only on purpose. Running the entry code is what proves a plugin
	# loads, but it must not be reached through enable(): that flipped every
	# plugin on and rewrote the state file, so asking for a diagnosis was
	# itself the thing that changed the setup being diagnosed.
	on = [i.id for i in infos if i.enabled]
	off = [i.id for i in infos if not i.enabled]
	print("enabled      :", len(on))
	print("disabled     :", len(off))
	print("loadable     :", end=" ")
	ok = 0
	skipped = []
	for info in sorted(infos, key=lambda i: i.id):
		was_enabled = info.enabled
		if not runs_here(info):
			skipped.append(info.id)
			print("\n  %-24s SKIP built for another platform" % info.id)
			continue
		try:
			ld.load_plugin_code(info)
			problems = app_compliance(root, info)
			if problems:
				print("\n  %-24s REJ %s" % (
					info.id, problems[0][:80]))
				continue
			ok += 1
		except Exception as e:
			detail = str(e).splitlines()[0][:80]
			# A plugin that reaches ayu_api can only be imported by the
			# interpreter the app itself loads. Reporting that as a broken
			# plugin sends the reader looking for a fault that is not there.
			if "conflicts with this version of Python" in str(e):
				skipped.append(info.id)
				print("\n  %-24s SKIP needs the app's interpreter" % info.id)
			else:
				print("\n  %-24s FAIL %s" % (info.id, detail))
		finally:
			info.enabled = was_enabled
			info.error = ""
	print("%d/%d" % (ok, len(infos)))
	if skipped:
		print()
		print("not importable here by design:", ", ".join(skipped))
		print("they import ayu_api, which only exists inside the app;")
		print("run the app and check %s<id>.log to verify those." % (
			str(root) + os.sep))

	orphans = report_orphans(ld, infos)
	print()
	if orphans:
		print("entries in the state file with no plugin on disk:")
		for pid in orphans:
			print("  %-24s %s" % (
				pid,
				"on" if ld._enabled.get(pid) else "off"))
	print("state file left unchanged by this command")
	report_supervisor()
	print()
	print("accepted packages:")
	print("  folders with plugin.json | manifest.json | manifest.yaml | manifest.yml")
	print("  manifest.toml | info.json | plugin.yaml | ayug.json")
	print("  entry scripts:", ", ".join(loader.ENTRY_CANDIDATES))
	print("  archives     :", ", ".join(loader.ARCHIVE_SUFFIXES))
	print("  single files :", ", ".join(loader.SINGLE_SUFFIXES))
	print("  (7z/rar: extract first, or repack as .zip/.tar)")
	return 0 if ok == len(infos) else 1


def _read_pid(claim: Path):
	try:
		return int(claim.read_text(encoding="ascii").strip())
	except Exception:
		return 0


def _alive(pid: int) -> bool:
	import ctypes
	from ctypes import wintypes
	SYNCHRONIZE = 0x00100000
	PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
	STILL_ACTIVE = 259
	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	# The prototypes matter: without them a handle comes back truncated to the
	# low 32 bits on a 64-bit build, every call on it fails, and a watchdog that
	# is plainly running reads as "stale" - which is the opposite of what this is
	# for. SYNCHRONIZE on its own is not enough either: reading the exit code
	# needs a query right as well, and without one the call fails and a running
	# process looks like a dead one.
	kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
									wintypes.DWORD]
	kernel32.OpenProcess.restype = wintypes.HANDLE
	kernel32.GetExitCodeProcess.argtypes = [
		wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
	kernel32.GetExitCodeProcess.restype = wintypes.BOOL
	kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
	handle = kernel32.OpenProcess(
		SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
	if not handle:
		return False
	try:
		code = ctypes.c_ulong()
		if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
			return False
		return code.value == STILL_ACTIVE
	finally:
		kernel32.CloseHandle(handle)


def report_supervisor():
	"""Say whether anything is watching the application, and with what patience.

	Asked "why does it keep falling over", this is the first thing worth knowing.
	Without a watchdog the application is simply gone after a crash; with one,
	how long it tolerates a frozen window decides whether an ordinary slow moment
	is treated as a crash. Five seconds used to be that number, and it made a
	healthy application restart every few minutes.
	"""
	watchdog = Path(__file__).resolve().parent.parent / "watchdog.py"
	if not watchdog.is_file():
		print("no watchdog next to this tool: the application is not "
			  "restarted after a crash or a freeze")
		return
	print("watchdog:")
	try:
		text = watchdog.read_text(encoding="utf-8", errors="replace")
	except Exception as e:
		print("  could not read it (%s)" % (e))
		return
	for name in ("HUNG_SECONDS", "HUNG_STRIKES", "STARTUP_GRACE",
				"BUDGET_STARTS"):
		found = re.search(r"^%s\s*=\s*([0-9.]+)" % name, text, re.M)
		if found:
			print("  %-22s %s" % (name, found.group(1)))

	claim = watchdog.parent / "watchdog.pid"
	pid = _read_pid(claim)
	if pid and _alive(pid):
		print("  %-22s running as pid %d" % ("process", pid))
	elif pid:
		print("  %-22s stale pid %d, nothing is watching the application"
			  % ("process", pid))
	elif watchdog.parent != Path.cwd():
		# Run from the repository rather than from an installation: the watchdog
		# that matters is the one beside AyuGram.exe, and saying which file was
		# looked at is more use than guessing at other folders.
		print("  %-22s no claim file at %s" % ("process", claim))
	else:
		print("  %-22s NOT RUNNING: start watchdog.bat beside AyuGram.exe"
			  % "process")


def report_orphans(ld, infos):
	"""Ids remembered in the state file whose plugin folder is gone."""
	known = {i.id for i in infos}
	return sorted(pid for pid in ld._enabled if pid not in known)


def cmd_log(args):
	"""Show a plugin's own error log, written by the app at runtime."""
	ld, root = make_loader(args)
	if args.id:
		ids = [args.id]
	else:
		ids = sorted(p.id for p in ld.scan())
	found = False
	for pid in ids:
		path = root / f"{pid}.log"
		if not path.is_file():
			continue
		found = True
		print(f"--- {path} ---")
		lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
		for line in lines[-args.lines:]:
			print(" ", line)
		print()
	if not found:
		if args.id:
			print(f"no log for '{args.id}' (looked in {root / (args.id + '.log')})")
		else:
			print(f"no plugin logs in {root}")
	return 0


def cmd_install(args):
	ld, root = make_loader(args)
	ld.scan()
	try:
		info = ld.install_path(Path(args.path))
	except Exception as e:
		print("install failed:", e)
		return 1
	ld.save_state()
	print("installed:", info.id, "->", root / info.id)
	print("entry    :", info.entry)
	return 0


def cmd_uninstall(args):
	ld, root = make_loader(args)
	infos = ld.scan()
	info = find(infos, args.id)
	if info.dir_path and info.dir_path.is_dir():
		import shutil

		shutil.rmtree(info.dir_path, ignore_errors=True)
	for extra in (settings_file(root, info.id), ld.state_path):
		if extra.name.endswith(".settings.json") and extra.is_file():
			extra.unlink()
	ld.plugins.pop(info.id, None)
	ld._enabled.pop(info.id, None)
	for remaining in ld.plugins.values():
		remaining.enabled = ld._enabled.get(remaining.id, True)
	ld.save_state()
	print("removed:", info.id)
	return 0


def cmd_enable(args):
	ld, _ = make_loader(args)
	infos = ld.scan()
	for pid in args.ids:
		info = find(infos, pid)
		info.enabled = True
		ld._enabled[info.id] = True
		print("enabled :", info.id)
	ld.save_state()
	return 0


def cmd_disable(args):
	ld, _ = make_loader(args)
	infos = ld.scan()
	for pid in args.ids:
		info = find(infos, pid)
		info.enabled = False
		ld._enabled[info.id] = False
		print("disabled:", info.id)
	ld.save_state()
	return 0


def cmd_settings(args):
	ld, root = make_loader(args)
	infos = ld.scan()
	info = find(infos, args.id)
	decl = declared(ld, info)
	decl.pop("__error__", None)
	stored = read_settings(root, info.id)
	keys = sorted(set(decl) | set(stored))
	if not keys:
		print(f"{info.id}: no settings declared")
		return 0
	print(f"{info.id} - {info.name}")
	for key in keys:
		meta = decl.get(key, {})
		value = stored.get(key, meta.get("default", ""))
		label = meta.get("text") or meta.get("subtext") or ""
		print("  %-22s = %-24s %s" % (key, repr(value), f"[{label}]" if label else ""))
	return 0


def cmd_set(args):
	ld, root = make_loader(args)
	infos = ld.scan()
	info = find(infos, args.id)
	decl = declared(ld, info)
	decl.pop("__error__", None)
	values = read_settings(root, info.id)
	raw = args.value
	if args.key in decl:
		raw = _coerce(decl[args.key].get("default"), args.value)
	values[args.key] = raw
	write_settings(root, info.id, values)
	print("%s.%s = %r" % (info.id, args.key, raw))
	return 0


def cmd_get(args):
	_, root = make_loader(args)
	values = read_settings(root, args.id)
	if args.key not in values:
		return 1
	print(values[args.key])
	return 0


def cmd_unset(args):
	_, root = make_loader(args)
	values = read_settings(root, args.id)
	if values.pop(args.key, None) is None:
		print("no such setting:", args.key)
		return 1
	write_settings(root, args.id, values)
	print("cleared", f"{args.id}.{args.key}")
	return 0


def cmd_reset(args):
	_, root = make_loader(args)
	fp = settings_file(root, args.id)
	if fp.is_file():
		fp.unlink()
		print("reset", args.id)
	else:
		print("nothing to reset for", args.id)
	return 0


def cmd_prune(args):
	"""Drop state entries for plugins that are no longer on disk.

	Deleting a plugin folder leaves its id in the state file forever, so `list`
	grows a permanent "in the state file but not on disk" section and an
	`enable` on that id succeeds without doing anything.
	"""
	ld, root = make_loader(args)
	infos = ld.scan()
	on_disk = {i.id for i in infos}
	stale = sorted(i for i in ld._enabled if i not in on_disk)
	if not stale:
		print("nothing to prune")
		return 0
	for pid in stale:
		if args.dry_run:
			print("would prune", pid)
		else:
			del ld._enabled[pid]
			print("pruned", pid)
	if not args.dry_run:
		ld.save_state()
		print(f"pruned {len(stale)} entry(ies)")
	return 0


def cmd_export(args):
	_, root = make_loader(args)
	values = read_settings(root, args.id)
	Path(args.file).write_text(
		json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8")
	print("exported", len(values), "value(s) to", args.file)
	return 0


def cmd_import(args):
	ld, root = make_loader(args)
	infos = ld.scan()
	info = find(infos, args.id)
	decl = declared(ld, info)
	decl.pop("__error__", None)
	data = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
	values = read_settings(root, info.id)
	for key, value in data.items():
		values[key] = _coerce(decl.get(key, {}).get("default"), value)
	write_settings(root, info.id, values)
	print("imported", len(values), "value(s) into", info.id)
	return 0


def _coerce(default, raw):
	if not isinstance(raw, str):
		return raw
	if isinstance(default, bool):
		return raw.strip().lower() in ("1", "true", "yes", "on", "да")
	if isinstance(default, int) and not isinstance(default, bool):
		try:
			return int(raw.strip())
		except Exception:
			return default
	if isinstance(default, float):
		try:
			return float(raw.strip())
		except Exception:
			return default
	if isinstance(default, (list, tuple)):
		parts = [p.strip() for p in raw.split(",") if p.strip()]
		return type(default)(parts)
	return raw


def build_parser():
	p = argparse.ArgumentParser(prog="ayu-plugin", description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter)
	p.add_argument("--root", help="plugins directory")
	p.add_argument("--state", help="state file (default <root>/plugins.json)")
	p.add_argument("-v", "--verbose", action="store_true")
	sub = p.add_subparsers(dest="cmd", required=True)

	sub.add_parser("list", help="show all plugins").set_defaults(func=cmd_list)
	sub.add_parser("doctor", help="environment + load check").set_defaults(func=cmd_doctor)
	q = sub.add_parser("prune", help="forget plugins that are no longer on disk")
	q.add_argument("--dry-run", action="store_true")
	q.set_defaults(func=cmd_prune)
	q = sub.add_parser("log", help="show a plugin's runtime error log")
	q.add_argument("id", nargs="?", help="plugin id (default: all)")
	q.add_argument("-n", "--lines", type=int, default=40)
	q.set_defaults(func=cmd_log)
	q = sub.add_parser("install", help="install from any supported package")
	q.add_argument("path")
	q.set_defaults(func=cmd_install)
	q = sub.add_parser("uninstall", help="remove a plugin")
	q.add_argument("id")
	q.set_defaults(func=cmd_uninstall)
	q = sub.add_parser("enable", help="enable plugins")
	q.add_argument("ids", nargs="+")
	q.set_defaults(func=cmd_enable)
	q = sub.add_parser("disable", help="disable plugins")
	q.add_argument("ids", nargs="+")
	q.set_defaults(func=cmd_disable)
	q = sub.add_parser("settings", help="show a plugin's settings")
	q.add_argument("id")
	q.set_defaults(func=cmd_settings)
	q = sub.add_parser("get", help="read one setting")
	q.add_argument("id")
	q.add_argument("key")
	q.set_defaults(func=cmd_get)
	q = sub.add_parser("set", help="write one setting")
	q.add_argument("id")
	q.add_argument("key")
	q.add_argument("value")
	q.set_defaults(func=cmd_set)
	q = sub.add_parser("unset", help="drop one setting")
	q.add_argument("id")
	q.add_argument("key")
	q.set_defaults(func=cmd_unset)
	q = sub.add_parser("reset", help="drop all settings of a plugin")
	q.add_argument("id")
	q.set_defaults(func=cmd_reset)
	q = sub.add_parser("export", help="dump settings to json")
	q.add_argument("id")
	q.add_argument("file")
	q.set_defaults(func=cmd_export)
	q = sub.add_parser("import", help="load settings from json")
	q.add_argument("id")
	q.add_argument("file")
	q.set_defaults(func=cmd_import)
	return p


def main(argv=None):
	args = build_parser().parse_args(argv)
	try:
		return args.func(args)
	except SystemExit:
		raise
	except Exception as e:
		print("error:", e)
		return 1


if __name__ == "__main__":
	sys.exit(main())
