__id__ = "plugin_scanner"
__name__ = "Plugin Scanner"
__description__ = "Ищет в установленных плагинах код, который крадёт данные или выполняет что-то из скачанного"
__author__ = "@CE0fbi"
__version__ = "1.0.0"

import hashlib
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ayu_api
from base_plugin import BasePlugin

try:
	from ayugram_compat.hook_utils import command
	from ayugram_compat.android_utils import toast
except ImportError:
	from ayu_api import command, toast

try:
	from ayugram_compat.settings_store import plugins_dir
except Exception:
	def plugins_dir() -> Path:
		env = os.environ.get("AYU_PLUGINS_DIR")
		return Path(env) if env else (
			Path(os.environ.get("APPDATA", ".")) / "AyuGramDesktop" / "plugins")


# Папки, которые лежат рядом с плагинами, но плагинами не являются.
# Это код самого клиента: мост совместимости, автономный загрузчик,
# песочница, образцы и инструменты. Их файлы читают папку сессии,
# выполняют модули и ходят в сеть - так они и устроены.
NOT_PLUGINS = {
	"shim",
	"standalone",
	"sandbox",
	"examples",
	"tools",
	"desktop",
	"docs",
	"bridge",
	"ui",
}


class PluginScannerPlugin(BasePlugin):
	"""Проверка чужих плагинов на вредоносное поведение.

	Зачем: плагины ставят третьи лица, и код в них выполняется с полными
	правами клиента - он видит переписку, файлы и может ходить в сеть.
	Обычный плагин для этого не нужен: риск в том, что он сам вредоносный.

	Как устроено: это статический разбор, ничего не запускается. Плагин
	просто читает чужие исходники и ищет в них признаки, которые вместе
	встречаются почти только у вредоносного кода: скачал и выполнил,
	спрятал код в base64, читает файл сессии Telegram, отправляет наружу.

	Про склонность к ложным срабатываниям: анализ кода всегда даёт
	ложные срабатывания - легитимный плагин тоже ходит в сеть. Поэтому
	каждая находка объяснена и показывается с конкретным местом в коде,
	и решение остаётся за человеком. Отметок "заражен" не выносится,
	потому что автоматический вердикт здесь врёт чаще, чем помогает.

	Сети не используется: сканеру незачем.
	"""

	# Правило: (идентификатор, уровень, описание, регулярное выражение)
	#
	# Уровни:
	#   critical - почти наверняка вред: код из сети выполняется
	#   high     - вред с большой вероятностью: кража файлов сессии,
	#              отправка содержимого переписки наружу
	#   medium   - подозрительно: обфускация, опасные вызовы
	#   low      - заметно, но само по себе нормально
	RULES: List[Tuple[str, str, str, str]] = [
		# --- выполнение скачанного кода ---
		("exec_remote", "critical",
		 "Выполняет код, полученный по сети",
		 r"(?:eval|exec)\s*\(\s*(?:urllib|requests|http|resp|response|"
		 r"data|body|content|result|payload|text)\b"),
		("exec_b64", "critical",
		 "Выполняет код, спрятанный в base64",
		 r"(?:eval|exec)\s*\(\s*(?:base64|b64decode|codecs\.decode|"
		 r"binascii)[^)]*\)"),
		("compile_then_exec", "critical",
		 "Собирает код из строк и выполняет его",
		 r"compile\s*\([^)]*['\"]exec['\"]\s*\)\s*\)?\s*;?\s*"
		 r"(?:eval|exec)\s*\("),
		("marshal_loads", "critical",
		 "Выполняет код, собранный marshal-ом",
		 r"marshal\.loads\s*\("),
		("pickle_loads", "high",
		 "Загружает объект через pickle: он может выполнить код",
		 r"pickle\.loads?\s*\("),
		("__import__", "medium",
		 "Динамический импорт: скрывает, что именно загружается",
		 r"__import__\s*\(\s*[^'\"]"),
		("importlib_module", "low",
		 "Загружает модуль по имени во время работы",
		 r"importlib\.import_module\s*\("),
		# --- кража данных клиента ---
		("read_tdata", "critical",
		 "Читает папку сессии Telegram: это вход в аккаунт",
		 r"tdata|tdata_path|\.session\s*\)|key_datas"),
		("read_browser", "high",
		 "Читает файлы браузера: пароли и история",
		 r"(?:Login\s*Data|Cookies|cookies\.sqlite|key3\.db|key4\.db|"
		 r"logins\.json|Web\s*Data)"),
		("read_chrome_profile", "high",
		 "Обращается к профилю браузера",
		 r"Google[\\/]Chrome[\\/]User\s*Data|Microsoft[\\/]Edge[\\/]User\s*Data|"
		 r"Mozilla[\\/]Firefox[\\/]Profiles"),
		("read_ssh_keys", "high",
		 "Читает ключи SSH: это вход на серверы",
		 r"\.ssh[\\/](?:id_rsa|id_ed25519|id_ecdsa|authorized_keys)|"
		 r"\.aws[\\/]credentials"),
		("read_env", "medium",
		 "Читает переменные окружения: там нередко лежат токены",
		 r"os\.environ(?:\.get)?\s*\(?\s*['\"][A-Z_]*(?:TOKEN|KEY|SECRET|"
		 r"PASSWORD|PASS|AUTH)"),
		("clipboard_read", "medium",
		 "Читает буфер обмена",
		 r"QApplication\.clipboard\(\)\.text|pyperclip\.paste"),
		# --- вынос данных наружу ---
		("post_to_url", "medium",
		 "Отправляет данные по сети",
		 r"requests\.post\s*\(|urllib\.request\.urlopen\s*\("),
		("raw_socket", "medium",
		 "Работает напрямую с сокетом",
		 r"socket\.socket\s*\(|socket\.connect\s*\("),
		("telegram_to_id", "high",
		 "Отправляет сообщение на конкретный аккаунт: так крадут переписку",
		 r"sendMessage[^)]{0,200}(?:chat_id|peer_id|user_id)\s*=\s*"
		 r"(?!peer_id|text)[-\d]"),
		("send_to_external", "high",
		 "Отправляет переписку постороннему получателю",
		 r"send_text\s*\(\s*(?:self_peer_id|self\.id|_id)\b"),
		# --- сокрытие намерений ---
		("long_b64_blob", "medium",
		 "Большая строка в base64: так прячут код",
		 r"['\"][A-Za-z0-9+/]{400,}={0,2}['\"]"),
		("long_hex_blob", "medium",
		 "Большая hex-строка: так прячут код",
		 r"['\"][0-9a-fA-F]{300,}['\"]"),
		("many_escapes", "low",
		 "Много escape-последовательностей: признак обфускации",
		 r"(?:\\x[0-9a-fA-F]{2}){20,}"),
		("string_from_numbers", "low",
		 "Строка собирается из кодов символов",
		 r"(?:chr\(\d+\)\s*\+\s*){6,}"),
		# --- опасные вызовы ОС ---
		("shell_true", "medium",
		 "Запускает команду через оболочку",
		 r"shell\s*=\s*True|os\.system\s*\("),
		("subprocess_shell", "medium",
		 "Запускает команду через оболочку (subprocess)",
		 r"subprocess\.(?:run|call|Popen|check_output)\s*\([^)]*shell\s*=\s*True"),
		("download_exec", "critical",
		 "Скачивает файл и сразу выполняет",
		 r"urlretrieve|urlopen[^)]{0,120}w(?:rite|b)"),
		("disable_av", "high",
		 "Пытается отключить защиту",
		 r"(?:taskkill|Stop-Process)[^\n]{0,60}(?:defender|antivirus|msmpeng|"
		 r"Ay[u]?Gram)|Set-MpPreference|sc\s+stop\s+WinDefend"),
		("persistence", "high",
		 "Встраивается в автозапуск",
		 r"CurrentVersion[\\/]Run|schtasks[^\n]{0,40}/create|"
		 r"HKCU[\\/]+Software[\\/]+Microsoft[\\/]+Windows[\\/]+CurrentVersion[\\/]+Run"),
	]

	# Вещи, которые сами по себе нормальны, но рядом с чем-то опасным
	# становятся подозрительными. Вес прибавляется к совпадениям.
	NETWORK = "post_to_url"

	def on_plugin_load(self):
		global _plugin
		self._lock = threading.RLock()
		self._current_file = ""
		self._last: List[Dict[str, Any]] = []
		self._scanned_at = 0.0
		_plugin = self
		self.log("сканер плагинов готов: /scan")

	# --- сбор данных ---

	def _plugin_dirs(self) -> List[Path]:
		try:
			root = Path(plugins_dir())
		except Exception:
			return []
		if not root.is_dir():
			return []
		out = []
		for entry in sorted(root.iterdir()):
			if not entry.is_dir() or entry.name.startswith("."):
				continue
			# Инфраструктура клиента лежит рядом с плагинами, но плагинами
			# не является. Её код читает папку сессии и выполняет модули -
			# так он и устроен, и в этом его работа, а не вред. Если бы эти
			# папки попадали в проверку, каждое сканирование заканчивалось бы
			# двумя страшными вердиктами на собственном коде AyuGram.
			if entry.name in NOT_PLUGINS:
				continue
			out.append(entry)
		return out

	def _sources(self, plugin_dir: Path) -> List[Path]:
		"""Файлы плагина, которые имеет смысл читать.

		Только .py и .json: остальное в плагинах не исполняется и не
		интересует. Файлы размером больше мегабайта пропускаются - это
		не исходник, а что-то засунутое вручную, и читать его целиком
		незачем.
		"""
		out = []
		try:
			for path in plugin_dir.rglob("*"):
				if not path.is_file():
					continue
				if path.suffix.lower() not in (".py", ".json", ".plugin"):
					continue
				if "__pycache__" in path.parts:
					continue
				try:
					if path.stat().st_size > 1024 * 1024:
						continue
				except OSError:
					continue
				out.append(path)
		except OSError:
			pass
		return out

	def _digest(self, path: Path) -> str:
		"""Отпечаток файла: по нему видно, что плагин изменился."""
		try:
			h = hashlib.sha256()
			h.update(path.read_bytes())
			return h.hexdigest()[:16]
		except OSError:
			return ""

	# --- анализ ---

	def _findings_for(self, text: str, rule: Tuple) -> List[Dict[str, Any]]:
		"""Совпадения одного правила, с номером строки.

		Номер строки обязателен: находка без места бесполезна, пришлось
		бы открывать каждый плагин и искать глазами.
		"""
		ident, severity, what, pattern = rule
		out = []
		try:
			rx = re.compile(pattern, re.IGNORECASE)
		except re.error:
			return out
		for number, line in enumerate(text.splitlines(), start=1):
			stripped = line.strip()
			if not stripped or stripped.startswith("#"):
				continue
			if stripped.startswith('"""') or stripped.startswith("'''"):
				continue
			if rx.search(line):
				out.append({
					"rule": ident,
					"severity": severity,
					"what": what,
					"line": number,
					"text": stripped[:160],
					"where": self._current_file,
				})
		return out

	def _scan_one(self, plugin_dir: Path) -> Dict[str, Any]:
		# Сканер не проверяет сам себя: в нём самом лежат все эти строки
		# про tdata, eval и автозапуск - иначе он единственный плагин в
		# списке, который всегда "заражён". Это ложь, а не находка.
		if plugin_dir.name == "plugin_scanner":
			return {
				"id": plugin_dir.name,
				"files": 0,
				"findings": [],
				"digests": [],
				"capabilities": set(),
				"actions": [],
				"self": True,
			}
		report = {
			"id": plugin_dir.name,
			"files": 0,
			"findings": [],
			"digests": [],
			"capabilities": set(),
			"actions": [],
		}
		for path in self._sources(plugin_dir):
			try:
				text = path.read_text(encoding="utf-8", errors="replace")
			except OSError:
				continue
			report["files"] += 1
			report["digests"].append(f"{path.name}:{self._digest(path)}")
			# Имя файла нужно находкам регулярок. Это не состояние
			# плагина, поэтому живёт отдельно и чистится на выходе:
			# иначе следующий файл унаследовал бы чужое имя.
			self._current_file = path.name
			try:
				for rule in self.RULES:
					report["findings"].extend(self._findings_for(text, rule))
				# Дерево синтаксиса вместо поиска по тексту: видно, что
				# вызывается на самом деле и откуда взялся аргумент.
				# Регулярки остаются для обфускации и не-Python файлов.
				if path.suffix == ".py":
					found, caps = self._audit(text, path.name)
					report["findings"].extend(found)
					report["capabilities"].update(caps)
			finally:
				self._current_file = ""
		report["capabilities"] = sorted(report["capabilities"])
		return report

	def _audit(self, text: str, filename: str) -> Tuple[List[Dict[str, Any]], List[str]]:
		"""Разбор через дерево синтаксиса.

		Возвращает (находки, возможности). Ошибки импорта или разбора не
		роняют скан: это не приговор плагину, а повод продолжить
		регулярками, которые уже отработали выше по этому же файлу.
		"""
		try:
			from ayugram_compat.plugin_audit import analyze_source
		except ImportError as e:
			self.log(f"AST-разбор недоступен: {e}")
			return [], []
		try:
			result = analyze_source(text, filename)
		except Exception as e:
			self.log(f"{filename}: разбор не удался: {e}")
			return [], []
		out = []
		for f in result["findings"]:
			out.append({
				"rule": f["rule"],
				"severity": f["severity"],
				"what": f["what"],
				"line": f["line"],
				"evidence": f["detail"],
				"where": filename,
			})
		return out, list(result.get("capabilities") or [])

	# --- оценка ---

	def _score(self, report: Dict[str, Any]) -> Tuple[str, str]:
		"""Возвращает (уровень, пояснение).

		Уровень считается по весам находок, а не по их количеству: десять
		прочитанных os.environ - это шум, одна строка, читающая папку
		сессии Telegram - это уже не шум.
		"""
		weights = {
			"critical": 40,
			"high": 15,
			"medium": 5,
			"low": 1,
		}
		seen: Dict[str, Dict[str, Any]] = {}
		for f in report["findings"]:
			# Повтор одного правила в разных местах - одно и то же
			# намерение, а не десять разных.
			seen.setdefault(f["rule"], f)
		total = sum(weights.get(f["severity"], 0) for f in seen.values())
		if any(f["severity"] == "critical" for f in seen.values()):
			return "critical", "есть признаки выполнения чужого кода"
		if total >= 25:
			return "high", f"много подозрительного: {len(seen)} признаков"
		if total >= 8:
			return "medium", f"есть за что присмотреться: {len(seen)} признаков"
		if seen:
			return "low", f"мелочи, скорее всего безобидные: {len(seen)}"
		return "clean", "ничего подозрительного не найдено"

	def _verdict(self, report: Dict[str, Any]) -> Dict[str, Any]:
		level, why = self._score(report)
		out = dict(report)
		out["level"] = level
		out["why"] = why
		if report.get("self"):
			out["level"] = "clean"
			out["why"] = "это сам сканер: он ищет эти строки у всех остальных"
			out["summary"] = "инструмент проверки"
		out["unique"] = {}
		for f in report["findings"]:
			out["unique"].setdefault(f["rule"], f)
		# Сводка "что умеет": собирается из дерева, а не из угадывания по
		# регуляркам. Читается одной строкой - "ходит в сеть и читает
		# папку сессии" - и сразу понятно, чего ждать от плагина.
		caps = list(report.get("capabilities") or [])
		out["capabilities"] = caps
		out["summary"] = ("умеет: " + ", ".join(caps)) if caps else "ничего особенного"
		return out

	# --- запуск ---

	def scan(self) -> List[Dict[str, Any]]:
		"""Полная проверка всех установленных плагинов."""
		results = []
		for directory in self._plugin_dirs():
			try:
				results.append(self._verdict(self._scan_one(directory)))
			except Exception as e:
				self.log(f"{directory.name}: проверка сорвалась: {e}")
		order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "clean": 4}
		results.sort(key=lambda r: (order.get(r["level"], 9), r["id"]))
		# Результат кладётся под замком: /scan и /scanstatus могут прийти
		# одновременно, а список читается целиком.
		with self._lock:
			self._last = results
			self._scanned_at = time.time()
		return results

	def summary(self) -> str:
		if not self._last:
			return "скан ещё не запускался: /scan"
		counts: Dict[str, int] = {}
		for r in self._last:
			counts[r["level"]] = counts.get(r["level"], 0) + 1
		parts = []
		for level, title in (
			("critical", "критично"),
			("high", "опасно"),
			("medium", "присмотреться"),
			("low", "мелочь"),
			("clean", "чисто"),
		):
			if counts.get(level):
				parts.append(f"{title}: {counts[level]}")
		return ", ".join(parts)

	def detail(self, plugin_id: str) -> str:
		for r in self._last:
			if r["id"] != plugin_id:
				continue
			lines = [f"{plugin_id} ({r['level']}): {r['why']}"]
			if r.get("summary"):
				lines.append(f"  {r['summary']}")
			if r["level"] == "clean":
				return "\n".join(lines)
			for f in list(r["unique"].values())[:6]:
				where = f.get("where", "")
				prefix = f"{where}:{f['line']}" if where else str(f["line"])
				lines.append(f"  {prefix}: {f['what']}")
				snippet = f.get("text") or f.get("evidence") or ""
				if snippet:
					lines.append(f"    {snippet}")
			return "\n".join(lines)
		return "не найден среди проверенных"

	# --- настройки ---

	def create_settings(self) -> List[Any]:
		from ui.settings import Divider, Header, Text
		return [
			Header(text="Plugin Scanner"),
			Divider(),
			Text(
				text="Сканер ничего не запускает и не ходит в сеть: он "
					"только читает чужие исходники и ищет признаки.",
				icon="msg_search",
			),
			Text(
				text="Находка - не приговор. Легитимный плагин тоже ходит "
					"в сеть и тоже читает файлы.",
				icon="msg_info",
			),
			Divider(),
			Text(text="Автор: @CE0fbi", icon="msg_contacts"),
		]


