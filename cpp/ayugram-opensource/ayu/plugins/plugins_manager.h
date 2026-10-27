// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "ayu/plugins/plugin_instance.h"
#include "crl/common/crl_common_config.h"

#include <QByteArray>
#include <QJsonObject>
#include <QMap>
#include <QVariant>

#include <map>
#include <memory>
#include <mutex>
#include <vector>

class QFileSystemWatcher;
class QTimer;
class QString;

namespace Ayu::Plugins {

// What the built-in check concluded about one plugin. `problems` are things
// to fix, `notes` are things worth knowing; a plugin with no problems and no
// notes is working.
struct PluginHealth {
	QString id;
	QString name;
	PluginState state = PluginState::Disabled;
	QStringList problems;
	QStringList notes;
	[[nodiscard]] bool ok() const {
		return problems.isEmpty() && state != PluginState::Error;
	}
};

class Manager final {
public:
	static Manager &instance();

	[[nodiscard]] QString pluginsRoot() const;
	[[nodiscard]] QString pluginDataRoot() const;
	[[nodiscard]] QString pluginsStatePath() const;
	[[nodiscard]] QString pluginLogPath(const QString &id) const;
	[[nodiscard]] QString pluginSettingsPath(const QString &id) const;

	// The settings UI reads the rows the plugin declared, captured while the
	// plugin was loading and the interpreter was already held, so opening the
	// dialog never has to enter Python from the main thread.
	[[nodiscard]] QByteArray pluginSettingsSchema(const QString &id) const;
	// How many rows in that schema the user can actually change. Header and
	// note rows are decoration and do not count, so the plugin list only offers
	// a settings entry for a plugin that has something to set.
	[[nodiscard]] int editablePluginSettingsRows(const QString &id) const;
	void setPluginSettingsSchema(const QString &id, const QByteArray &json);

	[[nodiscard]] QJsonObject pluginSettingsValues(const QString &id) const;
	bool writePluginSettings(const QString &id, const QJsonObject &values);

	void scan(bool forceReloadPython = true);
	[[nodiscard]] std::vector<PluginInstance> list() const;
	[[nodiscard]] bool enable(const QString &id, QString *error = nullptr);
	void disable(const QString &id);
// Stops a plugin's hooks without unloading its module: nothing on the
	// Python side is destroyed, so a plugin with live threads cannot fault
	// the interpreter the way an unload makes it.
	//
	// `reason` is shown in the plugin list. It matters because a plugin that was
	// muted on purpose looks exactly like a plugin that broke: nothing else
	// records why its hooks stopped being called, and "it stopped working" with
	// no explanation is the least actionable report there is.
	void mute(const QString &id, const QString &reason = QString());
	void unmute(const QString &id);
	bool remove(const QString &id, QString *error = nullptr);
	bool installFromZip(const QString &zipPath, QString *error = nullptr);
	bool installFromDir(const QString &dirPath, QString *error = nullptr);
	bool installFromPluginFile(const QString &filePath, QString *error = nullptr);
	bool installPath(const QString &path, QString *error = nullptr);

	// What the last installed plugin needs that Desktop cannot do. Empty
	// when the last one looks portable. A warning, not an error: the plugin
	// is installed and switched on either way, and saying so here is the
	// only chance to tell the user before they go looking for an effect
	// that will never come.
	[[nodiscard]] QString lastIncompatibility() const;

	[[nodiscard]] bool isEnabled(const QString &id) const;
	[[nodiscard]] std::vector<PluginHealth> health() const;
	[[nodiscard]] PluginHealth healthOf(const QString &id) const;
	void setPythonAvailable(bool v) { _pythonAvailable = v; }
	void addBusToken(const QString &id, int token);

private:
	Manager() = default;
	void loadState();
	void saveState() const;
	void ensureBundledPlugins();
	[[nodiscard]] bool loadOne(const QString &dir, QString *error);
	void unloadPython(const QString &id);
	void startWatching();
	void refreshWatchedPaths();
	void onFilesystemChanged();

	mutable std::recursive_mutex _mutex;
	std::map<QString, PluginInstance> _plugins;
	std::map<QString, bool> _enabled;
	std::map<QString, QByteArray> _settingsSchema;
	bool _pythonAvailable = false;
	// Set by the last install, cleared by the next one.
	QString _lastIncompatibility;
	QFileSystemWatcher *_watcher = nullptr;
	QTimer *_watchTimer = nullptr;
	crl::time _lastWatchReload = 0;
	int _scanDepth = 0;
};

} // namespace Ayu::Plugins
