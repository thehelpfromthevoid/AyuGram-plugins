// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/plugins/hook_points.h"

#include "api/api_common.h"
#include "apiwrap.h"
#include "ayu/ayu_settings.h"
#include "ayu/features/translator/implementations/google.h"
#include "ayu/features/translator/implementations/yandex.h"
#include "ayu/plugins/plugins_host.h"
#include "ayu/utils/telegram_helpers.h"
#include "core/application.h"
#include "crl/crl_async.h"
#include "data/data_session.h"
#include "history/history.h"
#include "lang_auto.h"
#include "main/main_session.h"
#include "ui/text/text_entity.h"
#include "ui/toast/toast.h"
#include "window/window_controller.h"

namespace Ayu::Plugins::Hooks {
namespace {

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
			const auto peerData = getPeerFromDialogId(static_cast<ID>(peer));
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

void OnMessageReceived(
		int64 msgId,
		int64 peerId,
		int64 fromId,
		const QString &text,
		int64 date,
		bool out) {
	auto msg = PluginMessage();
	msg.msgId = msgId;
	msg.peerId = peerId;
	msg.fromId = fromId;
	msg.text = text;
	msg.date = date;
	msg.out = out;
	crl::async([msg = std::move(msg)] {
		Host::instance().emitMessage(msg);
	});
}

bool OnMessageSending(int64 peerId, QString &text) {
	if (TryBuiltinOutgoingTranslate(peerId, text)) {
		return false;
	}
	auto ctx = MessageSendingContext();
	ctx.message.peerId = peerId;
	ctx.message.text = text;
	const auto ok = Host::instance().emitMessageSending(ctx);
	if (!ok) return false;
	if (ctx.cancel) return false;
	if (ctx.rewrite) text = ctx.rewrittenText;
	return true;
}

void OnChatOpened(int64 peerId, const QString &title) {
	auto chat = PluginChat();
	chat.peerId = peerId;
	chat.title = title;
	crl::async([chat = std::move(chat)] {
		Host::instance().emitChatOpened(chat);
	});
}

void OnMessageDeleted(int64 peerId, const std::vector<int64> &msgIds) {
	auto ctx = MessageDeletedContext();
	ctx.peerId = peerId;
	ctx.msgIds = msgIds;
	crl::async([ctx = std::move(ctx)] {
		Host::instance().emitDeleted(ctx);
	});
}

} // namespace Ayu::Plugins::Hooks
