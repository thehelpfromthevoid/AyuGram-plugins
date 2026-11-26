// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/features/dpi/ayu_dpi.h"
#include "ayu/features/dpi/ayu_dpi_state.h"

#include "ayu/ayu_settings.h"
#include "base/debug_log.h"
#include "core/application.h"
#include "settings.h"

#include <QtCore/QDir>
#include <QtCore/QElapsedTimer>
#include <QtCore/QFileInfo>
#include <QtCore/QProcess>
#include <QtCore/QStringList>
#include <QtNetwork/QTcpSocket>

#include <array>

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

// Где лежит запись владения портом. Рядом с рабочим каталогом клиента, чтобы
// находить собственного отцепившегося прокси, а не только что поднятого в
// этом процессе.
[[nodiscard]] std::filesystem::path OwnershipPath() {
	const auto path = QDir(cWorkingDir()).filePath(u"ayu-dpi.port"_q);
	return std::filesystem::path(path.toStdWString());
}

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

// Полный путь к образу процесса. Нужен, чтобы отличить собственный
// отцепившийся ayu-dpi.exe от чужого сервиса на том же порту, а не полагаться
// на один лишь номер: номера переиспользуются системой.
[[nodiscard]] QString ProcessImagePath(qint64 pid) {
#ifdef Q_OS_WIN
	if (pid <= 0) {
		return QString();
	}
	const auto handle = OpenProcess(
		PROCESS_QUERY_LIMITED_INFORMATION,
		FALSE,
		static_cast<DWORD>(pid));
	if (!handle) {
		return QString();
	}
	auto buffer = std::array<WCHAR, 1024>();
	auto size = DWORD(buffer.size());
	const auto ok = QueryFullProcessImageNameW(handle, 0, buffer.data(), &size);
	CloseHandle(handle);
	return ok ? QString::fromWCharArray(buffer.data(), static_cast<int>(size)) : QString();
#else
	Q_UNUSED(pid);
	return QString();
#endif
}

