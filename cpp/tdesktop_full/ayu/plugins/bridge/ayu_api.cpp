// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
//
// AyuGram plugin host for Desktop, (c) @CE0fbi
#ifdef DESKTOP_APP_WITH_PLUGINS
#include "ayu/plugins/bridge/python_bridge.h"

#include "api/api_common.h"
#include "api/api_editing.h"
#include "apiwrap.h"
#include "ayu/libs/json.hpp"
#include "ayu/plugins/event_bus.h"
#include "ayu/plugins/hook_points.h"
#include "ayu/plugins/plugins_host.h"
#include "ayu/plugins/bridge/ayu_style_table.h"
#include "ayu/plugins/plugins_manager.h"
#include "ayu/utils/telegram_helpers.h"
#include "crl/crl_async.h"
#include "core/application.h"
#include "data/data_drafts.h"
#include "data/data_peer.h"
#include "data/data_session.h"
#include "history/history.h"
#include "history/history_item.h"
#include "main/main_account.h"
#include "main/main_domain.h"
#include "main/main_session.h"
#include "settings/settings_recent_searches.h"
#include "storage/localimageloader.h"
#include "ui/chat/attach/attach_prepare.h"
#include "ui/text/text_entity.h"
#include "ui/toast/toast.h"
#include "window/window_controller.h"

#include <QtCore/QCoreApplication>
#include <QtCore/QDateTime>
#include <QtCore/QDir>
#include <QtCore/QFile>
#include <QtCore/QFileInfo>
#include <QtCore/QJsonArray>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QMimeDatabase>
#include <QtGui/QClipboard>
#include <QtGui/QGuiApplication>

#include <atomic>
#include <set>
#include <chrono>
#include <thread>

#include <pybind11/embed.h>
#include <pybind11/stl.h>

namespace py = pybind11;

namespace Ayu::Plugins::Bridge {
namespace {

constexpr auto kPluginLogMaxBytes = std::int64_t(256 * 1024);
constexpr auto kPluginLogKeepBytes = std::int64_t(192 * 1024);

void AppendCapped(const QString &path, const QString &text) {
	try {
		auto file = QFile(path);
		if (file.exists() && file.size() > kPluginLogMaxBytes) {
			// Keep the tail: the newest errors are the useful ones.
			if (file.open(QIODevice::ReadOnly)) {
				file.seek(file.size() - kPluginLogKeepBytes);
				const auto tail = file.readAll();
				file.close();
				if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
					file.write(tail);
				}
			}
		}
		if (file.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
			const auto line = QDateTime::currentDateTime().toString(
				u"yyyy-MM-dd hh:mm:ss "_q) + text + u"\n"_q;
			file.write(line.toUtf8());
		}
	} catch (...) {
	}
}

void BootLog(const QString &text) {
	AppendCapped(
		QCoreApplication::applicationDirPath() + u"/ayu_plugins_boot.log"_q,
		text);
}

[[nodiscard]] QString PluginErrorLogPath(const QString &pluginId) {
	return Manager::instance().pluginLogPath(pluginId);
}

using SettingPair = std::pair<std::string, std::string>;

[[nodiscard]] std::vector<SettingPair> ReadSettingsJson(const QByteArray &data) {
	auto out = std::vector<SettingPair>();
	try {
		auto j = nlohmann::json::parse(data.toStdString());
		if (!j.is_object()) {
			return out;
		}
		auto values = j.contains("settings") && j["settings"].is_object()
			? j["settings"]
			: j;
		for (const auto &[k, v]: values.items()) {
			if (v.is_string()) {
				out.emplace_back(k, v.get<std::string>());
			} else if (v.is_boolean()) {
				out.emplace_back(k, v.get<bool>() ? "true" : "false");
			} else if (v.is_number()) {
				out.emplace_back(k, std::to_string(v.get<double>()));
			} else if (!v.is_null() && !v.is_object() && !v.is_array()) {
				out.emplace_back(k, v.dump());
			}
		}
	} catch (...) {
	}
	return out;
}


// Every plugin gets its own <id>.log next to the other plugin files, so a
// broken plugin can be diagnosed without reading the whole application log.
void PluginLog(const QString &pluginId, const QString &text) {
	if (pluginId.isEmpty()) {
		AppendCapped(
			QCoreApplication::applicationDirPath() + u"/ayu_plugins_boot.log"_q,
			text);
		return;
	}
	AppendCapped(PluginErrorLogPath(pluginId), text);
}

std::unique_ptr<py::scoped_interpreter> g_guard;
std::map<QString, py::object> g_modules;
// Its own lock, and never g_mutex. The hook path takes g_mutex and then waits
// for the GIL, so anything that holds the GIL and then wants g_mutex deadlocks
// the whole app with no crash and no log line. LoadPlugin holds the GIL while
// it registers a module, so the map needed a lock that is never held across a
// wait for the GIL. Readers take it, copy what they need, and release it before
// touching the interpreter.
std::mutex g_modulesMutex;
std::mutex g_mutex;
// The embedded ayu_api is the only module with the real send_text and
// edit_message. The pure-Python stand-in used by the command line tools
// defines toast, log and hook but not those two, so a plugin that ends up
// bound to the stand-in silently loses the ability to send anything. Keeping
// the real module here lets LoadPlugin put it back and name the culprit.
py::object g_apiModule;
// Set once the app starts closing: after this, nothing may enter Python,
// because a plugin thread that is still inside the interpreter would fault
// when it is released.
std::atomic<bool> g_shuttingDown{false};
// True while the main thread is inside a hook, where a plugin must not
// be made to wait for the GIL.
std::atomic<bool> g_hookHoldsMainThread{false};
// Собственные peer id аккаунтов. Кэш обновляется на главном потоке, читается без
// ожидания: ждать главный поток из плагинного нельзя, вызывающий держит GIL,
// а главному потоку он нужен, чтобы вернуться в Python. Такая взаимоблокировка
// вешала приложение целиком.
//
// Индекс здесь - номер аккаунта, а не позиция в std::map: позиция меняется при
// добавлении и удалении аккаунтов, и плагин, запомнивший "свой" номер, вдруг
// начал бы писать в чужой диалог.
std::map<int, int64_t> g_selfPeerIds;

int64_t SelfPeerIdFor(int account) {
	// -1 и любой отрицательный знак это «основной аккаунт». Так его понимает
	// и Desktop (maybePrimarySession), и плагин, написанный до появления
	// второго аккаунта: он зовёт self_peer_id() без аргумента и должен
	// продолжать получать свой диалог, а не ноль.
	if (account < 0) {
		if (g_selfPeerIds.empty()) {
			return 0;
		}
		return g_selfPeerIds.begin()->second;
	}
	const auto i = g_selfPeerIds.find(account);
	return (i == g_selfPeerIds.end()) ? int64_t(0) : i->second;
}

void RefreshSelfPeerIds() {
	if (g_shuttingDown.load()) {
		return;
	}
	std::map<int, int64_t> fresh;
	for (const auto &entry : Core::App().domain().accounts()) {
		const auto *account = entry.account.get();
		if (!account) {
			continue;
		}
		if (const auto session = account->maybeSession()) {
			fresh[int(entry.index)] = int64_t(session->userPeerId().value);
		}
	}
	g_selfPeerIds = std::move(fresh);
}

// Обновляет кэш и возвращает id своего аккаунта.
int64_t RefreshSelfPeerId(int account) {
	RefreshSelfPeerIds();
	return SelfPeerIdFor(account);
}
QString g_shimDir;
thread_local QString g_loadingPluginId;
std::map<QString, int> g_hookErrors;
std::map<QString, QString> g_hookIssues;

// Сколько раз сработал хук плагина и какие клиентские вызовы он делал.
//
// Нужно, чтобы отличить плагин, который работает, от плагина, который
// загрузился и молчит. Раньше оба выглядели одинаково: в списке есть
// переключатель, в журнале "load ok", и никакого признака, что за всю
// сессию не произошло ничего. Пользователь видел включённый плагин и
// ждал эффекта, которого не будет никогда.
std::map<QString, int64_t> g_hookCalls;
std::map<QString, std::set<QString>> g_apiCalls;
std::mutex g_activityMutex;

// Запоминает, что плагин что-то сделал. Зовётся из горячего пути, поэтому
// дешёвое: одна запись в map под коротким замком.
void NotePluginActivity(
	const QString &pluginId,
	const char *hookName,
	const char *apiName = nullptr) {
	if (pluginId.isEmpty()) {
		return;
	}
	const auto guard = std::lock_guard(g_activityMutex);
	++g_hookCalls[pluginId];
	if (apiName) {
		g_apiCalls[pluginId].insert(QString::fromLatin1(apiName));
	}
}

struct PluginActivity {
	int64_t hookCalls = 0;
	std::set<QString> apiNames;
};

// Сколько хуков плагин вообще подписал. Считается при регистрации, и по
// нулю видно, что плагин не подписан ни на что: подписка без единого
// вызова - это и есть "загрузился и молчит".
std::map<QString, int> g_hookSubscriptions;

void NoteHookSubscription(const QString &pluginId, const QString &hookName) {
	if (pluginId.isEmpty()) {
		return;
	}
	const auto guard = std::lock_guard(g_activityMutex);
	++g_hookSubscriptions[pluginId];
}

PluginActivity ActivityOf(const QString &pluginId) {
	const auto guard = std::lock_guard(g_activityMutex);
	auto out = PluginActivity();
	const auto calls = g_hookCalls.find(pluginId);
	if (calls != g_hookCalls.end()) {
		out.hookCalls = calls->second;
	}
	const auto api = g_apiCalls.find(pluginId);
	if (api != g_apiCalls.end()) {
		out.apiNames = api->second;
	}
	return out;
}

int HookSubscriptionCount(const QString &pluginId) {
	const auto guard = std::lock_guard(g_activityMutex);
	const auto i = g_hookSubscriptions.find(pluginId);
	return (i == g_hookSubscriptions.end()) ? 0 : i->second;
}
// Plugins muted by the slow-hook breaker: still loaded, so nothing Python-side
// is destroyed, but their hooks stop being called.
std::set<QString> g_mutedPlugins;
// ayu_api names plugins asked for that Desktop has no implementation of.
// Its own mutex, and deliberately not g_mutex: this set is written while
// holding the GIL, and the hook path takes g_mutex and then waits for the GIL.
// Sharing the lock would let those two invert and hang the app.
std::set<std::string> g_apiGaps;
// Which hook each plugin actually subscribed to. A plugin that registers
// nothing can never affect anything, and that used to be indistinguishable
// from a plugin whose triggers simply never fired.
std::map<std::string, std::set<std::string>> g_pluginHooks;

void RunOnMainThread(const py::function &fn);
QString HookIdName(HookId id);
void RecordHook(const QString &pluginId, const QString &hook);
void ReportHooks(const QString &pluginId);
std::mutex g_apiGapsMutex;
// Filled once at startup so the plugin check can explain a missing library.
// Same reasoning as g_apiGaps: written with the GIL held, read from the UI
// thread, so it gets its own lock rather than the one the hooks wait on.
QStringList g_runtimeProblems;
std::mutex g_runtimeProblemsMutex;
constexpr int kMaxHookErrors = 12;

[[nodiscard]] bool HookMuted(const QString &pluginId) {
	const auto lock = std::lock_guard(g_mutex);
	return g_mutedPlugins.count(pluginId) > 0;
}

void NoteHookError(const QString &pluginId, const QString &detail) {
	if (pluginId.isEmpty()) {
		LOG(("AyuPlugins: hook error: %1").arg(detail.left(240)));
		PluginLog(pluginId, u"hook error: "_q + detail);
		return;
	}
	g_hookIssues[pluginId] = detail;
	auto &count = g_hookErrors[pluginId];
	++count;
	LOG(("AyuPlugins: hook error in %1 (%2): %3").arg(
		pluginId,
		QString::number(count),
		detail.left(240)));
	PluginLog(pluginId, u"hook error #"_q
		+ QString::number(count)
		+ u": "_q
		+ detail);
	if (count == kMaxHookErrors) {
		LOG(("AyuPlugins: auto-disabling plugin %1 after repeated hook errors"
			).arg(pluginId));
		PluginLog(pluginId,
			u"auto-disabled after "_q + QString::number(kMaxHookErrors)
			+ u" hook errors");
		crl::on_main([pluginId] {
			Manager::instance().disable(pluginId);
		});
	}
}

[[nodiscard]] bool HookBudgetExceeded(const QString &pluginId) {
	if (pluginId.isEmpty()) {
		return false;
	}
	const auto it = g_hookErrors.find(pluginId);
	return it != g_hookErrors.end() && it->second >= kMaxHookErrors;
}

// Shared body: acquires the GIL and runs the call, reporting any failure.
// Called with the GIL already held. Acquiring it here as well used to hide
// where the time went: the wait was counted against whichever plugin ran
// while another one held the lock.
template <typename Call>
void SafeCallHookBody(const QString &pluginId, Call &&call) {
	try {
		if (g_shuttingDown.load()) {
			return;
		}
		try {
			call();
		} catch (py::error_already_set &e) {
			NoteHookError(pluginId, QString::fromUtf8(e.what()));
			e.restore();
			PyErr_Clear();
		} catch (const std::exception &e) {
			NoteHookError(pluginId, QString::fromUtf8(e.what()));
		} catch (...) {
			NoteHookError(pluginId, u"unknown C++ exception in hook"_q);
		}
	} catch (const std::exception &e) {
		NoteHookError(pluginId, QString::fromUtf8(e.what()));
	} catch (...) {
		NoteHookError(pluginId, u"gil/hook failure"_q);
	}
}

// Hook tracing exists so that a plugin which never returns can be named from
// the log alone: the last line written before the window stops responding is
// the `enter` record of the call that is stuck. on_message_sending is the only
// hook that runs while the UI must stay responsive, so it is always traced; the
// high frequency hooks are traced only when they turn out to be slow, which
// keeps a busy account from flooding the log.
constexpr auto kHookSlow = std::chrono::milliseconds(250);
// A hook runs while holding the interpreter lock, so a plugin that blocks
// there does not just slow itself down: every other plugin, and any main
// thread entry into Python, waits behind it. One plugin spending seconds in
// a hook is enough to make the window look hung. Treat that as a fault rather
// than waiting it out, because nothing in-process can interrupt a plugin
// that has already blocked.
constexpr auto kHookHang = std::chrono::seconds(3);

// Called with the GIL held. Puts the embedded ayu_api back into sys.modules
// when something replaced it, and reports where the replacement came from,
// because the symptom is otherwise invisible: the plugin simply finds no
// send_text and quietly stops being able to send anything.
void VerifyApiModule(const char *when) {
	if (!g_apiModule) {
		return;
	}
	auto sys = py::module_::import("sys");
	auto modules = sys.attr("modules");
	const auto current = py::reinterpret_borrow<py::object>(
		modules[py::str("ayu_api")]);
	if (current.is_none()) {
		return;
	}
	if (current.is(g_apiModule)) {
		return;
	}
	auto file = current.attr("__file__");
	auto from = file.is_none()
		? QString::fromUtf8("<no __file__>")
		: QString::fromUtf8(file.cast<std::string>());
	LOG(("AyuPlugins: ayu_api was replaced (%1) by %2; restoring the built-in")
		.arg(QString::fromUtf8(when), from.left(160)));
	PluginLog(QString::fromUtf8(when),
		u"ayu_api replaced by "_q + from.left(160) + u"; restored"_q);
	modules[py::str("ayu_api")] = py::reinterpret_borrow<py::object>(g_apiModule);
}

// Which plugin each thread is currently running, so a fatal exception can name
// it without touching the interpreter.
//
// Asking the interpreter from the crash handler does not work: the faulting
// thread usually already holds the GIL, so acquiring it there hangs, and every
// attempt came back empty. This map is written while hooks run, so the handler
// only has to look the thread id up - no locks, no interpreter.
std::mutex g_threadOwnerMutex;
std::map<DWORD, std::string> g_threadOwner;

void RememberThreadOwner(const QString &pluginId) {
	const auto guard = std::lock_guard(g_threadOwnerMutex);
	g_threadOwner[::GetCurrentThreadId()] = pluginId.toStdString();
}

QString ThreadOwnerName() {
	const auto guard = std::lock_guard(g_threadOwnerMutex);
	const auto it = g_threadOwner.find(::GetCurrentThreadId());
	if (it == g_threadOwner.end() || it->second.empty()) {
		return QString();
	}
	return QString::fromStdString(it->second);
}

template <typename Call>
void SafeCallHookTraced(
	const QString &pluginId,
	const char *hookName,
	bool always,
	Call &&call) {
	auto hold = py::gil_scoped_acquire();
	if (g_shuttingDown.load()) {
		return;
	}
	// Start the clock only once the interpreter is ours. Timing the acquisition
	// charged whoever queued behind a slow plugin for that plugin's time:
	// a two-line toast handler was reported as blocking for 22 seconds and
	// disabled, while the plugin actually holding the lock went unnamed.
	const auto started = std::chrono::steady_clock::now();
	RememberThreadOwner(pluginId);
	// Отмечается до вызова: плагин, который упал внутри хука, тоже
	// работал, и его нельзя показывать молчащим.
	NotePluginActivity(pluginId, hookName);
	if (always && !pluginId.isEmpty()) {
		LOG(("AyuPlugins: hook enter %1 %2").arg(pluginId, hookName));
	}
	SafeCallHookBody(pluginId, std::forward<Call>(call));
	const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
		std::chrono::steady_clock::now() - started);
	if (elapsed < kHookSlow) {
		return;
	}
	LOG(("AyuPlugins: slow hook %1 %2 took %3ms").arg(
		pluginId,
		hookName,
		QString::number(elapsed.count())));
	g_hookIssues[pluginId] = QString::fromUtf8(hookName)
		+ u" took "_q + QString::number(elapsed.count()) + u"ms"_q;
	PluginLog(pluginId,
		u"slow hook: "_q + hookName + u" took "_q
			+ QString::number(elapsed.count()) + u"ms"_q);
	if (elapsed < kHookHang || pluginId.isEmpty()) {
		return;
	}
	const auto detail = u"disabled after "_q + hookName + u" blocked for "_q
		+ QString::number(elapsed.count()) + u"ms"_q;
	LOG(("AyuPlugins: %1 %2").arg(pluginId, detail));
	PluginLog(pluginId, detail);
	NoteHookError(pluginId, u"hook blocked for too long"_q);
	// Mute, do not unload. Unloading drops the module's last reference while
	// its threads may still be running inside it, and freeing those objects
	// faults inside the interpreter (observed as an access violation in
	// python314.dll). Muting needs no Python object to be destroyed.
	crl::on_main([pluginId, detail] {
		// The reason travels with the mute so the plugin list can say why the
		// hooks stopped. Without it a plugin muted by this breaker is
		// indistinguishable from one the user switched off, or one that broke.
		Manager::instance().mute(pluginId, detail);
	});
}

