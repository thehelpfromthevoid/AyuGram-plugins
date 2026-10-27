 AyuGram Desktop Plugins

Cross-platform Python plugin system (Windows / Linux / macOS).

## Layout

- `plugin_manifest.*` — `plugin.json` + legacy `__id__/__name__` support
- `event_bus.*` — in-process fan-out, no Python dependency
- `plugins_manager.*` — scan / enable / disable / install
- `plugins_host.*` — facade, lifetime bound to app
- `python_host.*` — stub when `DESKTOP_APP_WITH_PLUGINS=OFF`, real bridge when ON
- `bridge/ayu_api.cpp` — `pybind11` embedded module `ayu_api`
- `sandbox/` — `audithook.py`, `import_filter.py`
- `shim/ayugram_compat/` — mobile exteraGram compat
- `ui/plugins_section.*` — native Settings → AyuGram → Plugins (no separate tab/host UI)
- `hook_points.*` — 3-line adapters for core

The standalone Tk `plugins/standalone/` host is a **developer simulator only** — not the product UI. Users manage plugins inside AyuGram Settings.

## Build

Default build does NOT require Python (metadata-only mode):

```
cmake --build out --config Debug --target Telegram
```

Full mode:

```
cmake -DDESKTOP_APP_WITH_PLUGINS=ON \
  -DPython3_ROOT_DIR=<path-to-python-3.11> \
  -Dpybind11_DIR=<path> ...
```

Add to `Telegram/CMakeLists.txt` in `ayugram_files`:

```
ayu/plugins/plugin_manifest.cpp
ayu/plugins/plugin_manifest.h
ayu/plugins/event_bus.cpp
ayu/plugins/event_bus.h
ayu/plugins/plugin_instance.h
ayu/plugins/plugins_manager.cpp
ayu/plugins/plugins_manager.h
ayu/plugins/plugins_host.cpp
ayu/plugins/plugins_host.h
ayu/plugins/python_host.cpp
ayu/plugins/python_host.h
ayu/plugins/hook_points.cpp
ayu/plugins/hook_points.h
ayu/plugins/ui/plugins_section.cpp
ayu/plugins/ui/plugins_section.h
```

Plus guarded:

```
if (DESKTOP_APP_WITH_PLUGINS)
    ... ayu/plugins/bridge/ayu_api.cpp
    target_link_libraries(Telegram PRIVATE pybind11::embed Python3::Python)
    target_compile_definitions(Telegram PRIVATE DESKTOP_APP_WITH_PLUGINS=1)
endif()
```

## Runtime layout

```
<app-data>/plugins/<plugin-id>/plugin.json
<app-data>/plugins/<plugin-id>/main.py
<app-data>/plugins/plugins.json  (enabled map)
```

On first launch `Manager::scan()` seeds bundled demos from Qt resources
(`:/ayu/plugins/...`, see `Resources/qrc/ayu/ayu.qrc`) into
`<app-data>/plugins/`. Marker file `.bundled_seeded` prevents re-seeding after
the user removes a demo. Optional sidecar: `<exe-dir>/plugins/<id>/` is copied
when present and the destination is missing.

## Install

Settings → AyuGram → Plugins:

- **Install Plugin…** — `.plugin` / `.py` / `.zip` / `.ayupack` / `plugin.json`
- **Install from Folder…** — folder with `plugin.json` + entry
- **Plugin format** — help + **Copy JSON example** (`ExampleManifestJson()`)

`Manager::installPath` routes to `installFromDir` / `installFromZip` /
`installFromPluginFile`.

## Hook wiring (examples, do not paste blindly)

```cpp
// history/history.cpp addNewMessage tail:
#ifdef AYUG_PLUGINS
Ayu::Plugins::Hooks::OnMessageReceived(id, peer, from, text, date, out);
#endif

// compose send path:
#ifdef AYUG_PLUGINS
if (!Ayu::Plugins::Hooks::OnMessageSending(peer, text)) return;
#endif

// window show chat:
#ifdef AYUG_PLUGINS
Ayu::Plugins::Hooks::OnChatOpened(peer, title);
#endif

// AyuInfra::init() tail:
Ayu::Plugins::Host::instance().init();
// shutdown path:
Ayu::Plugins::Host::instance().shutdown();
```

See `docs/plugins/*` for full API.
