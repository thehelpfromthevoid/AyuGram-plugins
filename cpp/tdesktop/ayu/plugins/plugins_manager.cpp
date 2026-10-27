// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
//
// AyuGram plugin host for Desktop, (c) @CE0fbi
#include "ayu/plugins/plugins_manager.h"

#include "ayu/libs/json.hpp"
#include "ayu/plugins/event_bus.h"
#include "ayu/plugins/bridge/python_bridge.h"
#include "ayu/plugins/python_host.h"
#include "ayu/ui/boxes/plugin_info_box.h"
#include "base/zlib_help.h"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QFileSystemWatcher>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <QStandardPaths>
#include <QTemporaryDir>
#include <QTimer>
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

// One-shot report of how the first scan resolved each plugin, so a plugin
// that loads while the state file says it is off can be traced.
bool g_scanLogged = false;

Manager &Manager::instance() {
	static Manager m;
	return m;
}

QString Manager::pluginDataRoot() const {
	// Where plugins keep their own files, next to the plugins themselves so a
	// test build and the real profile cannot collide. Exposed to plugins as
	// ayu_api.data_dir: without it they fall back to a hardcoded path and
	// quietly write their state into the wrong place.
	return pluginsRoot() + u"/data"_q;
}

QString Manager::pluginsRoot() const {
	// An explicit override lets a test build keep its plugins, logs and
	// settings out of the real profile. The Python side already honours the
	// same variable, so both halves agree on where a plugin's files live.
	auto override = qEnvironmentVariable("AYU_PLUGINS_DIR");
	if (!override.isEmpty()) {
		return QDir::cleanPath(override);
	}
	auto base = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
	if (base.isEmpty()) base = QDir::homePath() + u"/.ayugram"_q;
	return base + u"/plugins"_q;
}

	QString Manager::pluginsStatePath() const {
	return pluginsRoot() + u"/plugins.json"_q;
}

QString Manager::pluginLogPath(const QString &id) const {
	auto safe = id;
	safe.replace(QChar('/'), QChar('_'));
	safe.replace(QChar('\\'), QChar('_'));
	safe.replace(QChar(':'), QChar('_'));
	return pluginsRoot() + u"/"_q + safe + u".log"_q;
}

QString Manager::pluginSettingsPath(const QString &id) const {
	auto safe = id;
	safe.replace(QChar('/'), QChar('_'));
	safe.replace(QChar('\\'), QChar('_'));
	safe.replace(QChar(':'), QChar('_'));
	// Matches the name ayugram_compat.settings_store uses on the Python
	// side, so both agree on where a plugin's values live.
	return pluginsRoot() + u"/"_q + safe + u".settings.json"_q;
}


void Manager::loadState() {
	// Read through QFile/QJsonDocument rather than a narrow-char
	// std::ifstream: a failed read here used to be swallowed, which left the
	// map empty, let every plugin fall back to its default and then rewrote
	// the file. Switching a plugin off appeared to do nothing and was undone
	// on the next start.
	auto file = QFile(pluginsStatePath());
	if (!file.open(QIODevice::ReadOnly)) {
		LOG(("AyuPlugins: no plugin state at %1").arg(pluginsStatePath()));
		return;
	}
	const auto parse = QJsonDocument::fromJson(file.readAll());
	file.close();
	if (!parse.isObject()) {
		LOG(("AyuPlugins: plugin state is not a JSON object, ignoring"));
		return;
	}
	const auto object = parse.object();
	for (auto it = object.constBegin(); it != object.constEnd(); ++it) {
		if (it.value().isBool()) {
			_enabled[it.key()] = it.value().toBool();
		}
	}
}

void Manager::saveState() const {
	auto dir = QDir(pluginsRoot());
	if (!dir.exists()) dir.mkpath(u"."_q);
	auto file = QFile(pluginsStatePath());
	if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
		LOG(("AyuPlugins: cannot write plugin state at %1")
			.arg(pluginsStatePath()));
		return;
	}
	auto object = QJsonObject();
	for (const auto &[k, v] : _enabled) {
		object.insert(k, v);
	}
	file.write(QJsonDocument(object).toJson(QJsonDocument::Indented));
}