template <typename Call>
void SafeCallHook(
	const QString &pluginId,
	const char *hookName,
	Call &&call) {
	if (g_shuttingDown.load() || HookBudgetExceeded(pluginId)
		|| HookMuted(pluginId)) {
		return;
	}
	try {
		// on_message_sending runs on the main thread with the window
		// blocked. If a plugin thread is inside Python right now, waiting
		// for the GIL here freezes the app, and a plugin that waits on the
		// main thread while holding it deadlocks both. Skip the hook
		// instead: the message goes out unchanged, which is far better than
		// an unresponsive window.
		if (g_hookHoldsMainThread.load() && !PyGILState_Check()) {
			return;
		}
		SafeCallHookTraced(pluginId, hookName,
			qstrcmp(hookName, "on_message_sending") == 0,
			std::move(call));
	} catch (const std::exception &e) {
		NoteHookError(pluginId, QString::fromUtf8(e.what()));
	} catch (...) {
		NoteHookError(pluginId, u"gil/hook failure"_q);
	}
}

[[nodiscard]] HookId ResolveHookId(const QString &name, bool *ok) {
	auto id = HookId::OnMessage;
	if (HookIdFromString(name, id)) {
		if (ok) *ok = true;
		return id;
	}
	const auto lower = name.toLower();
	if ((lower.contains(u"send"_q) && lower.contains(u"message"_q))
		|| lower.contains(u"before_send"_q)) {
		if (ok) *ok = true;
		return HookId::OnMessageSending;
	}
	if (lower.contains(u"delete"_q) && lower.contains(u"message"_q)) {
		if (ok) *ok = true;
		return HookId::OnMessageDeleted;
	}
	if (lower.contains(u"chat_opened"_q) || lower.contains(u"open_chat"_q)) {
		if (ok) *ok = true;
		return HookId::OnChatOpened;
	}
	if (lower.contains(u"typing"_q)) {
		if (ok) *ok = true;
		return HookId::OnTyping;
	}
	if (lower.contains(u"media"_q) && lower.contains(u"send"_q)) {
		if (ok) *ok = true;
		return HookId::OnMediaSending;
	}
	if (ok) *ok = false;
	return HookId::OnMessage;
}

void ToastOnMain(const std::string &text) {
	if (g_shuttingDown.load()) {
		return;
	}
	static auto lastAt = crl::time(0);
	static auto lastText = QString();
	static auto burst = 0;
	const auto q = QString::fromStdString(text);
	const auto now = crl::now();
	if (q == lastText && (now - lastAt) < crl::time(750)) {
		return;
	}
	if ((now - lastAt) < crl::time(200)) {
		++burst;
		if (burst > 8) {
			return;
		}
	} else {
		burst = 0;
	}
	lastAt = now;
	lastText = q;
	crl::on_main([q] {
		if (const auto window = Core::App().activeWindow()) {
			Ui::Toast::Show(window->widget(), { .text = { q } });
		}
	});
}

// Resolving the peer a plugin is talking about.
//
// Plugins get their peer id from the hook context and hand it straight back to
// send_text, but the two are not the same number in every case: some ids fit
// in ID, some do not, some arrive as the chat id behind a group, and a plugin
// written for a phone may subtract or mask the account offset. Truncating to
// ID, or rejecting the value outright, meant a plugin matched the command and
// its reply was dropped with only "unknown peer" in the log.
//
// Модуль числа без знака: идентификаторы диалогов бывают отрицательными
// (группы), и их нельзя сравнивать как знаковые числа.
[[nodiscard]] uint64 magnitudeOf(int64 value) {
	return static_cast<uint64>(value < 0 ? -value : value);
}

// Определена ниже; здесь нужно объявление, потому что поиск сессии
// пользуется ей раньше, чем она описана.
PeerData *ResolvePeerForPlugin(int64 peerId, Main::Session *session);

// Ищет сессию и чат одним куском.
//
// Аккаунт указывают не все плагины: старые о нём не знают и отвечают без
// него. Раньше такой ответ уходил в сессию нулевого аккаунта, где этого
// чата нет, и молча пропадал - то есть плагин работал на первом аккаунте и
// не работал на втором. Здесь сначала пробуется названный аккаунт, а если
// не вышло - все остальные по очереди, и ответ уходит туда, где чат есть.
struct ResolvedTarget {
	Main::Session *session = nullptr;
	PeerData *peer = nullptr;
	int account = -1;
};

[[nodiscard]] ResolvedTarget ResolveTarget(int64 peerId, int account) {
	auto session = Ayu::Plugins::Hooks::SessionForAccount(account);
	auto peer = session ? ResolvePeerForPlugin(peerId, session) : nullptr;
	if (peer) {
		return { session, peer, account };
	}
	const auto count = Ayu::Plugins::Hooks::AuthedAccountCount();
	for (auto candidate = 0; candidate != count; ++candidate) {
		if (candidate == account) {
			continue;
		}
		const auto other = Ayu::Plugins::Hooks::SessionForAccount(candidate);
		if (!other) {
			continue;
		}
		if (auto found = ResolvePeerForPlugin(peerId, other)) {
			LOG(("AyuPlugins: peer %1 belongs to account %2")
				.arg(peerId)
				.arg(candidate));
			return { other, found, candidate };
		}
	}
	return {};
}

// So every plausible reading is tried and the one that names a real peer wins.
//
// `session` matters and used to be missing here. The dialog id embeds the
// account, but the lookup that follows it - getPeerFromDialogId - searches
// one session, not all of them. With two accounts logged in, a reply to a
// chat of the second one found nothing, logged "unknown peer" and went no
// further: the plugin looked switched off exactly on that account. Resolving
// through the session first is what makes the reply land.
PeerData *ResolvePeerForPlugin(
		int64 peerId,
		Main::Session *session = nullptr) {
	if (!peerId) {
		return nullptr;
	}
	// The account's own session is asked first: the global helper walks all
	// accounts and hands the raw dialog id to accessors that expect a peer
	// id, which lands on the right peer only for the first account. On the
	// others a plugin's reply found nothing and vanished with nothing but
	// "unknown peer" in the log - which is why plugins looked switched off
	// on every account but the first.
	if (session) {
		if (const auto found = getPeerFromDialogId(
				static_cast<unsigned long long>(peerId),
				session)) {
			return found;
		}
	}
	if (const auto direct = getPeerFromDialogId(
			static_cast<uint64>(peerId))) {
		return direct;
	}
	// A group or channel arrives either as a bare id or with Telegram's
	// -100... prefix; plugins have been seen passing both.
	const auto magnitude = magnitudeOf(peerId);
	if (magnitude != static_cast<uint64>(peerId)) {
		if (const auto absolute = getPeerFromDialogId(magnitude)) {
			return absolute;
		}
	}
	constexpr auto kChannelPrefix = 1000000000000ull;
	if (magnitude > kChannelPrefix) {
		if (const auto bare = getPeerFromDialogId(
				magnitude - kChannelPrefix)) {
			return bare;
		}
	}
	// Last resort: a dialog id that only differs from a bare peer id by the
	// account/offset bits a plugin may have kept.
	if (const auto masked = getPeerFromDialogId(
			static_cast<uint64>(peerId) & 0xFFFFFFFFull)) {
		return masked;
	}
	return nullptr;
}

