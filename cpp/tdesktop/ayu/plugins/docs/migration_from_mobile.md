# Migration from mobile (exteraGram) plugins

Mobile plugin (`*.plugin` single file):

```python
__id__ = "demo"
__name__ = "Demo"
__description__ = "..."
__author__ = "..."
__version__ = "1.0"
__min_version__ = "10.0"
```

Desktop loads it as-is via legacy parser (`Ui::ParsePluginMetadata` +
`ManifestFromLegacyMetadata`). No `plugin.json` needed for v1 hooks.
Single `*.plugin` / `*.py` files, `manifest.json`, and entry scripts
`plugin.py` / `index.py` / `bot.py` / `__init__.py` are accepted too.

## Mapping

| mobile | desktop |
|---|---|
| `exteragram_utils.base_plugin.Plugin` | `ayugram_compat.base_plugin.Plugin` |
| `hook_utils.on_hook` | `ayu_api.hook` / `ayugram_compat.hook_utils.hook` |
| `android_utils.toast/context` | `ayu_api.toast`, no Context |
| `min_version` | `min_app_version` |

## Honest refusal (Android-only)

`import java` / `import android` raise a clear Android-only ImportError
which the bridge maps to a friendly load error. Optional Java usage:

```python
try:
    import java
    HAVE_JAVA = True
except ImportError:
    HAVE_JAVA = False
```

## Now stubbed (load OK, calls are no-ops)

`androidx.*`, `com.*` (incl. `com.exteragram.*`, `com.google.*`,
`com.android.*`), `org.telegram.*`, `ui.*`, `dalvik.*`, `kotlin*`,
`okhttp3`, `javax.*`. Plugin-local packages with the same names (e.g.
a bundled `ui/` helper) take precedence over stubs.

## Non-portable

`android.content.Intent`, `Context.getSystemService`, background services,
`ctypes` tricks - guard with `try/except NotImplementedError` and provide
desktop fallback.
