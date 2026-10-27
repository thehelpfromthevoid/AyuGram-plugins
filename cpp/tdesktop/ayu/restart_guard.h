/*
This file is part of AyuGram for Desktop,
the Desktop client with the AyuGram plugin host.

For license and copyright information please follow this link:
https://github.com/AyuGram/AyuGramDesktop/blob/master/LEGAL

Author of the AyuGram Desktop changes: @CE0fbi
*/
#pragma once

#include <QtCore/QString>
#include <QtCore/QStringList>

namespace Ayu {

// Whether these arguments describe the watch process rather than the client.
// The client is started with the very same executable, so this is how the two
// tell themselves apart.
[[nodiscard]] bool IsRestartMonitor(const QStringList &arguments);

// Runs the watch process: waits for the given process to end and starts the
// client again, then keeps watching the new one. Never returns.
[[nodiscard]] int RunRestartMonitor(
	const QString &executable,
	const QStringList &arguments);

// Starts the watch process for this client. Safe to call more than once: the
// second call does nothing.
void EnsureRestartMonitor(const QString &executable);

} // namespace Ayu
