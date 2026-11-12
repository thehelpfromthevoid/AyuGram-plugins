# API reference (v1)

## Module `ayu_api`

- `hook(name: str, fn)` - subscribe. Names: `on_message`, `on_message_sending`,
  `on_message_deleted`, `on_chat_opened`, `on_app_start`, `on_app_stop`, `on_setting_changed`.
  Mobile spellings are normalized (`before_send_message`, `new_message`,
  `message_deleted`, `chat_open` …); pure Java/TL hooks are accepted and
  ignored with a log line instead of failing the load.
- `toast(text: str)` - show toast on main thread.
- `log(text: str)` - write to debug log.
- `Message { msg_id, peer_id, from_id, text, date, out }`
- `Chat { peer_id, title, type }`

`on_message_sending(ctx)`: set `ctx.rewrite=True`, `ctx.rewrittenText=...` or
`ctx.cancel=True` to drop. Return False also cancels.

## Compat `ayugram_compat`

- `base_plugin.BasePlugin` - mobile-compatible base (lifecycle, send hooks, settings helpers).
- `base_plugin.Plugin` - legacy dataclass alias.
- Top-level `base_plugin.py` re-exports `BasePlugin` so `from base_plugin import BasePlugin` works.
- `hook_utils.hook(name)` / `on_hook` - alias to `ayu_api.hook`.
- `android_utils.toast/log/get_pref/set_pref` - desktop adaptations.
  `require_android_context()` / `get_context()` / `get_activity()` return None.
- `client_utils` controllers (`get_messages_controller()`, …) and
  `hook_utils` reflection (`find_class()`, `get_method()`, …) return
  chainable stubs, never None - `a.b().c()` chains survive load.
- `BasePlugin.hook_method()` / `.client()` raise `NotImplementedError`
  with a clear message - guard with try/except and provide a desktop
  fallback (see migration doc).
- `java` / `android` refuse with a clear Android-only `ImportError`
  (the bridge maps it to a friendly load error) - guard optional Java
  usage with try/except ImportError. `androidx.*`, `com.*`, `org.*`,
  `ui`, `dalvik`, `kotlin*`, `okhttp3`, `javax.*` import as live no-op
  stubs (callable, chainable, falsy). Real files on sys.path (e.g. a
  plugin-local `ui/` helper) always win over stubs.

## Real mobile modules (per exteraGram docs)

- `ui.settings`: real dataclasses `Header`, `Divider`, `Switch`,
  `Selector`, `Input`, `Text`, `EditText`, `Custom`,
  `SimpleSettingFactory`. `create_settings()` defaults are seeded into
  plugin settings on load (no Android UI is built).
- `ui.bulletin.BulletinHelper`: `show_info/error/success/simple/two_line/
  with_button/undo/copied_to_clipboard/link_copied/file_saved_to_gallery/
  file_saved_to_downloads` - all surface as toasts.
- `ui.alert.AlertDialogBuilder`: chainable builder, `show()` toasts
  title+message, listeners stored but never invoked.
- `file_utils`: real dirs + `ensure_dir_exists`, `list_dir`,
  `write/read/delete_file[_bytes]`; `FilesController` stores extension
  handlers (`SUPPORT_ICONS` is False, double-register/secret enforced).
- `extera_utils.classes`: `Base`, identity `java_subclass`/`joverride`.

## Accepted layouts

Folder with `plugin.json` **or** `manifest.json`; entry script `main.py` |
`__init__.py` | `plugin.py` | `index.py` | `bot.py`; single `*.plugin` /
`*.py` / `*.pyw` file (metadata from `__id__`/`__name__` or filename);
archive `*.zip` / `*.ayupack` / `*.ayu` / `*.tgx` with the root found
recursively. A vendored `.libs/` directory inside the plugin folder is
added to `sys.path` (no auto-install: the security model denies
subprocess and proprietary wheels). Tooling:
`tools/ayu-plugin-cli/validate.py` (lenient check),
`tools/ayu-plugin-cli/compat_check.py` (load report).

Dependencies: declare pure-python wheels in \__requirements__(mobile contract) or manifest equirements\; the standalone host
downloads verified \-none-any.whl\ files from PyPI into a shared
\.libs/\ (no subprocess, no binary wheels, never fatal).

Android-only refusal carries a +'Desktop hint:'+ line naming the closest
desktop alternative. Per-plugin settings edited in the host UI persist to
<id>.settings.json (simulator-side).

Desktop conveniences on top of the 7 hooks (no bridge changes needed):
`hook_utils.command("/ping")`, `hook_utils.interval(60)`,
`hook_utils.on_start` / `on_stop`, module lifecycle `on_load` / `setup` /
`register` / `activate` / `main` and `on_unload` / `deactivate`.

## Permissions

`ui.toast`, `storage.read`, `storage.write`, `net.http:<host>`, `process.spawn`,
`native.code`, `runtime.introspect`. Deny by default via `audithook.py` +
`import_filter.py`, enforced per manifest on every load (grants union
across loads, never narrow). Bare `net.http` allows any host.
