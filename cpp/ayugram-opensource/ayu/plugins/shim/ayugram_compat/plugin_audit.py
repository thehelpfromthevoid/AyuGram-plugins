"""Разбор плагина через дерево синтаксиса, а не поиском по тексту.

Зачем AST: регулярка видит символы, дерево - смысл. По тексту нельзя
ответить на главные вопросы:

  - действительно ли `eval` вызывается или это просто слово в комментарии;
  - откуда взялся аргумент: из сети, из файла или это константа;
  - что вообще делает плагин, если судить по тому, что он импортирует и
    что вызывает.

Здесь всё это вычисляется по настоящему дереву. Регулярки остаются
только там, где дерево бессильно: длинная строка в base64 - это данные,
а не код, и по дереву её не видно.

Каждая находка объяснена: имя вызова, путь до аргумента, номер строки.
Находка без объяснения бесполезна - пришлось бы открывать каждый плагин
и искать глазами.
"""

from __future__ import annotations

import ast
import base64
import re
from typing import Dict, List, Optional, Set, Tuple

# Уровень находки и её человекочитаемое объяснение.
CRITICAL = "critical"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"


class Finding:
	__slots__ = ("rule", "severity", "what", "line", "detail")

	def __init__(
			self,
			rule: str,
			severity: str,
			what: str,
			line: int,
			detail: str = "") -> None:
		self.rule = rule
		self.severity = severity
		self.what = what
		self.line = line
		self.detail = detail

	def as_dict(self) -> dict:
		return {
			"rule": RULE_ALIASES.get(self.rule, self.rule),
			"severity": self.severity,
			"what": self.what,
			"line": self.line,
			"detail": self.detail,
		}

	def __repr__(self) -> str:
		return f"Finding({self.rule}, {self.severity}, line={self.line})"


# Вызовы, которые сами по себе опасны. Ключ - как пишут в коде, значение -
# (уровень, объяснение, требует ли аргумент "посторонний").
DANGEROUS_CALLS: Dict[str, Tuple[str, str]] = {
	"eval": (CRITICAL, "Выполняет строку как код"),
	"exec": (CRITICAL, "Выполняет код из строки"),
	"compile": (MEDIUM, "Собирает код из строки (сам по себе не опасен)"),
	"__import__": (MEDIUM, "Динамический импорт: что грузится, видно не сразу"),
	"os.system": (HIGH, "Запускает команду через системный вызов"),
	"os.popen": (HIGH, "Запускает команду и читает её вывод"),
	"os.remove": (LOW, "Удаляет файлы"),
	"os.rmdir": (LOW, "Удаляет папки"),
	"shutil.rmtree": (MEDIUM, "Удаляет дерево файлов"),
}

# Вызовы, опасность которых зависит от первого аргумента.
CONTEXT_CALLS: Dict[str, Tuple[str, str, str]] = {
	# (как пишут, уровень, объяснение, какие аргументы считаются "посторонними")
	"pickle.loads": (HIGH, "Загружает объект pickle: он умеет выполнять код", "data"),
	"pickle.load": (HIGH, "Загружает объект pickle: он умеет выполнять код", "file"),
	"marshal.loads": (CRITICAL, "Выполняет код, собранный marshal", "data"),
	"urllib.request.urlopen": (MEDIUM, "Ходит в сеть", "url"),
	"requests.get": (MEDIUM, "Ходит в сеть", "url"),
	"requests.post": (MEDIUM, "Отправляет данные в сеть", "url"),
	"socket.socket": (MEDIUM, "Работает напрямую с сокетом", ""),
	"open": (LOW, "Открывает файл", "file"),
	"subprocess.run": (MEDIUM, "Запускает внешнюю программу", "args"),
	"subprocess.Popen": (MEDIUM, "Запускает внешнюю программу", "args"),
	"subprocess.check_output": (MEDIUM, "Запускает внешнюю программу", "args"),
}

