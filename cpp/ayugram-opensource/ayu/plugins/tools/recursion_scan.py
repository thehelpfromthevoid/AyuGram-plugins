"""Ищет безусловную рекурсию в коде AyuGram.

Зачем: выход из строя, который мы чинили, был ровно такой - функция
вызывала сама себя и съедала стек до конца. Открытие списка плагинов
приводило к падению.

Почему не "ищем любую рекурсию": законной рекурсии в клиенте много.
Обход дерева, разбор вложенного текста, поиск по списку с двумя
перегрузками - всё это рекурсия, и всё это правильно. Проверка, которая
находит 500 таких мест, бесполезна: её выключат после первого запуска.

Поэтому ищем одну узкую вещь: самовызов, который не защищён ничем.
Легальная рекурсия всегда либо под условием, либо с другим аргументом,
либо в возврате. Строка вида

    SomeFunction(arg);

стоящая сама по себе, означает, что функция вызывает себя всегда и
уйдёт в бесконечность. Это и есть ошибка, а не стиль.

Ограничение область применения - только код AyuGram. Upstream Telegram
не трогаем: он чужой, и его ошибки чинятся в апстриме.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(r'D:\TBuild\tdesktop\Telegram\SourceFiles')

# Только своё дерево. Чужой код здесь ни при чём.
AYU_ROOT = ROOT / 'ayu'

# Определение функции: имя и открывающая скобка тела.
DEF = re.compile(
	r'^[A-Za-z_][\w:<>,\s\*&\[\]]*?([A-Za-z_]\w*)\s*\([^;{]*?\)\s*(?:const\s*)?\{',
	re.M | re.S)

KEYWORDS = {
	'if', 'for', 'while', 'switch', 'return', 'catch', 'sizeof', 'else',
	'do', 'case', 'new', 'delete', 'static_cast', 'dynamic_cast',
	'const_cast', 'reinterpret_cast', 'defined', 'throw', 'noexcept',
}


def strip_noise(text: str) -> str:
	"""Убирает комментарии и строковые литералы.

	Без этого вызов из комментария считался бы настоящим вызовом, а
	проверка опять превратилась бы в шум. Длина строк сохраняется, чтобы
	номера строк оставались верными.
	"""
	def blank(match: re.Match) -> str:
		return re.sub(r'[^\n]', ' ', match.group(0))

	text = re.sub(r'//[^\n]*', blank, text)
	text = re.sub(r'/\*.*?\*/', blank, text, flags=re.S)
	text = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', text)
	text = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", text)
	return text


def function_bodies(text: str):
	"""Отдаёт (имя, начало, конец, номер_строки) для каждой функции."""
	for match in DEF.finditer(text):
		name = match.group(1)
		if name in KEYWORDS:
			continue
		depth = 1
		i = match.end()
		while i < len(text) and depth > 0:
			if text[i] == '{':
				depth += 1
			elif text[i] == '}':
				depth -= 1
			i += 1
		if depth != 0:
			continue
		line = text.count('\n', 0, match.start()) + 1
		yield name, match.end(), i, line


# Ведущие пробелы, необязательные `return` или присваивание, затем
# имя(аргументы) и точка с запятой.
#
# `return` включён не для красоты: ошибка, которая стоила нам падения,
# выглядела именно как `return PluginHookSubscriptions(id);`. Голый
# вызов без return - это уже другой случай, а return без условия -
# самый опасный, потому что компилятор на него не смотрит.
#
# Присваивание (`auto n = CountThings(id);`) - второй способ написать то
# же самое: временная переменная никого не спасает, стек расходуется
# так же.
BARE_CALL = re.compile(
	r'^[ \t]*(?:return[ \t]+)?(?:[A-Za-z_][\w:<>,\s\*&\[\]]*?[ \t]=[ \t]*)?'
	r'([A-Za-z_]\w*)\s*\(([^;{}]*)\)\s*;', re.M)

# Параметры функции: имя и необязательное значение по умолчанию.
PARAMS = re.compile(r'([A-Za-z_]\w*)\s*(?:=[^,]*)?(?:,|$)')


def split_arguments(text: str):
	"""Делит список аргументов по верхнему уровню.

	Разделять просто по запятой нельзя: `QMap<int, int>` или
	`std::vector<PeerId, Alloc>` содержат запятые внутри шаблонов, и
	такой вызов распался бы на лишние куски - а это уже совсем другой
	список параметров, и сравнение перестаёт иметь смысл.
	"""
	parts = []
	depth = 0
	current = []
	for char in text:
		if char in '(<[':
			depth += 1
		elif char in ')>]':
			depth -= 1
		if char == ',' and depth == 0:
			parts.append(''.join(current).strip())
			current = []
			continue
		current.append(char)
	tail = ''.join(current).strip()
	if tail:
		parts.append(tail)
	return [p for p in parts if p]


def params_of(signature: str):
	"""Имена параметров функции из строки в скобках."""
	body = signature.strip()
	if body.startswith('('):
		body = body[1:]
	if body.endswith(')'):
		body = body[:-1]
	body = body.strip()
	if not body or body == 'void':
		return []

	names = []
	for piece in split_arguments(body):
		# У параметра может быть значение по умолчанию: берём левую часть.
		piece = piece.split('=')[0].strip()
		# Последнее слово - имя, если только это не шаблонный тип.
		words = piece.replace('&', ' ').replace('*', ' ').split()
		if not words:
			continue
		last = words[-1]
		if re.fullmatch(r'[A-Za-z_]\w*', last):
			names.append(last)
		else:
			# `const QString &` без имени - безымянный параметр.
			names.append('')
	return names


def strip_trailing_group(text: str):
	"""Убирает последнюю сбалансированную группу в скобках.

	Нужно, чтобы увидеть слово перед `if (...)`: без этого
    `if (n->children.empty()) {`
    выглядит как условие без ветвления.
	"""
	stripped = text.rstrip()
	if not stripped.endswith(')'):
		return stripped
	depth = 0
	for index in range(len(stripped) - 1, -1, -1):
		char = stripped[index]
		if char == ')':
			depth += 1
		elif char == '(':
			depth -= 1
			if depth == 0:
				return stripped[:index].rstrip()
	return stripped


GUARDS = ('if', 'for', 'while', 'switch', 'else', 'try', 'do')


def guarded(body: str, position: int) -> bool:
	"""Защищён ли вызов условием, циклом, else-веткой или лямбдой.

	Идём назад от вызова по скобкам и находим ближайшую открывающую.
	Смотрим, что стоит непосредственно перед ней: если это `if`, `for`,
	`while`, `else`, `try` или запятая (лямбда), то путь к вызову не
	безусловный и рекурсия закончится на границе дерева.
	"""
	head = body[:position]
	depth = 0
	for index in range(len(head) - 1, -1, -1):
		char = head[index]
		if char == '}':
			# Закрывающая скобка: выходим из блока, значит её
			# соответствующая открывающая где-то впереди.
			depth += 1
		elif char == '{':
			if depth > 0:
				# Это открывающая к уже посчитанной закрывающей.
				depth -= 1
				continue
			# Дошли до самой внутренней открывающей - это и есть
			# граница блока, в котором живёт вызов.
			before = strip_trailing_group(head[:index])
			words = before.replace('(', ' ').replace(')', ' ')
			words = words.replace('{', ' ').replace('}', ' ')
			words = words.replace(':', ' ').replace(';', ' ')
			tokens = words.split()
			last = tokens[-1] if tokens else ''
			if last in GUARDS:
				return True
			if before.endswith(','):
				# лямбда внутри списка аргументов или тела
				return True
			if before.endswith(']'):
				# тело лямбды: `auto once = [&]() {`
				return True
			if '=' in before and not before.endswith(('==', '!=', '<=', '>=')):
				# присваивание функции лямдой или инициализация
				return True
			return False
	# Открывающей скобки перед вызовом не нашлось вовсе: он стоит
	# первым утверждением тела функции, и путь к нему безусловен.
	# Раньше здесь возвращалось "защищён", из-за чего ошибка
	# `return f(x);` в начале функции проходила как норма.
	return False


def scan_file(path: Path):
	raw = path.read_text(encoding='utf-8', errors='replace')
	text = strip_noise(raw)
	found = []
	for match in DEF.finditer(text):
		name = match.group(1)
		if name in KEYWORDS:
			continue
		depth = 1
		i = match.end()
		while i < len(text) and depth > 0:
			if text[i] == '{':
				depth += 1
			elif text[i] == '}':
				depth -= 1
			i += 1
		if depth != 0:
			continue

		signature = match.group(0)[match.group(0).find(name) + len(name):]
		signature = signature[:signature.rfind(')')] if ')' in signature else ''
		parameters = params_of(signature)
		if not parameters:
			continue

		body = text[match.end():i]
		for call in BARE_CALL.finditer(body):
			if call.group(1) != name:
				continue
			arguments = split_arguments(call.group(2))
			if len(arguments) != len(parameters):
				# Другое число аргументов - это другая перегрузка,
				# а не рекурсия.
				continue
			# Совпадение аргументов с параметрами - главный признак.
			# `return f(x)` внутри `f(x)` не сходится никогда: x не
			# меняется, значит и состояние не меняется, и следующий
			# вызов уходит ровно туда же. Если аргумент другой
			# (`f(node->next)`), ход идёт по дереву и в какой-то
			# момент упрётся в лист - это законная рекурсия, даже
			# если стоит без всякого условия.
			if arguments != parameters:
				continue
			# Те же самые аргументы. Осталось убедиться, что вызов
			# не под условием: `if (...) return f(x);` рекурсию
			# останавливает, а `return f(x);` в начале тела - нет.
			if guarded(body, call.start()):
				continue
			absolute = match.end() + call.start()
			real = text.count('\n', 0, absolute) + 1
			found.append((real, name, call.group(2).strip()[:70], ''))
	return found


def main() -> int:
	# Корень можно задать аргументом: проверка подставляет свой каталог.
	global ROOT, AYU_ROOT
	if len(sys.argv) > 1:
		ROOT = Path(sys.argv[1])
		AYU_ROOT = ROOT / 'ayu'

	files = sorted(AYU_ROOT.rglob('*.cpp')) + sorted(AYU_ROOT.rglob('*.h'))
	files = [f for f in files if 'libs' not in f.parts]

	total = 0
	for path in files:
		try:
			found = scan_file(path)
		except Exception as error:
			print(f'  {path.name}: {error}')
			continue
		for line, name, args, _ in found:
			total += 1
			print(f'  {path.relative_to(ROOT)}:{line}')
			print(f'      {name}({args});   <- безусловный самовызов')

	print('')
	print(f'  файлов AyuGram: {len(files)}')
	print(f'  безусловных самовызовов: {total}')
	if total:
		print('  каждый такой вызов - это падение со стеком')
	else:
		print('  чисто')
	return 1 if total else 0


if __name__ == '__main__':
	sys.exit(main())