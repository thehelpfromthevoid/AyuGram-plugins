# Integration patch (manual, to avoid breaking build)

All new code compiles WITHOUT Python. To enable full bridge:

## 1. `cmake/options.cmake` or root `CMakeLists.txt`

```cmake
option(DESKTOP_APP_WITH_PLUGINS "AyuGram python plugins" OFF)
```

## 2. `Telegram/CMakeLists.txt` — append to `ayugram_files` (after line ~248):

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
ayu/plugins/bridge/python_bridge.h
```

Guarded:

```cmake
if (DESKTOP_APP_WITH_PLUGINS)
    find_package(Python3 3.11 REQUIRED COMPONENTS Development.Embed Interpreter)
    include(FetchContent)
    FetchContent_Declare(pybind11 GIT_REPOSITORY https://github.com/pybind/pybind11.git GIT_TAG v2.12.0)
    FetchContent_MakeAvailable(pybind11)
    nice_target_sources(Telegram ${src_loc} PRIVATE ayu/plugins/bridge/ayu_api.cpp)
    target_link_libraries(Telegram PRIVATE pybind11::embed Python3::Python)
    target_compile_definitions(Telegram PRIVATE DESKTOP_APP_WITH_PLUGINS=1)
endif()
```

## 3. `AyuInfra::init()` tail (`ayu/ayu_infra.cpp`):

```cpp
#include "ayu/plugins/plugins_host.h"
...
Ayu::Plugins::Host::instance().init();
```

Shutdown path (Core::App quit): `Ayu::Plugins::Host::instance().shutdown();`

## 4. Hook points (all optional, guarded):

```cpp
// anywhere you have (msgId, peerId, fromId, text):
#include "ayu/plugins/hook_points.h"
Ayu::Plugins::Hooks::OnMessageReceived(...);
```

## 5. Settings UI (`ayu/ui/settings/settings_ayu.cpp` SetupAyu):

```cpp
#include "ayu/plugins/ui/plugins_section.h"
...
Ayu::Plugins::Ui::SetupPluginsSection(builder);
```

## 6. Plugin info box (`ayu/ui/boxes/plugin_info_box.cpp` FillPluginInfoBox tail):

Replace `ayu_PluginsNotAvailable` label with Install button calling
`Ayu::Plugins::Manager::instance().installFromDir(...)` when Python available,
else keep label. See README.
