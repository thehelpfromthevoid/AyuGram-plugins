"""Stage the plugin runtime and a freshly built AyuGram into a folder.

    python deploy.py --build-dir D:/TBuild/tdesktop/out --to ../dist/AyuGramFullPlugins

Copies the executable, the embedded-CPython runtime it needs, and the shim /
loader / tools / examples, so the target folder is self-contained: start
`AyuGram.exe` there and plugins just work.

--python points at a Python installation to take `python3xx.dll` and the
`python3xx.zip` stdlib from; it is auto-detected next to the build when
omitted.

AyuGram plugin host for Desktop, (c) @CE0fbi
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGINS = HERE.parent


def repo_root() -> Path | None:
	"""The repository root, when running from the source tree.

	Counting parents assumed the source layout (standalone -> plugins -> ayu ->
	SourceFiles -> Telegram -> root). A deployment puts standalone/ directly in
	the app folder, so the same expression raised IndexError at import time and
	made this file unusable for anything but running the script itself.
	"""
	enough = PLUGINS.parents[3:4]
	return enough[0] if enough else None


REPO = repo_root()

# Files the app directory must contain for the embedded interpreter to
# initialise. Everything else is optional.
RUNTIME_GLOBS = (
	"python*.dll",
	"python*.zip",
	"python*._pth",
	"vcruntime140*.dll",
	"msvcp140*.dll",
	"vccorlib140*.dll",
	"libcrypto-3.dll",
	"libssl-3.dll",
	"libffi-8.dll",
	"sqlite3.dll",
)

# pyd modules plugins commonly need at import time.
RUNTIME_PYD = (
	"_asyncio.pyd", "_ctypes.pyd", "_decimal.pyd", "_elementtree.pyd",
	"_hashlib.pyd", "_lzma.pyd", "_multiprocessing.pyd", "_overlapped.pyd",
	"_queue.pyd", "_socket.pyd", "_sqlite3.pyd", "_ssl.pyd", "_uuid.pyd",
	"_wmi.pyd", "_zoneinfo.pyd", "select.pyd", "unicodedata.pyd",
	"winsound.pyd", "pyexpat.pyd",
)

# Staged on purpose: without Tcl/Tk, and with no window to put a Tk widget in
# anyway, the only thing this would do is turn a clear "no tkinter on Desktop"
# into a third of a megabyte of unresolved imports.
SKIP_PYD = ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll")

SHIM_TOP_FILES = (
	"android_utils.py", "base_plugin.py", "client_utils.py",
	"file_utils.py", "hook_utils.py",
)
SHIM_PACKAGES = (
	"ayugram_compat", "android", "java", "ui", "org",
	"extera_utils", "exteragram_utils",
	# Mobile plugins import requests reflexively; without it a plugin fails at
	# load, which looks like AyuGram being broken rather than one library being
	# absent.
	"requests",
)
# The sandbox is a sibling of shim/, not a package inside it. Listing it as a
# shim package made copy_tree() find nothing and return 0 without a word, so
# the deployment looked fine and plugins ran with no permission checks at all.
SANDBOX_DIR = PLUGINS / "sandbox"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo",
								"_stage", "_pyi_spec", "_pyi_work")


def find_exe(build_dir: Path, config: str) -> Path:
	for name in ("AyuGram.exe", "Telegram.exe", "AyuGram", "Telegram"):
		candidate = build_dir / config / name
		if candidate.is_file():
			return candidate
	candidates = sorted(
		p for p in build_dir.rglob("*.exe")
		if p.name.lower() in ("ayugram.exe", "telegram.exe")
	)
	if candidates:
		return candidates[0]
	raise SystemExit(f"no built executable under {build_dir}")


def is_python_root(base: Path) -> bool:
	"""The app loads pythonNNN.dll from its own folder, so that file is what
	identifies a usable install.

	A virtualenv's Scripts folder holds python.exe but not the DLL, so matching
	on the interpreter alone picks a folder that cannot supply the runtime and
	silently stages nothing.
	"""
	if not base.is_dir():
		return False
	return any(base.glob("python3*.dll"))


def find_python(exe: Path, explicit: str | None) -> Path:
	if explicit:
		given = Path(explicit)
		# Accept either the install root or python.exe itself.
		for cand in (given, given.parent, given / "python.exe"):
			if cand.is_file() and cand.name.lower().startswith("python"):
				return cand
			if cand.is_dir() and (cand / "python.exe").is_file():
				return cand / "python.exe"
		for pattern in ("python3*/python.exe", "python.exe"):
			for cand in sorted(given.glob(pattern)):
				return cand
		raise SystemExit(f"no python.exe under {given}")
	# The app loads pythonNNN.dll from its own folder, so look next to the
	# built executable first, then at the usual install locations.
	guesses = [
		exe.parent,
		exe.parent.parent,
		PLUGINS.parents[3] / "ThirdParty" / "python",
		Path(sys.executable).parent,
		Path(sys.executable).parent.parent,
		Path(os.environ.get("LOCALAPPDATA", "")) / "Python",
	]
	seen = set()
	for base in guesses:
		# Installs nest the DLL one level down: .../Python/pythoncore-3.14-64
		# or .../Python/Python314. Check the folder itself, then its likely
		# children, before giving up on a location.
		candidates = [base]
		if base.is_dir():
			for pattern in ("python3*", "pythoncore-*", "Python3*", "Python314"):
				candidates.extend(sorted(base.glob(pattern)))
		for cand in candidates:
			if str(cand) in seen:
				continue
			seen.add(str(cand))
			if not is_python_root(cand):
				continue
			for pattern in ("python3*/python.exe", "python.exe"):
				for found in sorted(cand.glob(pattern)):
					return found
	raise SystemExit(
		"could not find a Python install holding python3NN.dll; "
		"pass --python")


def copy_tree(src: Path, dst: Path,
			  blocked: list[str] | None = None,
			  preserve: tuple = ()) -> int:
	if not src.exists():
		return 0
	saved: dict = {}
	if dst.exists():
		# Mirroring the tree means removing the old one first, which quietly
		# deletes anything that was written into it at runtime. The watchdog's
		# log and its pid file live in tools/ exactly like that, so a deploy
		# erased the record of every restart and switched off the check that
		# stops a second watchdog - both without saying anything.
		for name in preserve:
			held = dst / name
			if held.is_file():
				try:
					saved[name] = held.read_bytes()
				except OSError:
					pass
		# A running application holds its imported modules mapped, and the
		# interpreter can be part way through writing a .pyc into __pycache__
		# at the moment the directory is removed. Removing the tree first is
		# what made this fail: the whole deploy stopped on a file that gets
		# overwritten moments later. Anything left behind is simply copied
		# over, and the leftovers are named at the end instead.
		try:
			shutil.rmtree(dst)
		except OSError:
			if blocked is not None:
				blocked.append(
					f"{dst.name}/ still holds files in use - close AyuGram "
					"for a clean redeploy")
			shutil.rmtree(dst, ignore_errors=True)

	count = 0
	for path, _, files in os_walk(src):
		out = dst / Path(path).relative_to(src)
		out.mkdir(parents=True, exist_ok=True)
		for name in files:
			if IGNORE("", [name]):
				continue
			if copy_file(Path(path) / name, out / name, blocked):
				count += 1

	# Written back only now: the directory was just created by the loop above,
	# and restoring before that wrote into a folder that did not exist and failed
	# quietly - which looked exactly like the preserve option doing nothing.
	for name, data in saved.items():
		try:
			dst.mkdir(parents=True, exist_ok=True)
			(dst / name).write_bytes(data)
		except OSError:
			pass
	return count


def os_walk(top: Path):
	import os
	return os.walk(top)


def _tree_differs(src: Path, dst: Path) -> bool:
	"""True when the same plugin exists on disk with different contents."""
	if not dst.exists():
		return True
	for path, _, files in os_walk(src):
		rel = Path(path).relative_to(src)
		for name in files:
			a, b = Path(path) / name, dst / rel / name
			if not b.is_file():
				return True
			if a.read_bytes() != b.read_bytes():
				return True
	return False


def copy_file(src: Path, dst: Path, blocked: list[str] | None = None) -> bool:
	"""Copy one file, recording it as blocked if the OS holds it open.

	A running AyuGram keeps its own executable and every DLL it has loaded
	locked. That used to abort the deploy on the first such file, so the
	folder ended up with part of the new runtime and part of the old one and
	the run printed nothing about it. Nothing in here needs the files that are
	locked to be worth deploying, so the copy is skipped, named, and the deploy
	carries on to tell the caller at the end.
	"""
	try:
		shutil.copy2(src, dst)
		return True
	except PermissionError:
		if blocked is not None:
			blocked.append(f"{src.name} is in use - close AyuGram and deploy again")
		return False


def copy_first(base: Path, patterns, dst: Path,
			   blocked: list[str] | None = None) -> int:
	count = 0
	for pattern in patterns:
		for src in sorted(base.glob(pattern)):
			if src.is_file() and copy_file(src, dst / src.name, blocked):
				count += 1
	return count


# Libraries the extension modules link against. They ship inside the Python
# install under DLLs\, never next to python.exe.
NATIVE_LIBS = (
	"libssl-3.dll", "libcrypto-3.dll", "libffi-8.dll", "sqlite3.dll",
	"zlib1.dll",
)


def drop_unwanted(target: Path) -> int:
	"""Remove the modules we deliberately do not ship. Returns the count."""
	removed = 0
	for name in SKIP_PYD:
		stale = target / name
		if stale.is_file():
			stale.unlink()
			removed += 1
	return removed


def stage_native_libs(python: Path, target: Path,
					  blocked: list[str] | None = None) -> list[str]:
	"""Copy the DLLs the .pyd modules need. Returns the ones not found."""
	found: dict[str, Path] = {}
	for base in (python.parent / "DLLs", python.parent):
		if not base.is_dir():
			continue
		for name in NATIVE_LIBS:
			candidate = base / name
			if name not in found and candidate.is_file():
				found[name] = candidate

	staged = []
	for name, src in sorted(found.items()):
		if copy_file(src, target / name, blocked):
			staged.append(name)
	absent = [name for name in NATIVE_LIBS if name not in found]
	print(f"native   {len(staged)} libraries: "
		  f"{', '.join(staged) if staged else 'none'}")
	if absent:
		print(f"         not found in the Python install: "
			  f"{', '.join(absent)}")
	return absent


def dist_candidates(build_dir: Path) -> list[Path]:
	"""Folders a prepared distribution might sit in, relative to the build."""
	out = [build_dir / "dist" / "AyuGramFullPlugins"]
	for name in ("dist", "dist_plugins", "plugins_dist"):
		out.append(build_dir / name)
		out.append(build_dir.parent / name)
		# The prepared layout normally lands beside the build directory rather
		# than inside it, so dist/<name> has to be tried from both.
		out.append(build_dir.parent / name / "AyuGramFullPlugins")
	return out


def verify_runtime(target: Path) -> list[str]:
	"""Read the import table of every staged module and report what is absent.

	Copying the files above is not proof: a library can be present and still be
	the wrong one. Reading each module's own imports is, and it turns
	"DLL load failed while importing _ssl" into "copy libssl-3.dll".
	"""
	sys.path.insert(0, str(Path(__file__).resolve().parent))
	import native_deps

	report = native_deps.missing_dependencies(target)
	if not report:
		print("verify   every staged module can resolve its libraries")
		return []
	print("verify   unresolved libraries:")
	for line in native_deps.format_report(report).splitlines():
		print(line)
	return [f"{module}: {', '.join(names)}"
			for module, names in sorted(report.items())]


def deploy(build_dir: Path, target: Path, config: str,
		   python_dir: str | None, verbose: bool) -> list[str]:
	exe = find_exe(build_dir, config)
	python = find_python(exe, python_dir)
	target.mkdir(parents=True, exist_ok=True)
	blocked: list[str] = []

	# The executable goes first, but a running application holds it open and the
	# copy then dies half way through the deploy - which leaves the folder with a
	# new runtime and an old exe, and prints nothing about it. Everything else is
	# worth copying regardless, so a locked exe is reported at the end instead of
	# silently truncating the run.
	try:
		copy_file(exe, target / exe.name, blocked)
		print(f"exe      {exe} -> {target / exe.name}")
	except PermissionError:
		blocked.append(f"{exe.name} is running - close AyuGram and deploy again")
		print(f"exe      SKIPPED, {target / exe.name} is in use")

	n = copy_first(python.parent, RUNTIME_GLOBS, target, blocked)
	print(f"runtime  {n} files from {python.parent}")

	# pythonNNN.zip holds the stdlib for an embedded interpreter, and
	# pythonNNN._pth tells it where to look. A normal Windows Python install
	# ships neither, so they were previously only reported as a NOTE -- and a
	# target folder without them starts the interpreter into
	# "Failed to import encodings module", runs the app in metadata-only mode,
	# and loads no plugins at all. That looks exactly like "plugins do not work"
	# and survives every other check here, so this is now fatal.
	stdlib_zip = None
	for folder in (python.parent, target, *dist_candidates(build_dir)):
		if not folder.is_dir():
			continue
		found = sorted(folder.glob("python3*.zip"))
		if found:
			stdlib_zip = found[0]
			break

	if stdlib_zip is None:
		raise SystemExit(
			f"no python3NN.zip found for the embedded stdlib. Looked in "
			f"{python.parent}, {target} and "
			+ ", ".join(str(p) for p in dist_candidates(build_dir))
			+ "\nA deployment without it cannot start Python, so no plugin will "
			"load. Copy an embeddable python3NN.zip next to AyuGram.exe and "
			"run this again.")

	zip_target = target / stdlib_zip.name
	if zip_target.resolve() != stdlib_zip.resolve():
		copy_file(stdlib_zip, zip_target, blocked)
	print(f"stdlib   {zip_target.name} ({zip_target.stat().st_size // 1024} KB)"
		  f" from {stdlib_zip.parent}")

	# The ._pth is three lines and nothing else provides it: without it the
	# interpreter does not look next to the executable for its zip.
	pth_target = target / (zip_target.stem + "._pth")
	if not pth_target.is_file():
		pth_target.write_text(
			f"{zip_target.name}\n.\nimport site\n",
			encoding="utf-8")
		print(f"stdlib   wrote {pth_target.name}")

	pyd_dir = python.parent / "DLLs"
	if pyd_dir.is_dir():
		n = copy_first(pyd_dir, ("*.pyd",), target, blocked)
		skipped = drop_unwanted(target)
		print(f"pyd      {n - skipped} modules from {pyd_dir}"
			  + (f" (left out: {', '.join(SKIP_PYD)})" if skipped else ""))
	else:
		missing = []
		for name in RUNTIME_PYD:
			found = list(python.parent.glob(name))
			if not found:
				missing.append(name)
		n = copy_first(python.parent, ("*.pyd",), target, blocked)
		skipped = drop_unwanted(target)
		print(f"pyd      {n - skipped} modules from {python.parent}")
		if missing:
			print(f"         missing: {', '.join(missing)}")

	# The libraries the .pyd modules link against live in DLLs\, not next to
	# python.exe. Looking only at python.parent copied none of them, and since
	# nothing was reported, a plugin that imports ssl died with "DLL load
	# failed while importing _ssl" naming the extension module rather than the
	# library it actually needed.
	absent = stage_native_libs(python, target, blocked)

	shim = PLUGINS / "shim"
	for name in SHIM_TOP_FILES:
		src = shim / name
		if src.is_file():
			copy_file(src, target / name, blocked)
	for pkg in SHIM_PACKAGES:
		copy_tree(shim / pkg, target / pkg, blocked)
	print(f"shim     {len(SHIM_TOP_FILES)} files, {len(SHIM_PACKAGES)} packages")

	# Without this the app starts with no permission enforcement: nothing
	# checks what a plugin may import or write. Fail loudly rather than ship a
	# build that quietly removed the sandbox.
	if not SANDBOX_DIR.is_dir():
		raise SystemExit(
			f"the sandbox is missing from {SANDBOX_DIR}. Refusing to deploy a "
			f"build with no permission enforcement.")
	sandbox_files = copy_tree(SANDBOX_DIR, target / "sandbox")
	if not (target / "sandbox" / "import_filter.py").is_file():
		raise SystemExit(
			"sandbox/import_filter.py was not staged; the app would start "
			"with no permission enforcement.")
	print(f"sandbox  {sandbox_files} files (import_filter.py, audithook.py)")

	standalone = PLUGINS / "standalone"
	copy_tree(standalone, target / "standalone")
	print("standalone standalone/ (plugin_loader.py, requirements.py, host_app.py)")

	# Plugins.bat runs from the app directory because that is where users
	# expect it, and it reaches the loader through standalone/.
	if (standalone / "Plugins.bat").is_file():
		shutil.copy2(standalone / "Plugins.bat", target / "Plugins.bat")
	print("launcher Plugins.bat")

	# The watchdog launcher belongs next to the exe for the same reason: it is
	# what a person double-clicks to start the app. Only standalone/ was
	# receiving it, so the documented path did not exist and Windows said the
	# file was not found.
	if (standalone / "watchdog.bat").is_file():
		shutil.copy2(standalone / "watchdog.bat", target / "watchdog.bat")
	print("launcher watchdog.bat")

	# Deliberately NOT ayu_api.py: the app provides that module from C++, and
	# a pure-Python copy in the app directory shadows it and strips the
	# send_text / edit_message / hook surface. The CLI gets it from
	# standalone/ instead.
	#
	# plugin_loader.py and requirements.py were also landing in the app root
	# once, where they shadow nothing useful but resolve their own relative
	# paths from the wrong place and break tools/selftest.py. Remove them if
	# an older deployment put them there.
	for stale in ("plugin_loader.py", "requirements.py", "ayu_api.py"):
		leftover = target / stale
		if leftover.is_file():
			leftover.unlink()
			print(f"removed   stray {stale} from the app directory")

	copy_tree(PLUGINS / "tools", target / "tools", blocked,
				preserve=("watchdog.log", "watchdog.pid"))
	print("tools    tools/")

	copy_tree(PLUGINS / "examples", target / "examples", blocked)
	print("examples examples/")

	# The app loads plugins from plugins/, not from examples/, so the bundled
	# ones have to be there too. Without this they are installed once by hand
	# and then never updated: a stale neural_typing kept resending whole
	# messages and crashing the app long after the fix landed in examples/.
	# These are our own shipped defaults, so replacing them is the point; the
	# replacements are listed so it is never a silent overwrite.
	synced = 0
	for example in sorted((PLUGINS / "examples").iterdir()):
		if not example.is_dir() or example.name.startswith("_"):
			continue
		installed = target / "plugins" / example.name
		if not installed.exists():
			copy_tree(example, installed, blocked)
			synced += 1
			print(f"example  {example.name} (installed)")
		elif _tree_differs(example, installed):
			copy_tree(example, installed, blocked)
			synced += 1
			print(f"example  {example.name} (updated from the bundled copy)")
	if synced:
		print(f"examples {synced} bundled plugin(s) in plugins/")

	copy_tree(PLUGINS / "docs", target / "docs", blocked)
	print("docs     docs/")

	# A running application holds its crash log open, so tidying the folder is
	# best effort: an old log left behind is harmless, but refusing to finish
	# the deploy over one file is not.
	#
	# The watchdog's own files are left alone. Its log is the record of every
	# restart, and deleting it mid-flight threw away exactly what a person
	# complaining about restarts needs to read; the pid file it holds is how a
	# second watchdog is kept from starting, and removing it quietly turned the
	# duplicate check off.
	keep = {"watchdog.log", "watchdog.pid"}
	for stray in target.glob("*.log"):
		if stray.name.lower() in keep:
			continue
		try:
			stray.unlink()
		except OSError:
			pass
	for cache in list(target.rglob("__pycache__")):
		shutil.rmtree(cache, ignore_errors=True)

	unresolved = verify_runtime(target)

	print(f"\ndeployed to {target}")
	if verbose:
		print(f"python   {python}")
	if unresolved:
		# Fail loudly. Every module here is loadable by the interpreter the app
		# embeds, so anything unresolved means a plugin that imports it dies at
		# load time with a message that does not name the missing library.
		raise SystemExit(
			"the deployment is incomplete: plugins would fail to load. "
			"Copy the libraries listed above next to AyuGram.exe and redeploy.")

	return blocked


def main() -> int:
	ap = argparse.ArgumentParser(description=__doc__)
	ap.add_argument(
		"--build-dir",
		default=str((REPO / "out") if REPO else (PLUGINS / "out")),
		help="CMake build directory")
	ap.add_argument("--config", default="Release")
	ap.add_argument("--to", required=True, help="target folder")
	ap.add_argument("--python", help="Python install to take the runtime from")
	ap.add_argument("-v", "--verbose", action="store_true")
	args = ap.parse_args()

	blocked = deploy(
		Path(args.build_dir),
		Path(args.to),
		args.config,
		args.python,
		args.verbose,
	)
	if blocked:
		# Non-zero, because the folder now holds a new runtime next to an old
		# executable and the next launch runs a different build than the one the
		# plugins were deployed with.
		for note in blocked:
			print(f"NOT COPIED  {note}")
		return 2
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