// Returns 1 when the text was accepted for delivery, 0 when it was rejected
// outright.
//
// It used to return void, so Python saw None and a plugin that checked the
// result - `if not send_text(peer, text): report("failed")` - always concluded
// the send had failed, on every command, even though the message was queued and
// delivered. Plugins use that return as their success signal, so it has to mean
// something.
//
// The value is not a message id: the send is handed to the main thread and this
// API has no way to learn the id Telegram ends up assigning. 1 therefore means
// "queued", and a plugin that needs the id still cannot have one.
int SendTextOnMain(
		int64 peerId,
		const std::string &text,
		int64 replyToMsgId = 0,
		int64 replyToTopMsgId = 0,
		int account = -1) {
	if (g_shuttingDown.load()) {
		LOG(("AyuPlugins: send_text peer=%1 rejected: shutting down").arg(peerId));
		return 0;
	}
	const auto body = QString::fromStdString(text);
	// These two are decided here, without the main thread, so that a caller
	// asking "did this go out" gets a real answer. Whether the peer exists can
	// only be checked on the main thread, so that case is logged rather than
	// returned.
	if (body.isEmpty()) {
		LOG(("AyuPlugins: send_text peer=%1 rejected: empty body").arg(peerId));
		return 0;
	}
	// Logged before handing over to the main thread: a plugin that answers a
	// command has no other visible effect, so without this there is nothing in
	// the log to tell a working reply from a silent one.
	LOG(("AyuPlugins: send_text peer=%1 chars=%2 \"%3\"")
		.arg(peerId)
		.arg(body.size())
		.arg(body.left(60)));
	crl::on_main([peerId, body, replyToMsgId, replyToTopMsgId, account] {
		// Кэш собственных peer id обновляется здесь: это главный поток, и он
		// точно есть. Отдельного события старта нет, а ждать главный поток из
		// плагина нельзя.
		RefreshSelfPeerIds();
		auto session = Ayu::Plugins::Hooks::SessionForAccount(account);
		auto peer = session
			? ResolvePeerForPlugin(peerId, session)
			: nullptr;

		// Аккаунт не указан - ищем тот, которому чат принадлежит.
		//
		// Плагин получает событие с аккаунтом, но ответ на команду отправляет
		// часто без него, потому что старые плагины о параметре не знают. Раньше
		// такой ответ уходил в сессию нулевого аккаунта, где этого чата нет,
		// и молча пропадал: на первом аккаунте всё работало, на втором и
		// третьем - нет. Теперь ответ уходит туда, где этот чат есть.
		if (!peer) {
			const auto found = ResolveTarget(peerId, account);
			session = found.session;
			peer = found.peer;
		}

		if (!session || body.isEmpty()) {
			// Was a silent return: a plugin that answers a command has no
			// other visible effect, so a missing session looked exactly like a
			// plugin that ignored the command.
			LOG(("AyuPlugins: send_text dropped peer=%1 (%2)")
				.arg(peerId)
				.arg(session
					? u"empty body"_q
					: u"no session for account"_q));
			return;
		}
		if (!peer) {
			// Full 64 bits: peer ids outgrew ID (int32) long ago, and the cast
			// truncated them, so a reply to a real account went nowhere with
			// nothing but "unknown peer" in the log.
			peer = ResolvePeerForPlugin(peerId, session);
		}
		if (!peer) {
			LOG(("AyuPlugins: send_text unknown peer %1").arg(peerId));
			return;
		}
		// Mark the message as plugin-originated so on_message_sending does
		// not run for it; otherwise a plugin that re-sends its own text
		// recurses once per character.
		const auto guard = Ayu::Plugins::Hooks::PluginSendScope();
		auto message = Api::MessageToSend(
			Api::SendAction(peer->owner().history(peer)));
		// Carry the reply through. It used to be dropped, so a plugin replying
		// to a command posted its answer as a plain message into the chat -
		// one spam plugin passes replyToMsg on every send, and its replies all
		// came out unthreaded with nothing to show it had gone wrong.
		if (replyToMsgId > 0) {
			message.action.replyTo.messageId
				= FullMsgId(peer->id, MsgId(replyToMsgId));
		}
		if (replyToTopMsgId > 0) {
			message.action.replyTo.topicRootId = MsgId(replyToTopMsgId);
		}
		message.textWithTags = { body, TextWithTags::Tags() };
		session->api().sendMessage(std::move(message));
	});
	// Queued. Not "delivered": that is decided by the server, and this returns
	// long before the main thread has even run the lambda above.
	return 1;
}

// Returns whether the edit was handed to the main thread. A plugin that
// animates a message by editing it must be able to tell "sent" from
// "ignored": this used to return nothing, so `if not edit_message(...)` was
// true on every call and a typing plugin resent the whole text as a new
// message on every step, turning one bubble into a wall of them.
//
// The dispatch is asynchronous on purpose. Waiting for the main thread here
// would mean a plugin thread holding the GIL while the main thread waits for
// that GIL, which deadlocks; so this reports dispatch, not the server answer.
// What a performed Telegram request reports back to a plugin.
struct PluginRequestUser {
	int64 id = 0;
	std::string firstName;
	std::string lastName;
};

struct PluginRequestError {
	std::string text;
	int code = 400;
};

// What a performed Telegram request hands back. Mobile plugins read a user off
// the response of account.updateProfile, so it carries the fields a plugin
// actually looks at rather than being an empty object that satisfies nothing.
py::object RequestUser(int64 id, const QString &first, const QString &last) {
	return py::cast(PluginRequestUser(id, first.toStdString(), last.toStdString()));
}

// Telegram's own error shape: plugins read error.text, and a bare exception
// would make them print "None" instead of the reason the name change failed.
py::object RequestError(const QString &text) {
	return py::cast(PluginRequestError(text.toStdString()));
}

// Answer a plugin's request callback. The caller must already be on the main
// thread holding the interpreter lock: building the response objects needs the
// lock, and the callable is kept alive on its own because the plugin is free to
// drop it the moment send_request returns.
void DeliverRequestResultOnMain(
		const std::shared_ptr<py::object> &holder,
		const py::object &response,
		const py::object &error) {
	try {
		(*holder)(response, error);
	} catch (py::error_already_set &e) {
		const auto why = QString::fromUtf8(e.what()).left(160);
		e.restore();
		PyErr_Clear();
		LOG(("AyuPlugins: request callback raised: %1").arg(why));
	} catch (...) {
		LOG(("AyuPlugins: request callback raised"));
	}
}

// Telegram API methods a plugin asks for that Desktop can genuinely perform.
//
// A plugin that renames itself builds account.updateProfile and hands it to
// send_request; that used to be reported as unsupported, so the plugin loaded,
// logged a failure once a second, and never renamed anything. Only the methods
// listed here are answered, and anything else is refused by name: telling a
// plugin that a request succeeded when nothing happened leaves it showing data
// it believes is fresh.
bool NativeTelegramRequest(
		const std::string &method,
		const py::dict &fields,
		py::function callback,
		int account = -1) {
	if (method != "TL_account.updateProfile") {
		return false;
	}
	if (!callback || callback.is_none()) {
		return false;
	}
	QString first;
	QString last;
	{
		const auto g = py::gil_scoped_acquire();
		try {
			first = QString::fromStdString(
				fields["first_name"].cast<std::string>());
			last = QString::fromStdString(
				fields["last_name"].cast<std::string>());
		} catch (const py::error_already_set &) {
			PyErr_Clear();
			return false;
		}
	}
	auto holder = std::make_shared<py::object>(std::move(callback));
	crl::on_main([holder, first, last, account] {
		const auto g = py::gil_scoped_acquire();
		const auto session = Ayu::Plugins::Hooks::SessionForAccount(account);
		if (!session) {
			DeliverRequestResultOnMain(
				holder, py::none(),
				RequestError(u"no account is signed in"_q));
			return;
		}
		session->api().request(MTPaccount_UpdateProfile(
			MTP_flags(MTPaccount_UpdateProfile::Flag::f_first_name
				| MTPaccount_UpdateProfile::Flag::f_last_name),
			MTP_string(first),
			MTP_string(last),
			MTPstring()
		)).done([holder, first, last](const MTPUser &) {
			// The API thread answers; the plugin's callback is Python, so it
			// has to be handed over on the main thread under the lock. The id is
			// not carried back: the schema exposes it only through a data
			// accessor that throws for userEmpty, and the caller is told the
			// name it asked for, which is what it came for.
			crl::on_main([holder, first, last] {
				const auto g = py::gil_scoped_acquire();
				DeliverRequestResultOnMain(
					holder, RequestUser(0, first, last), py::none());
			});
		}).fail([holder](const MTP::Error &error) {
			crl::on_main([holder, text = error.type()] {
				const auto g = py::gil_scoped_acquire();
				DeliverRequestResultOnMain(
					holder, py::none(), RequestError(text));
			});
		});
	});
	return true;
}

bool EditMessageOnMain(
		int64 peerId,
		int64 msgId,
		const std::string &text,
		bool keepWebpage,
		int account = -1) {
	if (g_shuttingDown.load()) {
		return false;
	}
	const auto body = QString::fromStdString(text);
	if (body.isEmpty() || msgId <= 0) {
		return false;
	}
	crl::on_main([peerId, msgId, body, keepWebpage, account] {
		// Peer ищется по всем аккаунтам сразу, а вот саму правку Telegram
		// должен выполнить тем аккаунтом, в диалоге с которым пришло событие:
		// чужой аккаунт не имеет права редактировать это сообщение.
		const auto session = Ayu::Plugins::Hooks::SessionForAccount(account);
		if (!session) {
			LOG(("AyuPlugins: edit_message peer=%1 dropped: no session")
				.arg(peerId));
			return;
		}
		// Аккаунт не указан - чат ищется по всем сессиям, а не только по
		// нулевой: иначе правка сообщения работала на первом аккаунте и не
		// работала на остальных.
		const auto found = ResolveTarget(peerId, account);
		const auto editSession = found.session ? found.session : session;
		const auto peer = found.peer;
		if (!peer) {
			LOG(("AyuPlugins: edit_message unknown peer %1").arg(peerId));
			return;
		}
		const auto item = peer->owner().message(peer, MsgId(msgId));
		if (!item) {
			LOG(("AyuPlugins: edit_message unknown message %1 in peer %2")
				.arg(msgId)
				.arg(peerId));
			return;
		}
		Api::EditTextMessage(
			not_null { item },
			TextWithEntities { body, EntitiesInText() },
			Data::WebPageDraft { .removed = !keepWebpage },
			Api::SendOptions {},
			nullptr,
			nullptr,
			false);
	});
	return true;
}

// Media a plugin asked to send. `kind` is what the plugin's helper name says
// it is, because Telegram treats a photo, a video and a document differently and
// a file sent as the wrong one arrives as the wrong thing.
enum class SendFileKind {
	Photo,
	Document,
	Video,
	Audio,
};

// Reads the file here, on the calling worker, rather than on the main thread.
// A plugin sends whatever is on disk and that can be tens of megabytes; doing
// the read while the window is waiting would freeze it for the duration.
//
// Returns 1 when the send was handed over, 0 when it was refused, so a plugin
// can tell the same way it can for send_text.
int SendFileOnMain(
		int64 peerId,
		const std::string &path,
		const std::string &caption,
		const std::string &kindName,
		int account = -1) {
	if (g_shuttingDown.load()) {
		LOG(("AyuPlugins: send_file peer=%1 rejected: shutting down").arg(peerId));
		return 0;
	}
	const auto file = QString::fromStdString(path);
	if (file.isEmpty()) {
		LOG(("AyuPlugins: send_file peer=%1 rejected: no path").arg(peerId));
		return 0;
	}
	const auto info = QFileInfo(file);
	if (!info.exists() || !info.isFile()) {
		LOG(("AyuPlugins: send_file peer=%1 rejected: no such file %2")
			.arg(peerId)
			.arg(file));
		return 0;
	}
	auto kind = SendFileKind::Document;
	if (kindName == "photo") {
		kind = SendFileKind::Photo;
	} else if (kindName == "video") {
		kind = SendFileKind::Video;
	} else if (kindName == "audio") {
		kind = SendFileKind::Audio;
	}
	const auto text = QString::fromStdString(caption);
	LOG(("AyuPlugins: send_file peer=%1 kind=%2 bytes=%3 \"%4\"")
		.arg(peerId)
		.arg(kindName)
		.arg(info.size())
		.arg(info.fileName()));
	crl::on_main([peerId, file, text, kind, account] {
		const auto session = Ayu::Plugins::Hooks::SessionForAccount(account);
		if (!session) {
			LOG(("AyuPlugins: send_file dropped peer=%1 (no session)")
				.arg(peerId));
			return;
		}
		// Как и в send_text: аккаунт не указан - ищем тот, которому чат
		// принадлежит, иначе отправка файла работает только на первом.
		const auto found = ResolveTarget(peerId, account);
		const auto peer = found.peer;
		if (!peer) {
			LOG(("AyuPlugins: send_file unknown peer %1").arg(peerId));
			return;
		}
		const auto guard = Ayu::Plugins::Hooks::PluginSendScope();
		// Built by hand rather than through the attach flow, so the plugin's
		// caption is not replaced by the name of the file and the file keeps
		// the name it had on disk. The helpers that do this properly want a
		// QMimeData and a prepared upload, which a plugin has no way to
		// supply.
		auto prepared = Ui::PreparedFile(file);
		auto information = std::make_unique<Ui::PreparedFileInformation>();
		information->filemime = QMimeDatabase().mimeTypeForFile(file).name();
		prepared.information = std::move(information);
		if (!text.isEmpty()) {
			prepared.caption = TextWithTags { text, TextWithTags::Tags() };
		}
		switch (kind) {
		case SendFileKind::Photo:
			prepared.type = Ui::PreparedFile::Type::Photo;
			break;
		case SendFileKind::Video:
			prepared.type = Ui::PreparedFile::Type::Video;
			break;
		case SendFileKind::Audio:
			prepared.type = Ui::PreparedFile::Type::Music;
			break;
		case SendFileKind::Document:
		default:
			prepared.type = Ui::PreparedFile::Type::File;
			break;
		}
		auto list = Ui::PreparedList();
		list.files.push_back(std::move(prepared));
		session->api().sendFiles(
			std::move(list),
			(kind == SendFileKind::Audio)
				? SendMediaType::Audio
				: SendMediaType::File,
			nullptr,
			Api::SendAction(peer->owner().history(peer)));
	});
	return 1;
}

