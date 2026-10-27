// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/plugins/python_host.h"

#include "logs.h"

#ifdef DESKTOP_APP_WITH_PLUGINS
#include "ayu/plugins/bridge/python_bridge.h"
#endif

namespace Ayu::Plugins {

PythonHost &PythonHost::instance() {
	static PythonHost h;
	return h;
}

bool PythonHost::init() {
	if (_inited) return _available;
	_inited = true;
#ifdef DESKTOP_APP_WITH_PLUGINS
	_available = Bridge::InitPython();
	if (_available) {
		LOG(("AyuPlugins: python bridge ready"));
	} else {
		LOG(("AyuPlugins: python init failed, running in metadata-only mode"));
	}
#else
	_available = false;
	LOG(("AyuPlugins: built without python, metadata-only mode"));
#endif
	return _available;
}

void PythonHost::shutdown() {
	if (!_inited) return;
#ifdef DESKTOP_APP_WITH_PLUGINS
	Bridge::ShutdownPython();
#endif
	_inited = false;
	_available = false;
}

bool PythonHost::loadPlugin(const PluginManifest &manifest, QString *error) {
	if (!_available) return true;
#ifdef DESKTOP_APP_WITH_PLUGINS
	return Bridge::LoadPlugin(manifest, error);
#else
	(void)manifest;
	(void)error;
	return true;
#endif
}

void PythonHost::unloadPlugin(const QString &id) {
#ifdef DESKTOP_APP_WITH_PLUGINS
	Bridge::UnloadPlugin(id);
#else
	(void)id;
#endif
}

void PythonHost::logError(const QString &pluginId, const QString &msg) {
	LOG(("AyuPlugins [%1]: %2").arg(pluginId, msg));
}

} // namespace Ayu::Plugins
