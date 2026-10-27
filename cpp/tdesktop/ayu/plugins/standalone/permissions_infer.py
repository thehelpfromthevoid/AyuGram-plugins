"""Infer the permissions a plugin needs from the code it ships with.

A plugin written for a phone has no reason to declare anything: on Android
there is nothing to declare. On Desktop the sandbox then refuses the first
`import urllib` and the plugin never loads, with a message about a permission
rather than about the missing manifest field.

Reading the source is more reliable than asking the author, and it is exact for
the three permissions that matter. Nothing is invented: if the code never
touches the filesystem there is no reason to grant `storage.write`.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

MANIFEST_NAMES = ("plugin.json", "manifest.json", "manifest.yaml",
                  "manifest.yml", "manifest.toml", "info.json",
                  "plugin.yaml", "ayug.json")

# utf-8-sig, not utf-8: a manifest saved by a Windows editor or by PowerShell's
# Set-Content carries a BOM, and ast.parse rejects a source file that starts
# with one. Half the plugins in the wild were written that way.
ENCODING = "utf-8-sig"

# Import that needs each permission. Kept narrow on purpose: granting more
# than the code uses would defeat the point of declaring anything.
NETWORK_MODULES = {
    "urllib", "http", "ssl", "socket", "requests", "aiohttp", "ftplib",
    "telnetlib", "smtplib", "pycurl",
}
PROCESS_MODULES = {"subprocess", "multiprocessing"}

# Writing is the harder one: a plugin that only reads does not need it, and
# almost every plugin logs. Both have to be present for the permission to count.
WRITE_CALLS = {"w", "a", "wb", "ab", "w+", "a+", "x", "x+"}


def _entry(root: Path) -> Path | None:
	manifest = None
	for name in MANIFEST_NAMES:
		candidate = root / name
		if candidate.is_file():
			manifest = candidate
			break
	entry = "main.py"
	if manifest is not None:
		try:
			data = json.loads(manifest.read_text(encoding=ENCODING))
			entry = str(data.get("entry") or entry)
		except Exception:
			entry = "main.py"
	script = root / entry
	if script.is_file():
		return script
	for candidate in sorted(root.glob("*.py")):
		return candidate
	return None


def _walk_sources(root: Path) -> list[Path]:
	scripts = []
	for candidate in sorted(root.rglob("*.py")):
		parts = set(candidate.parts)
		if "__pycache__" in parts or candidate.parent == root:
			continue
		scripts.append(candidate)
	entry = _entry(root)
	if entry and entry not in scripts:
		scripts.insert(0, entry)
	return scripts


def infer(root: Path) -> dict[str, str]:
	"""Permission -> the import or call that made it necessary."""
	found: dict[str, str] = {}
	for script in _walk_sources(Path(root)):
		try:
			source = script.read_text(encoding=ENCODING, errors="replace")
			tree = ast.parse(source)
		except Exception:
			continue
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				for alias in node.names:
					top = alias.name.split(".")[0]
					if top in NETWORK_MODULES:
						found.setdefault("net.http", f"import {top}")
					elif top in PROCESS_MODULES:
						found.setdefault("process.spawn", f"import {top}")
			elif isinstance(node, ast.ImportFrom) and node.module:
				top = node.module.split(".")[0]
				if top in NETWORK_MODULES:
					found.setdefault("net.http", f"from {node.module}")
				elif top in PROCESS_MODULES:
					found.setdefault("process.spawn", f"from {node.module}")
			elif isinstance(node, ast.Call):
				if not isinstance(node.func, ast.Name) or node.func.id != "open":
					continue
				mode = None
				for keyword in node.keywords:
					if keyword.arg == "mode" and isinstance(
							keyword.value, ast.Constant):
						mode = keyword.value.value
				if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
					mode = node.args[1].value
				if isinstance(mode, str) and mode in WRITE_CALLS:
					found.setdefault("storage.write", f"open(..., {mode!r})")
	return found


def ensure_manifest_permissions(root: Path, dry_run: bool = False):
	"""Add the permissions the code needs to the plugin's manifest.

	Returns a dict of permission -> the import or call that justified it, empty
	when there was nothing to add. Nothing is written when the plugin already
	declares them, so installing a plugin twice cannot keep changing its
	manifest.
	"""
	root = Path(root)
	manifest = None
	for name in MANIFEST_NAMES:
		candidate = root / name
		if candidate.is_file():
			manifest = candidate
			break
	if manifest is None:
		return {}
	try:
		data = json.loads(manifest.read_text(encoding=ENCODING))
	except Exception:
		return {}
	if not isinstance(data, dict):
		return {}

	needed = infer(root)
	if not needed:
		return {}
	existing = data.get("permissions")
	existing = list(existing) if isinstance(existing, list) else []
	missing = {k: v for k, v in needed.items() if k not in existing}
	if not missing or dry_run:
		return missing

	data["permissions"] = existing + sorted(missing)
	manifest.write_text(
		json.dumps(data, ensure_ascii=False, indent=2) + "\n",
		encoding="utf-8")
	return missing


def describe(missing: dict[str, str]) -> str:
	return ", ".join(f"{name} (because {why})" for name, why in
                     sorted(missing.items()))