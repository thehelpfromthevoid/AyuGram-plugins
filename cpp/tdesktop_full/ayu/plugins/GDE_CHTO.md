# ГДЕ ЧТО — карта плагинов AyuGram за 2 минуты

## Три места (не путать)

1. **Живые плагины твоего AyuGram** — `%APPDATA%\AyuGramDesktop\plugins\`
   Каждая папка = плагин (`main.py` + опционально `plugin.json`).
   `plugins.json` рядом — вкл/выкл. Сюда ставит Настройки → AyuGram → Plugins
   и утилита `install_to_app.py`. Это то, что реально работает в мессенджере.
2. **Исходники системы** — `Telegram\SourceFiles\ayu\plugins\`
   `shim/` — перевод мобильных импортов на десктоп,
   `standalone/` — тестовый запуск без сборки (`host_app.py`),
   `sandbox/` — песочница прав, `examples/` — демо и шаблоны,
   `tools/ayu-plugin-cli/` — `validate.py`, `compat_check.py`, `install_to_app.py`,
   `bridge/ayu_api.cpp` — мост внутрь AyuGram (нужна пересборка C++).
3. **Готовое рядом с exe** — `dist\AyuGramFullPlugins\`
   Копии шима + демо, которые AyuGram подхватывает при старте.
   `dist\AyuGramPlugins\` — отдельно собранный тестовый стенд (START.bat).

## Поставить плагин в AyuGram (3 способа)

- Из мессенджера: Настройки → AyuGram → Plugins → Install → файл
  `.plugin` / `.py` / `.zip` / `.ayupack` или папка.
- Из консоли: `python tools\ayu-plugin-cli\install_to_app.py <папка-или-файл>`
  или `--examples autoreply` (шаблоны из исходников).
- Вручную: скопировать папку в `%APPDATA%\AyuGramDesktop\plugins\`,
  в `plugins.json` добавить `"id-плагина": true`, перезапустить.

## Что понимает загрузчик

Манифест `plugin.json` или `manifest.json`/`yaml`/`toml`; точка входа
`main.py`, `__init__.py`, `plugin.py`, `index.py`, `bot.py`; одиночные
`.plugin` / `.py` / `.pyw`; архивы `.zip` / `.ayupack` / `.ayu` / `.tgx`.

## Настоящие мобильные модули (как в доках exteraGram)

`ui.settings` (Header/Switch/Selector/Input/Text/EditText/Divider),
`ui.bulletin`, `ui.alert`, `file_utils`, `extera_utils.classes` —
реализованы по-настоящему, настройки из `create_settings()` подхватываются.

## Что НЕ заведётся (честно)

Плагины под Java-интерфейс Android (`import java`, `import android`,
навьюбары, хуки вьюшек чата) — только под мобильный AyuGram/exteraGram.
На десктопе они отклоняются с понятной ошибкой, а не висят мёртвым грузом.

## Проверить до установки

`python tools\ayu-plugin-cli\compat_check.py <папка>` — скажет PASS/FAIL
по каждому плагину. `validate.py` — быстрая проверка формата.
