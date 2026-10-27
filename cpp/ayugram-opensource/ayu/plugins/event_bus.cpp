// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
//
// AyuGram plugin host for Desktop, (c) @CE0fbi
#include "ayu/plugins/event_bus.h"

#include <pybind11/pybind11.h>

namespace py = pybind11;

namespace Ayu::Plugins {

EventBus &EventBus::instance() {
	static EventBus bus;
	return bus;
}

int EventBus::subscribeMessage(MessageCallback cb) {
	const auto lock = std::lock_guard(_mutex);
	const auto t = _nextToken++;
	_onMessage.emplace(t, std::move(cb));
	return t;
}

int EventBus::subscribeMessageSending(MessageSendingCallback cb) {
	const auto lock = std::lock_guard(_mutex);
	const auto t = _nextToken++;
	_onSending.emplace(t, std::move(cb));
	return t;
}

int EventBus::subscribeDeleted(MessageDeletedCallback cb) {
	const auto lock = std::lock_guard(_mutex);
	const auto t = _nextToken++;
	_onDeleted.emplace(t, std::move(cb));
	return t;
}

int EventBus::subscribeChatOpened(ChatOpenedCallback cb) {
	const auto lock = std::lock_guard(_mutex);
	const auto t = _nextToken++;
	_onChat.emplace(t, std::move(cb));
	return t;
}

int EventBus::subscribeAppStart(SimpleCallback cb) {
	const auto lock = std::lock_guard(_mutex);
	const auto t = _nextToken++;
	_onStart.emplace(t, std::move(cb));
	return t;
}

int EventBus::subscribeAppStop(SimpleCallback cb) {
	const auto lock = std::lock_guard(_mutex);
	const auto t = _nextToken++;
	_onStop.emplace(t, std::move(cb));
	return t;
}

int EventBus::subscribeSettingChanged(SettingChangedCallback cb) {
	const auto lock = std::lock_guard(_mutex);
	const auto t = _nextToken++;
	_onSetting.emplace(t, std::move(cb));
	return t;
}

int EventBus::subscribeTyping(TypingCallback cb) {
	const auto lock = std::lock_guard(_mutex);
	const auto t = _nextToken++;
	_onTyping.emplace(t, std::move(cb));
	return t;
}

void EventBus::unsubscribe(int token) {
	const auto lock = std::lock_guard(_mutex);
	_onMessage.erase(token);
	_onSending.erase(token);
	_onDeleted.erase(token);
	_onChat.erase(token);
	_onStart.erase(token);
	_onStop.erase(token);
	_onSetting.erase(token);
	_onTyping.erase(token);
	_onMedia.erase(token);
}

void EventBus::emitMessage(const PluginMessage &msg) {
	// The interpreter lock covers the whole function, not just the
	// calls: copying the callback list copies std::functions that hold
	// py::objects, and every copy and destruction moves a Python
	// reference count. Doing that without the GIL corrupts the
	// interpreter - the fault showed a pointer full of 0xDD, the
	// pattern an allocator leaves in freed memory.
	auto gil = py::gil_scoped_acquire();
	auto copy = std::vector<MessageCallback>();
	{
		const auto lock = std::lock_guard(_mutex);
		if (_onMessage.empty()) return;
		for (const auto &[_, cb] : _onMessage) copy.push_back(cb);
	}
	for (auto &cb : copy) {
		try { cb(msg); } catch (...) {}
	}
}

bool EventBus::emitMessageSending(MessageSendingContext &ctx) {
	// The interpreter lock covers the whole function, not just the
	// calls: copying the callback list copies std::functions that hold
	// py::objects, and every copy and destruction moves a Python
	// reference count. Doing that without the GIL corrupts the
	// interpreter - the fault showed a pointer full of 0xDD, the
	// pattern an allocator leaves in freed memory.
	auto gil = py::gil_scoped_acquire();
	auto copy = std::vector<MessageSendingCallback>();
	{
		const auto lock = std::lock_guard(_mutex);
		if (_onSending.empty()) return true;
		for (const auto &[_, cb] : _onSending) copy.push_back(cb);
	}
	for (auto &cb : copy) {
		try {
			cb(ctx);
			if (ctx.cancel) return false;
		} catch (...) {}
	}
	return true;
}

void EventBus::emitDeleted(const MessageDeletedContext &ctx) {
	// The interpreter lock covers the whole function, not just the
	// calls: copying the callback list copies std::functions that hold
	// py::objects, and every copy and destruction moves a Python
	// reference count. Doing that without the GIL corrupts the
	// interpreter - the fault showed a pointer full of 0xDD, the
	// pattern an allocator leaves in freed memory.
	auto gil = py::gil_scoped_acquire();
	auto copy = std::vector<MessageDeletedCallback>();
	{
		const auto lock = std::lock_guard(_mutex);
		for (const auto &[_, cb] : _onDeleted) copy.push_back(cb);
	}
	for (auto &cb : copy) {
		try { cb(ctx); } catch (...) {}
	}
}

void EventBus::emitChatOpened(const PluginChat &chat) {
	// The interpreter lock covers the whole function, not just the
	// calls: copying the callback list copies std::functions that hold
	// py::objects, and every copy and destruction moves a Python
	// reference count. Doing that without the GIL corrupts the
	// interpreter - the fault showed a pointer full of 0xDD, the
	// pattern an allocator leaves in freed memory.
	auto gil = py::gil_scoped_acquire();
	auto copy = std::vector<ChatOpenedCallback>();
	{
		const auto lock = std::lock_guard(_mutex);
		for (const auto &[_, cb] : _onChat) copy.push_back(cb);
	}
	for (auto &cb : copy) {
		try { cb(chat); } catch (...) {}
	}
}

void EventBus::emitAppStart() {
	// The interpreter lock covers the whole function, not just the
	// calls: copying the callback list copies std::functions that hold
	// py::objects, and every copy and destruction moves a Python
	// reference count. Doing that without the GIL corrupts the
	// interpreter - the fault showed a pointer full of 0xDD, the
	// pattern an allocator leaves in freed memory.
	auto gil = py::gil_scoped_acquire();
	auto copy = std::vector<SimpleCallback>();
	{
		const auto lock = std::lock_guard(_mutex);
		for (const auto &[_, cb] : _onStart) copy.push_back(cb);
	}
	for (auto &cb : copy) {
		try { cb(); } catch (...) {}
	}
}

void EventBus::emitAppStop() {
	// The interpreter lock covers the whole function, not just the
	// calls: copying the callback list copies std::functions that hold
	// py::objects, and every copy and destruction moves a Python
	// reference count. Doing that without the GIL corrupts the
	// interpreter - the fault showed a pointer full of 0xDD, the
	// pattern an allocator leaves in freed memory.
	auto gil = py::gil_scoped_acquire();
	auto copy = std::vector<SimpleCallback>();
	{
		const auto lock = std::lock_guard(_mutex);
		for (const auto &[_, cb] : _onStop) copy.push_back(cb);
	}
	for (auto &cb : copy) {
		try { cb(); } catch (...) {}
	}
}

void EventBus::emitSettingChanged(const QString &key, const QString &value) {
	// The interpreter lock covers the whole function, not just the
	// calls: copying the callback list copies std::functions that hold
	// py::objects, and every copy and destruction moves a Python
	// reference count. Doing that without the GIL corrupts the
	// interpreter - the fault showed a pointer full of 0xDD, the
	// pattern an allocator leaves in freed memory.
	auto gil = py::gil_scoped_acquire();
	auto copy = std::vector<SettingChangedCallback>();
	{
		const auto lock = std::lock_guard(_mutex);
		for (const auto &[_, cb] : _onSetting) copy.push_back(cb);
	}
	for (auto &cb : copy) {
		try { cb(key, value); } catch (...) {}
	}
}

int EventBus::subscribeMediaSending(MediaSendingCallback cb) {
	const auto lock = std::lock_guard(_mutex);
	const auto t = _nextToken++;
	_onMedia.emplace(t, std::move(cb));
	return t;
}

void EventBus::emitTyping(const PluginTyping &typing) {
	// The interpreter lock covers the whole function, not just the
	// calls: copying the callback list copies std::functions that hold
	// py::objects, and every copy and destruction moves a Python
	// reference count. Doing that without the GIL corrupts the
	// interpreter - the fault showed a pointer full of 0xDD, the
	// pattern an allocator leaves in freed memory.
	auto gil = py::gil_scoped_acquire();
	auto copy = std::vector<TypingCallback>();
	{
		const auto lock = std::lock_guard(_mutex);
		for (const auto &[_, cb] : _onTyping) copy.push_back(cb);
	}
	for (auto &cb : copy) {
		try { cb(typing); } catch (...) {}
	}
}

bool EventBus::emitMediaSending(MessageSendingContext &ctx) {
	// The interpreter lock covers the whole function, for the same reason as
	// every other emit here: the callback list holds py::objects and copying it
	// moves Python reference counts.
	auto gil = py::gil_scoped_acquire();
	auto copy = std::vector<MediaSendingCallback>();
	{
		const auto lock = std::lock_guard(_mutex);
		for (const auto &[_, cb] : _onMedia) copy.push_back(cb);
	}
	for (auto &cb : copy) {
		try { cb(ctx); } catch (...) {}
	}
	return !ctx.cancel;
}

void EventBus::clear() {
	const auto lock = std::lock_guard(_mutex);
	_onMessage.clear();
	_onSending.clear();
	_onDeleted.clear();
	_onChat.clear();
	_onStart.clear();
	_onStop.clear();
	_onSetting.clear();
	_onTyping.clear();
	_onMedia.clear();
}

int EventBus::subscriberCount() const {
	const auto lock = std::lock_guard(_mutex);
	return int(_onMessage.size()
		+ _onSending.size()
		+ _onDeleted.size()
		+ _onChat.size()
		+ _onStart.size()
		+ _onStop.size()
		+ _onSetting.size()
		+ _onTyping.size()
		+ _onMedia.size());
}

} // namespace Ayu::Plugins
