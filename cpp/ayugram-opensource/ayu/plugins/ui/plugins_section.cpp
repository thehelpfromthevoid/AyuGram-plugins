// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/plugins/ui/plugins_section.h"

#include "ayu/plugins/bridge/python_bridge.h"
#include "ayu/plugins/plugin_manifest.h"
#include "ayu/plugins/plugins_manager.h"
#include "ayu/ui/boxes/plugin_info_box.h"
#include "ayu/ui/boxes/plugin_settings_box.h"
#include "core/application.h"
#include "core/file_utilities.h"
#include "lang/lang_keys.h"
#include "lang_auto.h"
#include "settings/settings_builder.h"
#include "settings/settings_common.h"
#include "styles/style_ayu_styles.h"
#include "styles/style_layers.h"
#include "styles/style_menu_icons.h"
#include "styles/style_settings.h"
#include "ui/layers/generic_box.h"
#include "ui/vertical_list.h"
#include "ui/widgets/buttons.h"
#include "ui/widgets/checkbox.h"
#include "ui/widgets/labels.h"
#include "ui/text/text_entity.h"
#include "ui/wrap/vertical_layout.h"
#include "window/window_session_controller.h"

#include <QDir>
#include <QFileInfo>
#include <QtGui/QClipboard>
#include <QtGui/QGuiApplication>

