# AyuGram Desktop - Plugins & Open Source

## О проекте

AyuGram Desktop - клиент Telegram для десктопа на базе Telegram Desktop (Qt/C++), дополненный плагин-системой на Python.

Репозиторий содержит:
- **Открытые исходники C++** (3 источника: tdesktop, AyuGram-OpenSource, tdesktop_full) - в папке `cpp/`
- **Python standalone host** - отдельный Python-клиент для разработки и тестирования плагинов
- **Документацию** - BUILD_OWNERSHIP.md, API reference, quickstart и др.
- **Готовую сборку** - Release-бинарник в разделе Releases (тег v1.0.0)

Автор: @CE0fbi

## Структура репозитория

```
AyuGram-plugins/
├── cpp/                          # Открытые исходники C++ AyuGram Desktop
│   ├── tdesktop/ayu/             # 331 файлов, 13.6 MB — основная сборка
│   ├── ayugram-opensource/ayu/   # 375 файлов, 4.2 MB — open-source версия
│   └── tdesktop_full/ayu/        # 238 файлов, 12.9 MB — полная версия
├── standalone/                   # Python standalone host
├── ayugram_compat/               # Python-совместимость с Android API Telegram
├── examples/                     # Примеры плагинов (neural_typing, animtou и др.)
├── plugins/                      # Встроенные плагины
├── tools/                        # Инструменты: selftest, CLI, watchdog, deploy
├── ui/                           # UI компоненты Python хоста
├── android/, java/, org/         # Android API заглушки для совместимости
├── docs/                         # Документация
├── BUILD_OWNERSHIP.md            # Кто владеет сборкой, что исправлено, как собирать
├── .gitignore                    # Исключения: tdata/, .exe, .dll, build-артефакты
└── README.md                     # Этот файл
```

## Плагины: скачать и сделать свой

AyuGram запускает Python-плагины прямо внутри приложения. Отдельная программа
для этого не нужна: плагин - папка с `main.py` и манифестом, которую кладут в
`%APPDATA%\AyuGramDesktop\plugins`, и она появляется в **Настройки → Плагины**.

### Поставить готовый плагин

Из самого приложения: меню **Настройки → Плагины** предлагает установку из файла
и из папки. Также подходят `.zip`, `.ayupack`, `.ayu`, `.tgx` и архивы `.tar.*` -
распаковываются автоматически. Точку входа ищут среди `main.py`, `__init__.py`,
`plugin.py`, `bot.py` и других имён; полный список - в `docs/desktop_guide.md`.

Вручную: положите папку в `%APPDATA%\AyuGramDesktop\plugins` и выберите
перезагрузку плагинов в том же меню. Приложение следит за папкой и перезагружает
изменённый плагин само.

### Сделать свой плагин

Минимум - два файла:

```
my_plugin/
├── main.py            # точка входа (обязательно)
└── plugin.json        # манифест
```

`plugin.json`:

```json
{
  "id": "com.example.my-plugin",
  "name": "Мой плагин",
  "description": "Что делает плагин",
  "author": "Вы",
  "version": "1.0.0",
  "entry": "main.py",
  "hooks": ["on_message"],
  "api_version": 1
}
```

`main.py`:

```python
from ayu_api import hook, toast

@hook("on_message")
def on_message(msg):
    toast(msg.text)
```

Требуются только `id`, `name`, `entry` и `api_version=1`. Хук выполняется под
блокировкой интерпретатора: держите его короче 250 мс, тяжёлую работу уносите в
свой поток. Доступные хуки: `on_message`, `on_message_sending`,
`on_message_deleted`, `on_chat_opened`, `on_app_start`, `on_app_stop`,
`on_setting_changed`.

### Документация

| Документ | О чём |
|----------|-------|
| `docs/desktop_guide.md` | Полное руководство: установка, хуки, разрешения, `ayu_api`, настройки, отладка, частые проблемы |
| `docs/quickstart.md` | Первый плагин за пять минут: layout, манифест, шаблоны |
| `docs/api_reference.md` | Справочник API v1: `ayu_api`, `ayugram_compat` |
| `docs/migration_from_mobile.md` | Перенос Android-плагинов (exteraGram) на Desktop |
| `docs/security_model.md` | Песочница, разрешения, что плагину недоступно |
| `examples/` | 18 рабочих примеров: команды, таймеры, настройки, хранение состояния |

Диагностика: **Настройки → Плагины → Проверить плагины** показывает по каждому
плагину вердикт и причину. Из командной строки - `Plugins.bat doctor` и
`Plugins.bat log <id>`.

## Что исправлено

### Python standalone host

