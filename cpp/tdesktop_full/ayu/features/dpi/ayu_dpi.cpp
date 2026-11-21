// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/features/dpi/ayu_dpi.h"

#include "ayu/ayu_settings.h"
#include "core/application.h"
#include "settings.h"

#include <QtCore/QDir>
#include <QtCore/QElapsedTimer>
#include <QtCore/QFileInfo>
#include <QtCore/QProcess>
#include <QtCore/QStringList>
#include <QtNetwork/QTcpSocket>

#ifdef Q_OS_WIN
#include <windows.h>
#endif

namespace Ayu::Dpi {

namespace {

// Сколько ждём, пока поднятый процесс начнёт принимать соединения. Процесс
// стартует быстро, но клиент не должен уйти в сеть раньше, чем прокси готов:
// иначе первое подключение уйдёт в никуда.
constexpr auto kStartupWaitMs = 5000;

// Таймаут одной пробы порта. Достаточно, чтобы локальный слушатель ответил, и
// мало, чтобы не задерживать старт.
constexpr auto kProbeWaitMs = 200;

// Метод "no auth" в приветствии SOCKS5: `05 00` это версия и принятый метод.
// Именно `05 00`, а не просто `05`, доказывает, что на порту наш прокси.
constexpr auto kSocksNoAuth = 0x00;

// Стратегия по умолчанию: дробим первый сегмент после первого байта и по
// середине доменного имени в SNI. Именно она проверена сквозным TLS-хендшейком
// к telegram.org; менять её вслепую нельзя.
constexpr auto kDefaultStrategy = "--filter-tcp=443"
	" --dpi-desync=multisplit --dpi-desync-split-pos=1,midsld";

QString g_error;
qint64 g_pid = 0;
bool g_applied = false;

[[nodiscard]] bool ProcessAlive(qint64 pid) {
#ifdef Q_OS_WIN
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
#else
	Q_UNUSED(pid);
	return false;
#endif
}

void TerminateProcessById(qint64 pid) {
#ifdef Q_OS_WIN
	if (pid <= 0) {
		return;
	}
	const auto handle = OpenProcess(
		PROCESS_TERMINATE,
		FALSE,
		static_cast<DWORD>(pid));
	if (handle) {
		TerminateProcess(handle, 0);
		CloseHandle(handle);
	}
#else
	Q_UNUSED(pid);
#endif
}

// Ищет ayu-dpi.exe рядом с клиентом. Сборка кладёт его в тот же каталог, что и
// исполняемый файл; отсутствие бинарника - это отказ, а не повод молча
// выключиться, поэтому причина сохраняется в lastError.
[[nodiscard]] QString ExecutablePath() {
	const auto candidates = {
		QDir(cExeDir()).filePath(u"ayu-dpi.exe"_q),
		QDir(cWorkingDir()).filePath(u"ayu-dpi.exe"_q),
	};
	for (const auto &path : candidates) {
		if (QFileInfo::exists(path)) {
			return path;
		}
	}
	return QString();
}

// Слушает ли на порту SOCKS5. Переиспользование уже поднятого прокси
// (оставшегося от прошлого запуска клиента) безопасно только тогда, когда на
// порту действительно SOCKS5: иначе клиент пошёл бы через чужой сервис.
// Поэтому порт не "занят/свободен", а именно опрашивается приветствием
// протокола.
[[nodiscard]] bool ProbeSocks5(quint16 port) {
	QTcpSocket socket;
	socket.connectToHost(u"127.0.0.1"_q, port);
	if (!socket.waitForConnected(kProbeWaitMs)) {
		return false;
	}
	socket.write(QByteArray::fromHex("050100"));
	if (!socket.waitForBytesWritten(kProbeWaitMs)) {
		return false;
	}
	if (!socket.waitForReadyRead(kProbeWaitMs)) {
		return false;
	}
	const auto reply = socket.read(2);
	// Оба байта, а не только версия: `05 FF` означает SOCKS5, но без
	// принимаемого метода (в нашем случае no-auth), и клиент по такому порту
	// не пойдёт. Для переиспользования нужен именно `05 00`.
	return (reply.size() == 2) && (reply.at(0) == char(0x05))
		&& (reply.at(1) == char(kSocksNoAuth));
}

[[nodiscard]] MTP::ProxyData MakeSocksProxy(quint16 port) {
	auto proxy = MTP::ProxyData();
	proxy.type = MTP::ProxyData::Type::Socks5;
	proxy.host = u"127.0.0.1"_q;
	proxy.port = port;
	return proxy;
}

// Ждёт, пока на порту поднимется именно SOCKS5, либо пока процесс не умрёт.
// Голый TCP-connect недостаточен: порт может принимать соединения раньше, чем
// прокси начнёт отвечать, а чужой сервис на том же порту принял бы клиента.
[[nodiscard]] bool WaitReady(qint64 pid, quint16 port) {
	auto elapsed = QElapsedTimer();
	elapsed.start();
	while (elapsed.elapsed() < kStartupWaitMs) {
		if (!ProcessAlive(pid)) {
			return false;
		}
		if (ProbeSocks5(port)) {
			return true;
		}
	}
	return false;
}

// Снимает переопределение прокси и, если процесс поднимали мы, гасит его.
void teardown() {
	if (g_pid > 0) {
		TerminateProcessById(g_pid);
		g_pid = 0;
	}
	Core::App().setDpiProxyOverride(std::nullopt);
	g_applied = false;
}

// Поднимает прокси и ставит переопределение. Возвращает успех.
[[nodiscard]] bool bringUp(quint16 port, bool useWebSocket, const QString &strategy) {
	// Порт уже слушает SOCKS5: это мог быть прокси, поднятый прошлым запуском
	// клиента и переживший его. Переиспользуем, а не поднимаем второй.
	if (ProbeSocks5(port)) {
		Core::App().setDpiProxyOverride(MakeSocksProxy(port));
		g_applied = true;
		return true;
	}

	const auto executable = ExecutablePath();
	if (executable.isEmpty()) {
		g_error = u"ayu-dpi.exe not found next to the client"_q;
		return false;
	}

	auto arguments = QStringList{
		u"--listen"_q,
		u"127.0.0.1:%1"_q.arg(port),
	};
	if (useWebSocket) {
		arguments.push_back(u"--ws"_q);
	}
	arguments.push_back(u"--config"_q);
	arguments.push_back(strategy.isEmpty() ? QString(kDefaultStrategy) : strategy);

	qint64 pid = 0;
	const auto launched = QProcess::startDetached(
		executable,
		arguments,
		QFileInfo(executable).absolutePath(),
		&pid);
	if (!launched || pid <= 0) {
		g_error = u"failed to launch ayu-dpi.exe"_q;
		return false;
	}

	if (!WaitReady(pid, port)) {
		TerminateProcessById(pid);
		g_error = u"ayu-dpi did not start listening on the port"_q;
		return false;
	}

	g_pid = pid;
	Core::App().setDpiProxyOverride(MakeSocksProxy(port));
	g_applied = true;
	return true;
}

} // namespace

void applyFromSettings() {
	if (!Core::IsAppLaunched()) {
		return;
	}

	const auto &settings = AyuSettings::getInstance();
	g_error.clear();

	if (!settings.useDpiBypass()) {
		if (g_applied) {
			teardown();
		}
		return;
	}

	if (g_applied) {
		// Уже поднят. Настройки порта и режима меняются только вместе с
		// перезапуском клиента: перезапускать процесс на каждое движение
		// переключателя незачем.
		return;
	}

	const auto port = settings.dpiPort();
	if (port <= 0 || port > 65535) {
		g_error = u"invalid port in settings"_q;
		return;
	}

	// Результат важен только для lastError, который bringUp уже заполняет.
	// g_applied выставляется внутри на успехе, здесь он не нужен.
	(void)bringUp(
		static_cast<quint16>(port),
		settings.dpiUseWebSocket(),
		settings.dpiStrategy().trimmed());
}

void stop() {
	if (g_pid > 0) {
		TerminateProcessById(g_pid);
		g_pid = 0;
	}
	// Переопределение тут не снимается: stop() зовётся на завершении клиента,
	// когда трогать прокси уже нечего, а Core::App в этот момент разрушается.
	// Гасим только процесс, чтобы он не пережил клиент и не занял порт.
	g_applied = false;
}

bool running() {
	return g_applied;
}

const QString &lastError() {
	return g_error;
}

} // namespace Ayu::Dpi