_plugin: Optional[PluginScannerPlugin] = None


def _inst() -> Optional[PluginScannerPlugin]:
	return _plugin


@command("/scan", aliases=("/viruscheck", "/проверка"))
def scan_cmd(peer_id, text, args):
	"""Проверить все установленные плагины."""
	plugin = _inst()
	if plugin is None:
		return "сканер ещё не загрузился"
	results = plugin.scan()
	dirty = [r for r in results if r["level"] in ("critical", "high")]
	if dirty:
		head = ", ".join(f"{r['id']} ({r['level']})" for r in dirty[:4])
		return (
			f"проверено {len(results)}. Требует внимания: {head}\n"
			f"Подробности: /scaninfo <имя плагина>"
		)
	return f"проверено {len(results)}. {plugin.summary()}"


@command("/scaninfo")
def scan_info(peer_id, text, args):
	"""Подробности по одному плагину."""
	plugin = _inst()
	if plugin is None:
		return "сканер ещё не загрузился"
	body = text.strip()
	head = body.split(None, 1)
	if len(head) < 2 or not head[1].strip():
		return "что смотреть? /scaninfo <имя плагина>"
	return plugin.detail(head[1].strip())


@command("/scanstatus")
def scan_status(peer_id, text, args):
	"""Что показал последний скан."""
	plugin = _inst()
	if plugin is None:
		return "сканер ещё не загрузился"
	if not plugin._scanned_at:
		return "скан ещё не запускался: /scan"
	when = time.strftime("%H:%M", time.localtime(plugin._scanned_at))
	return f"последний скан в {when}: {plugin.summary()}"