// Наш ли это прокси: имя процесса `ayu-dpi.exe` и каталог совпадают с
// каталогом клиента. Проверка образа, а не одного номера, потому что по
// номеру можно и переиспользовать чужой процесс, и погасить его на teardown.
[[nodiscard]] bool IsOurProxy(qint64 pid) {
	const auto image = ProcessImagePath(pid);
	if (image.isEmpty()) {
		return false;
	}
	const auto ours = QDir(cExeDir()).filePath(u"ayu-dpi.exe"_q);
	return QFileInfo(image) == QFileInfo(ours);
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

// Слагами опций владеет Rust (ayu-core::dpi::stealth), поэтому здесь только
// имена, а не таблица трансляции значений. Число профиля приходит из настроек
// в порядке ProxyTlsProfile из proxy_data.h.
[[nodiscard]] QString TlsProfileSlug(int profile) {
	switch (profile) {
	case 1: return u"firefox"_q;
	case 2: return u"android_chrome"_q;
	case 3: return u"yandex"_q;
	case 4: return u"firefox_android"_q;
	case 5: return u"android_okhttp"_q;
	case 6: return u"auto_rotate"_q;
	case 7: return u"chrome_modern"_q;
	default: return QString(); // 0 Auto и всё вне диапазона: пусть решает Rust
	}
}

// Собирает `--stealth KEY=VALUE` из сохранённых тумблеров. Пустой список, когда
// всё выключено: тогда аргументов нет вовсе, и процесс, который этих ключей ещё
// не знает (старая сборка ayu-dpi.exe), продолжает работать как раньше. Ключ
// отправляется, только если он что-то меняет от дефолта Rust, иначе это шум и
// лишний способ уронить запуск на несовпадении версий.
[[nodiscard]] QStringList StealthArguments(const AyuSettings &settings) {
	auto out = QStringList();
	const auto add = [&](const QString &key, const QString &value) {
		out.push_back(u"--stealth"_q);
		out.push_back(key + u"="_q + value);
	};

	if (settings.dpiStealthFragmentClientHello()) {
		add(u"client_hello_fragmentation"_q, u"soft"_q);
	}
	if (settings.dpiStealthSpreadConnections()) {
		add(u"connection_pattern"_q, u"soft"_q);
	}
	if (settings.dpiStealthMaskStart()) {
		add(u"startup_cover"_q, u"soft"_q);
	}
	if (settings.dpiStealthVaryRecordSizes()) {
		add(u"record_sizing"_q, u"conservative"_q);
	}
	if (settings.dpiStealthPaceTraffic()) {
		add(u"timing"_q, u"gentle"_q);
	}
	if (settings.dpiStealthSyntheticPsk()) {
		add(u"synthetic_psk"_q, u"on"_q);
	}
	const auto profile = TlsProfileSlug(settings.dpiStealthTlsProfile());
	if (!profile.isEmpty()) {
		add(u"tls_profile"_q, profile);
	}
	// Пользовательский релей отправляется, только когда он задан: пустые
	// значения означали бы "официальный фронт", который и так дефолт Rust, а
	// лишний ключ - это лишний способ не запуститься на несовпадении версий.
	const auto wssHost = settings.dpiStealthWssHost().trimmed();
	if (!wssHost.isEmpty()) {
		add(u"wss_host"_q, wssHost);
		const auto wssPort = settings.dpiStealthWssPort();
		if (wssPort > 0) {
			add(u"wss_port"_q, QString::number(wssPort));
		}
		const auto wssPath = settings.dpiStealthWssPath().trimmed();
		if (!wssPath.isEmpty()) {
			add(u"wss_path"_q, wssPath);
		}
		const auto wssDomain = settings.dpiStealthWssDomain().trimmed();
		if (!wssDomain.isEmpty()) {
			add(u"wss_domain"_q, wssDomain);
		}
	}
	return out;
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
	// Запись владения уходит вместе с процессом: оставленный файл заставил бы
	// следующий запуск принять чужой сервис на этом порту за наш прокси.
	State::Remove(OwnershipPath());
	Core::App().setDpiProxyOverride(std::nullopt);
	g_applied = false;
}

// Поднимает прокси и ставит переопределение. Возвращает успех.
[[nodiscard]] bool bringUp(quint16 port, bool useWebSocket, const QString &strategy) {
	// Порт уже слушает SOCKS5. Переиспользуем его только тогда, когда запись
	// владения доказывает, что это наш отцепившийся прокси: любой локальный
	// SOCKS5 отвечает тем же приветствием, и молчаливый увод клиента в чужой
	// прокси - это обход, который ничего не обходит.
	if (ProbeSocks5(port)) {
		const auto owner = State::Parse(State::Read(OwnershipPath()));
		if (owner.valid && owner.port == port
			&& ProcessAlive(owner.pid)
			&& IsOurProxy(owner.pid)) {
			// Процесс доказанно наш, поэтому берём его под управление: иначе
			// teardown/stop его не погасят, и он останется висеть на порту
			// после выключения тумблера.
			g_pid = owner.pid;
			Core::App().setDpiProxyOverride(MakeSocksProxy(port));
			g_applied = true;
			return true;
		}
		g_error = u"port %1 is already serving SOCKS5, but not by our proxy"_q.arg(port);
		return false;
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
	arguments.append(StealthArguments(AyuSettings::getInstance()));

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
	// Запись владения: по ней следующий запуск отличит наш отцепившийся
	// прокси от чужого сервиса на том же порту.
	State::Write(OwnershipPath(), pid, port);
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
		LOG(("AyuDpi: %1; bypass not started").arg(g_error));
		return;
	}

	// Результат bringUp сохранён в g_error; сама неудача больше не молчит: без
	// записи в журнал выключенный обход выглядел бы как работающий.
	if (!bringUp(
			static_cast<quint16>(port),
			settings.dpiUseWebSocket(),
			settings.dpiStrategy().trimmed())) {
		LOG(("AyuDpi: %1; bypass not started").arg(g_error));
	}
}

void stop() {
	if (g_pid > 0) {
		TerminateProcessById(g_pid);
		g_pid = 0;
	}
	// Переопределение тут не снимается: stop() зовётся на завершении клиента,
	// когда трогать прокси уже нечего, а Core::App в этот момент разрушается.
	// Гасим только процесс, чтобы он не пережил клиент и не занял порт.
	State::Remove(OwnershipPath());
	g_applied = false;
}

void restartFromSettings() {
	if (!Core::IsAppLaunched()) {
		return;
	}
	// Смена стелса/транспорта читается процессом только при подъёме, поэтому
	// поднятый процесс гасится вместе с переопределением (teardown это делает)
	// и поднимается заново уже с новыми аргументами. Когда обход выключен,
	// g_applied ложь, teardown не зовётся, и applyFromSettings просто ничего не
	// поднимает. Так один сеттер обслуживает и включение, и смену политики.
	if (g_applied) {
		teardown();
	}
	applyFromSettings();
}

bool running() {
	return g_applied;
}

const QString &lastError() {
	return g_error;
}

} // namespace Ayu::Dpi
