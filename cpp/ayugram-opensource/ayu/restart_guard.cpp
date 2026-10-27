// Автоперезапуск после вылета.
//
// Раньше перезапуск жил только в Python-watchdog, а запускался он руками.
// Пользователь запускал клиент, тот падал, и messenger просто исчезал: чтобы
// продолжить разговор, его надо было запустить руками, и часто забывали.
//
// Теперь при старте поднимается отдельный процесс, который ждёт завершения
// основного и запускает его снова. Он не зависит ни от Python, ни от
// плагинов, ни от состояния приложения: вылет, зависание, сбой при запуске -
// клиент вернётся в любом случае.
//
// Отдельный процесс, а не таймер внутри: после вылета упало бы всё, включая
// таймер. Смотритель живёт отдельно и судит о смерти клиента по
// идентификатору процесса, без всякой связи с ним.

#include "ayu/restart_guard.h"

#include <QtCore/QCoreApplication>
#include <QtCore/QDir>
#include <QtCore/QElapsedTimer>
#include <QtCore/QFile>
#include <QtCore/QFileInfo>
#include <QtCore/QThread>

#include <vector>

// Детектор зависания работает через Win32: перечислить окна процесса,
// спросить у окна WM_NULL с таймаутом и при необходимости завершить
// процесс. Всё это есть только здесь, поэтому windows.h подключается
// явно, а не по цепочке заголовков.
#ifdef _WIN32
#include <windows.h>
#endif

