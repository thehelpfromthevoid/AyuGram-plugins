// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#ifdef DESKTOP_APP_WITH_PLUGINS
#include "ayu/plugins/bridge/python_bridge.h"

#include "api/api_common.h"
#include "apiwrap.h"
#include "ayu/plugins/event_bus.h"
#include "ayu/plugins/plugins_manager.h"
#include "ayu/utils/telegram_helpers.h"
#include "crl/crl_async.h"
#include "core/application.h"
#include "data/data_session.h"
#include "history/history.h"
#include "main/main_session.h"
#include "ui/toast/toast.h"
#include "window/window_controller.h"

#include <QtCore/QCoreApplication>
#include <QtCore/QDir>
#include <QtCore/QFile>
#include <QtCore/QFileInfo>

#include <pybind11/embed.h>
#include <pybind11/stl.h>

namespace py = pybind11;

namespace Ayu::Plugins::Bridge {
namespace {

std::unique_ptr<py::scoped_interpreter> g_guard;
std::map<QString, py::object> g_modules;
std::mutex g_mutex;
QString g_shimDir;
thread_local QString g_loadingPluginId;
std::map<QString, int> g_hookErrors;
constexpr int kMaxHookErrors = 12;

void NoteHookError(const QString &pluginId, const QString &detail) {
	if (pluginId.isEmpty()) {
		LOG(("AyuPlugins: hook error: %1").arg(detail.left(240)));
		return;
	}
	auto &count = g_hookErrors[pluginId];
	++count;
	LOG(("AyuPlugins: hook error in %1 (%2): %3").arg(
		pluginId,
		QString::number(count),
		detail.left(240)));
	if (count == kMaxHookErrors) {
		LOG(("AyuPlugins: auto-disabling plugin %1 after repeated hook errors"
			).arg(pluginId));
		crl::on_main([pluginId] {
			Manager::instance().disable(pluginId);
		});
	}
}

[[nodiscard]] bool HookBudgetExceeded(const QString &pluginId) {
	if (pluginId.isEmpty()) {
		return false;
	}
	const auto it = g_hookErrors.find(pluginId);
	return it != g_hookErrors.end() && it->second >= kMaxHookErrors;
}

template <typename Call>
void SafeCallHook(const QString &pluginId, Call &&call) {
	if (HookBudgetExceeded(pluginId)) {
		return;
	}
	try {
		auto g = py::gil_scoped_acquire();
		try {
			call();
		} catch (py::error_already_set &e) {
			NoteHookError(pluginId, QString::fromUtf8(e.what()));
			e.restore();
			PyErr_Clear();
		} catch (const std::exception &e) {
			NoteHookError(pluginId, QString::fromUtf8(e.what()));
		} catch (...) {
			NoteHookError(pluginId, u"unknown C++ exception in hook"_q);
		}
	} catch (const std::exception &e) {
		NoteHookError(pluginId, QString::fromUtf8(e.what()));
	} catch (...) {
		NoteHookError(pluginId, u"gil/hook failure"_q);
	}
}

[[nodiscard]] HookId ResolveHookId(const QString &name, bool *ok) {
	auto id = HookId::OnMessage;
	if (HookIdFromString(name, id)) {
		if (ok) *ok = true;
		return id;
	}
	const auto lower = name.toLower();
	if ((lower.contains(u"send"_q) && lower.contains(u"message"_q))
		|| lower.contains(u"before_send"_q)) {
		if (ok) *ok = true;
		return HookId::OnMessageSending;
	}
	if (lower.contains(u"delete"_q) && lower.contains(u"message"_q)) {
		if (ok) *ok = true;
		return HookId::OnMessageDeleted;
	}
	if (lower.contains(u"chat_opened"_q) || lower.contains(u"open_chat"_q)) {
		if (ok) *ok = true;
		return HookId::OnChatOpened;
	}
	if (ok) *ok = false;
	return HookId::OnMessage;
}

void ToastOnMain(const std::string &text) {
	static auto lastAt = crl::time(0);
	static auto lastText = QString();
	static auto burst = 0;
	const auto q = QString::fromStdString(text);
	const auto now = crl::now();
	if (q == lastText && (now - lastAt) < crl::time(750)) {
		return;
	}
	if ((now - lastAt) < crl::time(200)) {
		++burst;
		if (burst > 8) {
			return;
		}
	} else {
		burst = 0;
	}
	lastAt = now;
	lastText = q;
	crl::on_main([q] {
		if (const auto window = Core::App().activeWindow()) {
			Ui::Toast::Show(window->widget(), { .text = { q } });
		}
	});
}

void SendTextOnMain(int64 peerId, const std::string &text) {
	const auto body = QString::fromStdString(text);
	crl::on_main([peerId, body] {
		const auto session = Core::App().maybePrimarySession();
		if (!session || body.isEmpty()) {
			return;
		}
		const auto peer = getPeerFromDialogId(static_cast<ID>(peerId));
		if (!peer) {
			LOG(("AyuPlugins: send_text unknown peer %1").arg(peerId));
			return;
		}
		auto message = Api::MessageToSend(
			Api::SendAction(peer->owner().history(peer)));
		message.textWithTags = { body, TextWithTags::Tags() };
		session->api().sendMessage(std::move(message));
	});
}

void RememberToken(int token) {
	if (g_loadingPluginId.isEmpty() || token <= 0) {
		return;
	}
	Manager::instance().addBusToken(g_loadingPluginId, token);
}

void RegisterHook(const std::string &name, py::function fn) {
	auto ok = false;
	const auto qname = QString::fromStdString(name);
	const auto id = ResolveHookId(qname, &ok);
	if (!ok) {
		LOG(("AyuPlugins: ignoring unsupported hook '%1'").arg(qname));
		return;
	}
	const auto holder = std::make_shared<py::object>(std::move(fn));
	const auto pluginId = g_loadingPluginId;
	switch (id) {
	case HookId::OnMessage:
		RememberToken(EventBus::instance().subscribeMessage(
			[holder, pluginId](const PluginMessage &msg) {
				SafeCallHook(pluginId, [&] {
					(*holder)(msg);
				});
			}));
		break;
	case HookId::OnMessageSending:
		RememberToken(EventBus::instance().subscribeMessageSending(
			[holder, pluginId](MessageSendingContext &ctx) {
				SafeCallHook(pluginId, [&] {
					(*holder)(ctx);
				});
			}));
		break;
	case HookId::OnMessageDeleted:
		RememberToken(EventBus::instance().subscribeDeleted(
			[holder, pluginId](const MessageDeletedContext &ctx) {
				SafeCallHook(pluginId, [&] {
					(*holder)(ctx);
				});
			}));
		break;
	case HookId::OnChatOpened:
		RememberToken(EventBus::instance().subscribeChatOpened(
			[holder, pluginId](const PluginChat &c) {
				SafeCallHook(pluginId, [&] {
					(*holder)(c);
				});
			}));
		break;
	case HookId::OnAppStart:
		RememberToken(EventBus::instance().subscribeAppStart(
			[holder, pluginId] {
				SafeCallHook(pluginId, [&] {
					(*holder)();
				});
			}));
		break;
	case HookId::OnAppStop:
		RememberToken(EventBus::instance().subscribeAppStop(
			[holder, pluginId] {
				SafeCallHook(pluginId, [&] {
					(*holder)();
				});
			}));
		break;
	case HookId::OnSettingChanged:
		RememberToken(EventBus::instance().subscribeSettingChanged(
			[holder, pluginId](const QString &key, const QString &value) {
				SafeCallHook(pluginId, [&] {
					(*holder)(key.toStdString(), value.toStdString());
				});
			}));
		break;
	}
}

PYBIND11_EMBEDDED_MODULE(ayu_api, m) {
	m.doc() = "AyuGram Desktop plugin API";

	auto msg = py::class_<PluginMessage>(m, "Message");
	msg.def(py::init<>())
		.def_readwrite("msg_id", &PluginMessage::msgId)
		.def_readwrite("peer_id", &PluginMessage::peerId)
		.def_readwrite("from_id", &PluginMessage::fromId)
		.def_readwrite("date", &PluginMessage::date)
		.def_readwrite("out", &PluginMessage::out)
		.def_property("text",
			[](const PluginMessage &s) { return s.text.toStdString(); },
			[](PluginMessage &s, const std::string &v) {
				s.text = QString::fromStdString(v);
			})
		.def_property("message",
			[](const PluginMessage &s) { return s.text.toStdString(); },
			[](PluginMessage &s, const std::string &v) {
				s.text = QString::fromStdString(v);
			});

	auto chat = py::class_<PluginChat>(m, "Chat");
	chat.def(py::init<>())
		.def_readwrite("peer_id", &PluginChat::peerId)
		.def_readwrite("type", &PluginChat::type)
		.def_property("title",
			[](const PluginChat &s) { return s.title.toStdString(); },
			[](PluginChat &s, const std::string &v) {
				s.title = QString::fromStdString(v);
			});

	auto sending = py::class_<MessageSendingContext>(m, "MessageSendingContext");
	sending.def(py::init<>())
		.def_readwrite("message", &MessageSendingContext::message)
		.def_readwrite("cancel", &MessageSendingContext::cancel)
		.def_readwrite("rewrite", &MessageSendingContext::rewrite)
		.def_property("text",
			[](const MessageSendingContext &s) {
				return s.message.text.toStdString();
			},
			[](MessageSendingContext &s, const std::string &v) {
				s.message.text = QString::fromStdString(v);
			})
		.def_property("rewrittenText",
			[](const MessageSendingContext &s) {
				return s.rewrittenText.toStdString();
			},
			[](MessageSendingContext &s, const std::string &v) {
				s.rewrittenText = QString::fromStdString(v);
			})
		.def_property("rewritten_text",
			[](const MessageSendingContext &s) {
				return s.rewrittenText.toStdString();
			},
			[](MessageSendingContext &s, const std::string &v) {
				s.rewrittenText = QString::fromStdString(v);
			});

	auto deleted = py::class_<MessageDeletedContext>(m, "MessageDeletedContext");
	deleted.def(py::init<>())
		.def_readwrite("peer_id", &MessageDeletedContext::peerId)
		.def_property("msgIds",
			[](const MessageDeletedContext &s) { return s.msgIds; },
			[](MessageDeletedContext &s, const std::vector<int64> &v) {
				s.msgIds = v;
			})
		.def_property("msg_ids",
			[](const MessageDeletedContext &s) { return s.msgIds; },
			[](MessageDeletedContext &s, const std::vector<int64> &v) {
				s.msgIds = v;
			});

	m.def("toast", &ToastOnMain, py::arg("text"));
	m.def("log", [](const std::string &s) {
		LOG(("AyuPlugin: %1").arg(QString::fromStdString(s)));
	});
	m.def("send_text", &SendTextOnMain, py::arg("peer_id"), py::arg("text"));

	m.def("hook", [](const std::string &name, py::object fn) -> py::object {
		if (fn.is_none()) {
			return py::cpp_function([name](py::function f) {
				RegisterHook(name, std::move(f));
				return f;
			});
		}
		RegisterHook(name, fn.cast<py::function>());
		return py::none();
	}, py::arg("name"), py::arg("fn") = py::none());
}

} // namespace

bool InitPython() {
	try {
		g_guard = std::make_unique<py::scoped_interpreter>();
		auto g = py::gil_scoped_acquire();
		py::module_::import("ayu_api");
		auto sys = py::module_::import("sys");
		auto path = sys.attr("path");
		path.attr("insert")(0, "");

		const auto appDir = QCoreApplication::applicationDirPath();
		path.attr("insert")(0, appDir.toStdString());

		const auto shimEnv = qEnvironmentVariable("AYU_PLUGINS_SHIM");
		auto shimDirs = QStringList{
			shimEnv,
			appDir,
			appDir + u"/python_shim"_q,
			appDir + u"/../python_shim"_q,
		};
		for (const auto &dir : shimDirs) {
			if (dir.isEmpty()) {
				continue;
			}
			const auto cleaned = QDir::cleanPath(dir);
			if (QFileInfo(cleaned + u"/ayugram_compat"_q).isDir()
				|| QFile::exists(cleaned + u"/base_plugin.py"_q)) {
				path.attr("insert")(0, cleaned.toStdString());
				g_shimDir = cleaned;
				break;
			}
		}
		try {
			py::module_::import("ayugram_compat.mobile_stubs").attr("install")();
		} catch (py::error_already_set &e) {
			e.restore();
			PyErr_Clear();
			LOG(("AyuPlugins: mobile_stubs install skipped"));
		} catch (...) {
		}
		return true;
	} catch (const std::exception &e) {
		LOG(("AyuPlugins: InitPython failed: %1").arg(
			QString::fromUtf8(e.what(), -1)));
		return false;
	}
}

void ConfigureSandbox(const PluginManifest &manifest) {
	// Called with the GIL held. Best-effort: never fails the load.
	// Grants are unioned process-wide across loads, never narrowed.
	try {
		auto sys = py::module_::import("sys");
		auto path = sys.attr("path");
		auto haveSandbox = false;
		if (!g_shimDir.isEmpty()) {
			const auto dir = QDir::cleanPath(g_shimDir + u"/../sandbox"_q);
			if (QFile::exists(dir + u"/import_filter.py"_q)) {
				path.attr("insert")(0, dir.toStdString());
				haveSandbox = true;
			}
		}
		if (!haveSandbox) {
			const auto dir = QDir::cleanPath(
				QCoreApplication::applicationDirPath() + u"/sandbox"_q);
			if (QFile::exists(dir + u"/import_filter.py"_q)) {
				path.attr("insert")(0, dir.toStdString());
				haveSandbox = true;
			}
		}
		if (!haveSandbox) {
			return;
		}
		auto toStrings = [](const QStringList &list) {
			auto out = std::vector<std::string>();
			out.reserve(list.size());
			for (const auto &s : list) out.push_back(s.toStdString());
			return out;
		};
		static const auto kNetPrefix = u"net.http:"_q;
		auto granted = QStringList();
		auto netHosts = QStringList();
		auto storage = false;
		auto process = false;
		for (const auto &perm : manifest.permissions) {
			granted << perm;
			if (perm == u"net.http"_q) {
				netHosts << u"*"_q;
			} else if (perm.startsWith(kNetPrefix)) {
				const auto host = perm.mid(kNetPrefix.size());
				if (!host.isEmpty()) netHosts << host;
			} else if (perm == u"storage.read"_q || perm == u"storage.write"_q) {
				storage = true;
			} else if (perm == u"process.spawn"_q) {
				process = true;
			}
		}
		auto filt = py::module_::import("import_filter");
		filt.attr("grant")(py::cast(toStrings(granted)));
		auto audit = py::module_::import("audithook");
		audit.attr("grant")(
			py::arg("net_hosts") = py::cast(toStrings(netHosts)),
			py::arg("storage") = storage,
			py::arg("process") = process);
		filt.attr("install")();
		audit.attr("install")();
	} catch (py::error_already_set &e) {
		e.restore();
		PyErr_Clear();
		LOG(("AyuPlugins: sandbox setup skipped"));
	} catch (...) {
		LOG(("AyuPlugins: sandbox setup skipped"));
	}
}

void ShutdownPython() {
	const auto lock = std::lock_guard(g_mutex);
	g_modules.clear();
	g_guard.reset();
}

bool LoadPlugin(const PluginManifest &manifest, QString *error) {
	try {
		auto g = py::gil_scoped_acquire();
		g_loadingPluginId = manifest.id;
		const auto entryPath = manifest.sourcePath + u"/"_q + manifest.entry;
		auto sys = py::module_::import("sys");
		auto path = sys.attr("path");
		const auto source = manifest.sourcePath.toStdString();
		const auto root = Manager::instance().pluginsRoot().toStdString();
		path.attr("insert")(0, source);
		if (!root.empty() && root != source) {
			path.attr("insert")(0, root);
		}
		ConfigureSandbox(manifest);
		auto builtins = py::module_::import("builtins");
		const auto open = builtins.attr("open");
		auto file = open(entryPath.toStdString(), "r", py::arg("encoding") = "utf-8");
		const auto code = py::str(file.attr("read")());
		file.attr("close")();
		auto globals = py::dict();
		globals["__name__"] = "__main__";
		globals["__file__"] = entryPath.toStdString();
		py::exec(code, globals, globals);
		try {
			auto base = py::module_::import("base_plugin");
			if (py::hasattr(base, "activate_base_plugins")) {
				base.attr("activate_base_plugins")(globals);
			}
		} catch (py::error_already_set &e) {
			e.restore();
			PyErr_Clear();
		}
		{
			const auto lock = std::lock_guard(g_mutex);
			g_modules.emplace(
				manifest.id,
				py::reinterpret_borrow<py::object>(globals));
		}
		g_loadingPluginId.clear();
		return true;
	} catch (py::error_already_set &e) {
		g_loadingPluginId.clear();
		auto msg = QString::fromUtf8(e.what());
		const auto lower = msg.toLower();
		if (lower.contains(u"no module named 'java'"_q)
			|| lower.contains(u"no module named 'android'"_q)
			|| lower.contains(u"android-only plugin"_q)
			|| lower.contains(u"java runtime is not available"_q)) {
			msg = u"Android-only plugin — Java/Android APIs are not "
				u"available on Desktop AyuGram."_q;
		}
		if (error) *error = msg.left(512);
		LOG(("AyuPlugins: load %1 failed: %2").arg(manifest.id, msg));
		return false;
	} catch (const std::exception &e) {
		g_loadingPluginId.clear();
		if (error) *error = QString::fromUtf8(e.what()).left(512);
		LOG(("AyuPlugins: load %1 failed: %2").arg(
			manifest.id,
			QString::fromUtf8(e.what())));
		return false;
	}
}

void UnloadPlugin(const QString &id) {
	const auto lock = std::lock_guard(g_mutex);
	g_modules.erase(id);
	g_hookErrors.erase(id);
}

} // namespace Ayu::Plugins::Bridge
#endif
