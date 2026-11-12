// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/ui/settings/settings_plugins.h"

#include "ayu/plugins/ui/plugins_section.h"
#include "ayu/ui/settings/settings_main.h"
#include "lang_auto.h"
#include "settings/settings_builder.h"
#include "styles/style_menu_icons.h"
#include "ui/wrap/vertical_layout.h"

namespace Settings {

using namespace Builder;

namespace {

const auto kMeta = BuildHelper({
	.id = AyuPlugins::Id(),
	.parentId = AyuMain::Id(),
	.title = &tr::ayu_PluginsHeader,
	.icon = &st::menuIconManage,
}, [](SectionBuilder &builder) {
	builder.addSkip();
	Ayu::Plugins::Ui::SetupPluginsSection(builder);
	builder.addSkip();
});

} // namespace

rpl::producer<QString> AyuPlugins::title() {
	return tr::ayu_PluginsHeader();
}

AyuPlugins::AyuPlugins(
	QWidget *parent,
	not_null<Window::SessionController*> controller)
: Section(parent, controller)
, _controller(controller) {
	setupContent();
}

void AyuPlugins::setupContent() {
	const auto content = Ui::CreateChild<Ui::VerticalLayout>(this);
	build(content, kMeta.build);
	Ui::ResizeFitChild(this, content);
}

Type AyuPluginsId() {
	return AyuPlugins::Id();
}

} // namespace Settings