QByteArray Manager::pluginSettingsSchema(const QString &id) const {
	const auto lock = std::lock_guard(_mutex);
	const auto it = _settingsSchema.find(id);
	return (it != _settingsSchema.end()) ? it->second : QByteArray();
}

int Manager::editablePluginSettingsRows(const QString &id) const {
	const auto parsed = QJsonDocument::fromJson(pluginSettingsSchema(id));
	if (!parsed.isObject()) {
		return 0;
	}
	const auto encoded = parsed.object()
		.value(u"rows"_q)
		.toString()
		.toUtf8();
	if (encoded.isEmpty()) {
		return 0;
	}
	auto count = 0;
	for (const auto &entry : QJsonDocument::fromJson(encoded).array()) {
		const auto type = entry.toObject().value(u"type"_q).toString();
		// Header and note rows are decoration. Counting them would put a
		// settings entry in front of a plugin with nothing to set, which is
		// how people learn to ignore the button.
		if (type.isEmpty() || type == u"header"_q || type == u"note"_q) {
			continue;
		}
		++count;
	}
	return count;
}

void Manager::setPluginSettingsSchema(
	const QString &id,
	const QByteArray &json) {
	const auto lock = std::lock_guard(_mutex);
	if (json.isEmpty()) {
		_settingsSchema.erase(id);
		return;
	}
	_settingsSchema[id] = json;
	auto rows = 0;
	const auto parsed = QJsonDocument::fromJson(json);
	if (parsed.isObject()) {
		const auto encoded = parsed.object()
			.value(u"rows"_q)
			.toString()
			.toUtf8();
		if (!encoded.isEmpty()) {
			rows = QJsonDocument::fromJson(encoded).array().size();
		}
	}
	// Counted the same way the button is, and reported both numbers. Logging the
	// raw array size under the name "editable rows" made every schema look
	// larger than it was - custom_nickname reported 23 editable rows when 7 of
	// them are headers - which sent the search for missing settings in the wrong
	// direction entirely.
	LOG(("AyuPlugins: settings schema for %1: %2 rows, %3 editable")
		.arg(id)
		.arg(QString::number(rows))
		.arg(QString::number(editablePluginSettingsRows(id))));
}

QJsonObject Manager::pluginSettingsValues(const QString &id) const {
	auto out = QJsonObject();
	auto file = QFile(pluginSettingsPath(id));
	if (!file.open(QIODevice::ReadOnly)) {
		return out;
	}
	const auto parse = QJsonDocument::fromJson(file.readAll());
	file.close();
	if (!parse.isObject()) {
		return out;
	}
	const auto root = parse.object();
	const auto values = root.value(u"settings"_q);
	if (values.isObject()) {
		return values.toObject();
	}
	return root;
}

bool Manager::writePluginSettings(
	const QString &id,
	const QJsonObject &values) {
	auto file = QFile(pluginSettingsPath(id));
	if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
		return false;
	}
	auto root = QJsonObject();
	root.insert(u"id"_q, id);
	root.insert(u"version"_q, 1);
	root.insert(u"settings"_q, values);
	file.write(QJsonDocument(root).toJson(QJsonDocument::Indented));
	return true;
}

