// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/plugins/ui/plugins_section.h"

#include "ayu/plugins/plugin_manifest.h"
#include "ayu/plugins/plugins_manager.h"
#include "ayu/ui/boxes/plugin_info_box.h"
#include "core/application.h"
#include "core/file_utilities.h"
#include "lang/lang_keys.h"
#include "lang_auto.h"
#include "settings/settings_builder.h"
#include "settings/settings_common.h"
#include "styles/style_layers.h"
#include "styles/style_menu_icons.h"
#include "styles/style_settings.h"
#include "ui/layers/generic_box.h"
#include "ui/vertical_list.h"
#include "ui/widgets/buttons.h"
#include "ui/widgets/checkbox.h"
#include "ui/widgets/labels.h"
#include "ui/wrap/vertical_layout.h"
#include "window/window_session_controller.h"

#include <QDir>
#include <QtGui/QClipboard>
#include <QtGui/QGuiApplication>

namespace Ayu::Plugins::Ui {
namespace {

[[nodiscard]] ::Ui::PluginMetadata MetadataFromInstance(
		const PluginInstance &plugin) {
	return {
		.id = plugin.manifest.id,
		.name = plugin.manifest.name,
		.description = plugin.manifest.description,
		.author = plugin.manifest.author,
		.version = plugin.manifest.version,
		.icon = plugin.manifest.icon,
		.minVersion = plugin.manifest.minAppVersion,
		.requirements = plugin.manifest.requirements,
	};
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
			description = description.left(kMax - 1) + u"…"_q;
		}
		parts.push_back(description);
	}
	if (plugin.state == PluginState::Error && !plugin.lastError.isEmpty()) {
		parts.push_back(plugin.lastError);
	}
	return parts.join(u" — "_q);
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
