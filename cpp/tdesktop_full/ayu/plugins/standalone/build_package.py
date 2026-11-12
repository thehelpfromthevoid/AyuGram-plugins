#!/usr/bin/env python3
"""Build one launchable AyuGram Plugins package (PyInstaller onedir)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGINS = ROOT.parent
# standalone -> plugins -> ayu -> SourceFiles -> Telegram -> repo root
REPO = ROOT.parents[4]
OUT_PARENT = REPO / "dist"
STAGE = ROOT / "_stage"

EXAMPLES = {
    "hello-toast": "com.ayugram.hello-toast",
    "translator": "com.ayugram.translator-demo",
    "antidelete": "com.ayugram.antidelete",
    "world_time": "world_time",
    "scam_base_writer": "scam_base_writer",
    "animtou": "animtou",
}


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "_stage"),
    )


def data_arg(src: Path, dest_name: str) -> str:
    sep = ";" if sys.platform.startswith("win") else ":"
    return f"{src}{sep}{dest_name}"


def stage() -> Path:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for name in ("host_app.py", "plugin_loader.py", "ayu_api.py",
                 "requirements.py"):
        shutil.copy2(ROOT / name, STAGE / name)
    copy_tree(PLUGINS / "shim" / "ayugram_compat", STAGE / "ayugram_compat")
    copy_tree(PLUGINS / "sandbox", STAGE / "sandbox")
    for pkg in ("ui", "java", "android", "extera_utils", "exteragram_utils"):
        src = PLUGINS / "shim" / pkg
        if src.exists():
            copy_tree(src, STAGE / pkg)
    for name in ("android_utils.py", "base_plugin.py", "client_utils.py",
                 "file_utils.py", "hook_utils.py"):
        src = PLUGINS / "shim" / name
        if src.exists():
            shutil.copy2(src, STAGE / name)
    plugins_dst = STAGE / "plugins"
    plugins_dst.mkdir()
    for folder, pid in EXAMPLES.items():
        src = PLUGINS / "examples" / folder
        if src.exists():
            copy_tree(src, plugins_dst / pid)
    enabled = {pid: True for pid in EXAMPLES.values()}
    import json
    (plugins_dst / "plugins.json").write_text(
        json.dumps(enabled, indent=2) + "\n",
        encoding="utf-8",
    )
    return STAGE


def build() -> Path:
    stage_dir = stage()
    OUT_PARENT.mkdir(parents=True, exist_ok=True)
    built = OUT_PARENT / "AyuGramPlugins"
    if built.exists():
        shutil.rmtree(built)
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "AyuGramPlugins",
        "--distpath",
        str(OUT_PARENT),
        "--workpath",
        str(ROOT / "_pyi_work"),
        "--specpath",
        str(ROOT / "_pyi_spec"),
        "--add-data",
        data_arg(stage_dir / "plugins", "plugins"),
        "--add-data",
        data_arg(stage_dir / "ayugram_compat", "ayugram_compat"),
        "--add-data",
        data_arg(stage_dir / "sandbox", "sandbox"),
        "--add-data",
        data_arg(stage_dir / "ui", "ui"),
        "--add-data",
        data_arg(stage_dir / "java", "java"),
        "--add-data",
        data_arg(stage_dir / "android", "android"),
        "--add-data",
        data_arg(stage_dir / "extera_utils", "extera_utils"),
        "--add-data",
        data_arg(stage_dir / "exteragram_utils", "exteragram_utils"),
        "--add-data",
        data_arg(stage_dir / "ayu_api.py", "."),
        "--add-data",
        data_arg(stage_dir / "plugin_loader.py", "."),
        "--add-data",
        data_arg(stage_dir / "requirements.py", "."),
        str(stage_dir / "host_app.py"),
    ]
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(stage_dir))
    side_plugins = built / "plugins"
    if side_plugins.exists():
        shutil.rmtree(side_plugins)
    shutil.copytree(stage_dir / "plugins", side_plugins)
    for name in ("ayugram_compat", "sandbox", "ui", "java", "android",
                 "extera_utils", "exteragram_utils"):
        src = stage_dir / name
        if not src.exists():
            continue
        dst = built / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    for name in ("ayu_api.py", "plugin_loader.py", "host_app.py",
                 "requirements.py",
                 "android_utils.py", "base_plugin.py", "client_utils.py",
                 "file_utils.py", "hook_utils.py"):
        src = stage_dir / name
        if src.exists():
            shutil.copy2(src, built / name)
    (built / "START.bat").write_text(
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "start \"\" \"AyuGramPlugins.exe\"\r\n",
        encoding="utf-8",
    )
    (built / "README.txt").write_text(
        "AyuGram Plugins - standalone host\r\n"
        "\r\n"
        "Launch: double-click START.bat or AyuGramPlugins.exe\r\n"
        "\r\n"
        "Plugins are in the plugins\\ folder. Enable/disable in the UI.\r\n"
        "Simulate events with the buttons to verify hooks (toast/log panel).\r\n"
        "\r\n"
        "Install more: use Install… for .ayupack/.zip or a plugin folder.\r\n",
        encoding="utf-8",
    )
    return built


if __name__ == "__main__":
    path = build()
    print(f"OK: {path}")
    print(f"Launch: {path / 'START.bat'}")
