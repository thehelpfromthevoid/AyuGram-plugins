// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "ayu/plugins/plugin_manifest.h"

#include <QJsonObject>
#include <QMap>
#include <QString>
#include <QVariant>
#include <QVector>

class QString;

namespace Ayu::Plugins {

class PythonHost final {
public:
	static PythonHost &instance();

	[[nodiscard]] bool init();
	void shutdown();
	[[nodiscard]] bool available() const { return _available; }

	bool loadPlugin(const PluginManifest &manifest, QString *error);
	void unloadPlugin(const QString &id);

	void logError(const QString &pluginId, const QString &msg);


private:
	PythonHost() = default;
	bool _available = false;
	bool _inited = false;
};

} // namespace Ayu::Plugins
