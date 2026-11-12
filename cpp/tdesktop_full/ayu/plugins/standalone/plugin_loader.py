"""Scan / load / enable AyuGram plugins for the standalone host.

Accepted layouts (mirrors the C++ Manager, plus mobile conveniences):
  folder with plugin.json | manifest.json | manifest.yaml | manifest.yml |
      manifest.toml | info.json
  folder with an entry script: main.py | __init__.py | plugin.py |
      index.py | bot.py | handler.py | entry.py | app.py | run.py |
      script.py | mod.py | __main__.py | single *.plugin file | single *.py
  single file: *.plugin | *.py | *.pyw  (metadata from __id__/__name__
      or filename fallback)
  archive: *.zip | *.ayupack | *.ayu | *.tgx | *.tar | *.tar.gz | *.tgz |
      *.tar.bz2 | *.tbz2 | *.tar.xz | *.txz | *.tar.zst | *.7z | *.rar

AyuGram plugin host for Desktop, (c) @CE0fbi
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ID_RE_OK = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,63}$")
MANIFEST_NAMES = (
	"plugin.json",
	"manifest.json",
	"manifest.yaml",
	"manifest.yml",
	"manifest.toml",
	"info.json",
	"ayug.json",
	"plugin.yaml",
	"plugin.yml",
)
ENTRY_CANDIDATES = (
	"main.py",
	"__init__.py",
	"plugin.py",
	"index.py",
	"bot.py",
	"handler.py",
	"handlers.py",
	"entry.py",
	"app.py",
	"run.py",
	"script.py",
	"mod.py",
	"module.py",
	"__main__.py",
	"loader.py",
	"start.py",
	"init.py",
)
ARCHIVE_SUFFIXES = (
	".zip",
	".ayupack",
	".ayu",
	".tgx",
	".tar",
	".tar.gz",
	".tgz",
	".tar.bz2",
	".tbz2",
	".tar.xz",
	".txz",
	".tar.zst",
	".tzst",
	".7z",
	".rar",
)
SINGLE_SUFFIXES = (".plugin", ".py", ".pyw")
_META_KEYS = ("id", "name", "description", "author", "version")


def _is_archive_name(name: str) -> bool:
    """Suffix match that also understands multi-part names like `.tar.gz`."""
    low = str(name or "").lower()
    return any(low.endswith(sfx) for sfx in ARCHIVE_SUFFIXES)


def sanitize_id(raw: str, fallback: str = "plugin") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", str(raw or "")).strip("._") or fallback
    if not re.match(r"^[A-Za-z]", cleaned):
        cleaned = "p_" + cleaned
    return cleaned[:64]


def read_legacy_metadata(text: str) -> dict:
    fields: dict = {}
    for key in _META_KEYS:
        match = re.search(
            rf'__{key}__\s*=\s*["\']([^"\']+)["\']',
            text,
        )
        fields[key] = match.group(1).strip() if match else ""
    return fields


def _log(text: str) -> None:
    try:
        from ayu_api import log as _l
        _l(str(text))
    except Exception:
        print(f"[loader] {text}")


def android_hint(source_text: str, error_text: str) -> str:
    """Actionable hint for Android-only failures (appended, original kept)."""
    src = str(source_text or "")
    hints = []
    if "ChatMessageCell" in src or "setMessageObject" in src \
            or "applyNewText" in src:
        hints.append("needs message-view rendering hooks; Desktop has none. "
                     "Closest: hello-toast/autoreply templates for incoming text.")
    if "SendMessagesHelper" in src or "hook_all_methods" in src \
            or "Xposed" in src:
        hints.append("needs Java method interception; use on_message_sending "
                     "instead (macros/typing_effect pattern).")
    if "MainTabs" in src or "navbar" in src.lower() or "ActionBar" in src:
        hints.append("Android UI chrome has no Desktop equivalent.")
    if "Bulletin" in src:
        hints.append("ui.bulletin is available on Desktop (maps to toasts).")
    if re.search(r"(^|\W)(import java|from java|import android|from android)(\W|$)", src):
        hints.append("guard optional Java usage with try/except ImportError "
                     "and a desktop fallback.")
    if hints:
        return "Desktop hint: " + " ".join(dict.fromkeys(hints))
    return ""


@dataclass
class PluginInfo:
    id: str
    name: str
    description: str = ""
    author: str = ""
    version: str = "1.0"
    entry: str = "main.py"
    hooks: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    dir_path: Path | None = None
    enabled: bool = True
    loaded: bool = False
    error: str = ""


class PluginLoader:
    def __init__(self, plugins_root: Path, state_path: Path | None = None):
        self.plugins_root = Path(plugins_root)
        self.state_path = Path(state_path or (self.plugins_root / "plugins.json"))
        self.plugins: dict[str, PluginInfo] = {}
        self._enabled: dict[str, bool] = {}
        self._namespaces: dict[str, dict] = {}
        self.plugins_root.mkdir(parents=True, exist_ok=True)
        self._watch_for_stalls()

    def _watch_for_stalls(self) -> None:
        """Keep a record of a stuck main thread.

        The watchdog outside the process can only see that the window stopped
        answering, and killing it throws away the stacks that would say why.
        This has run several times for no visible reason, so the evidence is
        collected from inside, where it survives the kill.
        """
        self._enable_crash_dump()
        try:
            from ayugram_compat import stall_watch
            stall_watch.start()
        except Exception:
            pass

    def _enable_crash_dump(self) -> None:
        """Write every thread's stack on a timer, whatever state the app is in.

        faulthandler is used rather than Python code because the freeze this is
        here to explain happens with the interpreter lock held: a thread that
        needs that lock to run cannot notice anything, and the log was written
        empty. faulthandler works below the interpreter, so it still reports
        while the lock is stuck.
        """
        try:
            import faulthandler
        except Exception:
            return
        try:
            folder = self.plugins_root.parent
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / "python_crash.log"
            handle = target.open("a", encoding="utf-8", errors="replace")
        except Exception:
            return
        try:
            # Repeated, so a freeze that outlives one dump still leaves a trail
            # with timestamps rather than a single stale snapshot.
            faulthandler.enable(handle=handle, all_threads=True)
            faulthandler.dump_traceback_later(30, repeat=True, file=handle)
        except Exception:
            pass

    def load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                self._enabled = {str(k): bool(v) for k, v in data.items()}
        except Exception:
            self._enabled = {}

    def save_state(self) -> None:
        self.plugins_root.mkdir(parents=True, exist_ok=True)
        payload = {pid: info.enabled for pid, info in self.plugins.items()}
        payload.update(self._enabled)
        self.state_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _read_manifest_file(manifest: Path):
        try:
            text = manifest.read_text(encoding="utf-8-sig")
        except Exception:
            return None
        suffix = manifest.suffix.lower()
        if suffix == ".json":
            try:
                m = json.loads(text)
            except Exception:
                return None
            return m if isinstance(m, dict) else None
        if suffix in (".yaml", ".yml"):
            try:
                import yaml
                m = yaml.safe_load(text)
            except Exception:
                return None
            return m if isinstance(m, dict) else None
        if suffix == ".toml":
            try:
                import tomllib
                m = tomllib.loads(text)
            except Exception:
                return None
            return m if isinstance(m, dict) else None
        return None

    def _manifest_info(self, plugin_dir: Path, manifest: Path) -> PluginInfo | None:
        m = self._read_manifest_file(manifest)
        if not isinstance(m, dict):
            return None
        raw_id = str(m.get("id", "") or m.get("plugin_id", "") or "")
        pid = sanitize_id(raw_id, plugin_dir.name) if raw_id else sanitize_id(plugin_dir.name)
        name = str(m.get("name", "") or pid)
        entry = str(m.get("entry", "") or "")
        if not entry or ".." in entry or not (plugin_dir / entry).exists():
            entry = ""
            for candidate in ENTRY_CANDIDATES:
                if (plugin_dir / candidate).exists():
                    entry = candidate
                    break
        if not entry:
            return None
        return PluginInfo(
            id=pid,
            name=name,
            description=str(m.get("description", "")),
            author=str(m.get("author", "")),
            version=str(m.get("version", "1.0") or "1.0"),
            entry=entry,
            hooks=list(m.get("hooks") or []),
            permissions=list(m.get("permissions") or []),
            requirements=[str(r) for r in (m.get("requirements") or [])
                          if isinstance(r, str)],
            dir_path=plugin_dir,
        )

    def _entry_from_candidates(self, plugin_dir: Path) -> Path | None:
        for candidate in ENTRY_CANDIDATES:
            found = plugin_dir / candidate
            if found.is_file():
                return found
        singles = sorted(
            p for p in plugin_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() in SINGLE_SUFFIXES
            and p.name != "plugin.json"
        )
        if len(singles) == 1:
            return singles[0]
        if (plugin_dir / "main.py").is_file():
            return plugin_dir / "main.py"
        return None

    def parse_manifest(self, plugin_dir: Path) -> PluginInfo:
        for manifest_name in MANIFEST_NAMES:
            manifest = plugin_dir / manifest_name
            if manifest.is_file():
                info = self._manifest_info(plugin_dir, manifest)
                if info is not None:
                    break
        else:
            info = None
        if info is None:
            entry = self._entry_from_candidates(plugin_dir)
            if entry is None:
                raise ValueError("no plugin.json/manifest.json nor entry script (*.py/*.plugin)")
            try:
                text = entry.read_text(encoding="utf-8-sig", errors="ignore")
            except Exception:
                text = ""
            fields = read_legacy_metadata(text)
            try:
                from requirements import parse_requirements_list as _parse_req
                legacy_reqs = _parse_req(text)
            except Exception:
                legacy_reqs = []
            pid = fields["id"] or sanitize_id(plugin_dir.name)
            pid = sanitize_id(pid, plugin_dir.name)
            info = PluginInfo(
                id=pid,
                name=fields["name"] or pid,
                description=fields.get("description", ""),
                author=fields.get("author", ""),
                version=fields.get("version") or "1.0",
                entry=entry.name,
                requirements=legacy_reqs,
                dir_path=plugin_dir,
            )
        if not ID_RE_OK.match(info.id):
            raise ValueError(f"bad plugin id: {info.id!r}")
        if not info.name:
            raise ValueError("bad plugin name")
        entry = plugin_dir / info.entry
        if ".." in info.entry or not entry.exists():
            raise ValueError(f"missing entry {info.entry}")
        return info

    def scan(self) -> list[PluginInfo]:
        self.load_state()
        self.plugins.clear()
        for child in sorted(self.plugins_root.iterdir()):
            if child.name.startswith("."):
                continue
            if child.name == "__pycache__":
                continue
            if child.is_file():
                # Loose single files dropped into the folder are adopted once.
                if child.suffix.lower() in SINGLE_SUFFIXES:
                    try:
                        probe = read_legacy_metadata(
                            child.read_text(encoding="utf-8", errors="ignore"))
                        pid = sanitize_id(probe["id"] or child.stem)
                    except Exception:
                        pid = sanitize_id(child.stem)
                    if pid in self.plugins or pid in self._enabled:
                        continue
                    try:
                        info = self.install_from_file(child, rescan=False)
                        info.enabled = self._enabled.get(info.id, True)
                        self.plugins[info.id] = info
                    except Exception as e:
                        bad = PluginInfo(
                            id=sanitize_id(child.stem),
                            name=child.name,
                            enabled=False,
                            error=str(e),
                        )
                        self.plugins[bad.id] = bad
                continue
            if not child.is_dir():
                continue
            try:
                info = self.parse_manifest(child)
                info.enabled = self._enabled.get(info.id, True)
                self.plugins[info.id] = info
            except Exception as e:
                bad = PluginInfo(
                    id=child.name,
                    name=child.name,
                    dir_path=child,
                    enabled=False,
                    error=str(e),
                )
                self.plugins[bad.id] = bad
        self.save_state()
        return list(self.plugins.values())

    def _ensure_libs(self, info: PluginInfo) -> None:
        # Vendored `.libs/` inside the plugin, plus the shared root `.libs/`
        # (pure-python wheels resolved from `__requirements__`, never fatal).
        try:
            candidates = []
            if info.dir_path is not None:
                candidates.append(info.dir_path / ".libs")
            candidates.append(self.plugins_root / ".libs")
            for libs in candidates:
                if libs.is_dir():
                    path = str(libs.resolve())
                    if path not in sys.path:
                        sys.path.insert(0, path)
        except Exception:
            pass

    def _restore_settings(self, info: PluginInfo) -> None:
        # Persistent settings: <plugins_root>/<id>.settings.json, the same file
        # the shim's settings_store writes, so the CLI and the app agree.
        try:
            glb = self._namespaces.get(info.id)
            inst = glb.get("_ayu_plugin_instance") if isinstance(glb, dict) else None
            if inst is None or not hasattr(inst, "import_settings"):
                return
            fp = self.plugins_root / f"{info.id}.settings.json"
            if not fp.is_file():
                return
            data = json.loads(fp.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                values = data.get("settings", data)
                if isinstance(values, dict):
                    inst.import_settings(values)
        except Exception:
            pass

    @staticmethod
    def _install_sandbox(info: PluginInfo) -> None:
        # Dev parity with the C++ bridge: enforce manifest permissions via
        # sandbox modules when importable (host_app puts sandbox/ on path).
        # Best-effort and unioned across loads; never fails the load.
        try:
            from import_filter import install as _fi_install, grant as _fi_grant
            _fi_install()
            _fi_grant(info.permissions or [])
        except Exception:
            pass
        try:
            from audithook import install as _ah_install, grant as _ah_grant
            _ah_install()
            hosts: list = []
            storage = False
            process = False
            for perm in info.permissions or []:
                if perm == "net.http":
                    hosts.append("*")
                elif perm.startswith("net.http:"):
                    host = perm.split(":", 1)[1]
                    if host:
                        hosts.append(host)
                elif perm in ("storage.read", "storage.write"):
                    storage = True
                elif perm == "process.spawn":
                    process = True
            _ah_grant(net_hosts=hosts, storage=storage, process=process)
        except Exception:
            pass

    @staticmethod
    def _ensure_stubs() -> None:
        # Mobile stubs must be installed before ANY plugin import runs
        # (the C++ bridge does this in InitPython; standalone does it here).
        try:
            from ayugram_compat.mobile_stubs import install as _install
            _install()
        except Exception:
            pass
        # Ensure shim is on sys.path so `from base_plugin import ...` works
        shim_dir = str(Path(__file__).resolve().parent.parent / "shim")
        if shim_dir not in sys.path:
            sys.path.insert(0, shim_dir)

    def load_plugin_code(self, info: PluginInfo) -> None:
        if not info.dir_path or info.error:
            raise RuntimeError(info.error or "no dir")
        self._ensure_stubs()
        entry = info.dir_path / info.entry
        self._ensure_libs(info)
        self._install_sandbox(info)
        # Ensure plugin dir is importable for local modules.
        plugin_dir = str(info.dir_path.resolve())
        if plugin_dir not in sys.path:
            sys.path.insert(0, plugin_dir)
        code = entry.read_text(encoding="utf-8-sig")
        glb = {
            "__name__": info.id,
            "__file__": str(entry),
            "__package__": None,
        }
        try:
            exec(compile(code, str(entry), "exec"), glb, glb)
        except ImportError as e:
            hint = android_hint(code, str(e))
            raise ImportError(str(e) + ("\n" + hint if hint else "")) from e
        self._namespaces[info.id] = glb
        for hook_name in ("on_load", "setup", "register", "activate", "main"):
            candidate = glb.get(hook_name)
            if callable(candidate):
                try:
                    candidate()
                except TypeError:
                    try:
                        candidate(info)
                    except Exception:
                        pass
                except Exception:
                    pass
        try:
            activate = glb.get("activate_base_plugins")
            if activate is None:
                try:
                    from base_plugin import activate_base_plugins as activate
                except Exception:
                    try:
                        from ayugram_compat.base_plugin import activate_base_plugins as activate
                    except Exception:
                        activate = None
            if callable(activate):
                try:
                    activate(glb)
                except Exception:
                    pass
        except Exception:
            pass
        self._restore_settings(info)
        info.loaded = True
        info.error = ""

    def enable(self, plugin_id: str) -> None:
        info = self.plugins[plugin_id]
        info.enabled = True
        self._enabled[plugin_id] = True
        self.load_plugin_code(info)
        self.save_state()

    def disable(self, plugin_id: str) -> None:
        info = self.plugins[plugin_id]
        glb = self._namespaces.pop(plugin_id, None)
        if isinstance(glb, dict):
            for hook_name in ("on_unload", "deactivate", "teardown", "unload"):
                candidate = glb.get(hook_name)
                if callable(candidate):
                    try:
                        candidate()
                    except Exception:
                        pass
        try:
            from ayu_api import cancel_all_timers as _cancel_all
        except Exception:
            try:
                from ayugram_compat.hook_utils import cancel_all_timers as _cancel_all
            except Exception:
                _cancel_all = None
        if callable(_cancel_all):
            try:
                _cancel_all()
            except Exception:
                pass
        info.enabled = False
        info.loaded = False
        self._enabled[plugin_id] = False
        self.save_state()

    def _resolve_requirements(self, info: PluginInfo) -> None:
        if not info.requirements:
            return
        try:
            from requirements import ensure_requirements as _ensure_req
            _ensure_req(info.requirements, self.plugins_root / ".libs", _log)
        except Exception as e:
            _log(f"requirements skipped: {e}")

    def install_from_dir(self, src: Path) -> PluginInfo:
        src = Path(src)
        info = self.parse_manifest(src)
        self._resolve_requirements(info)
        dst = self.plugins_root / info.id
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(
            src,
            dst,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        self._enabled[info.id] = True
        self.scan()
        return self.plugins[info.id]

    def install_from_file(self, file_path: Path, rescan: bool = True) -> PluginInfo:
        """Adopt a single *.plugin / *.py / *.pyw file as a plugin folder."""
        file_path = Path(file_path)
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""
        fields = read_legacy_metadata(text)
        pid = sanitize_id(fields["id"] or file_path.stem)
        name = fields["name"] or file_path.stem
        dst = self.plugins_root / pid
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)
        shutil.copy2(file_path, dst / "main.py")
        try:
            from requirements import parse_requirements_list as _parse_req
            legacy_reqs = _parse_req(text)
        except Exception:
            legacy_reqs = []
        manifest = {
            "id": pid,
            "name": name,
            "description": fields.get("description", ""),
            "author": fields.get("author", ""),
            "version": fields.get("version") or "1.0",
            "entry": "main.py",
            "hooks": [],
            "permissions": [],
            "requirements": legacy_reqs,
        }
        (dst / "plugin.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self._resolve_requirements(PluginInfo(
            id=pid, name=name, requirements=legacy_reqs))
        self._enabled[pid] = True
        if rescan:
            self.scan()
            return self.plugins[pid]
        return PluginInfo(
            id=pid,
            name=name,
            description=manifest["description"],
            author=manifest["author"],
            version=manifest["version"],
            entry="main.py",
            dir_path=dst,
            enabled=True,
        )

    def _find_root(self, tmp: Path) -> Path | None:
        """Recursive plugin-root search, shallowest manifest wins."""
        manifests: list[Path] = []
        entries: list[Path] = []
        singles: list[Path] = []
        for root, dirs, files in os.walk(tmp):
            depth = len(Path(root).relative_to(tmp).parts)
            if depth > 4:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
            for name in MANIFEST_NAMES:
                if name in files:
                    manifests.append(Path(root))
            for name in ENTRY_CANDIDATES:
                if name in files:
                    entries.append(Path(root))
                    break
            for fname in files:
                if fname.lower().endswith(SINGLE_SUFFIXES):
                    singles.append(Path(root) / fname)
                    break
        for pool in (manifests, entries):
            if pool:
                pool.sort(key=lambda p: len(p.relative_to(tmp).parts))
                return pool[0]
        if singles:
            singles.sort(key=lambda p: len(p.relative_to(tmp).parts))
            return singles[0].parent
        return None

    def _extract_archive(self, archive: Path, tmp: Path) -> None:
        name = archive.name.lower()
        if archive.suffix.lower() == ".zip" or name.endswith((".ayupack", ".ayu", ".tgx")):
            with zipfile.ZipFile(archive, "r") as z:
                for member in z.infolist():
                    rel = member.filename.replace("\\", "/")
                    if ".." in rel or rel.startswith("/"):
                        raise ValueError(f"unsafe zip entry: {rel}")
                    dest = (tmp / rel).resolve()
                    if not str(dest).startswith(str(tmp.resolve())):
                        raise ValueError(f"unsafe zip entry: {rel}")
                    z.extract(member, tmp)
            return
        if archive.suffix.lower() == ".tar" or name.endswith((
            ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
            ".tar.zst", ".tzst",
        )):
            with tarfile.open(archive, "r:*") as t:
                for member in t.getmembers():
                    rel = member.name.replace("\\", "/")
                    if ".." in rel or rel.startswith("/"):
                        raise ValueError(f"unsafe tar entry: {rel}")
                    if member.issym() or member.islnk():
                        continue
                    dest = (tmp / rel).resolve()
                    if not str(dest).startswith(str(tmp.resolve())):
                        raise ValueError(f"unsafe tar entry: {rel}")
                    t.extract(member, tmp)
            return
        if archive.suffix.lower() in (".rar", ".7z"):
            raise ValueError(
                f"{archive.suffix} archives are not supported: extract it first "
                "or repack as .zip / .tar"
            )
        raise ValueError(f"unsupported archive: {archive.name}")

    def install_from_zip(self, zip_path: Path) -> PluginInfo:
        zip_path = Path(zip_path)
        tmp = self.plugins_root / f".tmp_{zip_path.stem}"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        try:
            self._extract_archive(zip_path, tmp)
            root = self._find_root(tmp)
            if root is None:
                # Flat single-file archive: adopt the lone script.
                scripts = [
                    p for p in tmp.iterdir()
                    if p.is_file() and p.suffix.lower() in SINGLE_SUFFIXES
                ]
                if len(scripts) == 1:
                    info = self.install_from_file(scripts[0])
                    return info
                raise ValueError("no plugin.json / manifest.json / entry script in archive")
            if root.suffix.lower() in SINGLE_SUFFIXES and root.is_file():
                return self.install_from_file(root)
            return self.install_from_dir(root)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def install_path(self, path: Path) -> PluginInfo:
        """Install anything the user drops in: dir, archive or single file."""
        path = Path(path)
        if not path.exists():
            raise ValueError(f"path missing: {path}")
        if path.is_dir():
            root = self._find_root(path)
            if root is None:
                raise ValueError("no plugin.json / manifest.json / entry script in folder")
            if root.is_file():
                return self.install_from_file(root)
            return self.install_from_dir(root)
        if path.name.lower() in MANIFEST_NAMES:
            return self.install_from_dir(path.parent)
        if _is_archive_name(path.name):
            return self.install_from_zip(path)
        if path.suffix.lower() in SINGLE_SUFFIXES:
            return self.install_from_file(path)
        raise ValueError(
            "unsupported plugin package (use a folder, or one of: "
            + ", ".join(ARCHIVE_SUFFIXES + SINGLE_SUFFIXES)
            + ")"
        )


def os_walk(top: Path):
    import os as _os
    return _os.walk(top)