namespace Ayu::Plugins::Ui {
namespace {

[[nodiscard]] ::Ui::PluginMetadata MetadataFromInstance(
		const PluginInstance &plugin) {
	// A plugin whose manifest has no id still has a folder, and that folder
	// name is what the settings file, the log file and the CLI all key off.
	// Falling back to it here is what keeps the settings and log buttons
	// reachable for third-party plugins: with an empty id the info box hides
	// both, and the plugin looks like it simply has no settings.
	const auto id = plugin.manifest.id.isEmpty()
		? QFileInfo(plugin.dirPath).fileName()
		: plugin.manifest.id;
	return {
		.id = id,
		.name = plugin.manifest.name.isEmpty() ? id : plugin.manifest.name,
		.description = plugin.manifest.description,
		.author = plugin.manifest.author,
		.version = plugin.manifest.version,
		.icon = plugin.manifest.icon,
		.minVersion = plugin.manifest.minAppVersion,
		.requirements = plugin.manifest.requirements,
	};
}

[[nodiscard]] QString PluginFailureLine(const QString &traceback) {
	if (traceback.isEmpty()) {
		return {};
	}
	// The exception line is the first one naming a type and a message; the
	// frames below it say where, which belongs in the log rather than in a
	// label that also holds the author and the description.
	auto reason = QString();
	for (const auto &line : traceback.split(u'\n')) {
		const auto trimmed = line.trimmed();
		if (trimmed.isEmpty()) {
			continue;
		}
		if (trimmed.contains(u"Error"_q)
			|| trimmed.contains(u"Exception"_q)) {
			reason = trimmed;
			break;
		}
	}
	if (reason.isEmpty()) {
		reason = traceback.split(u'\n').value(0).trimmed();
	}
	if (reason.isEmpty()) {
		return {};
	}

	// Name the fix where it is knowable. "DLL load failed while importing
	// _ssl" says nothing about which file is absent, and that is the single
	// most common reason a plugin will not load.
	if (reason.contains(u"importing _ssl"_q)) {
		return u"missing libssl-3.dll or libcrypto-3.dll next to AyuGram.exe - redeploy"_q;
	}
	if (reason.contains(u"needs permission 'net.http'"_q)) {
		return u"the manifest does not request net.http"_q;
	}
	if (reason.contains(u"needs permission 'storage.write'"_q)) {
		return u"the manifest does not request storage.write"_q;
	}
	if (reason.contains(u"No module named 'java'"_q)
		|| reason.contains(u"android-only"_q)) {
		return u"needs Android (Java) and cannot run on Desktop"_q;
	}
	if (reason.contains(u"No module named 'ayu_api'"_q)) {
		return u"cannot see the app's ayu_api bridge"_q;
	}

	constexpr auto kMax = 160;
	if (reason.size() > kMax) {
		reason = reason.left(kMax - 1) + u"…"_q;
	}
	return reason;
}
[[nodiscard]] QString ActivityLine(const QString &id) {
	// One line saying whether the plugin did anything, shown next to its
	// name. Empty means it worked and there is nothing worth saying.
	const auto calls = Bridge::PluginHookCalls(id);
	const auto api = Bridge::PluginApiCalls(id);
	if (calls || !api.isEmpty()) {
		return {};
	}
	const auto subscriptions = Bridge::PluginHookSubscriptions(id);
	if (!subscriptions) {
		return u"НЕ РАБОТАЕТ: ничего не подписал и ничего не вызвал"_q;
	}
	return u"НЕ РАБОТАЕТ: подписан на "_q
		+ QString::number(subscriptions)
		+ u" событий, но ни одного не пришло"_q;
}

[[nodiscard]] QString PluginSubtitle(const PluginInstance &plugin) {
	auto parts = QStringList();
	if (!plugin.manifest.author.isEmpty()) {
		parts.push_back(plugin.manifest.author);
	}
	if (!plugin.manifest.description.isEmpty()) {
		auto description = plugin.manifest.description;
		description.replace('\n', ' ');
		constexpr auto kMax = 120;
		if (description.size() > kMax) {
			description = description.left(kMax - 1) + "...";
		}
		parts.push_back(description);
	}
	// What the plugin actually did, said right in the list.
	//
	// Before this, a plugin that hooks Android classes Desktop has none of
	// looked exactly like a working one: switch on, "load ok" in the log,
	// and nothing for the whole session. Here it says so, so the user does
	// not keep waiting for an effect that will never come.
	if (plugin.state == PluginState::Loaded && plugin.pythonLoaded) {
		const auto id = plugin.manifest.id;
		if (const auto line = ActivityLine(id); !line.isEmpty()) {
			parts.push_back(line);
		}
	}
	if (plugin.state == PluginState::Error) {
		const auto failure = PluginFailureLine(plugin.lastError);
		if (!failure.isEmpty()) {
			parts.push_back(failure);
		}
	}
	return parts.join("  ·  ");
}

void FillPluginsList(
		not_null<::Ui::VerticalLayout*> list,
		not_null<Window::SessionController*> controller) {
	while (list->count()) {
		delete list->widgetAt(0);
	}

	const auto plugins = Manager::instance().list();
	if (plugins.empty()) {
		::Ui::AddSkip(list);
		::Ui::AddDividerText(list, tr::ayu_PluginsEmpty());
		list->resizeToWidth(list->width());
		return;
	}

	::Ui::AddSkip(list);
	auto first = true;
	for (const auto &plugin : plugins) {
		if (!first) {
			::Ui::AddSkip(list);
		}
		first = false;

		const auto id = plugin.manifest.id;
		const auto dirPath = plugin.dirPath;
		const auto metadata = MetadataFromInstance(plugin);
		const auto enabled = Manager::instance().isEnabled(id);
		const auto subtitle = PluginSubtitle(plugin);

		const auto button = Settings::AddButtonWithLabel(
			list,
			rpl::single(plugin.manifest.name),
			rpl::single(plugin.manifest.version),
			st::settingsButtonNoIcon);
		const auto [toggle, checkView] = Settings::AddSeparatedToggle(
			button,
			st::settingsButtonNoIcon,
			enabled);

		button->setClickedCallback([=] {
			::Ui::ShowPluginInfoBox(controller, dirPath, metadata);
		});

		toggle->clicks(
		) | rpl::on_next([=] {
			const auto enable = !checkView->checked();
			if (enable) {
				auto error = QString();
				if (!Manager::instance().enable(id, &error)) {
					controller->showToast(
						error.isEmpty()
							? tr::ayu_PluginsEnableFailed(tr::now)
							: error);
					checkView->setChecked(false, anim::type::normal);
					return;
				}
			} else {
				Manager::instance().disable(id);
			}
			checkView->setChecked(enable, anim::type::normal);
		}, toggle->lifetime());

		if (!subtitle.isEmpty()) {
			::Ui::AddDividerText(list, rpl::single(subtitle));
		}

		// Color-coded status: ON (green), off (gray), ERR (red).
		const auto statusText = plugin.state == PluginState::Loaded
			? QStringLiteral("ON")
			: plugin.state == PluginState::Error
			? QStringLiteral("ERR")
			: QStringLiteral("off");
		::Ui::AddDividerText(list, rpl::single(statusText));

		// A settings entry right here in the list, not only inside the info
		// box. It used to live one level down behind a click on the plugin, so
		// "the plugin has settings" was indistinguishable from "there are no
		// settings" unless you happened to open the box.
		if (Manager::instance().editablePluginSettingsRows(id) > 0) {
			const auto settingsButton = Settings::AddButtonWithLabel(
				list,
				tr::ayu_PluginsSettings(),
				rpl::single(plugin.manifest.name),
				st::settingsButton);
			settingsButton->setClickedCallback([=] {
				Ayu::Plugins::SettingsBox::ShowPluginSettingsBox(
					controller,
					id);
			});
		}
	}
	list->resizeToWidth(list->width());
}

void ShowPluginFormatBox(not_null<Window::SessionController*> controller) {
	controller->show(Box([=](not_null<::Ui::GenericBox*> box) {
		box->setTitle(tr::ayu_PluginsFormatTitle());
		box->addRow(
			object_ptr<::Ui::FlatLabel>(
				box,
				tr::ayu_PluginsFormatHelp(tr::rich),
				st::boxLabel),
			st::boxRowPadding);
		const auto json = ExampleManifestJson();
		const auto code = box->addRow(
			object_ptr<::Ui::FlatLabel>(
				box,
				rpl::single(json),
				st::boxLabel),
			st::boxRowPadding);
		code->setSelectable(true);
		box->addButton(tr::ayu_PluginsCopyJson(), [=] {
			QGuiApplication::clipboard()->setText(json);
			controller->showToast(tr::ayu_PluginsJsonCopied(tr::now));
		});
		box->addButton(tr::lng_close(), [=] {
			box->closeBox();
		});
	}));
}

void ShowPluginsHealthBox(not_null<Window::SessionController*> controller) {
	controller->show(Box([=](not_null<::Ui::GenericBox*> box) {
		const auto report = Manager::instance().health();
		box->setTitle(tr::ayu_PluginsHealthTitle());

		box->addRow(
			object_ptr<::Ui::FlatLabel>(
				box,
				tr::ayu_PluginsHealthSummary(),
				st::boxLabel),
			st::boxRowPadding);

		for (const auto &entry : report) {
			const auto verdict = entry.ok()
				? tr::ayu_PluginsHealthOk(tr::marked)
				: entry.state == PluginState::Error
				? tr::ayu_PluginsHealthFailed(tr::marked)
				: tr::ayu_PluginsHealthProblem(tr::marked);
			box->addRow(
				object_ptr<::Ui::FlatLabel>(
					box,
					entry.name,
					st::boxLabel),
				st::boxRowPadding);
			box->addRow(
				object_ptr<::Ui::FlatLabel>(
					box,
					rpl::duplicate(verdict),
					st::boxLabel),
				st::boxRowPadding);
			for (const auto &problem : entry.problems) {
				box->addRow(
					object_ptr<::Ui::FlatLabel>(
						box,
						rpl::single(u"- "_q + problem),
						st::boxLabel),
					st::boxRowPadding);
			}
			for (const auto &note : entry.notes) {
				box->addRow(
					object_ptr<::Ui::FlatLabel>(
						box,
						rpl::single(u"- "_q + note),
						st::boxLabel),
					st::boxRowPadding);
			}
		}
		if (report.empty()) {
			box->addRow(
				object_ptr<::Ui::FlatLabel>(
					box,
					tr::ayu_PluginsHealthEmpty(),
					st::boxLabel),
				st::boxRowPadding);
		}
		box->addButton(tr::lng_close(), [=] {
			box->closeBox();
		});
	}));
}

void InstallSelectedPath(
		not_null<Window::SessionController*> controller,
		const QString &path,
		const std::shared_ptr<Fn<void()>> &rebuild) {
	auto error = QString();
	if (!Manager::instance().installPath(path, &error)) {
		controller->showToast(
			error.isEmpty()
				? tr::ayu_PluginsInstallFailed(tr::now)
				: error);
		return;
	}
	controller->showToast(tr::ayu_PluginsInstalled(tr::now));
	if (*rebuild) {
		(*rebuild)();
	}
	// Плагин установлен и включён, но сразу сказано, что он не сможет.
	// Молчание здесь стоило пользователю недель поиска причины.
	const auto incompatible = Manager::instance().lastIncompatibility();
	if (!incompatible.isEmpty()) {
		controller->show(Box([=](not_null<::Ui::GenericBox*> warning) {
			warning->setTitle(tr::ayu_PluginsInstallTitle(tr::now));
			auto label = object_ptr<::Ui::FlatLabel>(
				warning,
				incompatible,
				st::pluginVersionAuthorLabel);
			label->setBreakEverywhere(true);
			warning->addRow<::Ui::FlatLabel>(std::move(label));
			warning->addButton(tr::lng_close(), [=] {
				warning->closeBox();
			});
		}));
	}
}

} // namespace

void FillPluginsManageContent(
		not_null<::Ui::VerticalLayout*> container,
		not_null<Window::SessionController*> controller,
		const std::shared_ptr<Fn<void()>> &rebuild) {
	while (container->count()) {
		delete container->widgetAt(0);
	}

	::Ui::AddSkip(container);
	::Ui::AddDividerText(container, tr::ayu_PluginsDescription());
	::Ui::AddSkip(container);

	const auto addAction = [&](
			rpl::producer<QString> title,
			const style::icon *icon,
			Fn<void()> callback) {
		const auto button = Settings::AddButtonWithIcon(
			container,
			std::move(title),
			st::settingsButton,
			{ icon });
		button->setClickedCallback(std::move(callback));
	};

	addAction(tr::ayu_PluginsInstall(), &st::menuIconAdd, [=] {
		const auto filter = u"AyuGram Plugins (*.plugin *.ayupack *.zip *.py *.pyw *.ayu *.tgx);;"_q
			+ FileDialog::AllFilesFilter();
		FileDialog::GetOpenPath(
			Core::App().getFileDialogParent(),
			tr::ayu_PluginsInstallTitle(tr::now),
			filter,
			crl::guard(controller, [=](FileDialog::OpenResult &&result) {
				if (result.paths.isEmpty()) {
					return;
				}
				InstallSelectedPath(
					controller,
					result.paths.front(),
					rebuild);
			}));
	});
	addAction(tr::ayu_PluginsInstallFolder(), &st::menuIconAddToFolder, [=] {
		FileDialog::GetFolder(
			Core::App().getFileDialogParent(),
			tr::ayu_PluginsInstallFolderTitle(tr::now),
			QString(),
			crl::guard(controller, [=](QString &&path) {
				if (path.isEmpty()) {
					return;
				}
				InstallSelectedPath(controller, path, rebuild);
			}));
	});
	addAction(tr::ayu_PluginsFormat(), &st::menuIconCopy, [=] {
		ShowPluginFormatBox(controller);
	});
	addAction(tr::ayu_PluginsOpenFolder(), &st::menuIconShowInFolder, [=] {
		const auto root = Manager::instance().pluginsRoot();
		QDir().mkpath(root);
		File::ShowInFolder(root);
	});
	addAction(tr::ayu_PluginsReload(), &st::menuIconRestore, [=] {
		Manager::instance().scan();
		if (*rebuild) {
			(*rebuild)();
		}
		controller->showToast(tr::ayu_PluginsReloaded(tr::now));
	});

	const auto list = container->add(
		object_ptr<::Ui::VerticalLayout>(container));
	FillPluginsList(list, controller);
	container->resizeToWidth(container->width());
}

void SetupPluginsSection(Settings::Builder::SectionBuilder &builder) {
	using namespace Settings::Builder;

	const auto controller = builder.controller();
	if (!controller) {
		return;
	}

	// С авторством: список плагинов - самое заметное место, где видно,
	// чья это сборка.
	const auto rebuild = std::make_shared<Fn<void()>>();

	builder.addButton({
		.id = u"ayu/plugins/manage"_q,
		.title = tr::ayu_PluginsManage(),
		.icon = { &st::menuIconManage },
		.onClick = [=] {
			ShowPluginsManageBox(controller);
		},
	});
	builder.addButton({
		.id = u"ayu/plugins/health"_q,
		.title = tr::ayu_PluginsHealth(),
		.icon = { &st::menuIconInfo },
		.onClick = [=] {
			ShowPluginsHealthBox(controller);
		},
	});
	builder.addButton({
		.id = u"ayu/plugins/scan"_q,
		.title = tr::ayu_PluginsScan(),
		.icon = { &st::menuIconInfo },
		.onClick = [=] {
			// Команда уходит в Избранное, а не рисуется отдельным
			// окном: так кнопка и /scan дают один и тот же ответ, и
			// его видно целиком, а не в узком окошке.
			if (Bridge::SendCommandToSelf(u"/scan"_q)) {
				controller->showToast(
					tr::ayu_PluginsScanSent(tr::now));
			} else {
				controller->showToast(
					tr::ayu_PluginsScanNoSelf(tr::now));
			}
		},
	});
	builder.addButton({
		.id = u"ayu/plugins/install"_q,
		.title = tr::ayu_PluginsInstall(),
		.icon = { &st::menuIconAdd },
		.onClick = [=] {
			const auto filter = u"AyuGram Plugins (*.plugin *.ayupack *.zip *.py *.pyw *.ayu *.tgx);;"_q
				+ FileDialog::AllFilesFilter();
			FileDialog::GetOpenPath(
				Core::App().getFileDialogParent(),
				tr::ayu_PluginsInstallTitle(tr::now),
				filter,
				crl::guard(controller, [=](FileDialog::OpenResult &&result) {
					if (result.paths.isEmpty()) {
						return;
					}
					InstallSelectedPath(
						controller,
						result.paths.front(),
						rebuild);
				}));
		},
	});
	builder.addButton({
		.id = u"ayu/plugins/install-folder"_q,
		.title = tr::ayu_PluginsInstallFolder(),
		.icon = { &st::menuIconAddToFolder },
		.onClick = [=] {
			FileDialog::GetFolder(
				Core::App().getFileDialogParent(),
				tr::ayu_PluginsInstallFolderTitle(tr::now),
				QString(),
				crl::guard(controller, [=](QString &&path) {
					if (path.isEmpty()) {
						return;
					}
					InstallSelectedPath(controller, path, rebuild);
				}));
		},
	});
	builder.addButton({
		.id = u"ayu/plugins/format"_q,
		.title = tr::ayu_PluginsFormat(),
		.icon = { &st::menuIconCopy },
		.onClick = [=] {
			ShowPluginFormatBox(controller);
		},
	});
	builder.addButton({
		.id = u"ayu/plugins/folder"_q,
		.title = tr::ayu_PluginsOpenFolder(),
		.icon = { &st::menuIconShowInFolder },
		.onClick = [=] {
			const auto root = Manager::instance().pluginsRoot();
			QDir().mkpath(root);
			File::ShowInFolder(root);
		},
	});
	builder.addButton({
		.id = u"ayu/plugins/reload"_q,
		.title = tr::ayu_PluginsReload(),
		.icon = { &st::menuIconRestore },
		.onClick = [=] {
			Manager::instance().scan();
			if (*rebuild) {
				(*rebuild)();
			}
			controller->showToast(tr::ayu_PluginsReloaded(tr::now));
		},
	});

	builder.add([=](const BuildContext &ctx) {
		v::match(ctx, [&](const WidgetContext &wctx) {
			const auto list = wctx.container->add(
				object_ptr<::Ui::VerticalLayout>(wctx.container));
			*rebuild = [=] {
				FillPluginsList(list, controller);
			};
			(*rebuild)();
		}, [&](const SearchContext &sctx) {
			for (const auto &plugin : Manager::instance().list()) {
				sctx.entries->push_back({
					.id = u"ayu/plugin/"_q + plugin.manifest.id,
					.title = plugin.manifest.name,
					.section = sctx.sectionId,
				});
			}
		});
	});

	builder.addSkip();
	builder.addDividerText(tr::ayu_PluginsDescription());
}

void ShowPluginsManageBox(
		not_null<Window::SessionController*> controller) {
	controller->show(Box([=](not_null<::Ui::GenericBox*> box) {
		box->setTitle(tr::ayu_PluginsHeader());
		box->setWidth(st::boxWideWidth);
		box->verticalLayout()->resizeToWidth(box->width());

		const auto content = box->verticalLayout();
		const auto rebuild = std::make_shared<Fn<void()>>();
		*rebuild = [=] {
			FillPluginsManageContent(content, controller, rebuild);
		};
		(*rebuild)();

		box->addButton(tr::lng_close(), [=] {
			box->closeBox();
		});
	}));
}

} // namespace Ayu::Plugins::Ui
