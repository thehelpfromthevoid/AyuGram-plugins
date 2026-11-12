// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#ifdef DESKTOP_APP_WITH_PLUGINS

#include <QtCore/QString>

namespace Ayu::Plugins::Bridge {

// Handle of python314.dll resolved at process start by ayu_python_loader.cpp.
// The executable deliberately does not import it: the linker cannot
// delay-load a DLL whose imports include data symbols, and this machine's
// loader does not resolve it from the application directory.
void *PythonModule();

// Non-zero when python314.dll could not be loaded at all, so the bridge can
// report one clear reason instead of failing later inside the interpreter.
int PythonModuleMissing();

} // namespace Ayu::Plugins::Bridge

#endif // DESKTOP_APP_WITH_PLUGINS