// Which chat is on screen right now.
//
// A plugin could only learn this by catching on_chat_opened, which is not the
// same thing: the hook is delivered on a worker, a burst of switching collapses
// into the last one, and a plugin that loads after the chat is already open never
// sees the event at all. It would then act on the wrong chat or do nothing, with
// nothing to explain why.
//
// Read from the cache the hook maintains rather than from the window: the window
// state lives in the session controller and may only be touched on the main
// thread, while a plugin asks from whatever thread its hook runs on. Returns None
// when no chat has been opened since startup.
py::object CurrentChat() {
	const auto chat = Ayu::Plugins::Hooks::LastChatOpened();
	if (!chat) {
		return py::none();
	}
	return py::cast(*chat);
}

void RememberToken(int token) {
	if (g_loadingPluginId.isEmpty() || token <= 0) {
		return;
	}
	Manager::instance().addBusToken(g_loadingPluginId, token);
}

void RegisterHook(const std::string &name, py::function fn) {
	auto ok = false;
	const auto qname = QString::fromStdString(name);
	const auto id = ResolveHookId(qname, &ok);
	if (!ok) {
		LOG(("AyuPlugins: ignoring unsupported hook '%1'").arg(qname));
		return;
	}
	const auto holder = std::make_shared<py::object>(std::move(fn));
	const auto pluginId = g_loadingPluginId;
	// Recorded here, before the switch. Statements placed before the first case
	// label are unreachable, which is why every plugin was reported as having
	// no hooks at all when this was recorded inside each case.
	if (!pluginId.isEmpty()) {
		RecordHook(pluginId, HookIdName(id));
		NoteHookSubscription(pluginId, HookIdName(id));
	}
	switch (id) {
	case HookId::OnMessage:
		RememberToken(EventBus::instance().subscribeMessage(
			[holder, pluginId](const PluginMessage &msg) {
				SafeCallHook(pluginId, "on_message", [&] {
					(*holder)(msg);
				});
			}));
		break;
	case HookId::OnMessageSending:
		RememberToken(EventBus::instance().subscribeMessageSending(
			[holder, pluginId](MessageSendingContext &ctx) {
				SafeCallHook(pluginId, "on_message_sending", [&] {
					(*holder)(ctx);
				});
				// What the plugin decided, in one line. The "hook enter"
				// line alone cannot tell "saw the command and chose to do
				// nothing" from "matched the command and acted on it", which
				// is exactly the pair of cases that look identical when a
				// command appears to be ignored.
				if (ctx.cancel || ctx.rewrite) {
					LOG(("AyuPlugins: %1 on_message_sending %2%3")
						.arg(pluginId,
							ctx.cancel
								? u"cancelled the message"_q
								: u"rewrote it to \""_q
									+ ctx.rewrittenText.left(80)
									+ u"\""_q,
							QString()));
				}
			}));
		break;
	case HookId::OnMessageDeleted:
		RememberToken(EventBus::instance().subscribeDeleted(
			[holder, pluginId](const MessageDeletedContext &ctx) {
				SafeCallHook(pluginId, "on_message_deleted", [&] {
					(*holder)(ctx);
				});
			}));
		break;
	case HookId::OnChatOpened:
		RememberToken(EventBus::instance().subscribeChatOpened(
			[holder, pluginId](const PluginChat &c) {
				// Logged per plugin: opening a chat is the step that made this
				// reproducible, and the coalescing upstream means there is no
				// other record of which plugin ran for it.
				LOG(("AyuPlugins: chat_opened -> %1 peer=%2")
					.arg(pluginId)
					.arg(c.peerId));
				SafeCallHook(pluginId, "on_chat_opened", [&] {
					(*holder)(c);
				});
				LOG(("AyuPlugins: chat_opened <- %1").arg(pluginId));
			}));
		break;
case HookId::OnTyping:
		RememberToken(EventBus::instance().subscribeTyping(
			[holder, pluginId](const PluginTyping &t) {
				SafeCallHook(pluginId, "on_typing", [&] {
					(*holder)(t);
				});
			}));
		break;
	case HookId::OnMediaSending:
		RememberToken(EventBus::instance().subscribeMediaSending(
			[holder, pluginId](MessageSendingContext &ctx) {
				SafeCallHook(pluginId, "on_media_sending", [&] {
					(*holder)(ctx);
				});
			}));
		break;
	case HookId::OnAppStart:
		RememberToken(EventBus::instance().subscribeAppStart(
			[holder, pluginId] {
				SafeCallHook(pluginId, "on_app_start", [&] {
					(*holder)();
				});
			}));
		break;
	case HookId::OnAppStop:
		RememberToken(EventBus::instance().subscribeAppStop(
			[holder, pluginId] {
				SafeCallHook(pluginId, "on_app_stop", [&] {
					(*holder)();
				});
			}));
		break;
	case HookId::OnSettingChanged:
		RememberToken(EventBus::instance().subscribeSettingChanged(
			[holder, pluginId](const QString &key, const QString &value) {
				SafeCallHook(pluginId, "on_setting_changed", [&] {
					(*holder)(key.toStdString(), value.toStdString());
				});
			}));
		break;
	}
}

void EmitTestHooksOnMain(
		int64 peerId,
		int64 msgId,
		const std::string &text) {
	// Dispatches one synthetic event of every kind that does not need a
	// signed-in account, so a plugin (or the bundled selftest) can prove the
	// whole C++ -> pybind11 -> shim -> plugin path works.
	crl::on_main([peerId, msgId, text] {
		auto message = PluginMessage();
		message.msgId = msgId;
		message.peerId = peerId;
		message.fromId = 4242;
		message.text = QString::fromStdString(text);
		message.date = 1700000000;
		message.out = false;
		Host::instance().emitMessage(message);

		auto chat = PluginChat();
		chat.peerId = peerId;
		chat.title = QString::fromStdString(text);
		Host::instance().emitChatOpened(chat);

		auto deleted = MessageDeletedContext();
		deleted.peerId = peerId;
		deleted.msgIds = { msgId };
		Host::instance().emitDeleted(deleted);

		auto sending = MessageSendingContext();
		sending.message.peerId = peerId;
		sending.message.text = QString::fromStdString(text);
		Host::instance().emitMessageSending(sending);
	});
}

void InstallMissingNameFallback(
		py::module_ &m,
		Fn<py::object(const QString &)> realPath);

// Живые ссылки на элементы отправляемого списка, а не их копии.
//
// Поле отсутствовало вовсе, поэтому плагин, который смотрит, что уходит из
// чата, видел пустой список и думал, что отправлять нечего. А если бы список
// был отдан копиями, то подмена файла и отмена отправки до C++ не дошли бы
// вовсе, и плагин выглядел бы рабочим.
py::list SendingMediaLive(MessageSendingContext &ctx) {
	auto out = py::list();
	for (auto &item : ctx.media) {
		out.append(py::cast(&item, py::return_value_policy::reference_internal));
	}
	return out;
}

bool SendingCancelRead(const MessageSendingContext &ctx) {
	return ctx.cancel;
}

void SendingCancelWrite(MessageSendingContext &ctx, bool value) {
	ctx.cancel = value;
}

void SendingMediaWrite(MessageSendingContext &ctx, const py::list &) {
	// Список только для чтения: подмена делается полем replacement_path у
	// элемента, а присваивание нового спика означало бы подмену коллекции
	// целиком, что не имеет смысла для отправки.
	throw std::runtime_error("media is read-only; set replacement_path on an item");
}

