// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "ayu/plugins/plugin_events.h"

#include <QString>
#include <QStringList>
#include <vector>

namespace Ayu::Plugins {

inline constexpr int kPluginApiVersion = 1;
inline constexpr int kMaxDescriptionLength = 8000;

struct PluginManifest {
	QString id;
	QString name;
	QString description;
	QString author;
	QString version = u"1.0"_q;
	QString minAppVersion;
	int apiVersion = kPluginApiVersion;
	QString entry = u"main.py"_q;
	std::vector<QString> hooks;
	std::vector<QString> permissions;
	QStringList requirements;
	QString icon;
	bool trusted = false;
	QString sourcePath;
	// Platforms the plugin can run on. Empty means "any". A plugin that
	// lists only android is skipped without executing its entry script,
	// instead of raising ModuleNotFoundError on every launch.
	QStringList platforms;
};

[[nodiscard]] bool ManifestRunsHere(const PluginManifest &m);
// Everything that would make the app refuse or skip this plugin, in the
// words a user can act on. Empty means the manifest is fine.
[[nodiscard]] QStringList ManifestProblems(const PluginManifest &m);
[[nodiscard]] bool ValidateManifest(const PluginManifest &m, QString *error = nullptr);
[[nodiscard]] bool ManifestHasPermission(const PluginManifest &m, const QString &perm);
[[nodiscard]] bool ParseManifestJson(const QByteArray &data, PluginManifest &out, QString *error = nullptr);
[[nodiscard]] QString ExampleManifestJson();
[[nodiscard]] PluginManifest ManifestFromLegacyMetadata(
	const QString &id,
	const QString &name,
	const QString &description,
	const QString &author,
	const QString &version,
	const QString &minVersion,
	const QStringList &requirements,
	const QString &icon);

} // namespace Ayu::Plugins
