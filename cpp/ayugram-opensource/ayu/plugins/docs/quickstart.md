# Quickstart: first desktop plugin

## 1. Layout

```
my-plugin/
  plugin.json
  main.py
```

## 2. plugin.json

See `examples/hello-toast/plugin.json`. Required: `id`, `name`, `entry`, `api_version=1`.
Optional: `hooks`, `permissions`, `requirements`.

## 3. main.py

```python
from ayu_api import hook, toast

@hook("on_message")
def on_message(msg):
    toast(msg.text)
```

For mobile compat use fallback import:

```python
try:
    from ayu_api import hook, toast
except ImportError:
    from ayugram_compat.hook_utils import hook
    from ayugram_compat.android_utils import toast
```

## 4. Install (dev)

Copy folder to `<app-data>/plugins/<id>/`, restart or Rescan.
Enable in Settings > AyuGram > Plugins.

## 5. Debug

Logs go to Telegram debug log (`AyuPlugins [...]`).
Keep hooks < 500ms, never block, wrap in try/except.

## 6. Templates (examples/)

| folder | style |
|---|---|
| `command_demo` | `@command("/ping")` command reception + aliases |
| `heartbeat` | `@interval(60)` + `@on_start` lifecycle |
| `manifest_bot` | mobile layout: `manifest.json` + `bot.py` |
| `legacy_single` | legacy single file: `*.plugin` entry + `on_unload` |
| `pkg_style` | package: `__init__.py` entry + local helper module |
| `macros` | stateful plugin with JSON storage |
| `antidelete` | message cache + delete events |

Accepted everywhere: manifest `plugin.json`/`manifest.json`/`manifest.yaml`/
`manifest.toml`; entry `main.py`/`__init__.py`/`plugin.py`/`index.py`/
`bot.py`; single `*.plugin`/`*.py`/`*.pyw`; archives `*.zip`/
`*.ayupack`/`*.ayu`/`*.tgx`; module lifecycle `on_load`/`setup`/
`register`/`activate`/`main` and `on_unload`/`deactivate`;
`requirements.txt` is NOT auto-installed (security model denies subprocess);
vendor pure-python deps into a `.libs/` folder inside the plugin instead.