PYBIND11_EMBEDDED_MODULE(ayu_api, m) {
	m.doc() = "AyuGram Desktop plugin API";

	auto msg = py::class_<PluginMessage>(m, "Message");
	msg.def(py::init<>())
		.def_readwrite("account", &PluginMessage::account)
		.def_readwrite("msg_id", &PluginMessage::msgId)
		.def_readwrite("peer_id", &PluginMessage::peerId)
		.def_readwrite("from_id", &PluginMessage::fromId)
		.def_readwrite("date", &PluginMessage::date)
		.def_readwrite("out", &PluginMessage::out)
		.def_property("text",
			[](const PluginMessage &s) { return s.text.toStdString(); },
			[](PluginMessage &s, const std::string &v) {
				s.text = QString::fromStdString(v);
			})
		.def_property("message",
			[](const PluginMessage &s) { return s.text.toStdString(); },
			[](PluginMessage &s, const std::string &v) {
				s.text = QString::fromStdString(v);
			})
		// dialog_id - это android-имя, но плагины на нем построены: модерация,
		// антиспам и удаление сообщений сравнивают его с тем же номером диалога
		// из исходящего сообщения. Без этого поля получался 0, и плагин тихо
		// выходил, решив, что сообщение не из того чата.
		//
		// Значение - тот же peer_id, что и выше: в Desktop peer_id положителен
		// и для каналов тоже. Плагин, который берёт модуль номера, работает и
		// с этим.
		.def_property("dialog_id",
			[](const PluginMessage &s) { return s.peerId; },
			[](PluginMessage &s, int64_t v) { s.peerId = v; })
		// id - второе android-имя на то же самое поле. Модерация удаляет по
		// message.id, и без него получался 0, то есть удалять было нечего.
		// msg_id остаётся: им пользуются плагины, написанные под Desktop.
		.def_property("id",
			[](const PluginMessage &s) { return s.msgId; },
			[](PluginMessage &s, int64_t v) { s.msgId = v; })
		// Псевдонимы в верблюжьем регистре: ими пользуются наши же примеры
		// плагинов, и одинаковое имя в двух стилях - повод для лишней ошибки.
		.def_property("msgId",
			[](const PluginMessage &s) { return s.msgId; },
			[](PluginMessage &s, int64_t v) { s.msgId = v; })
		.def_property("peerId",
			[](const PluginMessage &s) { return s.peerId; },
			[](PluginMessage &s, int64_t v) { s.peerId = v; })
		.def_property("fromId",
			[](const PluginMessage &s) { return s.fromId; },
			[](PluginMessage &s, int64_t v) { s.fromId = v; });

	auto chat = py::class_<PluginChat>(m, "Chat");
	chat.def(py::init<>())
		.def_readwrite("account", &PluginChat::account)
		.def_readwrite("peer_id", &PluginChat::peerId)
		.def_readwrite("type", &PluginChat::type)
		.def_property("title",
			[](const PluginChat &s) { return s.title.toStdString(); },
			[](PluginChat &s, const std::string &v) {
				s.title = QString::fromStdString(v);
			});

	// A user as a performed Telegram request reports it, so a plugin reading
// response.first_name gets a name instead of a missing attribute.
py::class_<PluginRequestUser>(m, "RequestUser")
	.def(py::init<int64, const std::string &, const std::string &>(),
		py::arg("id"), py::arg("first_name"), py::arg("last_name"))
	.def_readonly("id", &PluginRequestUser::id)
	.def_readonly("first_name", &PluginRequestUser::firstName)
	.def_readonly("last_name", &PluginRequestUser::lastName);
py::class_<PluginRequestError>(m, "RequestError")
	.def(py::init<const std::string &>(), py::arg("text"))
	.def_readonly("text", &PluginRequestError::text)
	.def_readonly("type", &PluginRequestError::text)
	.def_readonly("code", &PluginRequestError::code);

auto typing = py::class_<PluginTyping>(m, "Typing");
	typing.def(py::init<>())
		.def_readwrite("account", &PluginTyping::account)
		.def_readwrite("peer_id", &PluginTyping::peerId)
		.def_readwrite("user_id", &PluginTyping::userId)
		.def_readwrite("top_msg_id", &PluginTyping::topMsgId)
		.def_readwrite("kind", &PluginTyping::kind)
		.def_property("action",
			[](const PluginTyping &s) { return s.action.toStdString(); },
			[](PluginTyping &s, const std::string &v) {
				s.action = QString::fromStdString(v);
			});

	auto media = py::class_<PluginMedia>(m, "Media");
	media.def(py::init<>())
		.def_readwrite("size", &PluginMedia::size)
		.def_readwrite("kind", &PluginMedia::kind)
		.def_readwrite("sticker", &PluginMedia::sticker)
		.def_property("name",
			[](const PluginMedia &s) { return s.name.toStdString(); },
			[](PluginMedia &s, const std::string &v) {
				s.name = QString::fromStdString(v);
			})
		.def_property("path",
			[](const PluginMedia &s) { return s.path.toStdString(); },
			[](PluginMedia &s, const std::string &v) {
				s.path = QString::fromStdString(v);
			})
		.def_property("caption",
			[](const PluginMedia &s) { return s.caption.toStdString(); },
			[](PluginMedia &s, const std::string &v) {
				s.caption = QString::fromStdString(v);
			})
		// Настоящая подмена отправляемого файла: плагин пишет сюда путь, и
		// уходит то, что лежит по этому пути. Раньше этого не было вовсе, и
		// плагин, который на Android подменяет документ в параметрах отправки,
		// на Desktop молча ничего не мог сделать.
		.def_property("replacement_path",
			[](const PluginMedia &s) { return s.replacementPath.toStdString(); },
			[](PluginMedia &s, const std::string &v) {
				s.replacementPath = QString::fromStdString(v);
			});

auto sending = py::class_<MessageSendingContext>(m, "MessageSendingContext");
	sending.def(py::init<>())
		.def_readwrite("account", &MessageSendingContext::account)
		.def_readwrite("message", &MessageSendingContext::message)
		.def_readwrite("cancel", &MessageSendingContext::cancel)
		.def_readwrite("rewrite", &MessageSendingContext::rewrite)
		.def_property("text",
			[](const MessageSendingContext &s) {
				return s.message.text.toStdString();
			},
			[](MessageSendingContext &s, const std::string &v) {
				s.message.text = QString::fromStdString(v);
			})
		.def_property("rewrittenText",
			[](const MessageSendingContext &s) {
				return s.rewrittenText.toStdString();
			},
			[](MessageSendingContext &s, const std::string &v) {
				s.rewrittenText = QString::fromStdString(v);
			})
		.def_property("rewritten_text",
			[](const MessageSendingContext &s) {
				return s.rewrittenText.toStdString();
			},
			[](MessageSendingContext &s, const std::string &v) {
				s.rewrittenText = QString::fromStdString(v);
			})
		// Живые ссылки на элементы, а не их копии.
		//
		// Поле отсутствовало вовсе, поэтому плагин, который смотрит, что
		// уходит из чата, видел пустой список и думал, что отправлять нечего.
		// А если бы список был отдан копиями, то подмена файла и отмена
		// отправки до C++ не дошли бы вовсе, и плагин выглядел бы рабочим.
		.def_property("media", &SendingMediaLive, &SendingMediaWrite)
		.def_property("cancel_send", &SendingCancelRead, &SendingCancelWrite);

	auto deleted = py::class_<MessageDeletedContext>(m, "MessageDeletedContext");
	deleted.def(py::init<>())
		.def_readwrite("account", &MessageDeletedContext::account)
		.def_readwrite("peer_id", &MessageDeletedContext::peerId)
		.def_property("msgIds",
			[](const MessageDeletedContext &s) { return s.msgIds; },
			[](MessageDeletedContext &s, const std::vector<int64> &v) {
				s.msgIds = v;
			})
		.def_property("msg_ids",
			[](const MessageDeletedContext &s) { return s.msgIds; },
			[](MessageDeletedContext &s, const std::vector<int64> &v) {
				s.msgIds = v;
			});

	m.def("toast", &ToastOnMain, py::arg("text"));

	// Telegram API calls Desktop can really perform. A plugin hands over a
	// request object; the method name and its fields are picked out of it here,
	// so adding a method later does not mean changing every plugin that uses
	// one. Answered 0 means "not something this can do", and the caller says so
	// rather than pretending.
	m.def("request_telegram",
		[](const std::string &method, py::dict fields, py::function callback, int account) {
			return NativeTelegramRequest(method, std::move(fields),
				std::move(callback), account);
		},
		py::arg("method"), py::arg("fields"), py::arg("callback"), py::arg("account") = -1);
	m.def("log", [](const std::string &s) {
		LOG(("AyuPlugin: %1").arg(QString::fromStdString(s)));
	});
	m.def("send_text", &SendTextOnMain, py::arg("peer_id"), py::arg("text"), py::arg("reply_to_msg_id") = int64(0), py::arg("reply_to_top_msg_id") = int64(0), py::arg("account") = -1);
	m.def("edit_message", &EditMessageOnMain, py::arg("peer_id"), py::arg("msg_id"), py::arg("text"), py::arg("keep_webpage") = false, py::arg("account") = -1);
	m.def("send_file", &SendFileOnMain, py::arg("peer_id"), py::arg("path"), py::arg("caption") = std::string(), py::arg("kind") = std::string("document"), py::arg("account") = -1);
		// Отправка самому себе без знания своего числового id: peer ищется на
	// главном потоке прямо здесь. Снаружи его узнать нечем - значение
	// появляется только после первого же касания сессии, а плагину оно нужно
	// раньше.
	m.def("send_text_to_self", [](const std::string &text, int account) -> bool {
		if (text.empty()) {
			return false;
		}
		crl::on_main([text, account] {
			const auto id = RefreshSelfPeerId(account);
			if (!id) {
				return;
			}
			SendTextOnMain(id, text, int64_t(0), int64_t(0), account);
		});
		return true;
	}, py::arg("text"), py::arg("account") = -1, "Написать самому себе в Избранное.");

// Закрепление записи в истории поиска.
//
// Мобильные плагины делали это через SQLite и перестановку адаптера,
// чего в Desktop нет: плагин грузился и молчал. Теперь то же самое
// делается нативно, и плагину не нужно знать про Android вовсе.
m.def("search_pinned", [](int account) {
	const auto session = Ayu::Plugins::Hooks::SessionForAccount(account);
	auto list = py::list();
	if (session) {
		for (const auto &entry : session->recentSettingsSearches().pinned()) {
			list.append(py::str(entry.toStdString()));
		}
	}
	return list;
}, py::arg("account") = -1, "Закреплённые записи истории поиска.");
m.def("search_is_pinned", [](const std::string &entry, int account) {
	const auto session = Ayu::Plugins::Hooks::SessionForAccount(account);
	return session
		? session->recentSettingsSearches().isPinned(QString::fromStdString(entry))
		: false;
}, py::arg("entry"), py::arg("account") = -1, "Закреплена ли эта запись поиска.");
m.def("search_pin", [](const std::string &entry, int account) {
	const auto session = Ayu::Plugins::Hooks::SessionForAccount(account);
	return session
		? session->recentSettingsSearches().pin(QString::fromStdString(entry))
		: false;
}, py::arg("entry"), py::arg("account") = -1, "Закрепить запись в истории поиска.");
m.def("search_unpin", [](const std::string &entry, int account) {
	const auto session = Ayu::Plugins::Hooks::SessionForAccount(account);
	return session
		? session->recentSettingsSearches().unpin(QString::fromStdString(entry))
		: false;
}, py::arg("entry"), py::arg("account") = -1, "Открепить запись в истории поиска.");
m.def("get_current_chat", &CurrentChat, "The chat that is open now, or None.");
	m.def("self_peer_id", [](int account) -> int64_t {
		// Номер собственного диалога. Плагину он нужен, чтобы отличить
		// собственное сообщение от чужого и написать самому себе.
		// Значение кэшируется главным потоком: ждать его отсюда нельзя,
		// вызывающий держит GIL, а вернуться в Python главному потоку нужно.
		//
		// Номера аккаунтов у каждого свои, поэтому спрашивать надо с номером:
		// одно и то же «свой диалог» для двух вошедших аккаунтов это два
		// разных peer id, и плагин, считавший их одним, отвечал бы самому
		// себе в чужом чате. Без номера возвращается основной аккаунт.
		return SelfPeerIdFor(account);
	}, py::arg("account") = -1, "Собственный peer id аккаунта, или 0 если он ещё неизвестен.");
	// Сколько аккаунтов вообще вошло, и какой номер у этого. Плагину без этого
	// нельзя даже перечислить, что он обслуживает.
	m.def("account_count", []() -> int {
		return Ayu::Plugins::Hooks::AuthedAccountCount();
	}, "Сколько аккаунтов сейчас вошли в клиент.");
	m.def("is_valid_account", [](int account) -> bool {
		return account >= 0 && Ayu::Plugins::Hooks::AuthedAccountCount() > account;
	}, py::arg("account"), "Существует ли такой номер аккаунта.");
	m.def("copy_to_clipboard", &CopyToClipboard, py::arg("text"));
	// Цвета темы и иконки по имени.
		//
		// Плагин с Android спрашивает Theme.getColor(Theme.key_*) и ищет иконку
		// через getResources().getIdentifier(). Здесь то же самое по таблице имён,
		// собранной из сгенерированных клиентом заголовков. Раньше оба вызова
		// упирались в заглушку, которая молча возвращала 0, и плагин решал, что
		// цвета нет.
		m.def("theme_color", [](const std::string &name) -> std::string {
			const auto entry = AyuStyleTable::FindColor(name.c_str());
			if (!entry) {
				return std::string();
			}
			const auto data = entry->get();
			if (!data) {
				return std::string();
			}
			const auto c = data->c;
			if (!c.isValid()) {
				return std::string();
			}
			return QString("#%1%2%3")
				.arg(c.red(), 2, 16, QChar(u'0'))
				.arg(c.green(), 2, 16, QChar(u'0'))
				.arg(c.blue(), 2, 16, QChar(u'0'))
				.toStdString();
		}, py::arg("name"), "Цвет темы по имени, #rrggbb. Пустая строка, если имени нет.");
	
		m.def("theme_color_names", []() {
			py::list names;
			for (int i = 0; i != AyuStyleTable::kColorCount; ++i) {
				names.append(AyuStyleTable::Colors()[i].name);
			}
			return names;
		}, "Все имена цветов, которые понимает theme_color.");
	
		m.def("icon_file", [](const std::string &name) -> std::string {
			const auto entry = AyuStyleTable::FindIcon(name.c_str());
			if (!entry || entry->empty()) {
				return std::string();
			}
			// Иконки в клиенте живут в памяти. Плагину нужен файл: тот же
			// рисунок, что рисует клиент, а не выдуманный.
			auto image = entry->instance(QColor());
			if (image.isNull()) {
				return std::string();
			}
			const auto folder = QDir(QDir::tempPath()).filePath("ayuplugin-icons");
			QDir().mkpath(folder);
			const auto path = QDir(folder).filePath(QString::fromStdString(name) + ".png");
			if (!image.save(path, "PNG")) {
				return std::string();
			}
			return path.toStdString();
		}, py::arg("name"), "Путь к png с иконкой клиента по имени. Пустая строка, если нет.");
	
		m.def("icon_names", []() {
			py::list names;
			for (int i = 0; i != AyuStyleTable::kIconCount; ++i) {
				names.append(AyuStyleTable::Icons()[i].name);
			}
			return names;
		}, "Все имена иконок, которые понимает icon_file.");
	m.def("emit_test_hooks", &EmitTestHooksOnMain,
		py::arg("peer_id") = 1,
		py::arg("msg_id") = 1,
		py::arg("text") = std::string("selftest"));
	m.def("hook_count", []() {
		// Lets a plugin (or selftest) confirm the app really wired it up.
		return EventBus::instance().subscriberCount();
	});
	m.def("settings_read", [](const std::string &pluginId) {
		auto file = QFile(Manager::instance().pluginSettingsPath(
			QString::fromStdString(pluginId)));
		if (!file.open(QIODevice::ReadOnly)) {
			return std::vector<std::pair<std::string, std::string>>();
		}
		return ReadSettingsJson(file.readAll());
	}, py::arg("plugin_id"));
	m.def("settings_emit", [](const std::string &pluginId,
		const std::string &key,
		const std::string &value) {
		EventBus::instance().emitSettingChanged(
			QString::fromStdString(pluginId)
				+ u":"_q
				+ QString::fromStdString(key),
			QString::fromStdString(value));
	}, py::arg("plugin_id"), py::arg("key"), py::arg("value"));

	m.def("hook", [](const std::string &name, py::object fn) -> py::object {
		if (fn.is_none()) {
			return py::cpp_function([name](py::function f) {
				RegisterHook(name, std::move(f));
				return f;
			});
		}
		RegisterHook(name, fn.cast<py::function>());
		return py::none();
	}, py::arg("name"), py::arg("fn") = py::none());

	// A real value, not a stub: plugins ask where their data lives and an inert
	// answer silently sends everything to the wrong place. It showed up in the
	// plugin check as "reached for plugins_dir" because the fallback caught it.
	//
	// Resolved on first use rather than while this module is being initialised:
	// the module body runs from PyInit, before the manager exists, and touching
	// the singleton there made the whole interpreter fail to start.
	const auto pathFor = [](const QString &name) -> py::object {
		if (name == u"plugins_dir") {
			return py::str(
				Manager::instance().pluginsRoot().toStdString());
		}
		return py::str(
			Manager::instance().pluginDataRoot().toStdString());
	};

	m.attr("run_on_main_thread") = py::cpp_function(&RunOnMainThread);

	InstallMissingNameFallback(m, pathFor);
}

