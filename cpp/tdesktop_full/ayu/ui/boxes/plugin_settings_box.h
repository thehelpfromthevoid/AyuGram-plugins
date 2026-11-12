// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "base/weak_ptr.h"

#include <QtCore/QString>

namespace Window {
class SessionController;
} // namespace Window

namespace Ayu::Plugins::SettingsBox {

// Opens the settings dialog for a plugin, built from the rows the plugin
// declared through create_settings(). Shows a short notice instead when the
// plugin declares nothing editable, which is not an error: most plugins only
// ever read their settings file.
void ShowPluginSettingsBox(
	not_null<Window::SessionController*> controller,
	const QString &pluginId);

} // namespace Ayu::Plugins::SettingsBox