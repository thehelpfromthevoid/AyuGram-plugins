// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
//
// AyuGram plugin host for Desktop, (c) @CE0fbi
#include "ayu/plugins/plugin_manifest.h"

#include "ayu/libs/json.hpp"

#include "logs.h"

#include <QRegularExpression>

namespace Ayu::Plugins {
namespace {

[[nodiscard]] bool IsValidId(const QString &id) {
	static const auto re = QRegularExpression(u"^[a-zA-Z][a-zA-Z0-9_.-]{1,63}$"_q);
	return re.match(id).hasMatch();
}

} // namespace

bool ManifestRunsHere(const PluginManifest &m) {
	if (m.platforms.isEmpty()) {
		return true;
	}
	for (const auto &p : m.platforms) {
		const auto lower = p.toLower();
		if (lower == u"desktop"_q || lower == u"windows"_q
			|| lower == u"macos"_q || lower == u"linux"_q
			|| lower == u"all"_q || lower == u"any"_q) {
			return true;
		}
	}
	return false;
}

QStringList ManifestProblems(const PluginManifest &m) {
	auto out = QStringList();
	if (!IsValidId(m.id)) {
		out.append(u"id does not match the app's pattern"_q);
	}
	if (m.name.isEmpty() || m.name.size() > 100) {
		out.append(u"name must be 1..100 chars"_q);
	}
	if (m.description.size() > kMaxDescriptionLength) {
		out.append(u"description longer than "_q
			+ QString::number(kMaxDescriptionLength) + u" chars"_q);
	}
	if (m.entry.isEmpty() || m.entry.contains(u".."_q)) {
		out.append(u"entry must be a plain file name"_q);
	}
	if (m.apiVersion <= 0) {
		out.append(u"api_version must be a positive number"_q);
	} else if (m.apiVersion > kPluginApiVersion) {
		out.append(u"api_version "_q + QString::number(m.apiVersion)
			+ u" is newer than the app's "_q
			+ QString::number(kPluginApiVersion) + u": the app will refuse it"_q);
	}
	for (const auto &h : m.hooks) {
		auto id = HookId::OnMessage;
		if (!HookIdFromString(h, id)) {
			out.append(u"unknown hook: "_q + h);
		}
	}
	if (!ManifestRunsHere(m)) {
		out.append(u"built for "_q + m.platforms.join(u", "_q)
			+ u", not this platform"_q);
	}
	return out;
}

bool ValidateManifest(const PluginManifest &m, QString *error) {
	auto fail = [&](const QString &e) {
		if (error) *error = e;
		return false;
	};
	if (!IsValidId(m.id)) return fail(u"bad plugin id"_q);
	if (m.name.isEmpty() || m.name.size() > 100) return fail(u"bad plugin name"_q);
	if (m.description.size() > kMaxDescriptionLength) return fail(u"description too long"_q);
	if (m.entry.isEmpty() || m.entry.contains(u".."_q)) return fail(u"bad entry"_q);
	if (m.apiVersion <= 0 || m.apiVersion > kPluginApiVersion) return fail(u"unsupported api_version"_q);
	for (const auto &h : m.hooks) {
		auto id = HookId::OnMessage;
		if (!HookIdFromString(h, id)) return fail(u"unknown hook: "_q + h);
	}
	return true;
}

bool ManifestHasPermission(const PluginManifest &m, const QString &perm) {
	for (const auto &p : m.permissions) {
		if (p == perm) return true;
		if (p.endsWith(u".*"_q)) {
			const auto prefix = p.mid(0, p.size() - 2);
			if (perm.startsWith(prefix)) return true;
		}
	}
	return false;
}

bool ParseManifestJson(const QByteArray &data, PluginManifest &out, QString *error) {
	try {
		const auto j = nlohmann::json::parse(data.constData(), data.constData() + data.size());
		auto getStr = [&](const char *key, const QString &def = {}) {
			if (!j.contains(key) || !j[key].is_string()) return def;
			return QString::fromStdString(j[key].get<std::string>());
		};
		out.id = getStr("id");
		out.name = getStr("name").left(100);
		out.description = getStr("description").left(kMaxDescriptionLength);
		out.author = getStr("author").left(200);
		out.version = getStr("version", u"1.0"_q).left(32);
		out.minAppVersion = getStr("min_app_version", getStr("min_version"));
		out.entry = getStr("entry", u"main.py"_q);
		out.icon = getStr("icon");
		out.apiVersion = j.value("api_version", kPluginApiVersion);
		if (j.contains("hooks") && j["hooks"].is_array()) {
			for (const auto &h : j["hooks"]) {
				if (!h.is_string()) continue;
				const auto name = QString::fromStdString(h.get<std::string>());
				auto hid = HookId::OnMessage;
				if (!HookIdFromString(name, hid)) {
					LOG(("AyuPlugins: manifest %1: unknown hook '%2' skipped").arg(out.id, name));
					continue;
				}
				out.hooks.push_back(name);
			}
		}
		if (j.contains("permissions") && j["permissions"].is_array()) {
			for (const auto &p : j["permissions"]) {
				if (p.is_string()) out.permissions.push_back(QString::fromStdString(p.get<std::string>()));
			}
		}
		if (j.contains("requirements") && j["requirements"].is_array()) {
			for (const auto &r : j["requirements"]) {
				if (r.is_string()) out.requirements.append(QString::fromStdString(r.get<std::string>()));
			}
		}
		if (j.contains("platforms") && j["platforms"].is_array()) {
			for (const auto &p : j["platforms"]) {
				if (p.is_string()) out.platforms.append(QString::fromStdString(p.get<std::string>()));
			}
		} else if (j.value("desktop_only", false)) {
			out.platforms.append(u"desktop"_q);
		}
		return ValidateManifest(out, error);
	} catch (const std::exception &e) {
		if (error) *error = QString::fromLatin1(e.what()).left(256);
		return false;
	}
}

QString ExampleManifestJson() {
	return uR"({
  "id": "com.example.my-plugin",
  "name": "My Plugin",
  "description": "Short description of what the plugin does.",
  "author": "@username",
  "version": "1.0.0",
  "min_app_version": "5.10",
  "api_version": 1,
  "entry": "main.py",
  "hooks": [
    "on_message",
    "on_message_sending",
    "on_message_deleted",
    "on_chat_opened",
    "on_app_start",
    "on_app_stop",
    "on_setting_changed"
  ],
  "permissions": [
    "ui.toast",
    "storage.read"
  ],
  "requirements": []
})"_q;
}

PluginManifest ManifestFromLegacyMetadata(
		const QString &id,
		const QString &name,
		const QString &description,
		const QString &author,
		const QString &version,
		const QString &minVersion,
		const QStringList &requirements,
		const QString &icon) {
	auto m = PluginManifest();
	m.id = id;
	m.name = name.left(100);
	m.description = description.left(kMaxDescriptionLength);
	m.author = author.left(200);
	m.version = version.isEmpty() ? u"1.0"_q : version.left(32);
	m.minAppVersion = minVersion;
	m.requirements = requirements;
	m.icon = icon;
	m.entry = u"main.py"_q;
	m.apiVersion = kPluginApiVersion;
	m.hooks = { u"on_message"_q, u"on_message_sending"_q };
	m.permissions = { u"ui.toast"_q };
	return m;
}

} // namespace Ayu::Plugins
