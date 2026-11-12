// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "ayu/plugins/plugin_manifest.h"

#include <QByteArray>
#include <QJsonObject>
#include <QMap>
#include <QString>
#include <QStringList>
#include <QVariant>
#include <QVector>

class QString;

namespace Ayu::Plugins::Bridge {

[[nodiscard]] bool InitPython();
void ShutdownPython();

// Writes a marker on the normal shutdown path, so a supervisor can tell a quit
// from a crash. Reached from ShutdownPython.
void AnnounceCleanExit();
bool LoadPlugin(const PluginManifest &manifest, QString *error);
void UnloadPlugin(const QString &id);

// Reads a loaded plugin's declared rows and current values as JSON:
// {"rows": [...], "values": {...}}. Enter Python, so call it while the
// interpreter is already held rather than from a main-thread UI callback.
[[nodiscard]] QByteArray PluginSettingsJson(const QString &id);
bool ApplyPluginSettings(const QString &id, const QJsonObject &values);

// Health of one plugin's hook usage, for the built-in plugin check.
[[nodiscard]] int PluginHookErrors(const QString &id);
[[nodiscard]] QString PluginLastHookIssue(const QString &id);

// How much a plugin actually did this session. Used to tell a working
// plugin from one that merely loaded: a plugin with hooks registered but
// nothing ever delivered, and none of the client's own calls made, is
// silent, and saying so is the whole point.
[[nodiscard]] int PluginHookSubscriptions(const QString &id);
[[nodiscard]] int64_t PluginHookCalls(const QString &id);
[[nodiscard]] QStringList PluginApiCalls(const QString &id);

// Sends a command to the user's own Saved Messages, so a UI button can
// reuse a plugin's existing command instead of inventing a second place
// for the same answer. Answers whether it was handed over.
bool SendCommandToSelf(const QString &text, int account = -1);
// Cleared when plugins reload, so the counts describe one run and not all
// of them added together.
void ResetPluginActivity();
// Whether the interpreter is on its way out. Reading the counts while it is
// shutting down is pointless: the numbers stop changing halfway through and
// say nothing either way.
[[nodiscard]] bool BridgeShuttingDown();

// What the interpreter could not import at startup, with the fix. Empty when
// the runtime is complete.
[[nodiscard]] QStringList RuntimeProblems();

// ayu_api names plugins reached for that Desktop has no implementation of.
// Recorded rather than raised, so the plugin keeps running and the plugin
// check can say exactly what it could not do.
[[nodiscard]] QStringList ApiGaps();

// Mute / unmute a plugin: its hooks stop being called, and nothing is
// unloaded. This is what switching a plugin off does.
void SetPluginMuted(const QString &id, bool muted);
[[nodiscard]] bool IsPluginMuted(const QString &id);

// Puts text on the system clipboard. Answers whether it was handed over.
bool CopyToClipboard(const std::string &text);

// Tells a plugin to put itself away - cancels its timers, drops its registry
// entry and calls on_plugin_unload. Called when a plugin is switched off; its
// module stays loaded, so this is safe to call again on the way back.
void StopPluginInstance(const QString &id);

// Drops a plugin's event bus subscriptions with the interpreter locked, so the
// Python callables they hold are never released without it.
void ReleasePluginHooks(const QString &id, const std::vector<int> &tokens);

// Fills in the permissions a plugin's own code needs. Returns what was granted,
// each with the import that justified it. Nothing happens when the manifest
// already declares them.
[[nodiscard]] QStringList InferPermissions(const QString &dirPath);

// Marks that the main thread is inside a hook, where a plugin must not be
// made to wait for the GIL.
void SetHookOnMainThread(bool value);


} // namespace Ayu::Plugins::Bridge
