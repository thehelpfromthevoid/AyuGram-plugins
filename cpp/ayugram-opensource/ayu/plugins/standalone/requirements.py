"""Pure-Python dependency resolver for `__requirements__` (mobile contract).

Mirrors the mobile pip-like manager: only `-none-any.whl` wheels from
PyPI, sha256-verified against registry metadata, extracted into a shared
libs directory. No subprocess, no setup.py, no binary wheels, never fatal.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.request
import zipfile
from pathlib import Path

PYPI_JSON = "https://pypi.org/pypi/{name}/json"
TIMEOUT = 20


def parse_requirement(spec: str):
    """Split 'name[extra]>=1.0' into (name, pinned_version|None)."""
    text = str(spec or "").strip()
    match = re.match(r"^([A-Za-z0-9_.\-]+)", text)
    if not match:
        return None, None
    name = match.group(1)
    pinned = None
    eq = re.search(r"==\s*([A-Za-z0-9_.\-!+]+)", text)
    if eq:
        pinned = eq.group(1)
    return name, pinned


def parse_requirements_list(text: str) -> list:
    """Extract `__requirements__ = [...]` from plugin source (no exec)."""
    import ast
    match = re.search(r"__requirements__\s*=\s*(\[.*?\])", text, re.S)
    if not match:
        return []
    try:
        value = ast.literal_eval(match.group(1))
    except Exception:
        return []
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v) for v in value if isinstance(v, (str, bytes))]


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "AyuGramDesktop/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pick_pure_wheel(meta: dict, pinned=None):
    """Choose a `-none-any.whl` file from PyPI metadata (or None)."""
    try:
        if pinned:
            files = (meta.get("releases") or {}).get(pinned, [])
        else:
            version = (meta.get("info") or {}).get("version", "")
            files = (meta.get("releases") or {}).get(version, []) or meta.get("urls", [])
        for entry in files or []:
            filename = str(entry.get("filename", ""))
            if not filename.endswith(".whl"):
                continue
            stem = filename[:-4]
            tags = stem.split("-")
            if len(tags) < 3:
                continue
            if tags[-2] == "none" and tags[-1] == "any":
                digests = entry.get("digests") or {}
                return {
                    "url": entry.get("url", ""),
                    "sha256": digests.get("sha256", ""),
                    "filename": filename,
                }
    except Exception:
        pass
    return None


def _already_installed(libs_dir: Path, filename: str) -> bool:
    stem = filename[:-4]
    dist = stem.split("-")[0]
    try:
        for child in libs_dir.iterdir():
            if child.is_dir() and child.name.startswith(dist + "-") \
                    and child.name.endswith(".dist-info"):
                return True
    except Exception:
        pass
    return False


def install_wheel_bytes(data: bytes, filename: str, libs_dir: Path,
                        sha256: str = "") -> bool:
    """Verify (optional digest) and extract a wheel into libs_dir."""
    if not filename.endswith(".whl"):
        return False
    stem = filename[:-4]
    tags = stem.split("-")
    if len(tags) < 3 or tags[-2] != "none" or tags[-1] != "any":
        return False
    if sha256:
        actual = hashlib.sha256(bytes(data)).hexdigest()
        if actual != sha256.lower():
            return False
    try:
        libs_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as z:
            for member in z.infolist():
                name = member.filename.replace("\\", "/")
                if ".." in name or name.startswith("/"):
                    return False
            z.extractall(libs_dir)
        return True
    except Exception:
        return False


def ensure_requirements(requirements, libs_dir: Path, log=None) -> list:
    """Best-effort resolve of `__requirements__`; returns installed names."""
    installed: list = []
    log = log or (lambda s: None)
    try:
        libs = Path(libs_dir)
    except Exception:
        return installed
    for spec in requirements or []:
        name, pinned = parse_requirement(spec)
        if not name:
            continue
        try:
            meta = _fetch_json(PYPI_JSON.format(name=name))
            pick = pick_pure_wheel(meta, pinned)
            if not pick:
                log(f"requirements: no pure-python wheel for {spec!r}, skipped")
                continue
            if _already_installed(libs, pick["filename"]):
                installed.append(name)
                continue
            req = urllib.request.Request(
                pick["url"], headers={"User-Agent": "AyuGramDesktop/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if install_wheel_bytes(data, pick["filename"], libs, pick["sha256"]):
                installed.append(name)
                log(f"requirements: installed {pick['filename']}")
            else:
                log(f"requirements: failed to install {spec!r}")
        except Exception as e:
            log(f"requirements: {spec!r} skipped ({e})")
    return installed