# Пути, чтение которых означает кражу. Регистр не важен: на Windows
# пути равнозначны.
#
# Пятое поле - id правила. Оно разное у каждой цели, потому что ключи
# SSH и папка сессии Telegram - разные находки: если свалить их в одно
# правило, отчёт покажет "читает tdata" там, где плагин читает SSH, и
# предупреждение перестанет соответствовать тому, что видно в коде.
#
# Четвёртое поле - distinctive: подстрока, которой достаточно сама по
# себе. Если бы для пути "Google/Chrome/User Data" подходила часть
# "chrome", то плагин, в котором это слово встретилось в комментарии,
# читался бы как ворующий пароли браузера. Поэтому distinctive задаётся
# руками и только для узнаваемых имён: tdata, key_datas, .ssh.
SENSITIVE_PATHS: List[Tuple[Tuple[str, ...], str, str, Tuple[str, ...], str]] = [
	# (куски пути, уровень, объяснение, distinctive, id правила)
	(("tdata",), CRITICAL, "Папка сессии Telegram: это вход в аккаунт",
		("tdata",), "read_tdata"),
	(("key_datas",), CRITICAL, "Ключи сессии Telegram",
		("key_datas",), "read_tdata"),
	(("google", "chrome", "user data"), HIGH,
		"Профиль Chrome: пароли и история", (), "read_chrome_profile"),
	(("microsoft", "edge", "user data"), HIGH, "Профиль Edge",
		(), "read_chrome_profile"),
	(("mozilla", "firefox"), HIGH, "Профиль Firefox",
		(), "read_browser"),
	((".ssh",), HIGH, "Каталог SSH: ключи от серверов",
		(".ssh",), "read_ssh_keys"),
	((".aws", "credentials"), HIGH, "Ключи Amazon AWS",
		(".aws/credentials",), "read_ssh_keys"),
	((".gnupg",), HIGH, "Ключи GnuPG", (".gnupg",), "read_ssh_keys"),
	((".docker", "config.json"), MEDIUM, "Учётные данные Docker",
		(".docker/config.json",), "read_env"),
	(("login data",), HIGH, "Файл паролей Chrome",
		("login data",), "read_chrome_profile"),
	(("cookies.sqlite",), HIGH, "Файлы cookie браузера",
		("cookies.sqlite",), "read_browser"),
]

# Пути, у которых нет distinctive: нужно смотреть на полный путь целиком,
# иначе слово "chrome" в любом виде станет находкой.
_FULL_PATH_ONLY = {tuple(p) for p, _, _, d, _ in SENSITIVE_PATHS if not d}


def _normalise_path(text: str) -> str:
	"""Приводит путь к единому виду: слэши вниз, пробелы в '_'.

	Windows-пути в коде встречаются с обоими слэшами, с двойными
	обратными и разными регистрами. Сравнивать их напрямую бессмысленно.
	"""
	low = text.lower()
	low = low.replace(chr(92) * 2, chr(92)).replace(chr(92), "/")
	low = low.replace(" ", "_")
	return low


def _match_sensitive_path(text: str) -> Optional[Tuple[str, str, str, str]]:
	"""Ищет в строке путь к секретам.

	Возвращает (уровень, объяснение, совпавшая подстрока, id правила)
	или None.
	"""
	low = _normalise_path(text)
	low_under = low.replace("_", " ")
	for parts, severity, what, distinctive, rule in SENSITIVE_PATHS:
		# distinctive работает как есть: это уже уникальное имя.
		for needle in distinctive:
			if needle in low or needle.replace("_", " ") in low_under:
				return severity, what, needle, rule
		if tuple(parts) in _FULL_PATH_ONLY:
			# Полный путь целиком, со всеми кусками по порядку.
			needle = "/".join(parts).replace(" ", "_")
			if needle in low or needle.replace("_", " ") in low_under:
				return severity, what, needle, rule
	return None

# Куда уходят данные: адреса, для которых отправка выглядит подозрительно.
# Точный список чужих серверов постоянно меняется, поэтому проверяется
# не домен, а форма: спрятанный адрес в коде, а не в настройках.
SUSPICIOUS_HOST_HINTS = (
	"pastebin.com", "paste.ee", "hastebin", "ngrok.io", "requestbin",
	"webhook.site", "discord.com/api/webhooks", "telegram.org/bot",
	"bit.ly", "tinyurl.com", "is.gd", "anonfiles", "transfer.sh",
)


def _dotted(node: ast.AST) -> str:
	"""Собирает имя из Name/Attribute: os.path.join -> 'os.path.join'.

	Возвращает пустую строку для всего, что не является цепочкой
	обращений, - там имени нет.
	"""
	if isinstance(node, ast.Name):
		return node.id
	if isinstance(node, ast.Attribute):
		base = _dotted(node.value)
		return f"{base}.{node.attr}" if base else node.attr
	return ""


