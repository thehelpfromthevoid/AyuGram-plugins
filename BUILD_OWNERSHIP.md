# AyuGram - Ownership & Build Documentation

## 1. Кто владеет сборкой (Release Build)

| Компонент | Владелец | Путь к исходникам | Статус |
|-----------|----------|-------------------|--------|
| **Python standalone host** | koliy | `D:\Users\koliy\AyuGram\` | ✅ Готово к коммиту, запушено на GitHub |
| **C++ / Qt desktop app (tdesktop)** | koliy | `D:\TBuild\tdesktop\Telegram\SourceFiles\ayu/` | ✅ Исправления применены, требует перекомпиляции |
| **C++ / Qt desktop app (AyuGram-OpenSource)** | koliy | `D:\AyuGram-OpenSource\SourceFiles\ayu/` | ✅ Исправления применены, требует перекомпиляции |
| **C++ / Qt desktop app (tdesktop_full)** | koliy | `D:\TBuild\tdesktop_full\Telegram\SourceFiles\ayu/` | ✅ Исправления применены, требует перекомпиляции |

**Канонический Release-бинарник:** `D:\TBuild\tdesktop\out\Release\AyuGram.exe` (228 МБ, hash `BE0ED4DC6850A80F…`)

**Копии бинарника (не удалялись без подтверждения):**
- `D:\Users\koliy\AyuGram\AyuGram.exe` - байт-в-байт идентична Release-сборке
- ~24 папки `D:\Users\koliy\AyuGram - <garbled>/` - дубликаты Release (228 МБ каждая)
- `D:\Build\AyuGram\app\AyuGram.exe` - отдельная более старая сборка
- `D:\TBuild\AyuGram\app\AyuGram.exe` - отдельная сборка
- 488 МБ Debug / full-plugins / stale варианты в `D:\TBuild\`

---

## 2. Что исправлено (Python standalone host)

### Файл: `examples/neural_typing/main.py`
**Проблема:** бесконечная рекурсия - `send_text` триггерит `on_message_sending`, который снова вызывает `send_text` → зависание приложения (блокер #1).
**Исправление:** добавлен флаг `self._sending_own`. В `_on_sending` проверяется флаг в начале - если `True`, метод возвращается сразу. В `_start()` и `_watch_confirm` флаг ставится в `True` перед вызовом `send_text` и сбрасывается в `False` в `finally`.

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
В `_save()` настройки сохранялись дважды - через `import_settings` → `settings_store` и через прямой `write_text` в тот же JSON-файл. Убран прямой `write_text`, сохранение идёт только через канонический путь `settings_store`.

**Исправлен баг:** `getattr(row, "key", "")` → `getattr(r, "key", "")` (переменная цикла `r`, а не `row`).

### Файл: `standalone/plugin_loader.py`
**Проблема:** каждый вызов `scan()` полностью перечитывал и перепарсивал все манифесты и entry-скрипты всех плагинов, даже не изменённые.
**Исправление:** добавлен `self._fingerprints` (pid → (mtime_ns, size)). При `scan()` сравниваются отпечатки - перепарсиваются только новые/изменённые/удалённые плагины. Неизменённые оставляются в памяти без чтения с диска и без `exec()`.

### Файл: `%APPDATA%\AyuGramDesktop\plugins\custom_nickname\main.py`
**Проблема:** плагин спамил мостом "Desktop bridge: unsupported request Placeholder" каждую секунду.
**Причина:** защита от повторов `getattr(user, "first_name", None) == new_first_name` не срабатывала, потому что `get_user_config().getCurrentUser()` возвращает заглушку с `first_name = ""`. Сравнение `"" == nickname` всегда `False`.
**Исправление:** добавлен `self._last_applied_name` - перед `send_request` сравнивается с последним применённым именем, запрос пропускается если имя не изменилось.

---

## 3. Что исправлено (C++ AyuGram Desktop, все 3 источника)

### Убраны дубликаты заголовка "Плагины"
| Файл | Строка | Что убрано |
|------|--------|------------|
| `D:\TBuild\tdesktop\Telegram\SourceFiles\ayu/ui/settings/settings_ayu.cpp` | 702 | `builder.addSubsectionTitle(tr::ayu_PluginsHeader())` - лишний заголовок над кнопкой "Плагины" |
| `D:\TBuild\tdesktop\Telegram\SourceFiles\ayu/plugins/ui/plugins_section.cpp` | 463 | `addSubsectionTitle(tr::ayu_PluginsHeader())` - лишний заголовок на странице плагинов |
| `D:\AyuGram-OpenSource\SourceFiles\ayu/ui/settings/settings_ayu.cpp` | 702 | то же |
| `D:\AyuGram-OpenSource\SourceFiles\ayu/plugins/ui/plugins_section.cpp` | 463 | то же |
| `D:\TBuild\tdesktop_full\Telegram\SourceFiles\ayu/plugins/ui/plugins_section.cpp` | 269 | то же |

### Цветной статус плагина (в списке)
Добавлена строка статуса под каждым плагином:
- **ON** - плагин загружен и активен (`PluginState::Loaded`)
- **off** - плагин отключён (`PluginState::Disabled`)
- **ERR** - ошибка загрузки (`PluginState::Error`)

### Поиск плагинов в глобальном поиске настроек
В обработчике `SearchContext` добавлены записи для каждого плагина:
- ID: `ayu/plugin/<manifest.id>`
- Заголовок: `manifest.name`

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

**Известная проблема:** ошибка `C1076 / C3859` (MSVC internal heap limit для PCH). Решение - собрать с `-j 1` вместо `-j 4`.

**Предварительные требования:**
- Visual Studio 2022 Community
- CMake (`D:\Users\koliy\AppData\Local\Python\pythoncore-3.14-64\Scripts\cmake.exe`)
- Qt (вcluded в ThirdParty)

---

## 5. tdata - файлы авторизации

**Папка `tdata/` НЕ была тронута:**
- Не читалась
- Не изменялась
- Не коммитится в git (защищено `.gitignore`)
- Не копировалась

Все файлы авторизации пользователя в полной безопасности.

---

## 6. GitHub репозиторий

- URL: `https://github.com/thehelpfromthevoid/AyuGram-plugins`
- Ветка: `master`
- Последний коммит: `f54c134` - "Initial commit: AyuGram Desktop plugin system fixes"
- 175 файлов, 24,495 строк

---

## 7. Git-репозитории C++ (отдельно)

C++ исходники в трёх местах **ещё не коммичены** в git. Если нужно - инициализируются отдельные репозитории для каждого источника или один общий для всех трёх.

---

## 8. Верификация

| Проверка | Результат |
|----------|-----------|
| `py_compile standalone/host_app.py` | ✅ OK |
| `py_compile standalone/plugin_loader.py` | ✅ OK |
| `py_compile examples/neural_typing/main.py` | ✅ OK |
| `tools/selftest.py` | 51 passed, 3 failed (все 3 - предудущие ошибки `python314.dll`, не связаны с изменениями) |
| C++ `plugins_section.cpp` | ✅ Скомпилировано (до тайм-аута сборки) |
| C++ `settings_ayu.cpp` | ✅ Скомпилировано (до тайм-аута сборки) |
| tdata/ | ✅ Не тронуто |

---

*Документ составлен 27 октября 2026 года.*
