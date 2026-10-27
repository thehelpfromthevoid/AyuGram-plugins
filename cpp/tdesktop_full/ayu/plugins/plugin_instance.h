// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "ayu/plugins/plugin_manifest.h"

#include <QString>
#include <vector>

namespace Ayu::Plugins {

enum class PluginState {
	Loaded = 0,
	Disabled = 1,
	Error = 2,
};

struct PluginInstance {
	PluginManifest manifest;
	QString dirPath;
	PluginState state = PluginState::Disabled;
	QString lastError;
	std::vector<int> busTokens;
	bool pythonLoaded = false;
};

} // namespace Ayu::Plugins
