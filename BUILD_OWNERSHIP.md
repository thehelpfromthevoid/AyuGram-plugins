# AyuGram — Ownership & Build Documentation

## 1. Кто владеет сборкой

| Компонент | Путь к исходникам | Владелец | Статус |
|-----------|-------------------|----------|--------|
| C++ / Qt desktop app (tdesktop) | `cpp/tdesktop/ayu` | koliy | ✅ В репозитории GitHub |
| C++ / Qt desktop app (AyuGram-OpenSource) | `cpp/ayugram-opensource/ayu` | koliy | ✅ В репозитории GitHub |
| C++ / Qt desktop app (tdesktop_full) | `cpp/tdesktop_full/ayu` | koliy | ✅ В репозитории GitHub |
| Python standalone host | `standalone/` | koliy | ✅ В репозитории GitHub |

**Канонический Release-бинарник:** `D:\TBuild\tdesktop\out\Release\AyuGram.exe` (228 МБ)

**Копии бинарника (не удалялись без подтверждения):**
- `D:\Users\koliy\AyuGram\AyuGram.exe` — байт-в-байт идентична Release-сборке
- ~24 папки `D:\Users\koliy\AyuGram - <garbled>/` — дубликаты Release (228 МБ каждая)
- `D:\Build\AyuGram\app\AyuGram.exe` — отдельная более старая сборка
- `D:\TBuild\AyuGram\app\AyuGram.exe` — отдельная сборка
- 488 МБ Debug / full-plugins / stale варианты в `D:\TBuild\`

---

## 2. Что исправлено (Python standalone host)

### Файл: `examples/neural_typing/main.py`
**Проблема:** бесконечная рекурсия — `send_text` триггерит `on_message_sending`, который снова вызывает `send_text` → зависание приложения (блокер #1).
**Исправление:** добавлен флаг `self._sending_own`. В `_on_sending` проверяется флаг в начале — если `True`, метод возвращается сразу. В `_start()` и `_watch_confirm` флаг ставится в `True` перед вызовом `send_text` и сбрасывается в `False` в `finally`.

### Файл: `standalone/host_app.py`
**Проблема:** чистый-Python stub `standalone/ayu_api.py` затенял нативный C++ модуль `ayu_api`, из-за чего в production использовался нетолстый no-op мост вместо реального.
**Исправление:** перед импортом `ayu_api` временно удаляется `APP_DIR` из `sys.path`, затем пытается импортироваться нативный модуль; при `ImportError` путь восстанавливается и импортируется纯Python stub.

**GUI улучшения:**
- Поиск/фильтр плагинов по имени, ID, описанию, автору
- Цветовые статусы: ON = синий, off = серый, ERR = красный
- Скроллируемая панель деталей плагина (полная информация: имя, ID, версия, автор, enabled, ошибка, хуки, описание)
- Кнопка "Clear log"
- Заменен `StringVar` на `ScrolledText` для деталей
- Список `self._all_plugins` для фильтрации

**Окно настроек плагинов:**
- Окно изменяемое (`resizable`), минимальный размер 420×300
- Скроллируемая область настроек (Canvas + Scrollbar) для множества опций
- Кнопка "Cancel" рядом с "Save"
- Горячие клавиши: Enter = Save, Escape = Cancel
- Статус сохранения (зелёный "Settings saved." / красная ошибка)
- Разделители секций (Separator + жирный синий текст для заголовков)
- Колесо мыши работает в области настроек
- Очистка привязок мыши при закрытии окна

**Исправление дублирования сохранения:**
В `_save()` настройки сохранялись дважды — через `import_settings` → `settings_store` и через прямой `write_text` в тот же JSON-файл. Убран прямой `write_text`, сохранение идёт только через канонический путь `settings_store`.

**Исправлен баг:** `getattr(row, "key", "")` → `getattr(r, "key", "")` (переменная цикла `r`, а не `row`).

### Файл: `standalone/plugin_loader.py`
**Проблема:** каждый вызов `scan()` полностью перечитывал и перепарсивал все манифесты и entry-скрипты всех плагинов, даже не изменённые.
**Исправление:** добавлен `self._fingerprints` (pid → (mtime_ns, size)). При `scan()` сравниваются отпечатки — перепарсиваются только новые/изменённые/удалённые плагины. Неизменённые остаются в памяти без чтения с диска и без `exec()`.

### Файл: `%APPDATA%\AyuGramDesktop\plugins\custom_nickname\main.py`
**Проблема:** плагин спамил мостом "Desktop bridge: unsupported request Placeholder" каждую секунду.
**Причина:** защита от повторов `getattr(user, "first_name", None) == new_first_name` не срабатывала, потому что `get_user_config().getCurrentUser()` возвращает заглушку с `first_name = ""`. Сравнение `"" == nickname` всегда `False`.
**Исправление:** добавлен `self._last_applied_name` — перед `send_request` сравнивается с последним применённым именем, запрос пропускается если имя не изменилось.

---

## 3. Что исправлено (C++ AyuGram Desktop, все 3 источника в репозитории)

Исправления применены к исходникам в трёх местах в `cpp/`:

1. `cpp/tdesktop/ayu/`
2. `cpp/ayugram-opensource/ayu/`
3. `cpp/tdesktop_full/ayu/`

| Исправление | Что сделано |
|-------------|-------------|
| Убраны дубликаты заголовка "Плагины" | `addSubsectionTitle(tr::ayu_PluginsHeader())` удалён из `settings_ayu.cpp` и `plugins_section.cpp` |
| Цветной статус плагина (ON/off/ERR) | Добавлена строка статуса под каждым плагином в списке |
| Поиск плагинов в глобальном поиске настроек | Добавлены записи в обработчик `SearchContext` |

Требуется перекомпиляция для вступления в силу.

---

## 4. Как собрать C++ часть

Сборка через `D:\TBuild\run_build_release.bat`:

```cmd
cd /d D:\TBuild
run_build_release.bat
```

Скрипт:
1. Настраивает PATH к Python 3.14
2. Вызывает `vcvars64.bat` (Visual Studio 2022)
3. Запускает `cmake --build out --config Release --target Telegram -j 4`

**Известная проблема:** ошибка `C1076 / C3859` (MSVC internal heap limit для PCH). Решение — собрать с `-j 1` вместо `-j 4`.

**Предварительные требования:**
- Visual Studio 2022 Community
- CMake (`D:\Users\koliy\AppData\Local\Python\pythoncore-3.14-64\Scripts\cmake.exe`)
- Qt (включён в ThirdParty)

---

## 5. Релиз-бинарник

Готовый Release-бинарник: `D:\TBuild\tdesktop\out\Release\AyuGram.exe` (228 МБ)
Не коммитится в git из-за размера (.gitignore исключает .exe). Доступен локально.

## 6. tdata — файлы авторизации

**Папка `tdata/` НЕ была тронута:**
- Не читалась
- Не изменялась
- Не коммитится в git (защищено `.gitignore`)
- Не копировалась

Все файлы авторизации пользователя в полной безопасности.

---

## 7. GitHub репозиторий

- URL: `https://github.com/thehelpfromthevoid/AyuGram-plugins`
- Ветка: `master`
- Тег: `v1.0.0`
- Последний коммит: `e6bac1e` — "Restore cpp/ open source - all 3 C++ sources back on GitHub"

---

## 8. Верификация

| Проверка | Результат |
|----------|-----------|
| `py_compile standalone/host_app.py` | ✅ OK |
| `py_compile standalone/plugin_loader.py` | ✅ OK |
| `py_compile examples/neural_typing/main.py` | ✅ OK |
| `tools/selftest.py` | 51 passed, 3 failed (все 3 — предудущие ошибки `python314.dll`, не связаны с изменениями) |
| tdata/ | ✅ Не тронуто |

---

*Документ составлен 27 октября 2026 года.*
