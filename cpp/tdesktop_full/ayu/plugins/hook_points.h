// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include <QString>
#include <vector>

namespace Ayu::Plugins::Hooks {

void OnMessageReceived(
	int64 msgId,
	int64 peerId,
	int64 fromId,
	const QString &text,
	int64 date,
	bool out);

bool OnMessageSending(
	int64 peerId,
	QString &text);

void OnChatOpened(int64 peerId, const QString &title);

void OnMessageDeleted(int64 peerId, const std::vector<int64> &msgIds);

} // namespace Ayu::Plugins::Hooks
