// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include <QString>
#include <cstdint>
#include <vector>

namespace Ayu::Plugins {

enum class HookId : int {
	OnMessage = 0,
	OnMessageSending = 1,
	OnMessageDeleted = 2,
	OnChatOpened = 3,
	OnAppStart = 4,
	OnAppStop = 5,
	OnSettingChanged = 6,
};

[[nodiscard]] inline QString HookIdToString(HookId id) {
	switch (id) {
	case HookId::OnMessage: return u"on_message"_q;
	case HookId::OnMessageSending: return u"on_message_sending"_q;
	case HookId::OnMessageDeleted: return u"on_message_deleted"_q;
	case HookId::OnChatOpened: return u"on_chat_opened"_q;
	case HookId::OnAppStart: return u"on_app_start"_q;
	case HookId::OnAppStop: return u"on_app_stop"_q;
	case HookId::OnSettingChanged: return u"on_setting_changed"_q;
	}
	return u"unknown"_q;
}

[[nodiscard]] inline bool HookIdFromString(const QString &s, HookId &out) {
	if (s == u"on_message"_q) { out = HookId::OnMessage; return true; }
	if (s == u"on_message_sending"_q) { out = HookId::OnMessageSending; return true; }
	if (s == u"on_message_deleted"_q) { out = HookId::OnMessageDeleted; return true; }
	if (s == u"on_chat_opened"_q) { out = HookId::OnChatOpened; return true; }
	if (s == u"on_app_start"_q) { out = HookId::OnAppStart; return true; }
	if (s == u"on_app_stop"_q) { out = HookId::OnAppStop; return true; }
	if (s == u"on_setting_changed"_q) { out = HookId::OnSettingChanged; return true; }
	return false;
}

struct PluginMessage {
	int64 msgId = 0;
	int64 peerId = 0;
	int64 fromId = 0;
	QString text;
	int64 date = 0;
	bool out = false;
	bool post = false;
};

struct PluginChat {
	int64 peerId = 0;
	QString title;
	int type = 0;
};

struct MessageSendingContext {
	PluginMessage message;
	bool cancel = false;
	QString rewrittenText;
	bool rewrite = false;
};

struct MessageDeletedContext {
	int64 peerId = 0;
	std::vector<int64> msgIds;
};

} // namespace Ayu::Plugins
