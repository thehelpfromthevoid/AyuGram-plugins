// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
//
// AyuGram plugin host for Desktop, (c) @CE0fbi
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
	OnTyping = 7,
	OnMediaSending = 8,
};

struct PluginTyping {
	int account = 0;
	int64 peerId = 0;
	int64 userId = 0;
	QString action;
	int64 topMsgId = 0;
	// Telegram sends three separate typing updates that differ only in which
	// id they carry. A plugin subscribed to more than one of them has to be
	// told which one arrived, or a channel notification reads as a private one.
	// 0 = user, 1 = chat, 2 = channel.
	int kind = 0;
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
	case HookId::OnTyping: return u"on_typing"_q;
	case HookId::OnMediaSending: return u"on_media_sending"_q;
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
	if (s == u"on_typing"_q) { out = HookId::OnTyping; return true; }
	if (s == u"on_media_sending"_q) { out = HookId::OnMediaSending; return true; }
	return false;
}

struct PluginMessage {
	// Номер аккаунта, которому принадлежит событие. Раньше его не было
	// вовсе, и плагин не мог отличить, из какого аккаунта пришло
	// сообщение: номер всегда был нулём, то есть всегда основной.
	int account = 0;
	int64 msgId = 0;
	int64 peerId = 0;
	int64 fromId = 0;
	QString text;
	int64 date = 0;
	bool out = false;
	bool post = false;
};

struct PluginChat {
	int account = 0;
	int64 peerId = 0;
	QString title;
	int type = 0;
};

// An outgoing attachment, as far as a plugin can act on it. There is no
// Telegram document here on purpose: what a plugin gets is the file that is
// about to be uploaded, which is the thing it can reason about. Reading it
// costs nothing and rewriting it is refused rather than faked, because a
// half-replaced attachment would send the wrong file.
struct PluginMedia {
	QString name;
	QString path;
	QString caption;
	int64 size = 0;
// None, Photo, Video, Music, File - the same set the sender distinguishes.
		int kind = 0;
		bool sticker = false;
		// Set by a plugin to send a different file instead of this one. This is
		// what an Android plugin means by reaching into the send parameters and
		// replacing the document: there is no other way to change what goes
		// out. Applied by rebuilding the entry from this path, so nothing
		// shared with the local storage is touched.
		QString replacementPath;
	};

struct MessageSendingContext {
	int account = 0;
	PluginMessage message;
	bool cancel = false;
	QString rewrittenText;
	bool rewrite = false;
	// Set when this is a media send rather than a text send. Kept out of the
	// text event on purpose: a plugin that only knows about messages should not
	// suddenly be handed an empty one every time a file is sent, and one that
	// has to ask twice is worse than one that says what it is looking at.
	std::vector<PluginMedia> media;
};

struct MessageDeletedContext {
	int account = 0;
	int64 peerId = 0;
	std::vector<int64> msgIds;
};

} // namespace Ayu::Plugins
