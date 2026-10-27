# AyuGram — Ownership & Build Documentation

## 1. Кто владеет сборкой

| Компонент | Путь | Владелец | Статус |
|-----------|------|----------|--------|
| C++ (tdesktop) | `cpp/tdesktop/ayu` | @CE0fbi | В репозитории GitHub |
| C++ (AyuGram-OpenSource) | `cpp/ayugram-opensource/ayu` | @CE0fbi | В репозитории GitHub |
| C++ (tdesktop_full) | `cpp/tdesktop_full/ayu` | @CE0fbi | В репозитории GitHub |
| Python standalone host | `standalone/` | @CE0fbi | В репозитории GitHub |
| Release-бинарник | `D:\TBuild\tdesktop\out\Release\AyuGram.exe` (228 МБ) | @CE0fbi | Локально + GitHub Releases |

**Копии бинарника на диске (не удалялись без подтверждения):**
- `D:\Users\koliy\AyuGram\AyuGram.exe` — байт-в-байт идентична Release-сборке
- ~24 папки `D:\Users\koliy\AyuGram - <garbled>/` — дубликаты Release (228 МБ каждая)
- `D:\Build\AyuGram\app\AyuGram.exe` — отдельная более старая сборка
- `D:\TBuild\AyuGram\app\AyuGram.exe` — отдельная сборка
- 488 МБ Debug / full-plugins / stale варианты в `D:\TBuild\`

---

## 2. Что исправлено (Python standalone host)

### neural_typing hang (блокер #1)
Файл: `examples/neural_typing/main.py`
Проблема: бесконечная рекурсия — `send_text` триггерит `on_message_sending`, который снова вызывает `send_text` → зависание приложения.
Исправление: флаг `self._sending_own`. В `_on_sending` проверяется флаг в начале — если True, метод возвращается сразу. В `_start()` и `_watch_confirm` флаг ставится в True перед вызовом `send_text` и сбрасывается в False в `finally`.

### ayu_api stub затеняет нативный C++ модуль
Файл: `standalone/host_app.py`
Проблема: чистый-Python stub `standalone/ayu_api.py` затенял нативный C++ модуль `ayu_api`, из-за чего в production использовался no-op мост вместо реального.
Исправление: перед импортом `ayu_api` временно удаляется `APP_DIR` из `sys.path`, затем пытается импортироваться нативный модуль; при `ImportError` путь восстанавливается и импортируется pure-Python stub.

### GUI улучшения host_app.py
- Поиск/фильтр плагинов по имени, ID, описанию, автору
- Цветовые статусы: ON = синий, off = серый, ERR = красный
- Скроллируемая панель деталей плагина (полная информация: имя, ID, версия, автор, enabled, ошибка, хуки, описание)
- Кнопка "Clear log"
- Заменен `StringVar` на `ScrolledText` для деталей
- Список `self._all_plugins` для фильтрации

### Окно настроек плагинов (host_app.py)
- Окно изменяемое (resizable), минимальный размер 420×300
- Скроллируемая область настроек (Canvas + Scrollbar) для множества опций
- Кнопка "Cancel" рядом с "Save"
- Горячие клавиши: Enter = Save, Escape = Cancel
- Статус сохранения (зелёный "Settings saved." / красная ошибка)
- Разделители секций (Separator + жирный синий текст для заголовков)
- Колесо мыши работает в области настроек
- Очистка привязок мыши при закрытии окна

### Дублирующее сохранение настроек
В `_save()` настройки сохранялись дважды — через `import_settings` → `settings_store` и через прямой `write_text` в тот же JSON-файл. Убран прямой `write_text`, сохранение идёт только через канонический путь `settings_store`.

### Баг getattr(row→r)
`getattr(row, "key", "")` → `getattr(r, "key", "")` (переменная цикла `r`, а не `row`).

### Инкрементальный reload плагинов (plugin_loader.py)
Проблема: каждый вызов `scan()` полностью перечитывал и перепарсивал все манифесты и entry-скрипты всех плагинов, даже не изменённые.
Исправление: добавлен `self._fingerprints` (pid → (mtime_ns, size)). При `scan()` сравниваются отпечатки — перепарсиваются только новые/изменённые/удалённые плагины. Неизменённые остаются в памяти без чтения с диска и без `exec()`.

### custom_nickname спамит мостом каждую секунду
Файл: `%APPDATA%\AyuGramDesktop\plugins\custom_nickname\main.py`
Причина: защита от повторов `getattr(user, "first_name", None) == new_first_name` не срабатывала, потому что `get_user_config().getCurrentUser()` возвращает заглушку с `first_name = ""`. Сравнение `"" == nickname` всегда False.
Исправление: добавлен `self._last_applied_name` — перед `send_request` сравнивается с последним применённым именем, запрос пропускается если имя не изменилось.

---

## 3. Что исправлено (C++ AyuGram Desktop, все 3 источника)

Исправления применены к исходникам в трёх местах в `cpp/`:

1. `cpp/tdesktop/ayu/`
2. `cpp/ayugram-opensource/ayu/`
3. `cpp/tdesktop_full/ayu/`

| Исправление | Что сделано | Файлы |
|-------------|-------------|-------|
| Убраны дубликаты заголовка "Плагины" | Удалён `addSubsectionTitle(tr::ayu_PluginsHeader())` — он дублировал кнопку "Плагины" в разделе Ayu и на странице плагинов | `settings_ayu.cpp`, `plugins_section.cpp` |
| Цветной статус плагина (ON/off/ERR) | Добавлена строка статуса под каждым плагином в списке | `plugins_section.cpp` |
| Поиск плагинов в глобальном поиске настроек | Добавлены записи в обработчик `SearchContext` | `plugins_section.cpp` |

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

Известная проблема: ошибка `C1076 / C3859` (MSVC internal heap limit для PCH). Решение — собрать с `-j 1` вместо `-j 4`.

Требования: Visual Studio 2022 Community, CMake, Qt (включён в ThirdParty).

---

## 5. Релиз-бинарник

Готовый Release-бинарник: `D:\TBuild\tdesktop\out\Release\AyuGram.exe` (228 МБ)
Не коммитится в git из-за размера (.gitignore исключает .exe). Доступен локально и в GitHub Releases (тег v1.0.0).

---

## 6. tdata — файлы авторизации

Папка `tdata/` не была тронута — не читалась, не изменялась, не коммитится в git (защищено `.gitignore`), не копировалась. Все файлы авторизации пользователя в безопасности.

---

## 7. GitHub репозиторий

- URL: `https://github.com/thehelpfromthevoid/AyuGram-plugins`
- Ветка: `master`
- Тег: `v1.0.0`
- Последний коммит: `5fa2dea` — "Update docs - include cpp/ sources in README and BUILD_OWNERSHIP"

---

## 8. Верификация

| Проверка | Результат |
|----------|-----------|
| `py_compile standalone/host_app.py` | OK |
| `py_compile standalone/plugin_loader.py` | OK |
| `py_compile examples/neural_typing/main.py` | OK |
| `tools/selftest.py` | 51 passed, 3 failed (все 3 — предудущие ошибки `python314.dll`, не связаны с изменениями) |
| tdata/ | Не тронуто |

---

*Документ составлен 27 октября 2026 года. Автор: @CE0fbi.*
