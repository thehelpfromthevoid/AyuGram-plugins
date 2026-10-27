// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/plugins/plugins_manager.h"

#include "ayu/libs/json.hpp"
#include "ayu/plugins/event_bus.h"
#include "ayu/plugins/python_host.h"
#include "ayu/ui/boxes/plugin_info_box.h"
#include "base/zlib_help.h"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QRegularExpression>
#include <QStandardPaths>
#include <QTemporaryDir>
#include <fstream>

namespace Ayu::Plugins {
namespace {

struct BundledPluginSpec {
	const char *id;
	const char *files[2];
	bool enableByDefault = true;
};

constexpr BundledPluginSpec kBundledPlugins[] = {
	{ "com.ayugram.macros", { "plugin.json", "main.py" }, true },
	{ "com.ayugram.translator-demo", { "plugin.json", "main.py" }, false },
	{ "com.ayugram.antidelete", { "plugin.json", "main.py" }, true },
	{ "com.ayugram.hello-toast", { "plugin.json", "main.py" }, false },
	{ "world_time", { "plugin.json", "main.py" }, false },
	{ "scam_base_writer", { "plugin.json", "main.py" }, false },
	{ "animtou", { "plugin.json", "main.py" }, false },
};

constexpr auto kBundledRev = "3";

[[nodiscard]] bool CopyFileBytes(
		const QString &from,
		const QString &to,
		QString *error) {
	auto src = QFile(from);
	if (!src.open(QIODevice::ReadOnly)) {
		if (error) *error = u"cannot open "_q + from;
		return false;
	}
	QDir().mkpath(QFileInfo(to).absolutePath());
	auto dst = QFile(to);
	if (!dst.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
		if (error) *error = u"cannot write "_q + to;
		return false;
	}
	const auto data = src.readAll();
	if (dst.write(data) != data.size()) {
		if (error) *error = u"write failed "_q + to;
		return false;
	}
	return true;
}

void CopyTreeRecursive(const QString &from, const QString &to) {
	QDir().mkpath(to);
	const auto fromDir = QDir(from);
	for (const auto &name : fromDir.entryList(QDir::Files)) {
		const auto src = fromDir.filePath(name);
		const auto dst = to + u"/"_q + name;
		if (QFile::exists(dst)) {
			QFile::remove(dst);
		}
		QFile::copy(src, dst);
	}
	for (const auto &name : fromDir.entryList(QDir::Dirs | QDir::NoDotAndDotDot)) {
		if (name == u"__pycache__"_q) continue;
		CopyTreeRecursive(
			fromDir.filePath(name),
			to + u"/"_q + name);
	}
}

[[nodiscard]] QString FindPluginRoot(const QString &root) {
	if (QFile::exists(root + u"/plugin.json"_q)
		|| QFile::exists(root + u"/manifest.json"_q)
		|| QFile::exists(root + u"/main.py"_q)
		|| QFile::exists(root + u"/__init__.py"_q)
		|| QFile::exists(root + u"/plugin.py"_q)
		|| QFile::exists(root + u"/index.py"_q)
		|| QFile::exists(root + u"/bot.py"_q)) {
		return root;
	}
	const auto dirs = QDir(root).entryList(QDir::Dirs | QDir::NoDotAndDotDot);
	for (const auto &name : dirs) {
		const auto candidate = root + u"/"_q + name;
		if (QFile::exists(candidate + u"/plugin.json"_q)
			|| QFile::exists(candidate + u"/manifest.json"_q)
			|| QFile::exists(candidate + u"/main.py"_q)
			|| QFile::exists(candidate + u"/__init__.py"_q)
			|| QFile::exists(candidate + u"/plugin.py"_q)
			|| QFile::exists(candidate + u"/index.py"_q)
			|| QFile::exists(candidate + u"/bot.py"_q)) {
			return candidate;
		}
	}
	for (const auto &name : dirs) {
		const auto nested = FindPluginRoot(root + u"/"_q + name);
		if (!nested.isEmpty()) {
			return nested;
		}
	}
	const auto files = QDir(root).entryList(
		{ u"*.plugin"_q, u"*.py"_q },
		QDir::Files);
	if (!files.isEmpty()) {
		return root;
	}
	return {};
}

[[nodiscard]] bool ExtractZipTo(
		const QByteArray &bytes,
		const QString &folder,
		QString *error) {
	auto zip = zlib::FileToRead(bytes);
	if (zip.goToFirstFile() != UNZ_OK) {
		if (error) *error = u"bad archive"_q;
		return false;
	}
	constexpr auto kMaxFile = 8 * 1024 * 1024;
	const auto cleanFolder = QDir::cleanPath(folder);
	do {
		const auto name = zip.getCurrentFileName();
		if (name.isEmpty() || name.endsWith(u'/') || name.endsWith(u'\\')) {
			const auto jump = zip.goToNextFile();
			if (jump == UNZ_END_OF_LIST_OF_FILE) break;
			if (jump != UNZ_OK) {
				if (error) *error = u"archive read error"_q;
				return false;
			}
			continue;
		}
		if (name.contains(u".."_q)) {
			if (error) *error = u"unsafe path in archive"_q;
			return false;
		}
		const auto outPath = QDir::cleanPath(folder + u"/"_q + name);
		if (outPath != cleanFolder && !outPath.startsWith(cleanFolder + u"/"_q)) {
			if (error) *error = u"unsafe path in archive"_q;
			return false;
		}
		const auto content = zip.readCurrentFileContent(kMaxFile);
		if (zip.error() != UNZ_OK) {
			if (error) *error = u"cannot extract "_q + name;
			return false;
		}
		QDir().mkpath(QFileInfo(outPath).absolutePath());
		auto out = QFile(outPath);
		if (!out.open(QIODevice::WriteOnly | QIODevice::Truncate)
			|| out.write(content) != content.size()) {
			if (error) *error = u"cannot write extracted file"_q;
			return false;
		}
		const auto jump = zip.goToNextFile();
		if (jump == UNZ_END_OF_LIST_OF_FILE) break;
		if (jump != UNZ_OK) {
			if (error) *error = u"archive read error"_q;
			return false;
		}
	} while (true);
	return true;
}

} // namespace

Manager &Manager::instance() {
	static Manager m;
	return m;
}

QString Manager::pluginsRoot() const {
	auto base = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
	if (base.isEmpty()) base = QDir::homePath() + u"/.ayugram"_q;
	return base + u"/plugins"_q;
}

QString Manager::pluginsStatePath() const {
	return pluginsRoot() + u"/plugins.json"_q;
}

void Manager::loadState() {
	auto file = std::ifstream(pluginsStatePath().toStdString());
	if (!file.good()) return;
	try {
		auto j = nlohmann::json();
		file >> j;
		if (!j.is_object()) return;
		for (const auto &[k, v] : j.items()) {
			_enabled[QString::fromStdString(k)] = v.get<bool>();
		}
	} catch (...) {}
}

void Manager::saveState() const {
	auto dir = QDir(pluginsRoot());
	if (!dir.exists()) dir.mkpath(u"."_q);
	auto file = std::ofstream(pluginsStatePath().toStdString());
	if (!file.good()) return;
	auto j = nlohmann::json::object();
	for (const auto &[k, v] : _enabled) {
		j[k.toStdString()] = v;
	}
	file << j.dump(2);
}

void Manager::ensureBundledPlugins() {
	auto root = QDir(pluginsRoot());
	if (!root.exists()) {
		root.mkpath(u"."_q);
	}
	const auto marker = pluginsRoot() + u"/.bundled_seeded"_q;
	const auto revPath = pluginsRoot() + u"/.bundled_rev"_q;
	auto currentRev = QString();
	{
		auto revFile = QFile(revPath);
		if (revFile.open(QIODevice::ReadOnly)) {
			currentRev = QString::fromUtf8(revFile.readAll()).trimmed();
		}
	}
	const auto alreadySeeded = QFile::exists(marker);
	const auto needRefresh = (currentRev != QString::fromUtf8(kBundledRev));
	const auto sidecarRoot = QCoreApplication::applicationDirPath()
		+ u"/plugins"_q;
	auto anyNew = false;
	for (const auto &spec : kBundledPlugins) {
		const auto id = QString::fromUtf8(spec.id);
		const auto dstDir = pluginsRoot() + u"/"_q + id;
		const auto sidecarDir = sidecarRoot + u"/"_q + id;
		const auto exists = QDir(dstDir).exists();
		if (exists && !needRefresh) {
			if (!_enabled.count(id) && !alreadySeeded) {
				_enabled[id] = spec.enableByDefault;
			}
			continue;
		}
		anyNew = true;
		if (QDir(sidecarDir).exists()) {
			if (exists) {
				QDir(dstDir).removeRecursively();
			}
			CopyTreeRecursive(sidecarDir, dstDir);
			if (!_enabled.count(id)) {
				_enabled[id] = spec.enableByDefault;
			}
			continue;
		}
		if (exists && needRefresh) {
			continue;
		}
		auto ok = true;
		for (const auto *file : spec.files) {
			if (!file) continue;
			const auto name = QString::fromUtf8(file);
			const auto resource = u":/ayu/plugins/"_q + id + u"/"_q + name;
			if (!QFile::exists(resource)) {
				ok = false;
				break;
			}
			auto err = QString();
			if (!CopyFileBytes(resource, dstDir + u"/"_q + name, &err)) {
				ok = false;
				break;
			}
		}
		if (ok && !_enabled.count(id)) {
			_enabled[id] = spec.enableByDefault;
		}
	}
	if (!alreadySeeded || anyNew || needRefresh) {
		auto markerFile = QFile(marker);
		if (markerFile.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
			markerFile.write("1");
		}
		auto revFile = QFile(revPath);
		if (revFile.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
			revFile.write(kBundledRev);
		}
	}
}

bool Manager::loadOne(const QString &dir, QString *error) {
	auto manifestPath = dir + u"/plugin.json"_q;
	if (!QFile::exists(manifestPath)
		&& QFile::exists(dir + u"/manifest.json"_q)) {
		manifestPath = dir + u"/manifest.json"_q;
	}
	auto manifest = PluginManifest();
	if (QFile::exists(manifestPath)) {
		auto f = QFile(manifestPath);
		if (!f.open(QIODevice::ReadOnly)) {
			if (error) *error = u"cannot open plugin manifest"_q;
			return false;
		}
		if (!ParseManifestJson(f.readAll(), manifest, error)) return false;
	} else {
		const auto candidates = {
			dir + u"/main.py"_q,
			dir + u"/__init__.py"_q,
			dir + u"/plugin.py"_q,
			dir + u"/index.py"_q,
			dir + u"/bot.py"_q,
			dir + u".plugin"_q,
		};
		auto found = QString();
		auto data = QByteArray();
		for (const auto &c : candidates) {
			auto f = QFile(c);
			if (f.open(QIODevice::ReadOnly)) {
				data = f.readAll();
				found = c;
				break;
			}
		}
		if (found.isEmpty()) {
			const auto singles = QDir(dir).entryList(
				{ u"*.plugin"_q, u"*.py"_q }, QDir::Files, QDir::Name);
			for (const auto &name : singles) {
				auto f = QFile(dir + u"/"_q + name);
				if (f.open(QIODevice::ReadOnly)) {
					data = f.readAll();
					found = dir + u"/"_q + name;
					break;
				}
			}
		}
		if (found.isEmpty()) {
			if (error) *error = u"no plugin.json nor entry script"_q;
			return false;
		}
		const auto legacy = Ui::ParsePluginMetadata(data);
		if (legacy.id.isEmpty() || legacy.name.isEmpty()) {
			if (error) *error = u"bad legacy metadata"_q;
			return false;
		}
		manifest = ManifestFromLegacyMetadata(
			legacy.id,
			legacy.name,
			legacy.description,
			legacy.author,
			legacy.version,
			legacy.minVersion,
			legacy.requirements,
			legacy.icon);
		manifest.entry = QFileInfo(found).fileName();
	}
	manifest.sourcePath = dir;
	if (!ValidateManifest(manifest, error)) return false;

	auto instance = PluginInstance();
	instance.manifest = manifest;
	instance.dirPath = dir;
	const auto enabled = _enabled.count(manifest.id)
		? _enabled[manifest.id]
		: true;
	instance.state = enabled ? PluginState::Loaded : PluginState::Disabled;
	_plugins[manifest.id] = std::move(instance);
	return true;
}

void Manager::scan(bool forceReloadPython) {
	std::vector<PluginManifest> toLoad;
	{
		const auto lock = std::lock_guard(_mutex);
		loadState();
		ensureBundledPlugins();

		struct Prior {
			bool pythonLoaded = false;
			std::vector<int> busTokens;
			PluginState state = PluginState::Disabled;
			QString lastError;
		};
		auto prior = std::map<QString, Prior>();
		if (!forceReloadPython) {
			for (const auto &[id, p] : _plugins) {
				prior[id] = {
					.pythonLoaded = p.pythonLoaded,
					.busTokens = p.busTokens,
					.state = p.state,
					.lastError = p.lastError,
				};
			}
		} else {
			for (auto &[_, p] : _plugins) {
				for (const auto t : p.busTokens) {
					EventBus::instance().unsubscribe(t);
				}
				p.busTokens.clear();
				if (p.pythonLoaded) {
					PythonHost::instance().unloadPlugin(p.manifest.id);
					p.pythonLoaded = false;
				}
			}
		}
		_plugins.clear();
		auto root = QDir(pluginsRoot());
		if (!root.exists()) {
			root.mkpath(u"."_q);
			saveState();
			return;
		}
		const auto entries = root.entryList(QDir::Dirs | QDir::NoDotAndDotDot);
		for (const auto &e : entries) {
			auto err = QString();
			loadOne(root.filePath(e), &err);
		}
		for (auto &[id, p] : _plugins) {
			if (!forceReloadPython) {
				const auto it = prior.find(id);
				if (it != prior.end()) {
					p.pythonLoaded = it->second.pythonLoaded;
					p.busTokens = it->second.busTokens;
					if (it->second.state == PluginState::Error) {
						p.state = PluginState::Error;
						p.lastError = it->second.lastError;
					}
					prior.erase(it);
				}
			}
			if (p.state == PluginState::Loaded
				&& _pythonAvailable
				&& !p.pythonLoaded) {
				toLoad.push_back(p.manifest);
			}
		}
		if (!forceReloadPython) {
			for (const auto &[id, old] : prior) {
				for (const auto t : old.busTokens) {
					EventBus::instance().unsubscribe(t);
				}
				if (old.pythonLoaded) {
					PythonHost::instance().unloadPlugin(id);
				}
			}
		}
		saveState();
	}
	for (const auto &manifest : toLoad) {
		auto perr = QString();
		const auto ok = PythonHost::instance().loadPlugin(manifest, &perr);
		const auto lock = std::lock_guard(_mutex);
		const auto it = _plugins.find(manifest.id);
		if (it == _plugins.end()) {
			continue;
		}
		if (!ok) {
			it->second.state = PluginState::Error;
			it->second.lastError = perr;
			it->second.pythonLoaded = false;
		} else {
			it->second.pythonLoaded = true;
			it->second.state = PluginState::Loaded;
		}
	}
}

std::vector<PluginInstance> Manager::list() const {
	const auto lock = std::lock_guard(_mutex);
	auto out = std::vector<PluginInstance>();
	for (const auto &[_, p] : _plugins) out.push_back(p);
	return out;
}

bool Manager::isEnabled(const QString &id) const {
	const auto lock = std::lock_guard(_mutex);
	const auto it = _plugins.find(id);
	if (it == _plugins.cend()) return false;
	return it->second.state == PluginState::Loaded;
}

bool Manager::enable(const QString &id, QString *error) {
	PluginManifest manifest;
	auto needLoad = false;
	{
		const auto lock = std::lock_guard(_mutex);
		const auto it = _plugins.find(id);
		if (it == _plugins.end()) {
			if (error) *error = u"unknown plugin"_q;
			return false;
		}
		_enabled[id] = true;
		it->second.state = PluginState::Loaded;
		it->second.lastError.clear();
		saveState();
		needLoad = _pythonAvailable && !it->second.pythonLoaded;
		if (needLoad) {
			manifest = it->second.manifest;
		}
	}
	if (!needLoad) {
		return true;
	}
	auto perr = QString();
	if (!PythonHost::instance().loadPlugin(manifest, &perr)) {
		const auto lock = std::lock_guard(_mutex);
		const auto it = _plugins.find(id);
		if (it != _plugins.end()) {
			it->second.state = PluginState::Error;
			it->second.lastError = perr;
			it->second.pythonLoaded = false;
		}
		if (error) *error = perr;
		return false;
	}
	const auto lock = std::lock_guard(_mutex);
	const auto it = _plugins.find(id);
	if (it != _plugins.end()) {
		it->second.pythonLoaded = true;
		it->second.state = PluginState::Loaded;
	}
	return true;
}

void Manager::disable(const QString &id) {
	const auto lock = std::lock_guard(_mutex);
	const auto it = _plugins.find(id);
	if (it == _plugins.end()) return;
	_enabled[id] = false;
	it->second.state = PluginState::Disabled;
	for (const auto t : it->second.busTokens) EventBus::instance().unsubscribe(t);
	it->second.busTokens.clear();
	if (it->second.pythonLoaded) {
		PythonHost::instance().unloadPlugin(id);
		it->second.pythonLoaded = false;
	}
	saveState();
}

void Manager::unloadPython(const QString &id) {
	PythonHost::instance().unloadPlugin(id);
}

bool Manager::remove(const QString &id, QString *error) {
	disable(id);
	const auto lock = std::lock_guard(_mutex);
	const auto it = _plugins.find(id);
	if (it == _plugins.end()) return true;
	auto dir = QDir(it->second.dirPath);
	if (!dir.removeRecursively()) {
		if (error) *error = u"cannot remove dir"_q;
		return false;
	}
	_plugins.erase(it);
	_enabled.erase(id);
	saveState();
	return true;
}

bool Manager::installFromDir(const QString &dirPath, QString *error) {
	const auto src = QDir(dirPath);
	if (!src.exists()) {
		if (error) *error = u"source missing"_q;
		return false;
	}
	auto tmpManifest = PluginManifest();
	const auto mj = dirPath + u"/plugin.json"_q;
	if (QFile::exists(mj)) {
		auto f = QFile(mj);
		f.open(QIODevice::ReadOnly);
		if (!ParseManifestJson(f.readAll(), tmpManifest, error)) return false;
	} else {
		auto f = QFile(dirPath + u"/main.py"_q);
		if (!f.open(QIODevice::ReadOnly)) {
			if (error) *error = u"no entry"_q;
			return false;
		}
		const auto legacy = Ui::ParsePluginMetadata(f.readAll());
		if (legacy.id.isEmpty()) {
			if (error) *error = u"bad metadata"_q;
			return false;
		}
		tmpManifest = ManifestFromLegacyMetadata(
			legacy.id, legacy.name, legacy.description, legacy.author,
			legacy.version, legacy.minVersion, legacy.requirements, legacy.icon);
	}
	const auto dst = pluginsRoot() + u"/"_q + tmpManifest.id;
	auto dd = QDir(dst);
	if (dd.exists()) dd.removeRecursively();
	CopyTreeRecursive(dirPath, dst);
	_enabled[tmpManifest.id] = true;
	saveState();
	scan();
	return true;
}

bool Manager::installFromPluginFile(const QString &filePath, QString *error) {
	auto f = QFile(filePath);
	if (!f.open(QIODevice::ReadOnly)) {
		if (error) *error = u"cannot open plugin file"_q;
		return false;
	}
	const auto data = f.readAll();
	f.close();
	auto legacy = Ui::ParsePluginMetadata(data);
	if (legacy.id.isEmpty()) {
		const auto base = QFileInfo(filePath).completeBaseName();
		auto sanitized = base;
		sanitized.replace(QRegularExpression(u"[^A-Za-z0-9_.-]"_q), u"_"_q);
		if (sanitized.isEmpty()) {
			sanitized = u"plugin"_q;
		}
		legacy.id = sanitized;
		legacy.name = base.isEmpty() ? sanitized : base;
		legacy.version = u"1.0.0"_q;
	}
	if (legacy.name.isEmpty()) {
		legacy.name = legacy.id;
	}
	const auto dst = pluginsRoot() + u"/"_q + legacy.id;
	auto dd = QDir(dst);
	if (dd.exists()) dd.removeRecursively();
	QDir().mkpath(dst);
	auto out = QFile(dst + u"/main.py"_q);
	if (!out.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
		if (error) *error = u"cannot write main.py"_q;
		return false;
	}
	out.write(data);
	out.close();
	auto meta = nlohmann::json::object();
	meta["id"] = legacy.id.toStdString();
	meta["name"] = legacy.name.toStdString();
	meta["version"] = legacy.version.toStdString();
	meta["entry"] = "main.py";
	auto mj = QFile(dst + u"/plugin.json"_q);
	if (mj.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
		mj.write(QByteArray::fromStdString(meta.dump(2)));
		mj.close();
	}
	_enabled[legacy.id] = true;
	saveState();
	scan();
	return true;
}

bool Manager::installFromZip(const QString &zipPath, QString *error) {
	auto f = QFile(zipPath);
	if (!f.open(QIODevice::ReadOnly)) {
		if (error) *error = u"cannot open archive"_q;
		return false;
	}
	constexpr auto kMaxZip = 25 * 1024 * 1024;
	if (f.size() > kMaxZip) {
		if (error) *error = u"archive too large"_q;
		return false;
	}
	const auto bytes = f.readAll();
	f.close();
	auto tmp = QTemporaryDir();
	if (!tmp.isValid()) {
		if (error) *error = u"cannot create temp dir"_q;
		return false;
	}
	if (!ExtractZipTo(bytes, tmp.path(), error)) {
		return false;
	}
	const auto pluginRoot = FindPluginRoot(tmp.path());
	if (pluginRoot.isEmpty()) {
		if (error) *error = u"no plugin.json / main.py / .plugin in archive"_q;
		return false;
	}
	if (QFile::exists(pluginRoot + u"/plugin.json"_q)
		|| QFile::exists(pluginRoot + u"/main.py"_q)) {
		return installFromDir(pluginRoot, error);
	}
	const auto pluginFiles = QDir(pluginRoot).entryList(
		{ u"*.plugin"_q, u"*.py"_q },
		QDir::Files);
	if (!pluginFiles.isEmpty()) {
		return installFromPluginFile(
			pluginRoot + u"/"_q + pluginFiles.front(),
			error);
	}
	return installFromDir(pluginRoot, error);
}

bool Manager::installPath(const QString &path, QString *error) {
	const auto info = QFileInfo(path);
	if (!info.exists()) {
		if (error) *error = u"path missing"_q;
		return false;
	}
	if (info.isDir()) {
		const auto nested = FindPluginRoot(path);
		if (nested.isEmpty()) {
			if (error) *error = u"no plugin.json / main.py in folder"_q;
			return false;
		}
		return installFromDir(nested, error);
	}
	const auto suffix = info.suffix().toLower();
	if (suffix == u"zip"_q
		|| suffix == u"ayupack"_q
		|| suffix == u"ayu"_q
		|| suffix == u"tgx"_q) {
		return installFromZip(path, error);
	}
	if (info.fileName().compare(u"plugin.json"_q, Qt::CaseInsensitive) == 0) {
		return installFromDir(info.absolutePath(), error);
	}
	if (suffix == u"plugin"_q
		|| suffix == u"py"_q
		|| suffix == u"pyw"_q) {
		return installFromPluginFile(path, error);
	}
	if (error) {
		*error = u"unsupported plugin package (use folder, .zip, .ayupack, .plugin, .py)"_q;
	}
	return false;
}

void Manager::addBusToken(const QString &id, int token) {
	const auto lock = std::lock_guard(_mutex);
	const auto it = _plugins.find(id);
	if (it == _plugins.end()) return;
	it->second.busTokens.push_back(token);
}

} // namespace Ayu::Plugins
