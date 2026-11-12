// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "ayu/plugins/plugin_events.h"

#include <functional>
#include <map>
#include <mutex>
#include <vector>

namespace Ayu::Plugins {

using MessageCallback = std::function<void(const PluginMessage &)>;
using MessageSendingCallback = std::function<void(MessageSendingContext &)>;
using MessageDeletedCallback = std::function<void(const MessageDeletedContext &)>;
using ChatOpenedCallback = std::function<void(const PluginChat &)>;
using TypingCallback = std::function<void(const PluginTyping &)>;
using MediaSendingCallback = std::function<void(MessageSendingContext &)>;
using SimpleCallback = std::function<void()>;
using SettingChangedCallback = std::function<void(const QString &, const QString &)>;

class EventBus final {
public:
	static EventBus &instance();

	int subscribeMessage(MessageCallback cb);
	int subscribeMessageSending(MessageSendingCallback cb);
	int subscribeDeleted(MessageDeletedCallback cb);
	int subscribeChatOpened(ChatOpenedCallback cb);
	int subscribeAppStart(SimpleCallback cb);
	int subscribeAppStop(SimpleCallback cb);
	int subscribeSettingChanged(SettingChangedCallback cb);
	int subscribeTyping(TypingCallback cb);
	int subscribeMediaSending(MediaSendingCallback cb);
	void unsubscribe(int token);

	void emitMessage(const PluginMessage &msg);
	bool emitMessageSending(MessageSendingContext &ctx);
	void emitDeleted(const MessageDeletedContext &ctx);
	void emitChatOpened(const PluginChat &chat);
	void emitAppStart();
	void emitAppStop();
	void emitSettingChanged(const QString &key, const QString &value);
	void emitTyping(const PluginTyping &typing);
	bool emitMediaSending(MessageSendingContext &ctx);

	void clear();

	// Total live subscriptions, so a plugin can confirm the app wired it up.
	int subscriberCount() const;

private:
	EventBus() = default;
	mutable std::mutex _mutex;
	int _nextToken = 1;
	std::map<int, MessageCallback> _onMessage;
	std::map<int, MessageSendingCallback> _onSending;
	std::map<int, MessageDeletedCallback> _onDeleted;
	std::map<int, ChatOpenedCallback> _onChat;
	std::map<int, SimpleCallback> _onStart;
	std::map<int, SimpleCallback> _onStop;
	std::map<int, SettingChangedCallback> _onSetting;
	std::map<int, TypingCallback> _onTyping;
	std::map<int, MediaSendingCallback> _onMedia;
};

} // namespace Ayu::Plugins