// A mobile plugin expects the whole Android surface: sendPhoto, getUserConfig,
// editMessage, deleteMessage, and dozens more that Desktop has no equivalent
// for. Raising AttributeError at the first call takes the plugin down in the
// middle of a hook, long after it loaded, with a message that points at a name
// rather than at the missing capability.
//
// So an unknown name answers with a stub that accepts any call and does
// nothing, and the name is recorded. The plugin keeps running and the parts
// Desktop can do still work; the plugin check then lists exactly what that
// plugin wanted and could not have, which is actionable in a way
// "AttributeError: module 'ayu_api' has no attribute 'sendPhoto'" never was.
void InstallMissingNameFallback(
		py::module_ &m,
		Fn<py::object(const QString &)> realPath) {
	m.attr("__ayu_stub__") = true;
	m.attr("__getattr__") = py::cpp_function(
		[realPath](const std::string &name) -> py::object {
			if (name.size() > 1 && name[0] == '_') {
				throw py::attribute_error(name);
			}
			// Real answers first: these are the names that must not become
			// inert, because a plugin writing its state to the wrong place
			// fails quietly much later.
			if (name == "plugins_dir" || name == "data_dir") {
				return realPath(QString::fromStdString(name));
			}
			{
				// g_apiGapsMutex, never g_mutex: this runs with the GIL held.
				const auto guard = std::lock_guard(g_apiGapsMutex);
				g_apiGaps.insert(name);
			}
			auto stub = py::module_::import("org._stubs")
				.attr("Placeholder")(
					"ayu_api." + name);
			// Marked on the instance, not the class: the same Placeholder backs
			// inert Android classes, and a plugin has to be able to tell that
			// this particular bridge call did nothing.
			stub.attr("__ayu_stub__") = true;
			return stub;
		},
		py::arg("name"));
}

// Which hook a plugin subscribed to, recorded as it subscribes. Guarded by
// g_apiGapsMutex rather than g_mutex: this runs while the GIL is held during
// plugin load, and taking the hook lock there inverts against a hook that is
// already waiting for the GIL.
QString HookIdName(HookId id) {
	switch (id) {
	case HookId::OnMessage: return u"on_message"_q;
	case HookId::OnMessageSending: return u"on_message_sending"_q;
	case HookId::OnMessageDeleted: return u"on_message_deleted"_q;
	case HookId::OnChatOpened: return u"on_chat_opened"_q;
	case HookId::OnAppStart: return u"on_app_start"_q;
	case HookId::OnAppStop: return u"on_app_stop"_q;
	case HookId::OnSettingChanged: return u"on_setting_changed"_q;
	case HookId::OnTyping: return u"on_typing"_q;
	case HookId::OnMediaSending: return u"on_media_sending"_q;
	}
	return u"unknown"_q;
}

void RecordHook(const QString &pluginId, const QString &hook) {
	const auto lock = std::lock_guard(g_apiGapsMutex);
	g_pluginHooks[pluginId.toStdString()].insert(hook.toStdString());
}

// Mute is what "switching a plugin off" actually does.
//
// It used to be broken: Manager::mute set the instance's own flag and nothing
// else, while HookMuted - the only thing SafeCallHook consults - reads the set
// maintained here. That set was never written to, so every mute was a no-op: the
// plugin's hooks kept running, the log claimed they were skipped, and switching
// a plugin off could not stop anything. Toggling therefore did nothing useful
// and walked straight into the code that eventually faulted.

void ReportHooks(const QString &pluginId) {
	QStringList hooks;
	{
		const auto lock = std::lock_guard(g_apiGapsMutex);
		const auto it = g_pluginHooks.find(pluginId.toStdString());
		if (it == g_pluginHooks.end()) {
			hooks = QStringList();
		} else {
			for (const auto &hook : it->second) {
				hooks.append(QString::fromStdString(hook));
			}
		}
	}
	// Spelled out on purpose: a plugin with no hooks can never affect
	// anything, and until this line existed that was indistinguishable from a
	// plugin whose triggers simply had not fired yet.
	LOG(("AyuPlugins: %1 hooks: %2")
		.arg(pluginId)
		.arg(hooks.isEmpty()
			? u"none - this plugin cannot react to anything here"_q
			: hooks.join(u", "_q)));
}

QStringList AuditRuntime() {
	// A plugin that needs ssl does not say what is missing: it reports "DLL
	// load failed while importing _ssl" and names the extension module, not
	// the library. Importing the modules plugins reach for here turns that
	// into a list of concrete causes at startup, before any plugin runs.
	// Held for the whole body because this runs with the GIL, and the plugin
	// check reads the result from the UI thread without it.
	const auto lock = std::lock_guard(g_runtimeProblemsMutex);
	g_runtimeProblems.clear();
	const auto modules = {
		u"socket"_q,
		u"ssl"_q,
		u"ctypes"_q,
		u"sqlite3"_q,
		u"zlib"_q,
		u"urllib.request"_q,
	};
	for (const auto &name : modules) {
		try {
			py::module_::import(name.toUtf8().constData());
		} catch (py::error_already_set &e) {
			const auto detail = QString::fromUtf8(e.what()).left(160);
			e.restore();
			PyErr_Clear();
			auto hint = detail;
			if (name == u"ssl"_q || name == u"urllib.request"_q) {
				hint += u" РІР‚вЂќ copy libssl-3.dll and libcrypto-3.dll next to AyuGram.exe";
			} else if (name == u"sqlite3"_q) {
				hint += u" РІР‚вЂќ copy sqlite3.dll next to AyuGram.exe";
			} else if (name == u"ctypes"_q) {
				hint += u" РІР‚вЂќ copy libffi-8.dll next to AyuGram.exe";
			}
			g_runtimeProblems.append(name + u": "_q + hint);
		} catch (...) {
			g_runtimeProblems.append(name + u" (unexpected failure)"_q);
		}
	}
	if (g_runtimeProblems.isEmpty()) {
		LOG(("AyuPlugins: runtime complete, every common module imports"));
	} else {
		LOG(("AyuPlugins: runtime incomplete, %1 problem(s):")
			.arg(QString::number(g_runtimeProblems.size())));
		for (const auto &problem : g_runtimeProblems) {
			LOG(("AyuPlugins: runtime %1").arg(problem));
			PluginLog(QString(), u"runtime: "_q + problem);
		}
	}
	return g_runtimeProblems;
}


// Hands a callable to the main thread without waiting for it.
//
// run_on_ui_thread used to simply call the function inline, so UI work declared
// by a plugin ran on whichever plugin thread happened to call it. That work then
// touched the interface from a thread that does not own it, and it held the GIL
// while it did, which is enough to freeze the window for as long as it ran.
void RunOnMainThread(const py::function &fn) {
	if (!fn || fn.is_none()) {
		return;
	}
	// Kept alive on its own: the caller is free to drop its reference the moment
	// this returns, and a collected callable would arrive as a dead object.
	auto holder = std::make_shared<py::object>(fn);
	crl::on_main([holder] {
		// The interpreter lock is taken here, inside the main-thread callback.
		// Calling a Python object without it corrupts CPython: the lock is never
		// handed back and every other thread - the UI thread included - waits in
		// PyEval_RestoreThread forever, which is exactly the frozen window this
		// produced when it was left out.
		try {
			const auto g = py::gil_scoped_acquire();
			(*holder)();
		} catch (py::error_already_set &e) {
			const auto why = QString::fromUtf8(e.what()).left(160);
			e.restore();
			PyErr_Clear();
			LOG(("AyuPlugins: run_on_ui_thread raised: %1").arg(why));
		} catch (...) {
			LOG(("AyuPlugins: run_on_ui_thread raised"));
		}
	});
}

} // namespace

// Mute is what switching a plugin off actually does. It is deliberately not in
// the anonymous namespace: Manager calls it, so it has to have external linkage.
void SetPluginMuted(const QString &id, bool muted) {
	const auto lock = std::lock_guard(g_mutex);
	if (muted) {
		g_mutedPlugins.insert(id);
	} else {
		g_mutedPlugins.erase(id);
	}
	LOG(("AyuPlugins: %1 %2")
		.arg(id)
		.arg(muted
			? u"muted, its hooks will be skipped"_q
			: u"unmuted, its hooks run again"_q));
}

bool IsPluginMuted(const QString &id) {
	const auto lock = std::lock_guard(g_mutex);
	return g_mutedPlugins.count(id) > 0;
}

QStringList ApiGaps() {
	auto out = QStringList();
	{
		// Scoped, because the GIL is taken further down and this lock must
		// never be held across it: the ayu_api fallback records names from
		// inside the interpreter, and holding this while waiting for the GIL
		// inverts against it and hangs the app.
		const auto lock = std::lock_guard(g_apiGapsMutex);
		for (const auto &name : g_apiGaps) {
			out.append(QString::fromStdString(name));
		}
	}
	// Names the Python shim recorded, so one list covers both the bridge's own
	// fallback and the tolerant __getattr__ on the helper modules.
	if (!g_shuttingDown.load()) {
		try {
			const auto guard = py::gil_scoped_acquire();
			for (const auto &name : py::module_::import("ayugram_compat.gaps")
					.attr("all_gaps")()) {
				const auto text = QString::fromStdString(
					name.cast<std::string>());
				if (!out.contains(text)) {
					out.append(text);
				}
			}
		} catch (py::error_already_set &e) {
			e.restore();
			PyErr_Clear();
		} catch (...) {
		}
	}
	return out;
}

QStringList RuntimeProblems() {
	const auto lock = std::lock_guard(g_runtimeProblemsMutex);
	return g_runtimeProblems;
}

QStringList InferPermissions(const QString &dirPath) {
	// A plugin written for a phone declares nothing, because on Android there
	// is nothing to declare. Here the sandbox then refuses its first
	// `import urllib` and it never loads at all. Reading its own source says
	// exactly what it needs, so the manifest is filled in instead of the
	// plugin being reported as broken.
	auto out = QStringList();
	if (g_shuttingDown.load()) {
		return out;
	}
	try {
		auto g = py::gil_scoped_acquire();
		auto helper = py::module_::import("permissions_infer");
		auto added = helper.attr("ensure_manifest_permissions")(
			dirPath.toStdString(), true);
		const auto mapping = added.cast<py::dict>();
		for (const auto &item : mapping) {
			out.append(QString::fromStdString(
				item.first.cast<std::string>())
				+ u" ("_q
				+ QString::fromStdString(
					item.second.cast<std::string>()) + u")"_q);
		}
		if (!out.isEmpty()) {
			helper.attr("ensure_manifest_permissions")(
				dirPath.toStdString(), false);
			LOG(("AyuPlugins: granted %1 by reading its code: %2")
				.arg(dirPath)
				.arg(out.join(u", ")));
		}
	} catch (py::error_already_set &e) {
		const auto reason = QString::fromUtf8(e.what());
		e.restore();
		PyErr_Clear();
		LOG(("AyuPlugins: cannot infer permissions for %1: %2")
			.arg(dirPath)
			.arg(reason.left(300)));
	} catch (...) {
		LOG(("AyuPlugins: cannot infer permissions for %1").arg(dirPath));
	}
	return out;
}

// A crash that produces no Windows event and no Python traceback is usually
// abort() or terminate(): a std::terminate from a violated invariant, or an
// access violation handled by a runtime that never reaches the OS handler.
// Both simply stop the process, which is indistinguishable from a hang and
// leaves nothing to diagnose. This writes the code, address and thread first.
// Writes a full dump from inside the crashing process.
//
// The alternatives all failed here: Windows wrote no WER dump, cdb makes the app
// unusable while it is attached, procdump refused some threads, and attaching to
// a frozen process returned an error. By the time a handler runs the fault has
// already happened, so writing the dump here is the one moment the memory is
// still there.
void AnnounceCrash() {
	QDir().mkpath(u"D:/TBuild/dump/wer"_q);
	const auto path = u"D:/TBuild/dump/wer/crash_"
		+ QString::number(::GetCurrentProcessId()) + u".txt"_q;
	QFile marker(path);
	if (marker.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
		marker.write(QString::number(::GetCurrentProcessId()).toUtf8());
		marker.close();
	}
}

// Which plugin each thread is currently running, so a fatal exception can name
// it without touching the interpreter.
//
// Asking the interpreter from the crash handler does not work: the faulting
// thread usually already holds the GIL, so acquiring it there hangs, and every
// attempt came back empty. This map is written while hooks run, so the handler

