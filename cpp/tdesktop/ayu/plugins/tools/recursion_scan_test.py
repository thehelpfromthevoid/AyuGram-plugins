"""Проверка самого сканера рекурсии.

Сканер, который ни на чём не срабатывает, бесполезен: он молчит и на
настоящей ошибке. Поэтому проверка обратная - подставляем заведомо
сломанный код и убеждаемся, что он находится, а правильный код
одновременно остаётся чистым.

Ошибка здесь та же, что была в клиенте:

    int PluginHookSubscriptions(const QString &id) {
        return PluginHookSubscriptions(id);
    }
"""

import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).with_name('recursion_scan.py')

BROKEN = """
#include <string>
namespace Ayu {
// Возврат собственного вызова безусловно: стек кончится за секунду.
int PluginHookSubscriptions(const QString &id) {
	return PluginHookSubscriptions(id);
}

// То же, но через промежуточное присваивание.
int CountThings(const QString &id) {
	auto n = CountThings(id);
	return n + 1;
}
} // namespace Ayu
"""

HEALTHY = """
#include <string>
namespace Ayu {
// Перегрузка: аргументы другие, это другой метод.
void hide(int peerId, int id) {
	(void)peerId; (void)id;
}
void hide(void *item) {
	hide(peerIdOf(item), idOf(item));
}

// Рекурсия с другим аргументом: идём по дереву, в лист выходим.
void collect(node n) {
	if (n->children.empty()) {
		return;
	}
	collect(n->children.front());
}

// Рекурсия со счётчиком: уменьшается до нуля.
int depth(int level) {
	if (level <= 0) {
		return 0;
	}
	return 1 + depth(level - 1);
}

// Передача аргумента дальше, вызов не возвращается.
void forward(node n) {
	if (!n) {
		return;
	}
	forward(n->next);
}

// Те же аргументы, но под условием: рекурсия обязана закончиться.
int count(int depth, int limit) {
	if (depth <= 0) {
		return limit;
	}
	return count(depth - 1, limit);
}

// Рекурсия по lambda внутри условия.
void run(node n) {
	if (!n) {
		return;
	}
	auto once = [&]() {
		run(n->next);
	};
	once();
}
} // namespace Ayu
"""


def scan(source: str):
	with tempfile.TemporaryDirectory() as folder:
		path = Path(folder) / 'ayu' / 'case.cpp'
		path.parent.mkdir(parents=True)
		path.write_text(source, encoding='utf-8')
		result = subprocess.run(
			[sys.executable, '-X', 'utf8', str(TOOL),
				# Корень - папка рядом с 'ayu', внутри которой лежит case.cpp.
				str(path.parent.parent)],
			capture_output=True,
			# Разбор вывода: кодировка консоли дочернего процесса не
			# гарантирована, поэтому ошибки заменяются, а не роняют проверку.
			encoding='utf-8',
			errors='replace')
		return result.stdout or ''


def main() -> int:
	print('  === сломанный код должен быть найден ===')
	broken = scan(BROKEN)
	for line in broken.splitlines():
		if 'безусловный' in line:
			print('  ' + line.strip())
	found_broken = 'PluginHookSubscriptions(id);' in broken
	found_second = 'CountThings(id);' in broken
	print(f'  PluginHookSubscriptions найден: {found_broken}')
	print(f'  CountThings найден: {found_second}')

	print('')
	print('  === правильный код должен остаться чистым ===')
	healthy = scan(HEALTHY)
	clean = 'безусловных самовызовов: 0' in healthy
	print('  чисто' if clean else '  СТРОКА БЕЗ УСЛОВИЯ ПРОПУЩЕНА:')
	for line in healthy.splitlines():
		if 'безусловный' in line:
			print('    ' + line.strip())

	ok = found_broken and found_second and clean
	print('')
	print('  ИТОГ: ' + ('сканер работает' if ok else 'СКАНЕР БРАКОВАН'))
	return 0 if ok else 1


if __name__ == '__main__':
	sys.exit(main())