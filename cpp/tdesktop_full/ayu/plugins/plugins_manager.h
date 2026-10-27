// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "ayu/plugins/plugin_instance.h"

#include <map>
#include <mutex>
#include <vector>

class QString;

namespace Ayu::Plugins {

class Manager final {
public:
	static Manager &instance();

	[[nodiscard]] QString pluginsRoot() const;
	[[nodiscard]] QString pluginsStatePath() const;

	void scan(bool forceReloadPython = true);
	[[nodiscard]] std::vector<PluginInstance> list() const;
	[[nodiscard]] bool enable(const QString &id, QString *error = nullptr);
	void disable(const QString &id);
	bool remove(const QString &id, QString *error = nullptr);
	bool installFromZip(const QString &zipPath, QString *error = nullptr);
	bool installFromDir(const QString &dirPath, QString *error = nullptr);
	bool installFromPluginFile(const QString &filePath, QString *error = nullptr);
	bool installPath(const QString &path, QString *error = nullptr);

	[[nodiscard]] bool isEnabled(const QString &id) const;
	void setPythonAvailable(bool v) { _pythonAvailable = v; }
	void addBusToken(const QString &id, int token);

private:
	Manager() = default;
	void loadState();
	void saveState() const;
	void ensureBundledPlugins();
	[[nodiscard]] bool loadOne(const QString &dir, QString *error);
	void unloadPython(const QString &id);

	mutable std::recursive_mutex _mutex;
	std::map<QString, PluginInstance> _plugins;
	std::map<QString, bool> _enabled;
	bool _pythonAvailable = false;
};

} // namespace Ayu::Plugins
