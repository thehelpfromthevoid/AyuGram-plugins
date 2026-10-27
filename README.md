# AyuGram Desktop — Plugins & Open Source

Проект AyuGram Desktop — клиент Telegram для десктопа с плагин-системой на Python.
Репозиторий содержит Python standalone host, C++ исходники AyuGram-движка и документацию по сборке.

## Структура репозитория

| Папка | Содержимое |
|-------|------------|
| `standalone/` | Python standalone host (主 приложение без C++ моста) |
| `ayugram_compat/` | Python-совместимость с Android API Telegram |
| `examples/` | Примеры плагинов |
| `plugins/` | Встроенные плагины |
| `tools/` | Инструменты: selftest, CLI, watchdog,deploy |
| `ui/` | UI компоненты Python хоста |
| `android/`, `java/`, `org/` | Android API заглушки для совместимости |
| `docs/` | Документация |
| `BUILD_OWNERSHIP.md` | Кто владеет сборкой, что исправлено, как собирать |
| `.gitignore` | Исключения: tdata/, .exe, .dll, build-артефакты |

## Исправления в этом репозитории

### Python standalone host

| Проблема | Файл | Исправление |
|----------|------|-------------|
| Зависание neural_typing (блокер #1) | `examples/neural_typing/main.py` | Флаг `_sending_own` против рекурсии `_on_sending` → `send_text` → `_on_sending` |
| `ayu_api` stub затеняет нативный C++ модуль | `standalone/host_app.py` | Нативный модуль приоритетнее stub |
| custom_nickname спамит мостом каждую сек | `%APPDATA%\AyuGramDesktop\plugins\custom_nickname\main.py` | Guardia `_last_applied_name` |
| GUI плагинов унылый | `standalone/host_app.py` | Поиск/фильтр, цвет статусов ON/off/ERR, скролл деталей, Clear log |
| Окно настроек неудобное | `standalone/host_app.py` | Ресайз, скролл, Cancel, Enter/Save, Escape/Cancel, статус |
| Дублирующее сохранение настроек | `standalone/host_app.py` | Убран прямой `write_text`, только через `settings_store` |
| Live reload перечитывает все плагины | `standalone/plugin_loader.py` | Fingerprints (mtime_ns+size), только изменённые |
| Баг `getattr(row→r)` | `standalone/host_app.py` | Исправлено на `getattr(r, "key", "")` |

### C++ AyuGram Desktop (все 3 источника)

Исправления применены в трёх копиях исходников:

1. `D:\TBuild\tdesktop\Telegram\SourceFiles\ayu/`
2. `D:\AyuGram-OpenSource\SourceFiles\ayu/`
3. `D:\TBuild\tdesktop_full\Telegram\SourceFiles\ayu/`

| Исправление | Файлы |
|-------------|-------|
| Убран дубль заголовка "Плагины" | `settings_ayu.cpp`, `plugins_section.cpp` |
| Цветной статус плагина (ON/off/ERR) | `plugins_section.cpp` |
| Поиск плагинов в глобальном поиске настроек | `plugins_section.cpp` |

## Сборка C++ части

```cmd
cd /d D:\TBuild
run_build_release.bat
```

Требуется Visual Studio 2022 Community + CMake.

### Известная проблема сборки
Ошибка `C1076 / C3859` (MSVC internal heap limit для PCH). Решение: собрать с `-j 1` вместо `-j 4`.

## Релиз-бинарник

Готовый Release-бинарник: `D:\TBuild\tdesktop\out\Release\AyuGram.exe` (228 МБ)

Не коммитится в git из-за размера (.gitignore исключает .exe). Доступен локально по пути выше.

## tdata — файлы авторизации

Папка `tdata/` содержит личные файлы авторизации пользователя.
**Не коммитится, не читается, не изменяется.** Защищена `.gitignore`.

## Тестирование

```cmd
cd D:\Users\koliy\AyuGram
python tools/selftest.py
```

Результат: 51 passed, 3 failed (все 3 — предудущие ошибки `python314.dll`, не связаны с изменениями).

## Лицензия

Copyright @Radolyn, 2026. Исходный код AyuGram для Desktop.