| Проблема | Файл | Что сделано |
|----------|------|-------------|
| Зависание neural_typing (блокер #1) | `examples/neural_typing/main.py` | Флаг `_sending_own` против рекурсии: `_on_sending` → `send_text` → `_on_sending` → … |
| `ayu_api` stub затеняет нативный C++ модуль | `standalone/host_app.py` | Нативный модуль приоритетнее stub — временно удаляется `APP_DIR` из `sys.path` перед импортом |
| custom_nickname спамит мостом каждую секунду | `%APPDATA%\AyuGramDesktop\plugins\custom_nickname\main.py` | Guardia `_last_applied_name` — запрос не отправляется если имя не изменилось |
| GUI плагинов унылый | `standalone/host_app.py` | Поиск/фильтр по имени, ID, описанию, автору. Цветовые статусы: ON = синий, off = серый, ERR = красный. Скроллируемая панель деталей плагина. Кнопка Clear log. |
| Окно настроек неудобное | `standalone/host_app.py` | Окно изменяемое (resizable, minsize 420×300). Скроллируемая область настроек. Кнопка Cancel. Горячие клавиши Enter=Save, Escape=Cancel. Статус сохранения (зелёный/красный). Разделители секций. |
| Дублирующее сохранение настроек | `standalone/host_app.py` | Убран прямой `write_text` — сохранение только через `settings_store` (канонический путь) |
| Live reload перечитывает все плагины | `standalone/plugin_loader.py` | Fingerprints (mtime_ns+size) — перепарсиваются только изменённые/новые/удалённые плагины |
| Баг `getattr(row→r)` | `standalone/host_app.py` | Исправлено на `getattr(r, "key", "")` (переменная цикла `r`) |

### C++ AyuGram Desktop (все 3 источника в `cpp/`)

| Исправление | Что сделано | Файлы |
|-------------|-------------|-------|
| Дубликат заголовка "Плагины" | Удалён `addSubsectionTitle(tr::ayu_PluginsHeader())` - он дублировал кнопку "Плагины" в разделе Ayu и на странице плагинов | `settings_ayu.cpp`, `plugins_section.cpp` |
| Цветной статус плагина | Добавлена строка статуса под каждым плагином в списке: ON (загружен), off (отключён), ERR (ошибка) | `plugins_section.cpp` |
| Поиск плагинов в глобальном поиске | Добавлены записи в обработчик `SearchContext` - плагины находятся через поиск по настройкам | `plugins_section.cpp` |

**Требуется перекомпиляция** для вступления C++ изменений в силу.

## Как собрать

### Python standalone host
```cmd
cd D:\Users\koliy\AyuGram
python tools/selftest.py
```
Результат: 51 passed, 3 failed (все 3 - предудущие ошибки `python314.dll`, не связаны с изменениями).

### C++ AyuGram Desktop
```cmd
cd /d D:\TBuild
run_build_release.bat
```
Скрипт настраивает PATH к Python 3.14, вызывает `vcvars64.bat` (Visual Studio 2022) и запускает `cmake --build out --config Release --target Telegram -j 4`.

**Известная проблема:** ошибка `C1076 / C3859` (MSVC internal heap limit для PCH). Решение — собрать с `-j 1` вместо `-j 4`.

**Требования:** Visual Studio 2022 Community, CMake, Qt (включён в ThirdParty).

## Релиз-бинарник

Готовая сборка: `D:\TBuild\tdesktop\out\Release\AyuGram.exe` (228 МБ)
Не коммитится в git из-за размера (.gitignore исключает .exe). Доступен локально и в GitHub Releases (тег v1.0.0).

## tdata — файлы авторизации

Папка `tdata/` содержит личные файлы авторизации пользователя.
**Не коммитится, не читается, не изменяется.** Защищена `.gitignore`.

## Кто владеет сборкой

| Компонент | Путь | Владелец | Статус |
|-----------|------|----------|--------|
| C++ (tdesktop) | `cpp/tdesktop/ayu` | @CE0fbi | ✅ В репозитории |
| C++ (AyuGram-OpenSource) | `cpp/ayugram-opensource/ayu` | @CE0fbi | ✅ В репозитории |
| C++ (tdesktop_full) | `cpp/tdesktop_full/ayu` | @CE0fbi | ✅ В репозитории |
| Python standalone host | `standalone/` | @CE0fbi | ✅ В репозитории |
| Release-бинарник | `D:\TBuild\tdesktop\out\Release\AyuGram.exe` | @CE0fbi | Локально + GitHub Releases |

## Лицензия

Copyright @CE0fbi, 2026. Исходный код AyuGram для Desktop.