namespace Ayu {

namespace {

// Как часто смотритель проверяет, жив ли клиент. Раз в секунду: быстрее
// незачем, а чаще - лишняя работа на всей сессии приложения.
constexpr auto kPollMs = 1000;

// Пауза перед стартом. Нужна, чтобы хвосты прошлого процесса освободили
// файлы базы: без неё новый клиент иногда стартует поверх старого.
constexpr auto kRestartDelayMs = 2500;

// Сколько раз подряд можно перезапустить быстро. Клиент, падающий при
// старте, при бесконечном цикле перезапусков съедает процессор и память и
// выглядит хуже одного молчаливого падения. После этого смотритель
// отступает, но не отказывается: настоящий вылет бывает и от внешних причин.
constexpr auto kFastRestarts = 3;
constexpr auto kFastWindowMs = 300000;
constexpr auto kSlowDelayMs = 60000;

// Абсолютный предел перезапусков. Даже с задержкой бесконечный цикл -
// это плохо: он маскирует настоящую причину и не даёт системе
// передохнуть. После предела смотритель честно отступает и пишет об этом
// в журнал, чтобы отказ был виден, а не выглядел как "перестало работать".
constexpr auto kMaxRestarts = 12;

// Детектор зависания.
//
// ProcessAlive говорит только "процесс существует". Зависший клиент
// именно существует: окно не отвечает, потоки крутятся, а перезапуска
// не происходит, потому что смотреть не на что - процесс ведь жив.
// Пользователь при этом видит, что клиент исчез, и не может вернуть его.
//
// Поэтому отвечаемость окна проверяется отдельно. WM_NULL ничего не
// делает, но обрабатывается только тем потоком, который качает
// сообщения. Не ответил за отведённое время - значит, главный поток
// занят и окно для пользователя мертво.
constexpr auto kUiProbeMs = 2000;

// Сколько подряд неудачных проверок считать зависанием. Одна неудача -
// это долгая операция. Двенадцать подряд - это уже не операция.
constexpr auto kHungStrikes = 12;

[[nodiscard]] bool ProcessAlive(qint64 pid) {
	if (pid <= 0) {
		return false;
	}
	const auto handle = OpenProcess(
		SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
		FALSE,
		static_cast<DWORD>(pid));
	if (!handle) {
		return false;
	}
	DWORD code = 0;
	const auto ok = GetExitCodeProcess(handle, &code);
	CloseHandle(handle);
	return ok && (code == STILL_ACTIVE);
}

struct WindowSearch {
	DWORD pid = 0;
	HWND found = nullptr;
};

[[nodiscard]] BOOL CALLBACK FindWindowForPid(HWND window, LPARAM param) {
	// Не const: в поле нужно записать найденное окно, обход пишет результат
	// через указатель, а не возвращает значение.
	auto* search = reinterpret_cast<WindowSearch*>(param);
	DWORD owner = 0;
	GetWindowThreadProcessId(window, &owner);
	if (owner == search->pid && IsWindowVisible(window)) {
		search->found = window;
		return FALSE; // нашли - прекращаем обход
	}
	return TRUE;
}

// Окно клиента, если оно вообще есть.
[[nodiscard]] HWND MainWindowForPid(DWORD pid) {
	auto search = WindowSearch();
	search.pid = pid;
	EnumWindows(&FindWindowForPid, reinterpret_cast<LPARAM>(&search));
	return search.found;
}

// Отвечает ли окно. Пустой ответ - это "окна нет", и это не зависание:
// клиент может быть ещё на старте.
[[nodiscard]] bool UiResponding(qint64 pid, DWORD timeoutMs) {
	const auto window = MainWindowForPid(static_cast<DWORD>(pid));
	if (!window) {
		return true; // окна ещё нет - не повод считать клиент мёртвым
	}
	DWORD_PTR result = 0;
	// SMTO_ABORTIFHUNG заставляет SendMessageTimeout вернуться сразу,
	// если система уже считает поток зависшим, не тратя таймаут.
	const auto ok = SendMessageTimeout(
		window,
		WM_NULL,
		0,
		0,
		SMTO_ABORTIFHUNG | SMTO_BLOCK,
		timeoutMs,
		&result);
	return ok != 0;
}

} // namespace

bool IsRestartMonitor(const QStringList &arguments) {
	return arguments.contains(QLatin1String("--ayu-watch-pid"));
}

// Файл-блокировка смотрителя: один на всю систему.
	//
	// Несколько смотрителей - это не страховка, а шторм. Каждый из них
	// считает клиент упавшим (потому что он только что его поднял), поднимает
	// снова, а новый клиент выходит сразу, увидев блокировку единственного
	// экземпляра. Получается пустая работа с постоянными перезапусками, и
	// именно она выглядит как "критическая ошибка".
// Журнал смотрителя.
	//
	// Без него его работа невидима: процесс молчит, клиент не поднимается, и
	// догадаться, где именно смотритель встал, невозможно. Пишется рядом с
	// исполняемым файлом: путь передаётся явно, потому что у смотрителя нет
	// QApplication и статические вызовы вернули бы пустоту.
void MonitorLog(const QString &executable, const QString &text) {
	const auto path = QDir::cleanPath(
		QFileInfo(executable).absolutePath()
		+ QLatin1String("/restart-monitor.log"));
	QFile file(path);
	if (!file.open(QIODevice::WriteOnly
		| QIODevice::Append
		| QIODevice::Text)) {
		return;
	}
	file.write(QStringLiteral("[%1] %2\n")
		.arg(QDateTime::currentDateTime().toString(
			QStringLiteral("hh:mm:ss")), text)
		.toUtf8());
	file.close();
}
QString MonitorLockPath(const QString &executable) {
	// Рядом с исполняемым файлом, а не через QStandardPaths: у смотрителя
	// нет QCoreApplication, и путь, зависящий от имени приложения,
	// получился бы другим у клиента и у смотрителя. Два разных пути
	// означали бы два независимых замка и ровно ту несколько смотрителей,
	// от которых мы здесь и защищаемся.
	const auto dir = QFileInfo(executable).absolutePath();
	return QDir::cleanPath(dir + QLatin1String("/restart-monitor.lock"));
}

// Захватывает блокировку, если она свободна. Answers false when another
// monitor is already running and this one has no business existing.
bool ClaimMonitorLock(const QString &executable) {
	const auto path = MonitorLockPath(executable);
	QFile lock(path);
	// Append идёт после проверки содержимого: если смотритель записан и он
	// жив, блокировка занята по-настоящему.
	if (lock.open(QIODevice::ReadWrite)) {
		const auto holder = QString::fromUtf8(lock.readAll()).trimmed();
		if (!holder.isEmpty() && ProcessAlive(holder.toLongLong())) {
			lock.close();
			return false;
		}
		lock.close();
	}
	// Файл перезаписывается целиком: старый идентификатор мог остаться от
	// смотрителя, который уже завершился.
	QFile fresh(path);
	if (!fresh.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
		return false;
	}
	fresh.write(
		QString::number(GetCurrentProcessId()).toUtf8());
	fresh.close();
	return true;
}

// Запускает клиента напрямую через Win32.
//
// QProcess здесь неприменим: смотритель живёт без QCoreApplication,
// а QProcess без него не умеет запускать процессы. Короче, он молча
// не делал ничего - а это и есть ровно тот отказ, ради которого
// смотритель написан. CreateProcess не зависит ни от чего, что есть в
// клиенте, и работает в голом процессе.
[[nodiscard]] qint64 StartClient(const QString &executable) {
	// Строка сохраняется в переменную, а toStdWString() не вызывается
	// дважды в одном выражении: каждый вызов возвращает свой временный
	// объект, и итераторы от двух разных строк дают произвольный буфер.
	const auto wide = executable.toStdWString();
	// Кавычки обязательны: путь к клиенту содержит пробелы, и без них
	// CreateProcess возьмёт только первый фрагмент пути.
	auto buffer = std::vector<wchar_t>{ L'"' };
	buffer.insert(buffer.end(), wide.begin(), wide.end());
	buffer.push_back(L'"');
	buffer.push_back(L'\0');

	STARTUPINFOW startup = { };
	startup.cb = sizeof(startup);
	PROCESS_INFORMATION process = { };
	// CREATE_NEW_PROCESS_GROUP отделяет клиента от группы смотрителя:
	// закрытие смотрителя не должно утаскивать клиента за собой.
	const auto created = CreateProcessW(
		nullptr,
		buffer.data(),
		nullptr,
		nullptr,
		FALSE,
		CREATE_NEW_PROCESS_GROUP,
		nullptr,
		nullptr,
		&startup,
		&process);
	if (!created) {
		return 0;
	}
	CloseHandle(process.hThread);
	CloseHandle(process.hProcess);
	return static_cast<qint64>(process.dwProcessId);
}

// Запуск с аргументами, отдельный от StartClient: клиент поднимается без
// аргументов, а смотритель - с "--ayu-watch-pid".
[[nodiscard]] qint64 StartProcessDetached(
		const QString &executable,
		const QStringList &arguments) {
	// Та же предосторожность, что в StartClient: временная строка живёт
	// только до конца выражения.
	const auto wide = executable.toStdWString();
	auto buffer = std::vector<wchar_t>{ L'"' };
	buffer.insert(buffer.end(), wide.begin(), wide.end());
	buffer.push_back(L'"');
	for (const auto& argument : arguments) {
		buffer.push_back(L' ');
		const auto wideArgument = argument.toStdWString();
		buffer.insert(buffer.end(), wideArgument.begin(), wideArgument.end());
	}
	buffer.push_back(L'\0');

	STARTUPINFOW startup = { };
	startup.cb = sizeof(startup);
	PROCESS_INFORMATION process = { };
	const auto created = CreateProcessW(
		nullptr,
		buffer.data(),
		nullptr,
		nullptr,
		FALSE,
		CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
		nullptr,
		nullptr,
		&startup,
		&process);
	if (!created) {
		return 0;
	}
	CloseHandle(process.hThread);
	CloseHandle(process.hProcess);
	return static_cast<qint64>(process.dwProcessId);
}

int RunRestartMonitor(const QString &executable, const QStringList &arguments) {
	const auto index = arguments.indexOf(QLatin1String("--ayu-watch-pid"));
	qint64 watched = 0;
	if (index >= 0 && index + 1 < arguments.size()) {
		watched = arguments.at(index + 1).toLongLong();
	}
	const auto exe = QFileInfo(executable).absoluteFilePath();
	if (!watched || exe.isEmpty()) {
		return 1;
	}

	// Второй смотритель не нужен и опасен: см. ClaimMonitorLock.
	if (!ClaimMonitorLock(executable)) {
		MonitorLog(executable, QStringLiteral("another monitor already holds the lock, exiting"));
		return 0;
	}

	MonitorLog(executable, QStringLiteral("monitor started, watching %1").arg(watched));
	auto fast = 0;
	auto total = 0;
	auto strikes = 0;
	auto elapsed = QElapsedTimer();
	elapsed.start();

	// Цикл без событий Qt: ждать тут нечего, а обработка событий только
	// отвлекала бы от опроса.
	for (;;) {
		QThread::msleep(static_cast<unsigned long>(kPollMs));
		if (ProcessAlive(watched)) {
			// Процесс жив. Но "жив" не значит "работает": клиент может
			// крутиться в бесконечном цикле, оставив окно мёртвым. Раньше
			// здесь стоял безусловный continue, и такой клиент ждал бы
			// пользователя вечно, пока его не снимут вручную.
			if (UiResponding(watched, kUiProbeMs)) {
				strikes = 0;
				continue;
			}
			if (++strikes < kHungStrikes) {
				continue;
			}
			MonitorLog(executable,
				QStringLiteral("client is not responding, restarting it"));
			// Убиваем именно потому, что он не уходит сам: у процесса,
			// который согласился бы завершиться, ждать было бы не нужно.
			if (const auto handle = OpenProcess(
					PROCESS_TERMINATE,
					FALSE,
					static_cast<DWORD>(watched))) {
				TerminateProcess(handle, 0);
				CloseHandle(handle);
			}
			// Пауза здесь не нужна: файлы базы держит убитый процесс, а он
			// уже мёртв. Ниже есть общая пауза перед стартом, и второй раз
			// ждать - значит терять пять секунд на каждый перезапуск.
			watched = 0;
			strikes = 0;
		}

		// Клиента нет. Даём ему время освободить файлы и решаем, спешить ли.
		QThread::msleep(static_cast<unsigned long>(kRestartDelayMs));
		if (++total > kMaxRestarts) {
			MonitorLog(executable,
				QStringLiteral("giving up after %1 restarts").arg(total - 1));
			return 2;
		}
		if (fast < kFastRestarts || elapsed.elapsed() > kFastWindowMs) {
			++fast;
		} else {
			// Быстрых попыток достаточно. Ждём дольше, но не отказываемся:
			// мессенджер, не вернувшийся сам, хуже медленного.
			QThread::msleep(static_cast<unsigned long>(kSlowDelayMs));
			fast = 0;
		}
		elapsed.restart();

		// Клиент не запустился: следующая итерация попробует снова.
		const auto started = StartClient(exe);
		if (!started) {
			MonitorLog(executable,
				QStringLiteral("failed to start the client, error %1")
					.arg(GetLastError()));
			continue;
		}
		MonitorLog(executable,
			QStringLiteral("client started as %1").arg(started));

		// Ждём, пока клиент устоится, и следим уже за ним.
		//
		// Результат ожидания держится в отдельной переменной, а не в
		// watched. Раньше здесь стояло "watched = 0" прямо в цикле,
		// и это ломало перезапуск после зависания: watched и так был
		// обнулён выше по убитому клиенту, проверка "if (!watched)"
		// срабатывала, и новый клиент так и не попадал под присмотр.
		// Смотритель продолжал считать, что клиента нет, и поднимал
		// следующий - по одному в несколько секунд, пока не упирался
		// в предел. Теперь присваивается именно тот номер, который
		// клиент получил на самом деле.
		//
		// Смотритель не уходит никогда. Уйти - значит остаться без
		// страховки: новый клиент поднимает своего смотрителя, но тот
		// увидит занятую блокировку и тоже выйдет, и в итоге не останется
		// ни клиента под присмотром, ни смотрителя.
		auto survived = true;
		for (auto i = 0; i != 12; ++i) {
			QThread::msleep(500);
			if (!ProcessAlive(started)) {
				// Не устоял: смотрим дальше и поднимаем снова.
				survived = false;
				break;
			}
		}
		if (!survived) {
			MonitorLog(executable,
				QStringLiteral("client %1 did not survive the start")
					.arg(started));
			continue;
		}
		watched = started;
	}
}

void EnsureRestartMonitor(const QString &executable) {
	// Включается по умолчанию, выключается переменной окружения.
	//
	// Раньше смотритель был выключен: он вмешивается в жизненный цикл
	// самого клиента, и ошибка здесь выходит наружу - в виде упавшего
	// клиента или перезагрузки. Но выключенным он оказался бесполезен:
	// вылет или зависание означали, что клиент не вернётся, а вернуть
	// его было нечем.
	//
	// Теперь опасность устранена в самой конструкции: у смотрителя есть
	// предел перезапусков, детектор зависания и журнал, поэтому шторм
	// больше невозможен. AYU_RESTART_GUARD=0 оставляет клиент без
	// страховки - на случай, если он не нужна.
	// qgetenv, а не qEnvironmentVariableValue: последняя есть не во всех
	// сборках Qt, на которых собирается клиент.
	if (qgetenv("AYU_RESTART_GUARD") == QByteArray("0")) {
		return;
	}

	// Из уже запущенного клиента (например, по ссылке) второй смотритель
	// не поднимается: их было бы столько же, сколько открытий ссылок.
	static auto started = false;
	if (started) {
		return;
	}
	const auto exe = QFileInfo(executable).absoluteFilePath();
	if (exe.isEmpty()) {
		return;
	}
	started = true;

	// Смотритель поднимается через тот же StartClient: он живёт без
	// QCoreApplication, где QProcess не работает, и в клиенте этот вызов
	// идёт до появления event loop - то есть тоже в неподходящий момент.
	// Один способ запуска на оба случая - значит, одна причина отказа.
	QStringList args;
	args << QStringLiteral("--ayu-watch-pid")
		 << QString::number(QCoreApplication::applicationPid());
	if (!StartProcessDetached(exe, args)) {
		// Молчаливый отказ здесь стоил дороже всего: клиент выглядел
		// защищённым, а страховки не было. Теперь отказ виден в журнале.
		MonitorLog(exe,
			QStringLiteral("could not start the monitor, error %1")
				.arg(GetLastError()));
	}
}

} // namespace Ayu