// Writes every thread's Python stack out of the interpreter.
//
// A native dump never worked here: MiniDumpWriteDump walks into the page that
// faulted and gives up, the flag that would skip it is rejected outright, and an
// external dumper cannot be attached in time because the crash comes fast. The
// interpreter itself can answer without walking memory - sys._current_frames()
// hands back each thread's top Python frame, which is enough to name the plugin
// and the function that were running when it died.
void LogPythonStacksOnCrash() {
	try {
		const auto g = py::gil_scoped_acquire();
		const auto sys = py::module_::import("sys");
		const auto frames = sys.attr("_current_frames")();
		const auto items = frames.attr("items")();
		const auto traceback = py::module_::import("traceback");
		const auto formatStack = traceback.attr("format_stack");
		QStringList out;
		for (const auto &entry : items) {
			auto pair = py::reinterpret_borrow<py::tuple>(entry);
			const auto frame = pair[1];
			const auto threadId = QString::number(
				frame.attr("f_thread").attr("ident").cast<int64>());
			const auto stack = formatStack(frame);
			QStringList lines;
			for (const auto &line : stack) {
				lines.append(QString::fromStdString(
					line.cast<std::string>()));
			}
			out.append(u"--- thread " + threadId + u" ---");
			out.append(lines.join(u'\n'));
		}
		QDir().mkpath(u"D:/TBuild/dump"_q);
		QFile file(u"D:/TBuild/dump/python_stacks_at_crash.txt"_q);
		if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
			file.write(out.join(u"\n\n").toUtf8());
			file.close();
			LOG(("AyuPlugins: python stacks written (%1 thread(s))")
				.arg(out.size()));
		}
	} catch (...) {
		// Nothing useful to do this far into a crash.
	}
}

LONG WINAPI AyuCrashFilter(EXCEPTION_POINTERS *info) {
	if (info && info->ExceptionRecord) {
		const auto record = info->ExceptionRecord;
		LOG(("AyuPlugins: FATAL exception code=0x%1 address=0x%2 thread=0x%3 in %4")
			.arg(quint32(record->ExceptionCode), 8, 16, QChar('0'))
			.arg(quintptr(record->ExceptionAddress), 0, 16)
			.arg(quint32(::GetCurrentThreadId()), 8, 16, QChar('0'))
			.arg(ThreadOwnerName().isEmpty()
				? u"no plugin hook on this thread"_q
				: u"plugin " + ThreadOwnerName()));
		AnnounceCrash();
		LogPythonStacksOnCrash();
	}
	return EXCEPTION_CONTINUE_SEARCH;
}

void InstallCrashFilter() {
	SetUnhandledExceptionFilter(AyuCrashFilter);
}

bool InitPython() {
	g_shuttingDown.store(false);
	try {
		// Clear last session's clean-exit marker before anything else.
		//
		// The marker is how a supervisor tells a quit from a crash, and it is
		// only meaningful while this session is running. Left behind from a
		// previous run, it made the next crash look like a deliberate quit: the
		// watchdog killed a hung app, read a marker written ten minutes earlier,
		// and refused to bring it back - which is the one thing it exists to do.
		// Removing it here makes the marker's presence mean "this session ended
		// properly and nothing since".
		QFile::remove(QDir(QCoreApplication::applicationDirPath())
			.filePath(u"clean-exit.marker"_q));
		g_guard = std::make_unique<py::scoped_interpreter>();
		auto g = py::gil_scoped_acquire();
		BootLog(u"bridge: interpreter starting"_q);
		py::module_::import("ayu_api");
		auto sys = py::module_::import("sys");
		auto path = sys.attr("path");
		path.attr("insert")(0, "");

		const auto appDir = QCoreApplication::applicationDirPath();
		path.attr("insert")(0, appDir.toStdString());

		const auto shimEnv = qEnvironmentVariable("AYU_PLUGINS_SHIM");
		auto shimDirs = QStringList{
			shimEnv,
			appDir,
			appDir + u"/python_shim"_q,
			appDir + u"/../python_shim"_q,
		};
		for (const auto &dir : shimDirs) {
			if (dir.isEmpty()) {
				continue;
			}
			const auto cleaned = QDir::cleanPath(dir);
			if (QFileInfo(cleaned + u"/ayugram_compat"_q).isDir()
				|| QFile::exists(cleaned + u"/base_plugin.py"_q)) {
				path.attr("insert")(0, cleaned.toStdString());
				g_shimDir = cleaned;
				break;
			}
		}
		// standalone/ holds the deployment-time helpers the bridge itself
		// uses, such as the permission inference. Without it on the path they
		// are unreachable from inside the embedded interpreter.
		if (!g_shimDir.isEmpty()) {
			path.attr("append")(QDir::toNativeSeparators(
				g_shimDir + u"/standalone"_q).toStdString());
		}
		BootLog(u"bridge: shim dir = "_q + g_shimDir);
		try {
			py::module_::import("ayugram_compat.mobile_stubs").attr("install")();
			// Lets android_utils reach run_on_main_thread without importing
			// ayu_api, whose import would shadow the real bridge module.
			py::module_::import("ayugram_compat.android_utils")
				.attr("_ayu_bridge") = py::module_::import("ayu_api");
		} catch (py::error_already_set &e) {
			e.restore();
			PyErr_Clear();
			LOG(("AyuPlugins: mobile_stubs install skipped"));
		} catch (...) {
		}
		AuditRuntime();
		InstallCrashFilter();
		// A native access violation inside python314.dll leaves nothing in the
		// log: the interpreter dies before any Python code can report, and the
		// crash is always blamed on whichever plugin happened to be running.
		// faulthandler writes the Python-side stacks of every thread to a file
		// as it happens, which is the only account of what was executing when
		// it went. chain=True also covers a fault raised inside a C call.
		try {
			const auto guard = py::gil_scoped_acquire();
			const auto crashLog = QDir(appDir)
				.filePath(u"python_crash.log"_q);
			// Opened through builtins rather than faulthandler.open: in this embedded
			// build that attribute is not reachable, and enable() wants the file
			// object anyway.
			auto handle = py::module_::import("builtins").attr("open")(
				crashLog.toStdString(),
				"w");
			// enable(file, all_threads, chain)
			py::module_::import("faulthandler").attr("enable")(
				handle,
				true,
				true);
			LOG(("AyuPlugins: faulthandler -> %1").arg(crashLog));
		} catch (py::error_already_set &e) {
			const auto why = QString::fromUtf8(e.what());
			e.restore();
			PyErr_Clear();
			LOG(("AyuPlugins: faulthandler unavailable: %1")
				.arg(why.left(200)));
		} catch (...) {
			LOG(("AyuPlugins: faulthandler unavailable"));
		}
		BootLog(u"bridge: python ready"_q);
		g_apiModule = py::module_::import("ayu_api");
		VerifyApiModule("startup");
		return true;
	} catch (const std::exception &e) {
		LOG(("AyuPlugins: InitPython failed: %1").arg(
			QString::fromUtf8(e.what(), -1)));
		BootLog(u"bridge: python FAILED"_q);
		return false;
	}
}

void ConfigureSandbox(const PluginManifest &manifest) {
	// Called with the GIL held. Best-effort: never fails the load.
	// Grants are unioned process-wide across loads, never narrowed.
	try {
		auto sys = py::module_::import("sys");
		auto path = sys.attr("path");
		auto haveSandbox = false;
		if (!g_shimDir.isEmpty()) {
			const auto dir = QDir::cleanPath(g_shimDir + u"/../sandbox"_q);
			if (QFile::exists(dir + u"/import_filter.py"_q)) {
				path.attr("insert")(0, dir.toStdString());
				haveSandbox = true;
			}
		}
		if (!haveSandbox) {
			const auto dir = QDir::cleanPath(
				QCoreApplication::applicationDirPath() + u"/sandbox"_q);
			if (QFile::exists(dir + u"/import_filter.py"_q)) {
				path.attr("insert")(0, dir.toStdString());
				haveSandbox = true;
			}
		}
		if (!haveSandbox) {
			return;
		}
		auto toStrings = [](const QStringList &list) {
			auto out = std::vector<std::string>();
			out.reserve(list.size());
			for (const auto &s : list) out.push_back(s.toStdString());
			return out;
		};
		static const auto kNetPrefix = u"net.http:"_q;
		auto granted = QStringList();
		auto netHosts = QStringList();
		auto storage = false;
		auto process = false;
		for (const auto &perm : manifest.permissions) {
			granted << perm;
			if (perm == u"net.http"_q) {
				netHosts << u"*"_q;
			} else if (perm.startsWith(kNetPrefix)) {
				const auto host = perm.mid(kNetPrefix.size());
				if (!host.isEmpty()) netHosts << host;
			} else if (perm == u"storage.read"_q || perm == u"storage.write"_q) {
				storage = true;
			} else if (perm == u"process.spawn"_q) {
				process = true;
			}
		}
		auto filt = py::module_::import("import_filter");
		filt.attr("grant")(py::cast(toStrings(granted)));
		auto audit = py::module_::import("audithook");
		audit.attr("grant")(
			py::arg("net_hosts") = py::cast(toStrings(netHosts)),
			py::arg("storage") = storage,
			py::arg("process") = process);
		filt.attr("install")();
		audit.attr("install")();
	} catch (py::error_already_set &e) {
		e.restore();
		PyErr_Clear();
		LOG(("AyuPlugins: sandbox setup skipped"));
	} catch (...) {
		LOG(("AyuPlugins: sandbox setup skipped"));
	}
}

void AnnounceCleanExit() {
	// The counterpart of AnnounceCrash, and what lets a supervisor tell "the
	// user closed the app" from "the app died". Reading the exit status of a
	// process that has already gone is unreliable - a terminated process can be
	// reported as having exited cleanly - and getting that wrong means either
	// never restarting a crash or reopening an app someone deliberately closed.
	// A file written here, on the normal path, is unambiguous.
	const auto marker = QDir(QCoreApplication::applicationDirPath())
		.filePath(u"clean-exit.marker"_q);
	QFile file(marker);
	if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
		file.write(QByteArray::number(qint64(QDateTime::currentSecsSinceEpoch())));
		file.close();
	}
}

void ShutdownPython() {
	// A plugin may have left a daemon thread or a timer running Python
	// code. Releasing the interpreter while one of those is inside
	// python3xx.dll is an access violation, so close the gate first: no
	// hook or bridge call will enter Python from then on. Plugins are
	// already unloaded by the caller, which cancels their timers.
	AnnounceCleanExit();
	g_shuttingDown.store(true);
	{
		// Let anything already inside Python finish before it is dropped.
		auto g = py::gil_scoped_acquire();
	}
	const auto lock = std::lock_guard(g_mutex);
		{
			const auto lock = std::lock_guard(g_modulesMutex);
			g_modules.clear();
		}
	g_guard.reset();
}

bool LoadPlugin(const PluginManifest &manifest, QString *error) {
	try {
		auto g = py::gil_scoped_acquire();
		g_loadingPluginId = manifest.id;
		const auto entryPath = manifest.sourcePath + u"/"_q + manifest.entry;
		auto sys = py::module_::import("sys");
		auto path = sys.attr("path");
		const auto source = manifest.sourcePath.toStdString();
		const auto root = Manager::instance().pluginsRoot().toStdString();
		path.attr("insert")(0, source);
		if (!root.empty() && root != source) {
			path.attr("insert")(0, root);
		}
		ConfigureSandbox(manifest);
		BootLog(u"load: "_q + manifest.id);
		auto builtins = py::module_::import("builtins");
		const auto open = builtins.attr("open");
		auto file = open(entryPath.toStdString(), "r", py::arg("encoding") = "utf-8-sig");
		const auto code = py::str(file.attr("read")());
		file.attr("close")();
		auto globals = py::dict();
		globals["__name__"] = "__main__";
		globals["__file__"] = entryPath.toStdString();
		py::exec(code, globals, globals);
		try {
			auto base = py::module_::import("base_plugin");
			if (py::hasattr(base, "activate_base_plugins")) {
				base.attr("activate_base_plugins")(globals);
			} else {
				LOG(("AyuPlugins: %1: base_plugin has no "
					"activate_base_plugins").arg(manifest.id));
			}
		} catch (py::error_already_set &e) {
			// Swallowing this left a plugin that looked loaded but had no
			// instance, so its settings dialog was empty and its
			// on_plugin_load never ran. Report it instead.
			const auto what = QString::fromUtf8(e.what()).left(300);
			LOG(("AyuPlugins: %1 activate_base_plugins failed: %2")
				.arg(manifest.id, what));
			PluginLog(manifest.id,
				u"activate_base_plugins failed: "_q + what);
			e.restore();
			PyErr_Clear();
		}
		{
			const auto lock = std::lock_guard(g_modulesMutex);
			g_modules.emplace(
				manifest.id,
				py::reinterpret_borrow<py::object>(globals));
		}
		g_loadingPluginId.clear();
		VerifyApiModule(manifest.id.toStdString().c_str());
		Manager::instance().setPluginSettingsSchema(
			manifest.id,
			PluginSettingsJson(manifest.id));
		ReportHooks(manifest.id);
		BootLog(u"load ok: "_q + manifest.id);
		return true;
	} catch (py::error_already_set &e) {
		g_loadingPluginId.clear();
		BootLog(u"load FAILED: "_q + manifest.id);
		auto msg = QString::fromUtf8(e.what());
		const auto lower = msg.toLower();
		if (lower.contains(u"no module named 'java'"_q)
			|| lower.contains(u"no module named 'android'"_q)
			|| lower.contains(u"android-only plugin"_q)
			|| lower.contains(u"java runtime is not available"_q)) {
			msg = u"Plugin needs real Java/Android APIs. Desktop ships no-op "
				u"stubs, so most mobile plugins load; only this import could not "
				u"be satisfied. Guard it with try/except ImportError and add a "
				u"desktop fallback."_q;
		}
		if (error) *error = msg.left(512);
		LOG(("AyuPlugins: load %1 failed: %2").arg(manifest.id, msg));
		PluginLog(manifest.id, u"load failed: "_q + msg);
		return false;
	} catch (const std::exception &e) {
		g_loadingPluginId.clear();
		const auto what = QString::fromUtf8(e.what());
		if (error) *error = what.left(512);
		LOG(("AyuPlugins: load %1 failed: %2").arg(manifest.id, what));
		PluginLog(manifest.id, u"load failed: "_q + what);
		return false;
	}
}