def _const_str(node: Optional[ast.AST]) -> Optional[str]:
	"""Строка-константа, если узел ей является."""
	if isinstance(node, ast.Constant) and isinstance(node.value, str):
		return node.value
	return None


def _fold_string(node: ast.AST) -> str:
	"""Значение конкатенации строк, если её можно вычислить статически.

	Обфускация вида 'tda' + 'ta' складывается из констант. Складывать их
	заранее нужно, иначе путь вскрытия не увидит: в дереве там два
	слагаемых, а в жизни - одна строка 'tdata'.
	"""
	if isinstance(node, ast.Constant) and isinstance(node.value, str):
		return node.value
	if isinstance(node, ast.JoinedStr):
		return "".join(
			part.value
			for part in node.values
			if isinstance(part, ast.Constant)
			and isinstance(part.value, str))
	if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
		left = _fold_string(node.left)
		right = _fold_string(node.right)
		if left is not None and right is not None:
			return left + right
	return ""


def _walk(tree: ast.AST):
	yield from ast.walk(tree)


class PluginAstAnalyzer:
	"""Разбирает один исходник и собирает находки и возможности."""

	def __init__(self, source: str, filename: str = "") -> None:
		self.source = source
		self.filename = filename
		self.findings: List[Finding] = []
		self.imports: Set[str] = set()
		# Что плагин умеет: сеть, файлы, процессы, секреты.
		self.capabilities: Set[str] = set()
		# Что он делает по словам: словарь читается человеком.
		self.actions: Set[str] = set()
		self.parse_error: Optional[str] = None

	def analyze(self) -> List[Finding]:
		try:
			tree = ast.parse(self.source, filename=self.filename or "<plugin>")
		except SyntaxError as e:
			# Плагин на не-Python или сломанный синтаксис. Это само по себе
			# повод для внимания: клиент его не выполнит, а человек
			# подумает, что работает.
			self.parse_error = f"синтаксис не разбирается: {e}"
			self.findings.append(Finding(
				"unparsable",
				MEDIUM,
				"Код не разбирается как Python: возможно, это не Python "
				"или он сломан",
				(getattr(e, "lineno", 0) or 0),
				str(e)))
			# Дерево построить не вышло, но это не значит, что файл чист.
			# Наоборот: сломанный синтаксис - удобный способ спрятать вред
			# от любого разбора. Поэтому по тексту всё равно идём
			# регулярками, только помечая находки как подозрительные.
			self._text_fallback(getattr(e, "lineno", 0) or 0)
			return self.findings
		except Exception as e:
			self.parse_error = str(e)
			return self.findings

		for node in _walk(tree):
			if isinstance(node, ast.Import):
				for alias in node.names:
					self.imports.add(alias.name.split(".")[0])
					self._import_action(alias.name)
			elif isinstance(node, ast.ImportFrom):
				if node.module:
					self.imports.add(node.module.split(".")[0])
					self._import_action(node.module)
			elif isinstance(node, ast.Call):
				self._call(node)
			elif isinstance(node, ast.Attribute):
				self._attribute(node)
			elif isinstance(node, ast.BinOp):
				self._binop(node)
			elif isinstance(node, ast.Constant):
				self._constant(node)
			elif isinstance(node, ast.Name):
				if node.id in ("tdata", "key_datas"):
					self.findings.append(Finding(
						"session_dir", CRITICAL,
						"Обращается к папке сессии Telegram",
						node.lineno, node.id))
		return self.findings

	# --- склейка и повтор строк ---

	def _binop(self, node: ast.BinOp) -> None:
		"""Проверяет результат выражения над строками.

		Два приёма обфускации, которых не видно по константам:
		  p = 'tda' + 'ta'      - путь собран из кусков;
		  b = 'QUJD' * 30       - данные раздуты повтором.
		Оба выглядят в дереве безобидно, оба опасны, и оба складываются
		в строку настолько просто, что нет смысла делать вид, будто их
		нельзя посчитать.
		"""
		if isinstance(node.op, ast.Add):
			folded = _fold_string(node)
			if folded:
				self._check_sensitive_string(folded, node.lineno)
			return

		if isinstance(node.op, ast.Mult):
			# Повтор: оцениваем итоговую длину, не читая сам кусок.
			left = node.left
			times = node.right
			if not isinstance(times, ast.Constant):
				return
			count = times.value if isinstance(times.value, int) else 0
			if count < 8:
				return
			if isinstance(left, ast.Constant) and isinstance(left.value, str):
				chunk = left.value
			elif isinstance(left, (ast.Name, ast.Attribute)):
				chunk = _dotted(left) or left.id
			else:
				return
			total = len(chunk) * count
			if total < 400:
				return
			self.capabilities.add("обфускация")
			self.findings.append(Finding(
				"b64_blob", MEDIUM,
				"Строка раздута повтором: так прячут объёмный payload",
				node.lineno,
				f"{chunk[:40]}... повторяется {count} раз, {total} символов"))

	# --- что импортировано ---

	def _text_fallback(self, line: int) -> None:
		"""Поиск по тексту для кода, который не разобрался.

		Слабее AST в разы, поэтому находки здесь не выше medium и
		помечены: дерево построить не вышло, это может быть и обрывок
		конфига, а не код. Но лучше показать "похоже на чтение tdata,
		код не разобран", чем промолчать.
		"""
		low = self.source.lower()
		slash = chr(92)

		hit = _match_sensitive_path(self.source)
		if hit:
			severity, what, needle, rule = hit
			self.findings.append(Finding(
				rule,
				MEDIUM if severity == CRITICAL else LOW,
				what + " (по тексту: код не разобран)",
				line, needle))
			self.capabilities.add("секреты")

		for marker, rule, what in (
				("eval(", "eval_text", "Вызов eval (по тексту)"),
				("exec(", "exec_text", "Вызов exec (по тексту)"),
				("os.system", "os_system_text", "Запуск через os.system"),
				("pickle.loads", "pickle_text", "Загрузка pickle"),
				("base64.b64decode", "b64_decode_text", "Разбор base64"),
				(chr(92) + chr(92) + "Run", "persistence_text",
					"Путь автозагрузки Windows"),
		):
			if marker in low:
				self.findings.append(Finding(
					rule, MEDIUM, what + " (по тексту: код не разобран)",
					line, marker.strip()))

		blob = _longest_blob(self.source)
		if blob:
			self.capabilities.add("обфускация")
			self.findings.append(Finding(
				"b64_blob", LOW,
				"Большая строка в base64: возможно, спрятанный код",
				line, f"{blob} символов"))

	def _import_action(self, name: str) -> None:
		low = name.lower()
		if low in ("urllib", "urllib.request", "requests", "httpx", "aiohttp",
				"socket", "http", "ftplib", "telnetlib"):
			self.capabilities.add("сеть")
			self.actions.add(f"ходит в сеть ({name})")
		elif low in ("subprocess", "os", "shutil"):
			self.capabilities.add("процессы")
			self.actions.add(f"управляет процессами ({name})")
		elif low in ("base64", "codecs", "binascii", "marshal", "pickle"):
			self.capabilities.add("разбор данных")
			self.actions.add(f"разбирает данные ({name})")
		elif low in ("winreg", "win32api", "win32con", "ctypes"):
			self.capabilities.add("реестр и WinAPI")
			self.actions.add(f"обращается к системе Windows ({name})")

	# --- вызовы ---

	def _call(self, node: ast.Call) -> None:
		name = _dotted(node.func)
		if not name:
			return

		# eval и exec с аргументом "откуда-то"
		if name in ("eval", "exec"):
			self._dynamic_exec(node, name)
			return

		if name == "compile":
			args = node.args
			if args and _const_str(args[0]) == "exec":
				self.findings.append(Finding(
					"compile_exec", CRITICAL,
					"Собирает код в исполняемый блок",
					node.lineno))

		if name in DANGEROUS_CALLS:
			severity, what = DANGEROUS_CALLS[name]
			if name == "compile":
				return
			self.findings.append(Finding(
				name.replace(".", "_"), severity, what, node.lineno, name))
			if name in ("os.system", "os.popen"):
				self.capabilities.add("процессы")
			elif name in ("__import__",):
				self.capabilities.add("динамический импорт")

		if name in CONTEXT_CALLS:
			severity, what, arg_name = CONTEXT_CALLS[name]
			self._context_call(node, name, severity, what, arg_name)

		# shell=True
		if name.startswith("subprocess."):
			for kw in node.keywords:
				if kw.arg == "shell" and _const_str(kw.value) == "True":
					self.findings.append(Finding(
						"shell_true", MEDIUM,
						"Запускает команду через оболочку",
						node.lineno, name))
					self.capabilities.add("процессы")
					break

	# --- опасность по аргументу ---

	def _dynamic_exec(self, node: ast.Call, name: str) -> None:
		"""Опасен ли eval/exec - зависит от того, что попало в него.

		eval("2 + 2") - безобидно. eval(загруженный_из_сети) - нет.
		Константу считаем безопасной, всё остальное - нет.
		"""
		if not node.args:
			self.findings.append(Finding(
				name + "_noarg", MEDIUM,
				f"{name} вызывается без аргумента",
				node.lineno))
			return
		arg = node.args[0]
		if _const_str(arg) is not None:
			return
		source = _source_name(arg)
		self.capabilities.add("выполнение кода")
		severity = CRITICAL if source else MEDIUM
		extra = f" (источник: {source})" if source else ""
		self.findings.append(Finding(
			name + "_dynamic", severity,
			f"{name} выполняет код, полученный не из исходника{extra}",
			node.lineno))

	def _context_call(
			self,
			node: ast.Call,
			name: str,
			severity: str,
			what: str,
			arg_name: str) -> None:
		"""Опасность вызова решается по тому, откуда взят аргумент."""
		if not arg_name:
			self.findings.append(Finding(
				name.replace(".", "_"), severity, what, node.lineno, name))
			return
		arg = None
		for kw in node.keywords:
			if kw.arg == arg_name:
				arg = kw.value
				break
		if arg is None and node.args:
			arg = node.args[0]
		if arg is None:
			return

		origin = _source_name(arg)
		text = _fold_string(arg)
		if not origin and not text:
			# Константа или непонятное выражение: сигнал слабый.
			self.findings.append(Finding(
				name.replace(".", "_"), LOW, what, node.lineno, name))
			return

		if "сеть" in origin or text.startswith(("http://", "https://")):
			self.capabilities.add("сеть")
			severity = CRITICAL if name in ("eval", "exec", "marshal.loads") \
				else HIGH
			self.findings.append(Finding(
				name.replace(".", "_"), severity,
				what + ", а аргумент приходит из сети",
				node.lineno, origin or text[:80]))
			return
		if origin:
			self.findings.append(Finding(
				name.replace(".", "_"), MEDIUM,
				what + f", аргумент берётся из {origin}",
				node.lineno))
			return
		self.findings.append(Finding(
			name.replace(".", "_"), severity, what, node.lineno, text[:80]))

	# --- атрибуты и строки ---

	def _attribute(self, node: ast.Attribute) -> None:
		full = _dotted(node)
		if not full:
			return
		hit = _match_sensitive_path(full)
		if hit:
			severity, what, needle, rule = hit
			self.findings.append(Finding(
				rule, severity, what,
				node.lineno, full))
			self.capabilities.add("секреты")

	def _constant(self, node: ast.Constant) -> None:
		if not isinstance(node.value, str):
			return
		text = node.value
		low = text.lower()
		for host in SUSPICIOUS_HOST_HINTS:
			if host in low:
				self.capabilities.add("сеть")
				self.findings.append(Finding(
					"external_host", HIGH,
					"В коде указан адрес, куда обычно сливают данные",
					node.lineno, host))
				break

		self._check_sensitive_string(text, node.lineno)

		# Большая строка в base64: так прячут код, и дерево её не покажет.
		if len(text) >= 400 and re.fullmatch(r"[A-Za-z0-9+/=\s]{400,}", text):
			self.capabilities.add("обфускация")
			self.findings.append(Finding(
				"b64_blob", MEDIUM,
				"Большая строка в base64: возможно, спрятанный код",
				node.lineno, f"{len(text)} символов"))
		elif len(text) >= 300 and re.fullmatch(r"[0-9a-fA-F\s]{300,}", text):
			self.capabilities.add("обфускация")
			self.findings.append(Finding(
				"hex_blob", MEDIUM,
				"Большая hex-строка: возможно, спрятанный код",
				node.lineno, f"{len(text)} символов"))

	def _check_sensitive_string(self, text: str, line: int) -> None:
		"""Путь к секретам внутри строки.

		Отдельно от атрибутов, потому что путь чаще лежит именно в
		строке: os.path.join(home, 'tdata') - это не атрибут tdata, это
		аргумент. Регуляркой такая находка тоже находилась, но здесь она
		видна и после склейки строк, когда в дереве её не видно вовсе.
		"""
		hit = _match_sensitive_path(text)
		if hit:
			severity, what, needle, rule = hit
			self.capabilities.add("секреты")
			self.findings.append(Finding(
				rule, severity, what, line,
				text[:120]))

	# --- итог ---

	def _dedupe(self) -> List[Finding]:
		"""Одно и то же правило в одном файле - одна находка.

		Иначе плагин, который читает путь сессии в двадцати местах,
		показывался бы двадцатью находками и выглядел страшнее, чем есть.
		Имена приводятся к общим id через RULE_ALIASES, поэтому находка
		от дерева и от регулярки схлопывается в одну.
		"""
		best: Dict[str, Finding] = {}
		for f in self.findings:
			name = RULE_ALIASES.get(f.rule, f.rule)
			keep = best.get(name)
			if keep is None or _sev_rank(f.severity) > _sev_rank(keep.severity):
				best[name] = f
		return sorted(best.values(), key=lambda f: (-_sev_rank(f.severity), f.line))

	def report(self) -> dict:
		return {
			"findings": [f.as_dict() for f in self._dedupe()],
			"imports": sorted(self.imports),
			"capabilities": sorted(self.capabilities),
			"actions": sorted(self.actions),
			"parse_error": self.parse_error,
		}


