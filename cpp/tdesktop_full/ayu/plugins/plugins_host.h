// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "ayu/plugins/plugin_events.h"

class QString;

namespace Ayu::Plugins {

class Host final {
public:
	static Host &instance();

	void init();
	void ensureStarted();
	void shutdown();

	void emitMessage(const PluginMessage &msg);
	bool emitMessageSending(MessageSendingContext &ctx);
	void emitDeleted(const MessageDeletedContext &ctx);
	void emitChatOpened(const PluginChat &chat);
	void emitTyping(const PluginTyping &typing);
	bool emitMediaSending(MessageSendingContext &ctx);
	void emitAppStart();
	void emitAppStop();

	[[nodiscard]] bool pythonAvailable() const { return _pythonAvailable; }
	[[nodiscard]] bool started() const { return _started; }

private:
	Host() = default;
	bool _inited = false;
	bool _started = false;
	bool _pythonAvailable = false;
};

} // namespace Ayu::Plugins
