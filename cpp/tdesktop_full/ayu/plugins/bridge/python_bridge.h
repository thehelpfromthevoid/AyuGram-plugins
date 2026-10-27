// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "ayu/plugins/plugin_manifest.h"

class QString;

namespace Ayu::Plugins::Bridge {

[[nodiscard]] bool InitPython();
void ShutdownPython();
bool LoadPlugin(const PluginManifest &manifest, QString *error);
void UnloadPlugin(const QString &id);

} // namespace Ayu::Plugins::Bridge