namespace {
// The sandbox refuses these unless the manifest asks for them, and the refusal
// is an exception that a plugin usually swallows. Reading the plugin's own
// source is the only way to tell the user before it happens.
[[nodiscard]] QStringList PermissionProblems(
		const PluginManifest &manifest,
		const QString &dirPath) {
	auto out = QStringList();
	auto file = QFile(dirPath + u'/' + manifest.entry);
	if (!file.open(QIODevice::ReadOnly)) {
		return out;
	}
	const auto source = QString::fromUtf8(file.readAll());
	file.close();

	const auto wants = [&source](std::initializer_list<const char *> needles) {
		for (const auto needle : needles) {
			if (source.contains(QLatin1StringView(needle))) {
				return true;
			}
		}
		return false;
	};
	const auto has = [&manifest](const char *permission) {
		return ManifestHasPermission(manifest, QString::fromLatin1(permission));
	};

	if (wants({ "import urllib", "import socket", "import requests",
		"import http.client", "from urllib" })
		&& !has("net.http")) {
		out.append(u"uses the network but does not request net.http"_q);
	}
	if (wants({ "import subprocess", "import multiprocessing" })
		&& !has("process.spawn")) {
		out.append(u"spawns processes but does not request process.spawn"_q);
	}
	if ((wants({ "open(", "os.remove", "os.makedirs", "shutil." })
			&& (source.contains(u".log"_q) || source.contains(u".json"_q))
			&& source.contains(u"\"a\""_q))
		&& !has("storage.write")) {
		out.append(
			u"writes files or its own log but does not request "
			"storage.write, so the sandbox will refuse the write"_q);
	}
	return out;
}

} // namespace

std::vector<PluginHealth> Manager::health() const {
	const auto lock = std::lock_guard(_mutex);
	auto out = std::vector<PluginHealth>();
	out.reserve(_plugins.size());
	for (const auto &[id, instance] : _plugins) {
		auto entry = PluginHealth();
		entry.id = id;
		entry.name = instance.manifest.name;
		entry.state = instance.state;
		entry.problems = ManifestProblems(instance.manifest);
		if (!ManifestRunsHere(instance.manifest)) {
			// Reported by ManifestProblems already; nothing loaded, and that
			// is expected rather than broken.
			out.push_back(std::move(entry));
			continue;
		}
		entry.problems += PermissionProblems(
			instance.manifest,
			instance.dirPath);
		// A missing library breaks every plugin that needs it, so it is
		// repeated on each entry rather than shown once: the user is looking at
		// one plugin and wants to know why it cannot load.
		entry.problems += Bridge::RuntimeProblems();

		// What this plugin reached for and could not have. Recorded, not fatal:
		// the plugin keeps running and the parts Desktop can do still work.
		const auto gaps = Bridge::ApiGaps();
		if (!gaps.isEmpty()) {
			entry.notes.append(
				u"reached for bridge features Desktop cannot do: "
				+ gaps.join(u", ").left(220));
		}
		if (instance.state == PluginState::Error
			&& !instance.lastError.isEmpty()) {
			entry.problems.append(u"failed to load: "_q + instance.lastError.left(300));
		} else if (instance.muted && !instance.lastError.isEmpty()) {
			// A plugin muted on purpose, or by the slow-hook breaker, reports why
			// here. It keeps the Disabled state and stays loaded, so without this
			// its hooks would just stop being called with nothing on screen to
			// say so - indistinguishable from a plugin that quietly broke.
			entry.problems.append(instance.lastError.left(300));
		}
		const auto hookErrors = Bridge::PluginHookErrors(id);
		if (hookErrors > 0) {
			entry.problems.append(u"raised "_q + QString::number(hookErrors)
				+ u" error(s) in its hooks; last: "_q
				+ Bridge::PluginLastHookIssue(id).left(160));
		} else {
			const auto slow = Bridge::PluginLastHookIssue(id);
			if (!slow.isEmpty()) {
				entry.notes.append(u"slow hook: "_q + slow.left(160));
			}
		}
		if (instance.state == PluginState::Disabled) {
			entry.notes.append(instance.muted
				? u"muted: a hook blocked the app, so it was put aside "
					u"without unloading"_q
				: u"switched off"_q);
		}
	if (instance.state == PluginState::Loaded
		&& !instance.pythonLoaded) {
		entry.notes.append(u"loaded, but its Python entry never ran"_q);
	}
	// A plugin that is on, has run, and has never done anything. This is
	// the case that used to look exactly like a working plugin: the switch
	// was on, the journal said "load ok", and nothing happened all session.
	// Usually it hooks Android classes Desktop has none of, so every
	// registration quietly found nothing to attach to.
	if (instance.state == PluginState::Loaded
		&& instance.pythonLoaded
		&& !Bridge::BridgeShuttingDown()) {
		const auto subscriptions = Bridge::PluginHookSubscriptions(id);
		const auto calls = Bridge::PluginHookCalls(id);
		const auto apiCalls = Bridge::PluginApiCalls(id);
		if (!calls && apiCalls.isEmpty()) {
			if (!subscriptions) {
				entry.problems.append(
					u"subscribed to nothing and called nothing: "
					u"it will never do anything"_q);
			} else {
				entry.problems.append(
					u"subscribed to "_q + QString::number(subscriptions)
					+ u" event(s), but none ever came. "
					u"It will not act until such an event happens."_q);
			}
		}
	}
	out.push_back(std::move(entry));
	}
	return out;
}

