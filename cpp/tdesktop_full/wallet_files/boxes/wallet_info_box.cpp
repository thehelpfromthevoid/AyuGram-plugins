// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "boxes/wallet_info_box.h"

#include "boxes/abstract_box.h"
#include "lang/lang_keys.h"
#include "styles/style_layers.h"
#include "styles/style_widgets.h"
#include "ui/vertical_list.h"
#include "ui/widgets/labels.h"
#include "ui/wrap/vertical_layout.h"
#include "ui/wrap/wrap.h"

namespace {

void AddSection(
		not_null<Ui::VerticalLayout*> container,
		rpl::producer<QString> title,
		rpl::producer<QString> about) {
	Ui::AddSubsectionTitle(container, std::move(title));
	container->add(
		object_ptr<Ui::FlatLabel>(
			container,
			std::move(about),
			st::boxDividerLabel),
		st::boxRowPadding);
	Ui::AddSkip(container);
}

void AddNotice(
		not_null<Ui::VerticalLayout*> container,
		rpl::producer<TextWithEntities> text) {
	container->add(
		object_ptr<Ui::FlatLabel>(
			container,
			std::move(text),
			st::boxDividerLabel),
		st::boxRowPadding);
	Ui::AddSkip(container);
}

} // namespace

WalletInfoBox::WalletInfoBox(QWidget*) {
}

void WalletInfoBox::prepare() {
	setTitle(tr::lng_wallet_info_controller_how_it_works());

	auto wrap = object_ptr<Ui::VerticalLayout>(this);
	const auto content = wrap.data();
	setInnerWidget(object_ptr<Ui::OverrideMargins>(this, std::move(wrap)));

	AddSection(
		content,
		tr::lng_wallet_info_controller_gram(),
		tr::lng_wallet_info_controller_gram_info());
	AddSection(
		content,
		tr::lng_wallet_info_controller_instant_transfers(),
		tr::lng_wallet_info_controller_instant_transfers_info());
	AddSection(
		content,
		tr::lng_wallet_info_controller_useful(),
		tr::lng_wallet_info_controller_useful_info());
	AddSection(
		content,
		tr::lng_wallet_info_controller_fast(),
		tr::lng_wallet_info_controller_fast_info());
	AddSection(
		content,
		tr::lng_wallet_info_controller_cheap(),
		tr::lng_wallet_info_controller_cheap_info());
	AddSection(
		content,
		tr::lng_wallet_info_controller_store(),
		tr::lng_wallet_info_controller_store_info());
	AddSection(
		content,
		tr::lng_wallet_info_controller_trade(),
		tr::lng_wallet_info_controller_trade_info());

	// The number of free daily transfers is a runtime value, so the box
	// shows the feature title without its rate description.
	Ui::AddSubsectionTitle(content, tr::lng_wallet_info_controller_zero_fees());
	Ui::AddSkip(content);

	AddSection(
		content,
		tr::lng_wallet_info_controller_secret_phrase(),
		tr::lng_wallet_info_controller_secret_phrase_info());
	AddNotice(content, tr::lng_wallet_info_controller_never_share(tr::rich));
	AddNotice(content, tr::lng_wallet_info_controller_can_steal(tr::rich));
	AddNotice(content, tr::lng_wallet_info_controller_support_never_asks(tr::rich));
	AddSection(
		content,
		tr::lng_wallet_info_controller_blockchain_verified(),
		tr::lng_wallet_info_controller_blockchain_verified_info());

	addButton(
		tr::lng_wallet_info_controller_got_it(),
		[=] { closeBox(); });

	setDimensionsToContent(st::boxWidth, content);
}

namespace Ui {

object_ptr<WalletInfoBox> MakeWalletInfoBox() {
	return Box<WalletInfoBox>();
}

void ShowWalletInfoBox() {
	show(MakeWalletInfoBox());
}

} // namespace Ui
