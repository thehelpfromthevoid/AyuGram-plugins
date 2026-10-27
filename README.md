# AyuGram Desktop — Plugins & Open Source

Проект AyuGram Desktop — клиент Telegram для десктопа с плагин-системой на Python.
Репозиторий содержит Python standalone host и документацию.

## Структура репозитория

| Папка | Содержимое |
|-------|------------|
| `cpp/tdesktop/ayu` | C++ исходники AyuGram Desktop (tdesktop) — 331 файлов, 13.6 MB |
| `cpp/ayugram-opensource/ayu` | C++ исходники AyuGram-OpenSource — 375 файлов, 4.2 MB |
| `cpp/tdesktop_full/ayu` | C++ исходники tdesktop_full — 238 файлов, 12.9 MB |
| `standalone/` | Python standalone host |
| `ayugram_compat/` | Python-совместимость с Android API Telegram |
| `examples/` | Примеры плагинов |
| `plugins/` | Встроенные плагины |
| `tools/` | Инструменты: selftest, CLI, watchdog, deploy |
| `ui/` | UI компоненты Python хоста |
| `android/`, `java/`, `org/` | Android API заглушки для совместимости |
| `docs/` | Документация |
| `BUILD_OWNERSHIP.md` | Кто владеет сборкой, что исправлено, как собирать |
| `.gitignore` | Исключения: tdata/, .exe, .dll, build-артефакты |

## Исправления в этом репозитории

### Python standalone host

| Проблема | Файл | Исправление |
|----------|------|-------------|
| Зависание neural_typing (блокер #1) | `examples/neural_typing/main.py` | Флаг `_sending_own` против рекурсии |
| `ayu_api` stub затеняет нативный C++ модуль | `standalone/host_app.py` | Нативный модуль приоритетнее stub |
| custom_nickname спамит мостом каждую сек | `%APPDATA%\AyuGramDesktop\plugins\custom_nickname\main.py` | Guardia `_last_applied_name` |
| GUI плагинов унылый | `standalone/host_app.py` | Поиск/фильтр, цвет статусов ON/off/ERR, скролл деталей, Clear log |
| Окно настроек неудобное | `standalone/host_app.py` | Ресайз, скролл, Cancel, Enter/Save, Escape/Cancel, статус |
| Дублирующее сохранение настроек | `standalone/host_app.py` | Убран прямой `write_text`, только через `settings_store` |
| Live reload перечитывает все плагины | `standalone/plugin_loader.py` | Fingerprints (mtime_ns+size), только изменённые |
| Баг `getattr(row→r)` | `standalone/host_app.py` | Исправлено на `getattr(r, "key", "")` |

## Сборка C++ части

C++ исходники AyuGram Desktop остаются локально (не в этом репозитории). Сборка через:
```cmd
cd /d D:\TBuild
run_build_release.bat
```

Требуется Visual Studio 2022 Community + CMake.

### Известная проблема сборки
Ошибка `C1076 / C3859` (MSVC internal heap limit для PCH). Решение: собрать с `-j 1` вместо `-j 4`.

## Релиз-бинарник

Готовый Release-бинарник: `D:\TBuild\tdesktop\out\Release\AyuGram.exe` (228 МБ)
Не коммитится в git из-за размера (.gitignore исключает .exe). Доступен локально.

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