PluginHealth Manager::healthOf(const QString &id) const {
	const auto all = health();
	for (const auto &entry : all) {
		if (entry.id == id) {
			return entry;
		}
	}
	return PluginHealth();
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
	// A plugin that declares no permissions is taken to need none, which is
	// true on a phone and fatal here: the sandbox refuses the first import it
	// makes and it never loads. Read its own code before deciding that.
	if (manifest.permissions.empty() && _pythonAvailable) {
		const auto granted = Bridge::InferPermissions(dir);
		if (!granted.isEmpty()) {
			auto refreshed = QFile(dir + u"/plugin.json"_q);
			if (!refreshed.exists()) {
				refreshed.setFileName(dir + u"/manifest.json"_q);
			}
			if (refreshed.open(QIODevice::ReadOnly)) {
				auto again = PluginManifest();
				if (ParseManifestJson(refreshed.readAll(), again, nullptr)) {
					again.sourcePath = manifest.sourcePath;
					instance.manifest = again;
				}
				refreshed.close();
			}
		}
	}
	if (!ManifestRunsHere(manifest)) {
		// Declared for another platform. Report it as skipped rather than
		// loading it and letting the entry script fail on a missing Java or
		// Android module, which put an error in the log on every launch.
		instance.state = PluginState::Disabled;
		instance.lastError = u"built for another platform: "_q
			+ manifest.platforms.join(u", "_q);
		_plugins[manifest.id] = std::move(instance);
		return true;
	}
	const auto enabled = _enabled.count(manifest.id)
		? _enabled[manifest.id]
		: true;
	instance.state = enabled ? PluginState::Loaded : PluginState::Disabled;
	_plugins[manifest.id] = std::move(instance);
	return true;
}

