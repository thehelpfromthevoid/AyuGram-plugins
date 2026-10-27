#!/usr/bin/env python3
"""AyuGram Plugins standalone host - runnable without building Telegram."""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

# Resolve resource roots for source run and frozen (PyInstaller) run.
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent
    BUNDLE_DIR = APP_DIR.parent  # ayu/plugins

# Prefer packaged runtime modules, then sibling repo layout.
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(BUNDLE_DIR / "shim"))
sys.path.insert(0, str(BUNDLE_DIR / "sandbox"))
if (APP_DIR / "ayugram_compat").exists():
    sys.path.insert(0, str(APP_DIR))
elif (BUNDLE_DIR / "shim").exists():
    sys.path.insert(0, str(BUNDLE_DIR / "shim"))

# Prefer native C++ ayu_api (frozen/production builds) over the pure-Python
# stub in standalone/. APP_DIR is first on sys.path, so the stub would shadow
# the native module. Remove it temporarily so native can be found if bundled.
_standalone_on_path = str(APP_DIR)
if _standalone_on_path in sys.path:
    sys.path.remove(_standalone_on_path)

try:
    import ayu_api  # native C++ module (frozen builds)
except ImportError:
    sys.path.insert(0, _standalone_on_path)
    import ayu_api  # pure-Python stub (source run)

from plugin_loader import PluginLoader  # noqa: E402

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
except Exception as e:  # pragma: no cover
    print("tkinter is required:", e)
    raise SystemExit(1)


def default_plugins_root() -> Path:
    env = os.environ.get("AYU_PLUGINS_DIR")
    if env:
        return Path(env)
    local = APP_DIR / "plugins"
    if local.exists() or getattr(sys, "frozen", False):
        return local
    examples = BUNDLE_DIR / "examples"
    if examples.exists():
        return examples
    return local


class HostApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("AyuGram Plugins Host")
        self.root.geometry("920x640")
        self.root.minsize(720, 480)

        self.plugins_root = default_plugins_root()
        self.loader = PluginLoader(self.plugins_root)
        self.msg_counter = 1
        self._all_plugins = []

        ayu_api.set_sinks(toast=self._on_toast, log=self._on_log)

        self._build_ui()
        self.reload_plugins()

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Plugins dir:").pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value=str(self.plugins_root))
        ttk.Entry(top, textvariable=self.path_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=6
        )
        ttk.Button(top, text="Browse", command=self.browse_root).pack(side=tk.LEFT)
        ttk.Button(top, text="Reload", command=self.reload_plugins).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(top, text="Install…", command=self.install_plugin).pack(
            side=tk.LEFT, padx=(6, 0)
        )

        mid = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        mid.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        left = ttk.Frame(mid, padding=4)
        right = ttk.Frame(mid, padding=4)
        mid.add(left, weight=1)
        mid.add(right, weight=2)

        # ---- Left: plugin list + filter ----
        ttk.Label(left, text="Installed plugins").pack(anchor=tk.W)
        filter_frame = ttk.Frame(left)
        filter_frame.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(filter_frame, text="Search:").pack(side=tk.LEFT)
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self._filter_plugins())
        ttk.Entry(filter_frame, textvariable=self.filter_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0)
        )
        self.listbox = tk.Listbox(left, exportselection=False)
        self.listbox.pack(fill=tk.BOTH, expand=True, pady=4)
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self._show_selected())
        # Color tags for status
        self.listbox.tag_configure("on", foreground="#007acc")
        self.listbox.tag_configure("off", foreground="#888888")
        self.listbox.tag_configure("err", foreground="#c00000")

        btns = ttk.Frame(left)
        btns.pack(fill=tk.X)
        ttk.Button(btns, text="Enable", command=self.enable_selected).pack(
            side=tk.LEFT, expand=True, fill=tk.X
        )
        ttk.Button(btns, text="Disable", command=self.disable_selected).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0)
        )
        ttk.Button(
            left, text="Plugin settings…", command=self.edit_settings
        ).pack(fill=tk.X, pady=(4, 0))

        ttk.Separator(left, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(8, 4))
        ttk.Label(left, text="Details").pack(anchor=tk.W)
        self.detail_text = scrolledtext.ScrolledText(
            left, height=10, wrap=tk.WORD, state=tk.DISABLED
        )
        self.detail_text.pack(fill=tk.BOTH, expand=True, pady=(2, 0))

        sim = ttk.LabelFrame(right, text="Simulate Telegram events", padding=8)
        sim.pack(fill=tk.X)
        row = ttk.Frame(sim)
        row.pack(fill=tk.X)
        ttk.Label(row, text="Text:").pack(side=tk.LEFT)
        self.event_text = tk.StringVar(value="hello from AyuGram")
        ttk.Entry(row, textvariable=self.event_text).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=6
        )

        actions = ttk.Frame(sim)
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(
            actions, text="on_message", command=self.fire_message
        ).pack(side=tk.LEFT)
        ttk.Button(
            actions, text="on_message_sending", command=self.fire_sending
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            actions, text="on_message_deleted", command=self.fire_deleted
        ).pack(side=tk.LEFT)
        ttk.Button(
            actions, text="on_chat_opened", command=self.fire_chat
        ).pack(side=tk.LEFT, padx=4)

        log_frame = ttk.LabelFrame(right, text="Toast / log output", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        log_ctrl = ttk.Frame(log_frame)
        log_ctrl.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(log_ctrl, text="Clear log", command=self._clear_log).pack(side=tk.LEFT)
        self.log = scrolledtext.ScrolledText(log_frame, height=18, wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True)
        self.log.configure(state=tk.DISABLED)

        status = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        status.pack(fill=tk.X)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(status, textvariable=self.status_var).pack(side=tk.LEFT)

    def _append_log(self, line: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, line + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _on_toast(self, text: str) -> None:
        self._append_log(f"[toast] {text}")
        self.status_var.set(f"Toast: {text[:80]}")

    def _on_log(self, text: str) -> None:
        self._append_log(f"[log] {text}")

    def _clear_log(self) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)

    def browse_root(self) -> None:
        chosen = filedialog.askdirectory(initialdir=str(self.plugins_root))
        if not chosen:
            return
        self.plugins_root = Path(chosen)
        self.path_var.set(str(self.plugins_root))
        self.loader = PluginLoader(self.plugins_root)
        self.reload_plugins()

    def install_plugin(self) -> None:
        path = filedialog.askopenfilename(
            title="Install plugin (any format; Cancel+dir for a folder)",
            filetypes=[
                ("Plugins", "*.ayupack *.zip *.ayu *.tgx *.plugin *.py *.pyw"),
                ("All files", "*.*"),
            ],
        )
        try:
            if path:
                info = self.loader.install_path(Path(path))
            else:
                folder = filedialog.askdirectory(title="Install plugin folder")
                if not folder:
                    return
                info = self.loader.install_path(Path(folder))
            self.reload_plugins(load_enabled=True)
            messagebox.showinfo("Installed", f"Installed {info.name} ({info.id})")
        except Exception as e:
            messagebox.showerror("Install failed", str(e))

    def reload_plugins(self, load_enabled: bool = True) -> None:
        ayu_api.clear_hooks()
        root = Path(self.path_var.get().strip() or self.plugins_root)
        if root != self.plugins_root:
            self.plugins_root = root
            # Reuse the same loader instance so namespaces and fingerprints
            # persist across reloads; only the root directory changes.
            self.loader = PluginLoader(root)
        plugins = self.loader.scan()
        self._all_plugins = list(plugins)
        loaded = 0
        for p in plugins:
            mark = "ON" if p.enabled else "off"
            tag = None
            if p.error:
                mark = "ERR"
                tag = "err"
            elif p.enabled:
                tag = "on"
            else:
                tag = "off"
            # Listbox is rebuilt by _filter_plugins(); no insert needed here.
            # Load only enabled, error-free, not-yet-loaded plugins.
            # Unchanged plugins keep their loaded namespace and are skipped.
            if load_enabled and p.enabled and not p.error and not p.loaded:
                try:
                    self.loader.load_plugin_code(p)
                    loaded += 1
                except Exception as e:
                    p.error = str(e)
                    p.enabled = False
                    self._append_log(f"Failed {p.id}: {e}")
                    self._append_log(traceback.format_exc())
        self._filter_plugins()
        ayu_api.emit("on_app_start")
        self.status_var.set(
            f"{len(plugins)} plugins, {loaded} loaded from {self.plugins_root}"
        )
        self._show_selected()

    def _settings_path(self, pid: str) -> Path:
        return self.plugins_root / f"{pid}.settings.json"

    def edit_settings(self) -> None:
        pid = self._selected_id()
        if not pid:
            return
        ns = self.loader._namespaces.get(pid, {})
        inst = ns.get("_ayu_plugin_instance") if isinstance(ns, dict) else None
        rows = []
        if inst is not None and hasattr(inst, "create_settings"):
            try:
                rows = list(inst.create_settings() or [])
            except Exception as e:
                messagebox.showerror("Settings", f"create_settings failed: {e}")
                return

        p = self.loader.plugins.get(pid)
        title = f"Settings — {p.name}" if p else f"Settings — {pid}"

        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("520x480")
        win.minsize(420, 300)
        win.transient(self.root)
        win.grab_set()

        container = ttk.Frame(win, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            container, text=title,
            font=("Segoe UI", 12, "bold")
        ).pack(anchor=tk.W, pady=(0, 10))

        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            container, orient=tk.VERTICAL, command=canvas.yview)
        settings_frame = ttk.Frame(canvas)
        settings_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=settings_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-3, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(3, "units"))

        editable = [r for r in rows
                    if hasattr(r, "key") and getattr(r, "key", "")]
        if not editable:
            ttk.Label(
                settings_frame,
                text=f"{pid} has no editable settings.",
                foreground="#666666").pack(anchor=tk.W, pady=20)
        else:
            controls = {}
            for row in rows:
                kind = type(row).__name__
                key = getattr(row, "key", None)
                label = getattr(row, "text", "") or getattr(row, "key", "")

                if kind == "Header":
                    ttk.Separator(
                        settings_frame, orient=tk.HORIZONTAL
                    ).pack(fill=tk.X, pady=(10, 4))
                    ttk.Label(
                        settings_frame, text=label,
                        font=("Segoe UI", 10, "bold"),
                        foreground="#007acc").pack(anchor=tk.W, pady=(0, 6))
                    continue

                if not key:
                    continue

                current = inst.get_setting(
                    key, getattr(row, "default", "")) if inst else ""

                frm = ttk.Frame(settings_frame)
                frm.pack(fill=tk.X, pady=(0, 8))

                ttk.Label(frm, text=label).pack(anchor=tk.W)

                if kind == "Switch":
                    var = tk.BooleanVar(value=bool(current))
                    ttk.Checkbutton(frm, variable=var).pack(anchor=tk.W)
                    controls[key] = lambda v=var: bool(v.get())
                elif kind == "Selector":
                    items = list(getattr(row, "items", []) or ["0"])
                    try:
                        idx = int(current)
                    except Exception:
                        idx = 0
                    idx = max(0, min(len(items) - 1, idx))
                    var = tk.StringVar(value=items[idx])
                    ttk.Combobox(
                        frm, textvariable=var, values=items,
                        state="readonly").pack(fill=tk.X, pady=(2, 0))
                    controls[key] = (
                        lambda it=items, v=var: it.index(v.get()))
                else:
                    var = tk.StringVar(value=str(current))
                    ttk.Entry(frm, textvariable=var).pack(
                        fill=tk.X, pady=(2, 0))
                    controls[key] = lambda v=var: v.get()

        status_var = tk.StringVar(value="")
        status_lbl = ttk.Label(
            container, textvariable=status_var,
            foreground="#007acc", font=("Segoe UI", 9))
        status_lbl.pack(anchor=tk.W, pady=(8, 4))

        btn_frame = ttk.Frame(container)
        btn_frame.pack(fill=tk.X, pady=(6, 0))

        def _save() -> None:
            if not controls:
                return
            payload = {}
            errors = []
            for key, getter in controls.items():
                try:
                    payload[key] = getter()
                except Exception as exc:
                    errors.append(f"{key}: {exc}")
            if errors:
                status_var.set("Save error: " + "; ".join(errors))
                status_lbl.configure(foreground="#c00000")
                return
            if inst is None:
                messagebox.showerror("Settings", "Plugin instance not loaded.")
                return
            try:
                inst.import_settings(payload)
            except Exception as exc:
                messagebox.showerror(
                    "Settings", f"import_settings failed: {exc}")
                return
            status_var.set("Settings saved.")
            status_lbl.configure(foreground="#008000")
            try:
                ayu_api.toast("Settings saved")
            except Exception:
                pass
            win.after(600, win.destroy)

        def _cancel() -> None:
            win.destroy()

        ttk.Button(btn_frame, text="Cancel", command=_cancel).pack(
            side=tk.RIGHT, padx=(6, 0))
        save_btn = ttk.Button(
            btn_frame, text="Save", command=_save)
        save_btn.pack(side=tk.RIGHT)

        win.bind("<Return>", lambda e: _save())
        win.bind("<Escape>", lambda e: _cancel())
        save_btn.focus_set()

        def _on_destroy(_e=None):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")
        win.protocol("WM_DELETE_WINDOW", lambda: (_on_destroy(), win.destroy()))

    def _selected_id(self) -> str | None:
        sel = self.listbox.curselection()
        if not sel:
            return None
        idx = sel[0]
        plugins = list(self.loader.plugins.values())
        if idx >= len(plugins):
            return None
        return plugins[idx].id

    def _filter_plugins(self) -> None:
        """Show only listbox items matching the search text."""
        query = self.filter_var.get().strip().lower()
        self.listbox.delete(0, tk.END)
        for p in self._all_plugins:
            haystack = f"{p.name} {p.id} {p.description or ''} {p.author or ''}".lower()
            if query and query not in haystack:
                continue
            mark = "ON" if p.enabled else "off"
            tag = None
            if p.error:
                mark = "ERR"
                tag = "err"
            elif p.enabled:
                tag = "on"
            else:
                tag = "off"
            self.listbox.insert(tk.END, f"[{mark}] {p.name} - {p.id}", (tag,))

    def _show_selected(self) -> None:
        pid = self._selected_id()
        self.detail_text.configure(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        if not pid:
            self.detail_text.insert(tk.END, "Select a plugin")
            self.detail_text.configure(state=tk.DISABLED)
            return
        p = self.loader.plugins.get(pid)
        if p is None:
            self.detail_text.insert(tk.END, "Plugin not found")
            self.detail_text.configure(state=tk.DISABLED)
            return
        lines = [
            f"Name:   {p.name}",
            f"ID:     {p.id}",
            f"Version: {p.version}",
            f"Author: {p.author}",
            f"Enabled: {'yes' if p.enabled else 'no'}",
            f"Error:   {p.error or '-'}",
            f"Hooks:   {', '.join(p.hooks) or '-'}",
            f"Description: {p.description or '-'}",
        ]
        self.detail_text.insert(tk.END, "\n".join(lines))
        self.detail_text.configure(state=tk.DISABLED)

    def enable_selected(self) -> None:
        pid = self._selected_id()
        if not pid:
            return
        try:
            ayu_api.clear_hooks()
            for p in self.loader.plugins.values():
                if p.id == pid:
                    p.enabled = True
                    self.loader._enabled[pid] = True
                if p.enabled and not p.error:
                    self.loader.load_plugin_code(p)
            self.loader.save_state()
            self.reload_plugins()
        except Exception as e:
            messagebox.showerror("Enable failed", str(e))

    def disable_selected(self) -> None:
        pid = self._selected_id()
        if not pid:
            return
        self.loader.disable(pid)
        self.reload_plugins()

    def fire_message(self) -> None:
        text = self.event_text.get()
        msg = ayu_api.Message(
            msg_id=self.msg_counter,
            peer_id=1001,
            from_id=42,
            text=text,
            date=int(time.time()),
            out=False,
        )
        self.msg_counter += 1
        self._append_log(f"→ on_message: {text!r}")
        ayu_api.emit("on_message", msg)

    def fire_sending(self) -> None:
        text = self.event_text.get()
        ctx = ayu_api.MessageSendingContext(
            message=ayu_api.Message(peer_id=1001, text=text, out=True)
        )
        self._append_log(f"→ on_message_sending: {text!r}")
        ok = ayu_api.emit_message_sending(ctx)
        if not ok:
            self._append_log("  cancelled by plugin")
        elif ctx.rewrite:
            self._append_log(f"  rewritten → {ctx.rewrittenText!r}")
            self.event_text.set(ctx.rewrittenText)

    def fire_deleted(self) -> None:
        ctx = ayu_api.MessageDeletedContext(
            peer_id=1001,
            msgIds=[max(1, self.msg_counter - 1)],
        )
        self._append_log(f"→ on_message_deleted: {ctx.msgIds}")
        ayu_api.emit("on_message_deleted", ctx)

    def fire_chat(self) -> None:
        chat = ayu_api.Chat(peer_id=1001, title="Demo chat", type=0)
        self._append_log("→ on_chat_opened")
        ayu_api.emit("on_chat_opened", chat)

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self) -> None:
        try:
            ayu_api.emit("on_app_stop")
        finally:
            self.root.destroy()


def main() -> int:
    HostApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
