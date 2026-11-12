// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include <QString>
#include <optional>
#include <vector>

#include "ayu/plugins/plugin_events.h"

// Вперёд, чтобы заголовок не тянул за собой всю сессию Telegram.
namespace Main {
class Session;
}

namespace Ayu::Plugins::Hooks {

// Номер аккаунта и сессия по номеру.
//
// Раньше номер аккаунта не передавался никуда, и весь хост работал только с
// основным: плагин получал события и отвечал от первого аккаунта, а на
// втором и третьем молчал. Сессия берётся по номеру аккаунта, поэтому
// вызовы плагина попадают туда же, откуда пришло событие.
int AccountForSession(Main::Session *session);
Main::Session *SessionForAccount(int account);
int AuthedAccountCount();

// True while a message sent by a plugin itself is being delivered. Such a
// message must not re-enter the on_message_sending hook: a plugin that
// rewrites and re-sends would otherwise recurse once per character.
bool InPluginSend();

// Scoped guard around a plugin-initiated send.
class PluginSendScope {
public:
	PluginSendScope();
	~PluginSendScope();

	PluginSendScope(const PluginSendScope &) = delete;
	PluginSendScope &operator=(const PluginSendScope &) = delete;
};

void OnMessageReceived(
		int64 msgId,
		int64 peerId,
		int64 fromId,
		const QString &text,
		int64 date,
		bool out,
		int account);

bool OnMessageSending(
	int64 peerId,
	QString &text,
		int account);

void OnChatOpened(int64 peerId, const QString &title, int account);

// The last chat that was opened, or nothing if none has been since startup.
// Lets a plugin find out where it is instead of only being told when it
// changes, which it can miss: the delivery is coalesced across a burst of
// switching, and a plugin that loads after the chat is already open never sees
// the event at all.
std::optional<PluginChat> LastChatOpened();

void OnMessageDeleted(int64 peerId, const std::vector<int64> &msgIds, int account);

// Outgoing attachments, before the upload starts. Answers whether the send
// should go ahead: a plugin may stop it, the same way it can stop a text send,
// but it cannot swap the file - half a replacement would upload the wrong one.
bool OnMediaSending(int64 peerId, const std::vector<PluginMedia> &media, int account);

// Somebody is typing, uploading or recording in a chat. Telegram sends this
// constantly - a few times a second while someone writes - so the delivery is
// coalesced per peer: a plugin that wants to know a chat went quiet only needs
// the latest state, and delivering every keystroke would put the plugin on the
// message path for no reason.
void OnPeerTyping(
		int64 peerId,
		int64 userId,
		const QString &action,
		int64 topMsgId,
		int kind,
		int account);

} // namespace Ayu::Plugins::Hooks