void SetHookOnMainThread(bool value) {
	g_hookHoldsMainThread.store(value);
}

// Releases a plugin's event bus subscriptions.
//
// Called from Manager::disable, which used to do this itself while holding the
// manager's own lock and without the interpreter locked. Unsubscribing destroys
// the subscriber, and each subscriber holds a py::function captured from the
// plugin's module: dropping that reference with no GIL is undefined behaviour
// and showed up as an access violation inside python314.dll the moment a plugin
// was switched off and on again.
// Put text on the system clipboard.
//
// It used to show a toast with the text and copy nothing, and a plugin that
// uses this to hand a price or a link to the user then reported success and
// told them it had been copied. Desktop has a real clipboard, and Telegram's own
// code writes to it through the same call.
bool CopyToClipboard(const std::string &text) {
	try {
		const auto body = QString::fromStdString(text);
		crl::on_main([body] {
			auto clipboard = QGuiApplication::clipboard();
			if (clipboard) {
				clipboard->setText(body);
			}
		});
		return true;
	} catch (...) {
		LOG(("AyuPlugins: copy to clipboard failed"));
		return false;
	}
}

void StopPluginInstance(const QString &id) {
	// Let a plugin put itself away when it is switched off.
	//
	// Nothing called on_plugin_unload, so a disabled plugin went on doing
	// everything except answering: a monitoring thread kept looping, a flag
	// that stops a worker was never cleared, pending timers stayed armed. The
	// hooks were muted, so the symptom was a plugin that looked switched off
	// and still consumed memory and CPU.
	//
	// Best effort by design: a plugin whose cleanup raises must not stop the
	// switch-off from completing, and the module is left loaded either way.
	try {
		py::dict globals;
		{
			const auto lock = std::lock_guard(g_modulesMutex);
			const auto it = g_modules.find(id);
			if (it == g_modules.end()) {
				return;
			}
			globals = py::reinterpret_borrow<py::dict>(it->second);
		}
		auto g = py::gil_scoped_acquire();
		if (g_shuttingDown.load() || !globals.contains("_ayu_plugin_instance")) {
			return;
		}
		auto plugin = globals["_ayu_plugin_instance"];
		if (plugin.is_none()) {
			return;
		}
		if (py::hasattr(plugin, "on_plugin_unload")) {
			plugin.attr("on_plugin_unload")();
		}
		// Stops whatever the plugin left running on the shim's own lists.
		py::module_::import("ayugram_compat.hook_utils").attr("release_instance")(
			plugin);
		LOG(("AyuPlugins: %1 was told to shut down").arg(id));
	} catch (py::error_already_set &e) {
		LOG(("AyuPlugins: %1 on_plugin_unload failed: %2")
			.arg(id, QString::fromUtf8(e.what()).left(160)));
		e.restore();
		PyErr_Clear();
	} catch (...) {
		LOG(("AyuPlugins: %1 on_plugin_unload failed").arg(id));
	}
}

void ReleasePluginHooks(const QString &id, const std::vector<int> &tokens) {
	if (tokens.empty()) {
		return;
	}
	try {
		const auto g = py::gil_scoped_acquire();
		for (const auto token : tokens) {
			EventBus::instance().unsubscribe(token);
		}
	} catch (...) {
		for (const auto token : tokens) {
			try {
				EventBus::instance().unsubscribe(token);
			} catch (...) {
			}
		}
	}
	LOG(("AyuPlugins: released %1 hook subscription(s) for %2")
		.arg(tokens.size())
		.arg(id));
}

void UnloadPlugin(const QString &id) {
	// The module is deliberately left loaded.
	//
	// Every crash here was the same shape: an access violation inside
	// python314.dll reading a pointer full of 0xDD, on the thread doing the
	// disable, immediately before the plugin loaded again. Releasing a module's
	// globals pulls the ground out from under anything still holding it - a
	// plugin's own worker threads, its timers, its queued callbacks - and none of
	// those can be reliably stopped first.
	//
	// Turning a plugin off already does what matters: its subscriptions are
	// released, so its hooks stop running. Keeping the module resident costs a
	// few hundred kilobytes per plugin and removes this whole class of crash.
	const auto lock = std::lock_guard(g_mutex);
	g_hookErrors.erase(id);
	g_hookIssues.erase(id);
	LOG(("AyuPlugins: %1 left loaded, hooks released").arg(id));
}

int PluginHookErrors(const QString &id) {
	const auto lock = std::lock_guard(g_mutex);
	const auto it = g_hookErrors.find(id);
	return (it != g_hookErrors.end()) ? it->second : 0;
}

QString PluginLastHookIssue(const QString &id) {
	const auto lock = std::lock_guard(g_mutex);
	const auto it = g_hookIssues.find(id);
	return (it != g_hookIssues.end()) ? it->second : QString();
}

int PluginHookSubscriptions(const QString &id) {
	const auto lock = std::lock_guard(g_activityMutex);
	const auto it = g_hookSubscriptions.find(id);
	return (it != g_hookSubscriptions.end()) ? it->second : 0;
}

int64_t PluginHookCalls(const QString &id) {
	return ActivityOf(id).hookCalls;
}

QStringList PluginApiCalls(const QString &id) {
	const auto names = ActivityOf(id).apiNames;
	auto out = QStringList();
	out.reserve(int(names.size()));
	for (const auto &name : names) {
		out.push_back(name);
	}
	return out;
}

// Отправляет команду самому себе, в Избранное.
//
// Так кнопка проверки в настройках плагинов не изобретает свой
// интерфейс: она пользуется тем же /scan, который и так работает, и
// ответ приходит в обычный чат, где его видно целиком. Свой отдельный
// список находок означал бы второй формат одного и того же отчёта.
//
// Избранное ищется по первому вошедшему аккаунту: команда от пользователя
// не привязана к аккаунту, а спрашивать выбирать аккаунт здесь не за что.
bool SendCommandToSelf(const QString &text, int account) {
	if (account < 0) {
		account = 0;
	}
	const auto peer = SelfPeerIdFor(account);
	if (!peer) {
		return false;
	}
	return SendTextOnMain(
		peer,
		text.toStdString(),
		0,
		0,
		account) > 0;
}

void ResetPluginActivity() {
	const auto lock = std::lock_guard(g_activityMutex);
	g_hookCalls.clear();
	g_apiCalls.clear();
	g_hookSubscriptions.clear();
}

bool BridgeShuttingDown() {
	return g_shuttingDown.load();
}

// Asks a loaded plugin for its declared settings and current values, as
// JSON: {"rows": [...], "values": {...}}. One serialisation path, and the
// C++ side only ever parses.
QByteArray PluginSettingsJson(const QString &id) {
	auto out = QByteArray();
	auto reason = u"ok"_q;
	try {
		// The interpreter has to be entered before any py:: object exists.
		// `py::dict globals;` calls PyDict_New, which reads the current thread
		// state; on a thread that has never held the GIL that state is null and
		// it faults reading it, which is an access violation inside
		// python314.dll and no exception anybody can catch. This used to be
		// declared above the acquire below, so every call from a worker thread
		// landed on it.
		//
		// g_mutex is still only held to copy the module out, never while Python
		// runs. values_for() calls the plugin's own get_setting, which can come
		// straight back into the bridge.
		auto g = py::gil_scoped_acquire();
		py::dict globals;
		{
			const auto lock = std::lock_guard(g_modulesMutex);
			const auto it = g_modules.find(id);
			if (it == g_modules.end()) {
				reason = u"not loaded"_q;
				return out;
			}
			globals = py::reinterpret_borrow<py::dict>(it->second);
		}
		if (g_shuttingDown.load()) {
			reason = u"shutting down"_q;
		} else {
				// A plugin keeps its settings under a key in its module globals, but many
				// mobile plugins never create an instance that inherits
				// BasePlugin, so asking the instance for setting_schema was the
				// only route and it reported "no live instance" for plugins
				// that had plainly declared their settings. The module globals
				// are searched as well, and the flattening lives in Python so
				// both plugin styles go through one implementation.
				auto schema = py::module_::import("ayugram_compat.schema");
				// py::object, not auto: auto would deduce py::none, and assigning the
				// instance to it is an implicit conversion that pybind11 rejects
				// unless the value really is None.
				py::object plugin = py::none();
				if (globals.contains("_ayu_plugin_instance")) {
					auto instance = globals["_ayu_plugin_instance"];
					if (!instance.is_none()) {
						plugin = instance;
					}
				}
				auto rows = schema.attr("rows_for")(plugin, globals);
				// __len__() rather than py::len(): the C-API size function is
				// not in this loader's symbol table, and calling the method
				// goes through machinery that is already resolved.
				if (rows.attr("__len__")().cast<int>() == 0) {
					reason = u"no declared settings"_q;
				} else {
					auto values = schema.attr("values_for")(plugin, globals);
					const auto codec = py::module_::import("json");
					auto doc = py::dict();
					doc["rows"] = codec.attr("dumps")(rows);
					doc["values"] = codec.attr("dumps")(values);
out = QByteArray::fromStdString(
					codec.attr("dumps")(doc).cast<std::string>());
				}
			}
	} catch (py::error_already_set &e) {
		e.restore();
		reason = QString::fromUtf8(e.what()).left(120);
		PyErr_Clear();
	} catch (const std::exception &e) {
		reason = u"c++: "_q + QString::fromUtf8(e.what()).left(100);
	} catch (...) {
		reason = u"unknown failure"_q;
	}
	// Only failures are worth a line: the manager logs the row count for
	// every plugin that does produce a schema, and two lines per plugin at
	// startup buries everything else.
	if (out.isEmpty()) {
		LOG(("AyuPlugins: no settings schema for %1 (%2)")
			.arg(id)
			.arg(reason));
	}
	return out;
}

bool ApplyPluginSettings(const QString &id, const QJsonObject &values) {
	try {
		// Acquired before the py::dict below, not after: declaring it calls
		// PyDict_New, which dereferences the current thread state, and on a
		// worker thread that state does not exist yet. That fault is an access
		// violation deep inside python314.dll rather than a catchable error, and
		// it fired every single time a setting was saved, because the save runs
		// on crl::async precisely so the UI would not block on the interpreter.
		auto g = py::gil_scoped_acquire();
		// The module globals are copied out under the module lock and the lock is
		// released before any Python runs. Holding it across the call was the
		// crash: apply_settings reaches back into the bridge (set_setting,
		// on_setting_changed, timers), which takes the same lock again, and
		// std::mutex is not recursive - so the second lock on the same thread was
		// undefined behaviour, not a deadlock.
		py::dict globals;
		{
			const auto lock = std::lock_guard(g_modulesMutex);
			const auto it = g_modules.find(id);
			if (it == g_modules.end()) {
				// Every early return used to be silent, so a setting that was
				// written to the file but never reached the running plugin left
				// nothing in the log at all: the file said one thing, the plugin
				// used another, and there was no way to tell which. Each of these
				// says which half failed.
				LOG(("AyuPlugins: apply settings for %1 skipped: not loaded")
					.arg(id));
				return false;
			}
			globals = py::reinterpret_borrow<py::dict>(it->second);
		}
		if (g_shuttingDown.load()) {
			LOG(("AyuPlugins: apply settings for %1 skipped: shutting down")
				.arg(id));
			return false;
		}
		if (!globals.contains("_ayu_plugin_instance")) {
			LOG(("AyuPlugins: apply settings for %1 skipped: it made no instance")
				.arg(id));
			return false;
		}
		auto plugin = globals["_ayu_plugin_instance"];
		if (plugin.is_none() || !py::hasattr(plugin, "apply_settings")) {
			LOG(("AyuPlugins: apply settings for %1 skipped: no apply_settings")
				.arg(id));
			return false;
		}
		auto codec = py::module_::import("json");
		auto text = QJsonDocument(values).toJson(QJsonDocument::Compact);
		plugin.attr("apply_settings")(
			codec.attr("loads")(text.constData()));
		return true;
	} catch (py::error_already_set &e) {
		const auto why = QString::fromUtf8(e.what()).left(200);
		e.restore();
		PyErr_Clear();
		LOG(("AyuPlugins: apply settings for %1 failed: %2").arg(id, why));
	} catch (...) {
		LOG(("AyuPlugins: apply settings for %1 failed").arg(id));
	}
	return false;
}
} // namespace Ayu::Plugins::Bridge
#endif
