// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
//
// AyuGram plugin host for Desktop, (c) @CE0fbi
#include "ayu/plugins/bridge/python_bridge.h"
#include "ayu/plugins/hook_points.h"

#include <chrono>
#include <future>
#include <map>
#include <mutex>
#include <optional>
#include <thread>

#include "api/api_common.h"
#include "apiwrap.h"
#include "ayu/ayu_settings.h"
#include "ayu/features/translator/implementations/google.h"
#include "ayu/features/translator/implementations/yandex.h"
#include "ayu/plugins/plugins_host.h"
#include "ayu/utils/telegram_helpers.h"
#include "core/application.h"
#include "crl/crl_async.h"
#include "crl/crl_queue.h"
#include "data/data_session.h"
#include "history/history.h"
#include "main/main_account.h"
#include "main/main_domain.h"
#include "main/main_session.h"
#include "lang_auto.h"
#include "main/main_session.h"
#include "ui/text/text_entity.h"
#include "ui/toast/toast.h"
#include "window/window_controller.h"

namespace Ayu::Plugins::Hooks {
namespace {

thread_local int t_pluginSendDepth = 0;

[[nodiscard]] bool TryBuiltinOutgoingTranslate(int64 peerId, QString &text) {
	const auto &settings = AyuSettings::getInstance();
	if (!settings.outgoingTranslateCommands()) {
		return false;
	}
	auto trimmed = text.trimmed();
	QString toLang;
	QString payload;
	if (trimmed.startsWith(u"/en "_q, Qt::CaseInsensitive)) {
		toLang = u"en"_q;
		payload = trimmed.mid(4).trimmed();
	} else if (trimmed.startsWith(u"/ru "_q, Qt::CaseInsensitive)) {
		toLang = u"ru"_q;
		payload = trimmed.mid(4).trimmed();
	} else if (trimmed.startsWith(u"/tr "_q, Qt::CaseInsensitive)) {
		const auto rest = trimmed.mid(4).trimmed();
		const auto sp = rest.indexOf(' ');
		if (sp <= 0) {
			return false;
		}
		toLang = rest.left(sp).trimmed().toLower();
		payload = rest.mid(sp + 1).trimmed();
	} else {
		return false;
	}
	if (payload.isEmpty() || toLang.isEmpty()) {
		return false;
	}

	const auto peer = peerId;
	const auto body = payload;
	const auto lang = toLang;
	crl::on_main([peer, body, lang] {
		const auto session = Core::App().maybePrimarySession();
		if (!session) {
			return;
		}
		auto args = Ayu::Translator::MultiThreadArgs();
		args.parsedData.text = { body };
		args.parsedData.fromLang = u"auto"_q;
		args.parsedData.toLang = lang;
		args.onSuccess = [peer, body](const TextWithEntities &translated) {
			const auto out = translated.text.isEmpty() ? body : translated.text;
			const auto peerData = getPeerFromDialogId(static_cast<uint64>(peer));
			if (!peerData) {
				return;
			}
			auto message = Api::MessageToSend(
				Api::SendAction(peerData->owner().history(peerData)));
			message.textWithTags = { out, TextWithTags::Tags() };
			if (const auto session = Core::App().maybePrimarySession()) {
				session->api().sendMessage(std::move(message));
			}
			if (const auto window = Core::App().activeWindow()) {
				Ui::Toast::Show(
					window->widget(),
					{ .text = { tr::ayu_OutgoingTranslated(tr::now) } });
			}
		};
		args.onFail = [] {
			if (const auto window = Core::App().activeWindow()) {
				Ui::Toast::Show(
					window->widget(),
					{ .text = { tr::ayu_OutgoingTranslateFailed(tr::now) } });
			}
		};

		const auto provider = AyuSettings::getInstance().translationProvider();
		if (provider == TranslationProvider::Yandex) {
			(void)Ayu::Translator::YandexTranslator::instance().startSingleTranslation(args);
		} else {
			(void)Ayu::Translator::GoogleTranslator::instance().startSingleTranslation(args);
		}
	});
	return true;
}

} // namespace

bool InPluginSend() {
	return t_pluginSendDepth > 0;
}

int AccountForSession(Main::Session *session) {
	if (!session) {
		return 0;
	}
	for (const auto &entry : Core::App().domain().accounts()) {
		const auto *account = entry.account.get();
		if (!account) {
			continue;
		}
		const auto *maybe = account->maybeSession();
		if (maybe == session) {
			return entry.index;
		}
	}
	return 0;
}

Main::Session *SessionForAccount(int account) {
	// Номер -1 и любой отрицательный означает «основной»: так его понимают
	// плагины, и так же он трактуется в Desktop.
	if (account < 0) {
		return Core::App().maybePrimarySession();
	}
	for (const auto &entry : Core::App().domain().accounts()) {
		if (entry.index != account) {
			continue;
		}
		const auto *owned = entry.account.get();
		if (!owned) {
			continue;
		}
		if (const auto *session = owned->maybeSession()) {
			// maybeSession отдаёт константный указатель, а сессией владеет
			// домен: менять её здесь нельзя, но и запрещать плагину звать её
			// неправильно.
			return const_cast<Main::Session *>(session);
		}
	}
	if (account == 0) {
		return Core::App().maybePrimarySession();
	}
	return nullptr;
}

int AuthedAccountCount() {
	auto count = 0;
	for (const auto &entry : Core::App().domain().accounts()) {
		const auto *account = entry.account.get();
		if (account && account->maybeSession()) {
			++count;
		}
	}
	return count;
}

PluginSendScope::PluginSendScope() {
	++t_pluginSendDepth;
}

PluginSendScope::~PluginSendScope() {
	--t_pluginSendDepth;
}

// Incoming messages are batched onto a single worker instead of one pool task
// each.
//
// Opening a chat replays its whole history at once, and one crl::async per
// message filled the shared pool: twelve workers were all sitting in
// SafeCallHookTraced waiting for the interpreter lock, so anything else that
// needs that pool stopped running and the window stopped responding. A dump of
// the frozen process showed exactly that, and it is why entering a chat was
// what triggered it.
//
// One task at a time also means plugin on_message hooks queue up serially
// instead of all contending for the lock simultaneously.
//
// It also runs on a queue of its own rather than through crl::async, which is
// the main thread. A dump of a frozen application showed the main thread
// waiting inside this code with no thread inside the interpreter at all: not a
// plugin taking too long, but the thread that draws the window sitting in a
// wait it cannot leave. Anything that can block, and this path takes two locks
// and the interpreter lock to do it, does not belong on that thread.
std::mutex g_inboxMutex;
std::vector<PluginMessage> g_inbox;
bool g_inboxScheduled = false;
crl::queue g_inboxQueue;

void RescheduleInbox();

void DrainInbox() {
	// Сделано так, чтобы очередь не могла встать навсегда.
	//
	// Раньше любой выход из тела функции оставлял g_inboxScheduled в true:
	// дальше каждый новый видел «разбор уже запланирован» и молча уходил, а
	// разбор больше никто не планировал. Сообщения копились в памяти и до
	// плагинов не доходили ни одно - при этом нативный счётчик продолжал
	// расти, и выглядело всё так, будто доставка работает.
	while (true) {
		std::vector<PluginMessage> batch;
		{
			const auto lock = std::lock_guard(g_inboxMutex);
			if (g_inbox.empty()) {
				g_inboxScheduled = false;
				return;
			}
			batch.swap(g_inbox);
		}
		// Пока хост не поднят, отдавать некуда: он стартует с главного
		// потока, а мы здесь рабочий. Сообщения возвращаются в очередь, и
		// разбор повторится, как только хост будет готов.
		if (!Host::instance().started()) {
			{
				const auto lock = std::lock_guard(g_inboxMutex);
				g_inboxScheduled = false;
				g_inbox.insert(g_inbox.begin(), batch.begin(), batch.end());
			}
			// Перепланирование - после снятия блокировки: RescheduleInbox
			// берёт тот же мьютекс, а он не переиспользуемый.
			RescheduleInbox();
			return;
		}
		for (const auto &msg : batch) {
			try {
				Host::instance().emitMessage(msg);
			} catch (const std::exception &e) {
			} catch (...) {
			}
		}
	}
}

void RescheduleInbox() {
	// Ставит разбор в очередь, если он ещё не стоит.
	{
		const auto lock = std::lock_guard(g_inboxMutex);
		if (g_inbox.empty() || g_inboxScheduled) {
			return;
		}
		g_inboxScheduled = true;
	}
	// Разбор идёт на главном потоке. С рабочего потока доставка не
	// доходила: захват GIL на нём в этом встроенном интерпретаторе не
	// возвращается, и очередь вставала после первого же пакета.
	crl::on_main(DrainInbox);
}

void OnMessageReceived(
		int64 msgId,
		int64 peerId,
		int64 fromId,
		const QString &text,
		int64 date,
		bool out,
		int account) {
	auto msg = PluginMessage();
	msg.account = account;
	msg.msgId = msgId;
	msg.peerId = peerId;
	msg.fromId = fromId;
	msg.text = text;
	msg.date = date;
	msg.out = out;
	{
		const auto lock = std::lock_guard(g_inboxMutex);
		g_inbox.push_back(std::move(msg));
	}
	RescheduleInbox();
}

bool OnMessageSending(int64 peerId, QString &text, int account) {
	// A message a plugin is sending itself must not run the hook again:
	// that is how a "rewrite and resend" plugin turns into one message per
	// character, and eventually blows the stack.
	if (InPluginSend()) {
		return true;
	}
	if (TryBuiltinOutgoingTranslate(peerId, text)) {
		return false;
	}
	auto ctx = MessageSendingContext();
	ctx.account = account;
	ctx.message.account = account;
	ctx.message.peerId = peerId;
	ctx.message.text = text;
	// Runs on the main thread, so the bridge refuses to make a plugin wait
	// for the GIL here: the answer decides whether the message is
	// cancelled, rewritten or sent as is.
	Bridge::SetHookOnMainThread(true);
	Host::instance().emitMessageSending(ctx);
	Bridge::SetHookOnMainThread(false);
	if (ctx.cancel) return false;
	if (ctx.rewrite) text = ctx.rewrittenText;
	return true;
}

namespace {

// Chat switching is bursty: clicking through ten chats queues ten deliveries,
// and each one waits for the interpreter lock while holding a thread-pool
// slot. With a slow plugin that is enough to starve the pool and leave the
// window unresponsive. Only the chat the user ended up in matters, so a burst
// collapses into one delivery carrying the newest.
std::mutex g_chatOpenedMutex;
std::optional<PluginChat> g_chatOpenedPending;
// The last chat actually opened, kept so a plugin can ask where it is instead of
// only being told. Kept separate from the pending slot on purpose: the pending
// value is cleared the moment it is taken for delivery, so a plugin that asked
// a moment later would be told nothing.
std::optional<PluginChat> g_chatOpenedLast;
bool g_chatOpenedScheduled = false;

void FlushChatOpened() {
	auto next = std::optional<PluginChat>();
	{
		const auto lock = std::lock_guard(g_chatOpenedMutex);
		next = g_chatOpenedPending;
		g_chatOpenedPending.reset();
		if (!next) {
			g_chatOpenedScheduled = false;
			return;
		}
	}
	Host::instance().emitChatOpened(*next);
	{
		const auto lock = std::lock_guard(g_chatOpenedMutex);
		if (!g_chatOpenedPending) {
			g_chatOpenedScheduled = false;
			return;
		}
	}
	// Something arrived while the hook was running; deliver that too.
	g_inboxQueue.async(FlushChatOpened);
}

} // namespace

void OnChatOpened(int64 peerId, const QString &title, int account) {
	{
		const auto lock = std::lock_guard(g_chatOpenedMutex);
		auto chat = PluginChat();
		chat.account = account;
		chat.peerId = peerId;
		chat.title = title;
		g_chatOpenedLast = chat;
		g_chatOpenedPending = std::move(chat);
		if (g_chatOpenedScheduled) {
			return;
		}
		g_chatOpenedScheduled = true;
	}
	g_inboxQueue.async(FlushChatOpened);
}

std::optional<PluginChat> LastChatOpened() {
	const auto lock = std::lock_guard(g_chatOpenedMutex);
	return g_chatOpenedLast;
}

// Typing updates are received but not delivered. See FlushTyping for why, and
// for what has to change before a plugin can be given one again.
//
// The state below is kept so that turning delivery back on is writing the loop
// again, rather than working out what was needed.
std::mutex g_typingMutex;
std::map<int64, PluginTyping> g_typingPending;
std::map<int64, crl::time> g_typingSent;
bool g_typingScheduled = false;
crl::queue g_typingQueue;
// How many were received and not passed on, so the number can be reported
// without delivering any of them.
size_t g_typingSeen = 0;

// The shortest gap between two deliveries to the same chat. Telegram sends a
// typing update every few hundred milliseconds, and calling a plugin that often
// buys it nothing: the answer it wanted has not changed.
constexpr auto kTypingMinIntervalMs = 1000;

void FlushTyping() {
	// Delivery is off, and this time it is not going to be argued back on.
	//
	// Two runs with it off lasted 93 and 67 minutes with nothing happening. Two
	// runs with it on each froze the application inside 40-90 minutes, and the
	// freeze is the bad kind: the window stops answering and nothing anywhere
	// records why, because it happens with the interpreter lock held, and every
	// in-process reporter needs that lock to say anything.
	//
	// The cause is not the plumbing, which delivers on a queue of its own
	// precisely so that plugin code stays off the thread that draws the window.
	// It is that a plugin handler is ordinary Python: while it runs, nothing
	// else in the process can. draft_ghost reacts to typing by spawning session
	// bookkeeping of its own, and that work ends up under the same lock.
	//
	// So the updates are counted and dropped. Re-enabling this needs the handler
	// to be interruptible or the work to move off the interpreter entirely, and
	// neither can be done from here.
	auto batch = std::map<int64, PluginTyping>();
	{
		const auto lock = std::lock_guard(g_typingMutex);
		if (g_typingPending.empty()) {
			g_typingScheduled = false;
			return;
		}
		batch.swap(g_typingPending);
	}
	g_typingSeen += batch.size();
	{
		const auto lock = std::lock_guard(g_typingMutex);
		if (g_typingPending.empty()) {
			g_typingScheduled = false;
			return;
		}
	}
	g_typingQueue.async(FlushTyping);
}

void OnPeerTyping(
	int64 peerId,
	int64 userId,
	const QString &action,
	int64 topMsgId,
	int kind,
	int account) {
	if (peerId == 0) {
		return;
	}
	{
		const auto lock = std::lock_guard(g_typingMutex);
		auto typing = PluginTyping();
		typing.account = account;
		typing.peerId = peerId;
		typing.userId = userId;
		typing.action = action;
		typing.topMsgId = topMsgId;
		typing.kind = kind;
		// Telegram repeats this several times a second while someone types, and
		// a plugin asking "is anyone typing" does not need every one of them.
		// The newest state wins, so the pending value is replaced rather than
		// appended, and a chat that has been quiet for a moment is skipped
		// instead of waking a handler for a state it has already reported.
		auto last = g_typingSent.find(peerId);
		const auto now = crl::time(0);
		if (last != g_typingSent.end()
			&& now - last->second < kTypingMinIntervalMs) {
			g_typingPending[peerId] = std::move(typing);
			if (!g_typingScheduled) {
				g_typingScheduled = true;
			} else {
				return;
			}
		} else {
			g_typingSent[peerId] = now;
			g_typingPending[peerId] = std::move(typing);
			if (g_typingScheduled) {
				return;
			}
			g_typingScheduled = true;
		}
	}
	g_typingQueue.async(FlushTyping);
}

void OnMessageDeleted(int64 peerId, const std::vector<int64> &msgIds, int account) {
	auto ctx = MessageDeletedContext();
	ctx.account = account;
	ctx.peerId = peerId;
	ctx.msgIds = msgIds;
	g_inboxQueue.async([ctx = std::move(ctx)] {
		Host::instance().emitDeleted(ctx);
	});
}

bool OnMediaSending(
		int64 peerId,
		const std::vector<PluginMedia> &media,
		int account) {
	// Answers synchronously: this runs on the thread that is about to start an
	// upload, and a plugin that stops the send has to be able to stop it before
	// the first byte goes out. Waiting for a queued delivery would mean the file
	// is already on its way.
	//
	// It is its own event, not the text one. Delivering a file as an empty
	// message handed every text plugin an event it cannot act on, and a plugin
	// that rewrites messages would be asked to rewrite nothing.
	auto ctx = MessageSendingContext();
	ctx.account = account;
	ctx.message.account = account;
	ctx.message.peerId = peerId;
	ctx.media = media;
	return Host::instance().emitMediaSending(ctx);
}

} // namespace Ayu::Plugins::Hooks
