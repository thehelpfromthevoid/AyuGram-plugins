// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
//
// AyuGram plugin host for Desktop, (c) @CE0fbi
#include "ayu/plugins/plugins_host.h"

#include "ayu/plugins/event_bus.h"
#include "ayu/plugins/plugins_manager.h"
#include "ayu/plugins/python_host.h"
#include "crl/crl_async.h"

namespace Ayu::Plugins {

Host &Host::instance() {
	static Host h;
	return h;
}

void Host::init() {
	if (_inited) return;
	_inited = true;
	crl::on_main([] {
		Host::instance().ensureStarted();
	});
}

void Host::ensureStarted() {
	if (!_inited || _started) {
		return;
	}
	// Только с главного потока. Старт поднимает интерпретатор и грузит
	// плагины, а разбор входящих идёт из рабочего потока: тот ждал главный,
	// а главный ждал интерпретатор - и вставали оба. Из рабочего потока
	// сообщения теперь просто ждут, пока хост поднимется.
	if (QCoreApplication::instance()
		&& (QThread::currentThread()
			!= QCoreApplication::instance()->thread())) {
		return;
	}
	_started = true;
	_pythonAvailable = PythonHost::instance().init();
	Manager::instance().setPythonAvailable(_pythonAvailable);
	Manager::instance().scan(false);
	emitAppStart();
}

void Host::shutdown() {
	if (!_inited) return;
	if (_started) {
		emitAppStop();
		PythonHost::instance().shutdown();
	}
	_inited = false;
	_started = false;
	_pythonAvailable = false;
}

void Host::emitMessage(const PluginMessage &msg) {
	ensureStarted();
	if (!_started) return;
	EventBus::instance().emitMessage(msg);
}

bool Host::emitMessageSending(MessageSendingContext &ctx) {
	ensureStarted();
	if (!_started) return true;
	return EventBus::instance().emitMessageSending(ctx);
}

void Host::emitDeleted(const MessageDeletedContext &ctx) {
	ensureStarted();
	if (!_started) return;
	EventBus::instance().emitDeleted(ctx);
}

void Host::emitChatOpened(const PluginChat &chat) {
	ensureStarted();
	if (!_started) return;
	EventBus::instance().emitChatOpened(chat);
}

void Host::emitTyping(const PluginTyping &typing) {
	ensureStarted();
	if (!_started) return;
	EventBus::instance().emitTyping(typing);
}

bool Host::emitMediaSending(MessageSendingContext &ctx) {
	ensureStarted();
	if (!_started) return true;
	return EventBus::instance().emitMediaSending(ctx);
}

void Host::emitAppStart() {
	if (!_started) return;
	EventBus::instance().emitAppStart();
}

void Host::emitAppStop() {
	EventBus::instance().emitAppStop();
}

} // namespace Ayu::Plugins