def _longest_blob(source: str) -> int:
	"""Длина самой длинной строки, похожей на base64 или hex.

	Ищется по исходнику целиком, а не по константам дерева: в коде,
	который не разобрался, дерева нет.
	"""
	best = 0
	for match in re.finditer(r"[A-Za-z0-9+/=]{400,}", source):
		best = max(best, len(match.group(0)))
	for match in re.finditer(r"\b[0-9a-fA-F]{300,}\b", source):
		best = max(best, len(match.group(0)))
	return best


# Имена правил AST приводятся к тем же id, что и у регулярных проверок в
# plugin_scanner. Иначе одна и та же находка пришла бы под двумя именами
# и не схлопнулась бы: файл с чтением tdata показал бы и read_tdata, и
# sensitive_path, будто это две разные проблемы.
RULE_ALIASES = {
	"exec_dynamic": "exec_remote",
	"eval_dynamic": "exec_remote",
	"compile_exec": "compile_then_exec",
	"marshal_loads": "marshal_loads",
	"pickle_loads": "pickle_loads",
	"__import__": "__import__",
	"subprocess_run": "subprocess_shell",
	"external_host": "send_to_external",
	"b64_blob": "long_b64_blob",
	"hex_blob": "long_hex_blob",
	"os_system": "subprocess_shell",
	"os_popen": "subprocess_shell",
	"shutil_rmtree": "subprocess_shell",
	"eval_noarg": "exec_remote",
	"exec_noarg": "exec_remote",
	"eval_text": "exec_remote",
	"exec_text": "exec_remote",
	"os_system_text": "subprocess_shell",
	"pickle_text": "pickle_loads",
	"b64_decode_text": "long_b64_blob",
	"persistence_text": "persistence",
}


def _sev_rank(severity: str) -> int:
	return {CRITICAL: 3, HIGH: 2, MEDIUM: 1, LOW: 0}.get(severity, 0)


def _source_name(node: ast.AST) -> str:
	"""Откуда взялось значение: имя переменной, вызов, чтение файла.

	Возвращает короткое имя источника или пустую строку для констант.
	"""
	name = _dotted(node)
	if name:
		return name
	if isinstance(node, ast.Call):
		inner = _dotted(node.func)
		if inner:
			return f"{inner}()"
	if isinstance(node, ast.Subscript):
		base = _source_name(node.value)
		if base:
			return base
	if isinstance(node, ast.Attribute):
		return node.attr
	return ""


def analyze_source(source: str, filename: str = "") -> dict:
	"""Разбирает один исходник и отдаёт отчёт.

	Отчёт, а не список находок: вызывающему нужны ещё и возможности
	плагина, чтобы сказать "ходит в сеть и читает папку сессии" одной
	строкой, а не собирать это из находок.
	"""
	analyzer = PluginAstAnalyzer(source, filename)
	analyzer.analyze()
	return analyzer.report()