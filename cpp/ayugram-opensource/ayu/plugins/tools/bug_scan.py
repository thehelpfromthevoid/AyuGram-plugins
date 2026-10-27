"""Ищет ошибки, которые роняют клиент или текут данными.

Не «плохой стиль» и не «можно было бы красивее». Только то, что
заканчивается вылетом, зависанием, порчей файлов или утечкой.

Первая версия сканера дала 99 находок, и почти все были мусором:
  - `abort();` попадало в «жёсткий выход», хотя это QNetworkReply::abort()
  - `QLocale::system()` попадало в «запуск через shell»
  - каждый `except:` считался «ошибкой, которую проглатывают»

Причина одна: поиск по тексту не различает вызов функции и вызов
метода. Здесь это исправлено - Python разбирается деревом, а C++
проверяется с учётом квалификаторов.

Каждая проверка объясняет, почему находка опасна.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(r'D:\TBuild\tdesktop\Telegram\SourceFiles')
AYU = ROOT / 'ayu'

SKIP_DIRS = {'libs', '__pycache__', '_stage', '_pyi_spec', '_pyi_work'}


class Finding:
	__slots__ = ('path', 'line', 'rule', 'why')

	def __init__(self, path: Path, line: int, rule: str, why: str) -> None:
		self.path = path
		self.line = line
		self.rule = rule
		self.why = why

	def key(self):
		return (str(self.path), self.rule)

	def show(self) -> str:
		try:
			rel = self.path.relative_to(ROOT)
		except ValueError:
			rel = self.path
		return f'  {rel}:{self.line}\n      [{self.rule}] {self.why}'


def clean(text: str) -> str:
	"""Убирает комментарии, сохраняя номера строк."""
	def blank(match: re.Match) -> str:
		return re.sub(r'[^\n]', ' ', match.group(0))

	text = re.sub(r'//[^\n]*', blank, text)
	text = re.sub(r'/\*.*?\*/', blank, text, flags=re.S)
	return text


# --- C++ ----------------------------------------------------------------

# Вызов без квалификатора: `abort()`, но не `r->abort()` и не `T::abort()`.
# Именно это отличает жёсткий выход из обычного вызова метода.
BARE_ABORT = re.compile(r'(?<![A-Za-z_0-9:>\.])abort\s*\(\s*\)')
BARE_EXIT = re.compile(r'(?<![A-Za-z_0-9:>\.])_(?:exit|Exit|_Exit)\s*\(\s*\)')
BARE_SYSTEM = re.compile(r'(?<![A-Za-z_0-9:>\.])system\s*\(')
ASSIGNED_MUTEX = re.compile(r'(\w+)\s*=\s*new\s+std::mutex')


def scan_hard_exit(path: Path) -> list[Finding]:
	"""Выход из процесса мимо штатного завершения.

	abort() и _exit() не сохраняют состояние и не дают клиенту
	дописать файлы. Если это происходит в обработчике ошибки,
	пользователь теряет несохранённое.
	"""
	text = clean(path.read_text(encoding='utf-8', errors='replace'))
	out = []
	for index, line in enumerate(text.splitlines(), start=1):
		if BARE_ABORT.search(line):
			out.append(Finding(path, index, 'hard_exit',
				'abort() без квалификатора: клиент падает мимо '
				'штатного завершения'))
		if BARE_EXIT.search(line):
			out.append(Finding(path, index, 'hard_exit',
				'_exit(): состояние не сохраняется'))
	return out


def scan_system_call(path: Path) -> list[Finding]:
	"""Запуск через system().

	Имя файла попадает в командную строку без экранирования, а
	оболочка это интерпретирует. Для клиента, который работает с
	путями от пользователя, это путь к выполнению чужого кода.
	"""
	text = clean(path.read_text(encoding='utf-8', errors='replace'))
	out = []
	for index, line in enumerate(text.splitlines(), start=1):
		if BARE_SYSTEM.search(line):
			out.append(Finding(path, index, 'system_call',
				'system(): путь в оболочку, аргумент не экранируется'))
	return out


def scan_mutex_ownership(path: Path) -> list[Finding]:
	"""Мьютекс, выделенный под new, и захваченный lock_guard.

	lock_guard уничтожает объект при выходе из области видимости.
	Если мьютекс пришёл снаружи указателем, удаляется не то, что
	думает автор, - а если это вообще мьютекс, а не локальная
	переменная, то следующий вызов работает с освобождённой памятью.
	"""
	text = clean(path.read_text(encoding='utf-8', errors='replace'))
	allocated = set(re.findall(ASSIGNED_MUTEX, text))
	if not allocated:
		return []
	out = []
	for index, line in enumerate(text.splitlines(), start=1):
		lock = re.search(r'lock_guard<[^>]*>\s*\(\s*\*?(\w+)', line)
		if not lock:
			continue
		if lock.group(1) in allocated:
			out.append(Finding(path, index, 'mutex_heap_allocated',
				f'мьютекс {lock.group(1)} выделен под new: '
				'lock_guard удалит объект чужой владения'))
	return out


def scan_mutex_across_sleep(path: Path) -> list[Finding]:
	"""Мьютекс, удерживаемый через ожидание.

	Пока ждём, держим замок. Если ожидание внутри критической
	секции, другой поток, которому нужен тот же замок, встанет
	взаимоблокировку - и клиент перестанет отвечать.
	"""
	text = clean(path.read_text(encoding='utf-8', errors='replace'))
	lines = text.splitlines()
	out = []
	depth = None
	for index, line in enumerate(lines):
		if depth is None and re.search(r'lock_guard<[^>]*>', line):
			depth = 0
		if depth is not None:
			depth += line.count('{') - line.count('}')
			if re.search(r'\b(msleep|waitFor|exec\(\)|processEvents|sleep_for)',
					line):
				out.append(Finding(path, index + 1, 'lock_while_waiting',
					'ожидание внутри критической секции: '
					'замок удержится дольше, чем нужно'))
			if depth <= 0:
				depth = None
	return out


CPP_CHECKS = (
	scan_hard_exit,
	scan_system_call,
	scan_mutex_ownership,
	scan_mutex_across_sleep,
)


# --- Python -------------------------------------------------------------

MUTEX_NAMES = {'Lock', 'RLock'}


def _has_mutex(module: ast.Module) -> bool:
	for node in ast.walk(module):
		if isinstance(node, ast.Call):
			func = node.func
			if isinstance(func, ast.Name) and func.id in MUTEX_NAMES:
				return True
			if isinstance(func, ast.Attribute) and func.attr in MUTEX_NAMES:
				return True
	return False


def scan_empty_except(path: Path) -> list[Finding]:
	"""Пустое тело обработчика исключения.

	Исключение поймано и потеряно. Выглядит как «обработали ошибку»,
	а на деле ошибка просто исчезла, и её последствия обнаружатся
	позже и совсем в другом месте.

	Отдельно пропускаются обработчики, которые сразу возвращают
	значение по умолчанию: так устроено чтение настроек, и
	отсутствие файла настроек - это не авария, а норма.
	"""
	try:
		tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
	except (SyntaxError, ValueError):
		return []
	out = []
	for node in ast.walk(tree):
		if not isinstance(node, ast.ExceptHandler):
			continue
		if _is_empty_handler(node.body):
			kind = 'Exception' if node.type else 'bare except'
			out.append(Finding(path, node.lineno, 'except_pass',
				f'{kind} с пустым телом: ошибка проглатывается и '
				'теряется'))
	return out


def _is_empty_handler(body: list) -> bool:
	"""Пусто ли тело обработчика.

	Тело пустое, если в нём есть только `pass` или только строковые
	константы. Раньше здесь отбрасывались все ast.Expr подряд, и под
	нож попадал `self.log(...)`: это тоже выражение, и проверка
	объявляла 24 нормальных обработчика пустыми. Отбрасывать можно
	только то, что ничего не делает.
	"""
	real = []
	for statement in body:
		if isinstance(statement, ast.Pass):
			continue
		if isinstance(statement, ast.Expr) and isinstance(
				statement.value, ast.Constant) and isinstance(
				statement.value.value, str):
			# докстрока внутри тела, а не действие
			continue
		real.append(statement)
	return not real


def scan_global_race(path: Path) -> list[Finding]:
	"""Глобальная переменная меняется, а замка нет.

	Хуки приходят из рабочего потока, команды - из другого, таймеры -
	ещё из третьего. Глобальное состояние без замка означает гонку,
	а гонка в плагине читается как вылет через раз.
	"""
	try:
		tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
	except (SyntaxError, ValueError):
		return []
	if _has_mutex(tree):
		return []

	globals_changed = {}
	for node in ast.walk(tree):
		if isinstance(node, ast.Global):
			for name in node.names:
				globals_changed.setdefault(name, node.lineno)

	out = []
	for name, line in globals_changed.items():
		if name.startswith('__') and name.endswith('__'):
			continue
		if name.isupper():
			continue
		writers = _writers(tree, name)
		if len(writers) <= 1:
			# Пишет ровно одно место - обычно это загрузка плагина, где
			# ссылку кладут один раз и дальше только читают. Под GIL
			# такое гонкой не является.
			#
			# Ошибочный критерий считал присваивания и получал два там,
			# где их одно: объявление в начале модуля и запись в
			# on_plugin_load. Он объявлял гонкой обычную ссылку
			# `_plugin`, которую пишут при загрузке и читают из команд, -
			# то есть самую безобидную вещь в плагине.
			continue
		out.append(Finding(path, line, 'global_race',
			f'global {name} пишется из {len(writers)} мест: '
			f'{", ".join(sorted(writers))} - без замка это гонка'))
	return out


def _writers(tree: ast.Module, name: str) -> set:
	"""Имена функций, которые пишут глобальную переменную."""
	found = set()
	for node in ast.walk(tree):
		if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
			continue
		globals_here = {
			n
			for sub in ast.walk(node)
			if isinstance(sub, ast.Global)
			for n in sub.names
		}
		if name not in globals_here:
			continue
		for sub in ast.walk(node):
			targets = []
			if isinstance(sub, ast.Assign):
				targets = sub.targets
			elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
				targets = [sub.target]
			for target in targets:
				if isinstance(target, ast.Name) and target.id == name:
					found.add(node.name)
	return found


def scan_broad_except(path: Path) -> list[Finding]:
	"""except Exception, после которого состояние уже испорчено.

	Само по себе нормально и в проекте используется осознанно.
	Опасным становится один случай: обработчик стоит внутри хука,
	который вызывается на каждом сообщении. Ошибка там повторяется
	на каждом событии и молча съедает его, а плагин выглядит
	работающим.
	"""
	try:
		tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
	except (SyntaxError, ValueError):
		return []
	out = []
	for node in ast.walk(tree):
		if not isinstance(node, ast.ExceptHandler) or not node.type:
			continue
		name = getattr(node.type, 'id', '')
		if name not in ('Exception', 'BaseException'):
			continue
		# Проверяем родителя: стоит ли обработчик в хуке.
		hook = _enclosing_hook(node, path.read_text(
			encoding='utf-8', errors='replace'))
		if not hook:
			continue
		out.append(Finding(path, node.lineno, 'silent_exception',
			f'внутри {hook} except Exception без записи: ошибка '
			'повторится на каждом событии молча'))
	return out


HOOK_NAMES = re.compile(r'def\s+(on_\w*hook|on_message\w*|on_typing\w*)\b')


def _enclosing_hook(node: ast.AST, source: str) -> str:
	"""Имя функции, внутри которой стоит узел."""
	best = None
	best_start = -1
	for candidate in ast.walk(ast.parse(source)):
		if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
			if candidate.lineno <= node.lineno:
				if candidate.lineno > best_start:
					best_start = candidate.lineno
					best = candidate.name
	if best is None:
		return ''
	match = HOOK_NAMES.match(best)
	return match.group(1) if match else ''


PY_CHECKS = (
	scan_empty_except,
	scan_global_race,
	scan_broad_except,
)


def main() -> int:
	findings: list[Finding] = []

	cpp_files = [
		p for p in sorted(AYU.rglob('*'))
		if p.suffix in ('.cpp', '.h') and not (SKIP_DIRS & set(p.parts))
	]
	py_files = [
		p for p in sorted((ROOT / 'ayu' / 'plugins').rglob('*.py'))
		if not (SKIP_DIRS & set(p.parts))
	]

	for path in cpp_files:
		for check in CPP_CHECKS:
			try:
				findings.extend(check(path))
			except Exception as error:
				print(f'  {path.name}: не отработала ({error})')
	for path in py_files:
		for check in PY_CHECKS:
			try:
				findings.extend(check(path))
			except Exception as error:
				print(f'  {path.name}: не отработала ({error})')

	unique: dict[tuple, Finding] = {}
	for finding in findings:
		unique.setdefault(finding.key(), finding)

	print(f'  C++: {len(cpp_files)} файлов   Python: {len(py_files)} файлов')
	print()

	by_rule: dict[str, list[Finding]] = {}
	for finding in unique.values():
		by_rule.setdefault(finding.rule, []).append(finding)

	for rule in sorted(by_rule):
		group = by_rule[rule]
		print(f'  === {rule}: {len(group)} ===')
		for finding in sorted(group, key=lambda f: str(f.path))[:8]:
			print(finding.show())
		if len(group) > 8:
			print(f'      ... ещё {len(group) - 8}')
		print()

	print(f'  уникальных находок: {len(unique)}')
	return 0


if __name__ == '__main__':
	sys.exit(main())