void Manager::scan(bool forceReloadPython) {
	// While we are writing the state file and copying bundled plugins, the
	// watcher would otherwise reload in response to our own changes.
	++_scanDepth;
	// Counts describe one run. Left over from the previous run, a plugin
	// that did nothing just now would still look busy from last time, and
	// the health report would call it working.
	if (!Bridge::BridgeShuttingDown()) {
		Bridge::ResetPluginActivity();
	}
	std::vector<PluginManifest> toLoad;
	{
		const auto lock = std::lock_guard(_mutex);
		const auto statePath = pluginsStatePath();
		const auto before = int(_enabled.size());
		loadState();
		ensureBundledPlugins();

		// A plugin that is switched off must stay off. Reporting how the
		// state was resolved makes an unexpected load traceable instead of
		// looking like the disable command simply did not work.
		auto onCount = 0;
		for (const auto &[_, v] : _enabled) {
			if (v) ++onCount;
		}
		LOG(("AyuPlugins: scan root=%1 state=%2 known=%3->%4 on=%5")
			.arg(pluginsRoot())
			.arg(statePath)
			.arg(QString::number(before))
			.arg(QString::number(int(_enabled.size())))
			.arg(QString::number(onCount)));

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
			if (!loadOne(root.filePath(e), &err) && !err.isEmpty()) {
				// The reason was collected and then dropped, so a plugin whose
				// manifest is not acceptable simply did not appear: nothing in
				// the log, nothing in the list, no error anywhere. A manifest
				// missing its name is enough to cause it. The directory is named
				// because the id is not always known - that is often the problem.
				LOG(("AyuPlugins: skipped %1: %2").arg(e, err.left(200)));
			}
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
if (!g_scanLogged) {
		g_scanLogged = true;
		for (const auto &[id, p] : _plugins) {
			LOG(("AyuPlugins: resolved %1 -> %2").arg(
				id,
				p.state == PluginState::Loaded ? u"load"_q
				: p.state == PluginState::Error ? u"error"_q
				: u"skip"_q));
		}
		LOG(("AyuPlugins: python available=%1 to load=%2")
			.arg(_pythonAvailable ? u"yes"_q : u"no"_q)
			.arg(QString::number(int(toLoad.size()))));
	}
	// Report what the built-in check concludes, so the reason a plugin is
	// not working is readable from the log without opening any dialog.
	for (const auto &entry : health()) {
		for (const auto &problem : entry.problems) {
			LOG(("AyuPlugins: check %1: %2").arg(entry.id, problem));
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
	--_scanDepth;
	refreshWatchedPaths();
	if (!_scanDepth && !_watcher && QCoreApplication::instance()) {
		startWatching();
	}
}
void Manager::refreshWatchedPaths() {
	if (!_watcher) {
		return;
	}
	const auto root = QDir(pluginsRoot());
	if (!root.exists()) {
		return;
	}
	// Only the source of a plugin is worth watching, and only the files
	// themselves: watching a plugin *directory* means any write inside it
	// counts, and Python writes __pycache__/*.pyc into the directory every
	// time it imports something. That turns one reload into a self-sustaining
	// loop, each pass re-executing every plugin on the main thread, which is
	// what made the window look hung.
	//
	// The root folder is not watched at all, and the files the app writes
	// itself (state, per-plugin logs, settings) are skipped for the same
	// reason: a scan must never trigger the next scan.
	QStringList pluginDirs;
	for (const auto &entry: root.entryList(QDir::Dirs | QDir::NoDotAndDotDot)) {
		if (entry.startsWith('.') || entry == u"__pycache__"_q) {
			continue;
		}
		pluginDirs.push_back(root.filePath(entry));
	}
	QStringList watched;
	for (const auto &dir: pluginDirs) {
		for (const auto &file: QDir(dir).entryList(
				QDir::Files | QDir::NoDotAndDotDot)) {
			if (!file.endsWith(u".py"_q) && !file.endsWith(u".json"_q)) {
				continue;
			}
// Skipped because the app writes them itself. The manifest is not
			// the obvious case: inferring a plugin's permissions rewrites
			// plugin.json, and a manifest the bridge just wrote must never be
			// mistaken for the user editing the plugin. Reloading on it
			// re-executed every plugin on top of hooks that were still live.
			if (file == u"plugins.json"_q
				|| file.endsWith(u".settings.json"_q)
				|| file.endsWith(u".log"_q)
				|| file == u"plugin.json"_q
				|| file == u"manifest.json"_q
				|| file == u"manifest.yaml"_q
				|| file == u"manifest.yml"_q) {
				continue;
			}
			watched.push_back(dir + u'/' + file);
		}
	}
	// Rebuild the watch list from scratch: addPath/removePath on a watched
	// directory can itself raise a change notification.
	const auto current = _watcher->directories() + _watcher->files();
	for (const auto &path: current) {
		_watcher->removePath(path);
	}
	for (const auto &path: watched) {
		if (QFileInfo::exists(path)) {
			_watcher->addPath(path);
		}
	}
}

void Manager::startWatching() {
	if (_watcher) {
		return;
	}
	// Manager is not a QObject, so the watcher and timer are parented to the
	// application and drive plain callbacks back into this class.
	_watcher = new QFileSystemWatcher();
	_watchTimer = new QTimer();
	_watchTimer->setSingleShot(true);
	_watchTimer->setInterval(700);
	QObject::connect(
		_watcher,
		&QFileSystemWatcher::directoryChanged,
		_watcher,
		[this] { onFilesystemChanged(); });
	QObject::connect(
		_watcher,
		&QFileSystemWatcher::fileChanged,
		_watcher,
		[this] { onFilesystemChanged(); });
	QObject::connect(
		_watchTimer,
		&QTimer::timeout,
		_watchTimer,
		[this] {
			// A replaced file drops out of the watch list, so re-add it
			// and then reload.
			refreshWatchedPaths();
			if (_scanDepth) {
				return;
			}
			// A reloading plugin rewrites files under the watch root, which
			// would queue another reload. Rate-limit so a pathological case
			// degrades into periodic reloads instead of a hot loop.
			const auto now = crl::now();
			if ((now - _lastWatchReload) < crl::time(3)) {
				return;
			}
			_lastWatchReload = now;
			LOG(("AyuPlugins: plugin change detected, reloading"));
			scan(true);
		});
	refreshWatchedPaths();
	LOG(("AyuPlugins: watching %1 for changes").arg(
		QString::number(_watcher->directories().size()
			+ _watcher->files().size())));
}

void Manager::onFilesystemChanged() {
	if (!_watchTimer) {
		return;
	}
	// Editors fire several events per save; coalesce them.
	_watchTimer->start();
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
		if (!ManifestRunsHere(it->second.manifest)) {
			if (error) {
				*error = u"this plugin is built for "_q
					+ it->second.manifest.platforms.join(u", "_q);
			}
			return false;
		}
		_enabled[id] = true;
		it->second.state = PluginState::Loaded;
		it->second.lastError.clear();
		it->second.muted = false;
		saveState();
		// Already loaded means the module is resident and its hooks subscribed,
		// so switching it back on is only a matter of clearing the mute. Running
		// main.py a second time would leave the first copy's threads alive.
		needLoad = _pythonAvailable && !it->second.pythonLoaded;
		if (needLoad) {
			manifest = it->second.manifest;
		}
	}
	Bridge::SetPluginMuted(id, false);
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

void Manager::mute(const QString &id, const QString &reason) {
	// Stops calling a plugin's hooks without unloading it. Unloading drops the
	// last reference to its module while its own threads may still be running
	// inside it, and freeing those objects faults inside the interpreter: the
	// app was observed dying with an access violation in python314.dll right
	// after the slow-hook breaker disabled something. Nothing is destroyed
	// here, so a misbehaving plugin costs some background work and no crash.
	{
		const auto lock = std::lock_guard(_mutex);
		_enabled[id] = false;
		const auto it = _plugins.find(id);
		if (it == _plugins.end()) {
			return;
		}
		it->second.state = PluginState::Disabled;
		it->second.muted = true;
		// Recorded where the plugin list can show it. The breaker is the only
		// thing that mutes without being asked, and a plugin that silently stops
		// answering looks identical to one that broke.
		if (!reason.isEmpty()) {
			it->second.lastError = reason;
		}
		saveState();
	}
	// Outside the manager's lock. Setting the flag on the instance was not
	// enough: the bridge decides from its own set, and that set stayed empty, so
	// nothing was actually skipped.
	Bridge::SetPluginMuted(id, true);
	if (!reason.isEmpty()) {
		LOG(("AyuPlugins: muted %1 (%2)").arg(id).arg(reason));
	}
}

void Manager::unmute(const QString &id) {
	bool loaded = false;
	{
		const auto lock = std::lock_guard(_mutex);
		_enabled[id] = true;
		const auto it = _plugins.find(id);
		if (it == _plugins.end()) {
			return;
		}
		it->second.muted = false;
		loaded = it->second.pythonLoaded;
		if (loaded) {
			it->second.state = PluginState::Loaded;
		}
		saveState();
	}
	// Clears the bridge's set, which is what HookMuted reads. Without this a
	// muted plugin stayed silent even after being switched back on.
	Bridge::SetPluginMuted(id, false);
	LOG(("AyuPlugins: unmuted %1").arg(id));
}

void Manager::disable(const QString &id) {
	// Mute rather than tear down.
	//
	// Tearing down meant releasing the event bus subscriptions and then
	// re-running main.py when the plugin was switched on again. Both halves were
	// dangerous: releasing the subscriptions destroys the Python callables they
	// hold, and re-running the file produced a second copy of the plugin whose
	// old threads were still alive - every toggle leaked one. Muting keeps the
	// module and its threads, and the bridge simply stops calling its hooks.
	{
		const auto lock = std::lock_guard(_mutex);
		const auto it = _plugins.find(id);
		if (it == _plugins.end()) return;
		_enabled[id] = false;
		it->second.state = PluginState::Disabled;
		it->second.muted = true;
		saveState();
	}
	Bridge::SetPluginMuted(id, true);
	// And give it the chance to stop. Muting only stops hooks being called; a
	// plugin left running goes on consuming a thread and a flag loop for as long
	// as the app is open, with nothing on screen to say so.
	crl::on_main([id] { Bridge::StopPluginInstance(id); });
}

void Manager::unloadPython(const QString &id) {
	PythonHost::instance().unloadPlugin(id);
	_settingsSchema.erase(id);
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

// Что установленный плагин собирается делать и чего он не сможет.
//
// Смотрит текст исходника на Android-зависимости, которые Desktop
// выполнить не в состоянии: подписки через find_class, перехват Java-методов,
// рисование через android.graphics.Canvas и прямую работу с базой Telegram.
// Ничего не запускается, только чтение файла, поэтому проверка безопасна и
// мгновенна.
//
// Раньше установка молчала, плагин писал "load ok", а эффекта не наступало
// никогда. Пользователь узнавал об этом через неделю и приходил с вопросом
// "почему плагин молчит".
[[nodiscard]] QString InspectInstalledPlugin(
	const QString &dirPath,
	const PluginManifest &manifest) {
	auto android = QStringList();
	const auto dir = QDir(dirPath);
	const auto files = dir.entryList(QDir::Files, QDir::Name);
	for (const auto &name : files) {
		if (name != u"main.py"_q
			&& name != u"plugin.py"_q
			&& name != u"__init__.py"_q
			&& name != u"index.py"_q
			&& name != u"bot.py"_q) {
			continue;
		}
		auto f = QFile(dir.filePath(name));
		if (!f.open(QIODevice::ReadOnly)) {
			continue;
		}
		const auto text = QString::fromUtf8(f.readAll());
		// find_class всегда возвращает пустоту на Desktop, поэтому каждая
		// подписка через него не сработает никогда.
		if (text.contains(u"find_class("_q)) {
			android.push_back(u"подписки через find_class"_q);
		}
		// Java-методы и конструкторы перехватывать нечем.
		if (text.contains(u"hook_all_methods("_q)
			|| text.contains(u"hook_method("_q)
			|| text.contains(u"hook_all_constructors("_q)) {
			android.push_back(u"перехват Java-методов"_q);
		}
		// Диалоги шим умеет, а вот Canvas у Desktop нет: рисовать нечем.
		// AlertDialogBuilder намеренно не проверяется - через него работают
		// плагины, которые иначе были бы помечены зря.
		if (text.contains(u"android.graphics.Canvas"_q)
			|| text.contains(u"canvas.draw"_q)) {
			android.push_back(u"рисование на Android Canvas"_q);
		}
		// Прямая работа с базой Telegram: в Desktop другое хранилище.
		if (text.contains(u"MessagesStorage"_q)
			|| text.contains(u"executeFast("_q)) {
			android.push_back(u"прямой доступ к базе Telegram"_q);
		}
	}
	if (android.isEmpty()) {
		return {};
	}
	android.removeDuplicates();
	return manifest.name
		+ u" (" + manifest.id + u") сможет работать на Desktop не полностью. Не сможет: "_q
		+ android.join(u", "_q)
		+ u". Плагин загрузится, но эта его часть работать не будет."_q;
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

	// Отчёт о несовместимости копится отдельно: установка при этом
	// успешна, и ошибкой её называть нельзя. Раньше установка молчала,
	// плагин писал "load ok", а эффекта не наступало никогда: все хуки
	// уходили в find_class, которого в Desktop нет.
	const auto incompatibility = InspectInstalledPlugin(dst, tmpManifest);
	if (!incompatibility.isEmpty()) {
		_lastIncompatibility = incompatibility;
	} else {
		_lastIncompatibility.clear();
	}
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

QString Manager::lastIncompatibility() const {
	const auto lock = std::lock_guard(_mutex);
	return _lastIncompatibility;
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
