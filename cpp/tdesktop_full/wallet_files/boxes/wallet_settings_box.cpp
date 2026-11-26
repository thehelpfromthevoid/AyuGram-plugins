// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "boxes/wallet_settings_box.h"

#include "boxes/abstract_box.h"
#include "data/components/wallet.h"
#include "lang/lang_keys.h"
#include "main/main_session.h"
#include "ui/boxes/confirm_box.h"
#include "ui/layers/generic_box.h"
#include "ui/toast/toast.h"
#include "ui/vertical_list.h"
#include "ui/widgets/buttons.h"
#include "ui/widgets/labels.h"
#include "ui/wrap/slide_wrap.h"
#include "ui/wrap/vertical_layout.h"
#include "styles/style_layers.h"
#include "styles/style_widgets.h"

namespace Ui {
namespace {

void RebuildLayout(not_null<VerticalLayout*> container) {
	container->resizeToWidth(container->width());
}

void AddSecretPhraseSection(
		not_null<VerticalLayout*> container,
		not_null<Main::Session*> session) {
	Ui::AddSubsectionTitle(
		container,
		tr::lng_wallet_settings_controller_secret_phrase_header());

	const auto info = container->add(
		object_ptr<Ui::FlatLabel>(
			container,
			tr::lng_wallet_settings_controller_show_secret_phrase_info(),
			st::boxDividerLabel),
		st::boxRowPadding);
	info->setSelectable(true);

	const auto phrase = container->add(
		object_ptr<Ui::SlideWrap<Ui::FlatLabel>>(
			container,
			object_ptr<Ui::FlatLabel>(container, QString(), st::boxLabel)),
		st::boxRowPadding,
		style::al_top);
	phrase->toggle(false, anim::type::instant);
	phrase->entity()->setSelectable(true);
	phrase->entity()->setBreakEverywhere(true);

	const auto missing = container->add(
		object_ptr<Ui::SlideWrap<Ui::FlatLabel>>(
			container,
			object_ptr<Ui::FlatLabel>(
				container,
				tr::lng_wallet_settings_controller_secret_phrase_not_stored(),
				st::boxLabel)),
		st::boxRowPadding,
		style::al_top);
	missing->toggle(false, anim::type::instant);
	missing->entity()->setSelectable(true);

	const auto showPhrase = [=] {
		const auto text = session->wallet().secretPhrase();
		const auto stored = !text.isEmpty();
		if (stored) {
			phrase->entity()->setText(text);
		}
		phrase->toggle(stored, anim::type::instant);
		missing->toggle(!stored, anim::type::instant);
		RebuildLayout(container);
	};

	container->add(
		object_ptr<Ui::SettingsButton>(
			container,
			tr::lng_wallet_settings_controller_show_secret_phrase(),
			st::defaultSettingsButton)
	)->setClickedCallback(showPhrase);

	Ui::AddSkip(container);
}

void AddBackupSection(
		not_null<Ui::GenericBox*> box,
		not_null<VerticalLayout*> container,
		not_null<Main::Session*> session) {
	Ui::AddSubsectionTitle(
		container,
		tr::lng_wallet_settings_controller_encrypted_backup_header());

	const auto wallet = &session->wallet();

	const auto info = container->add(
		object_ptr<Ui::FlatLabel>(
			container,
			wallet->stateValue() | rpl::map([](const Data::WalletState &state) {
				return state.backupEnabled
					? tr::lng_wallet_settings_controller_backup_enabled_info(
						tr::now)
					: tr::lng_wallet_settings_controller_backup_disabled_info(
						tr::now);
			}),
			st::boxDividerLabel),
		st::boxRowPadding);
	info->setSelectable(true);

	Ui::AddSkip(container);

	const auto submit = [=](bool enabled) {
		wallet->setBackupEnabled(
			enabled,
			crl::guard(box, [=](bool ok, QString error) {
				if (ok) {
					return;
				}
				Ui::Toast::Show(error.isEmpty()
					? tr::lng_wallet_settings_controller_check_network(tr::now)
					: error);
			}));
	};

	const auto button = container->add(
		object_ptr<Ui::SettingsButton>(
			container,
			wallet->stateValue() | rpl::map([](const Data::WalletState &state) {
				return state.backupEnabled
					? tr::lng_wallet_settings_controller_disable_backup(
						tr::now)
					: tr::lng_wallet_settings_controller_enable_backup(
						tr::now);
			}),
			st::defaultSettingsButton));
	button->setClickedCallback([=] {
		if (!wallet->backupEnabled()) {
			submit(true);
			return;
		}
		box->uiShow()->showBox(Ui::MakeConfirmBox({
			.text = tr::lng_wallet_settings_controller_disable_backup_confirm(),
			.confirmed = [=](Fn<void()> &&close) {
				submit(false);
				close();
			},
			.confirmText = tr::lng_wallet_settings_controller_disable(),
			.cancelText = tr::lng_wallet_settings_controller_not_now(),
			.confirmStyle = &st::attentionBoxButton,
			.title = tr::lng_wallet_settings_controller_disable_backup_confirm_title(),
		}));
	});

	Ui::AddSkip(container);
}

void AddDeleteSection(
		not_null<Ui::GenericBox*> box,
		not_null<VerticalLayout*> container,
		not_null<Main::Session*> session) {
	Ui::AddSkip(container);
	Ui::AddDivider(container);

	const auto button = container->add(
		object_ptr<Ui::SettingsButton>(
			container,
			tr::lng_wallet_settings_controller_delete_wallet(),
			st::defaultSettingsButton));
	button->setColorOverride(st::boxTextFgError->c);
	button->setClickedCallback([=] {
		box->uiShow()->showBox(Ui::MakeConfirmBox({
			.text = tr::lng_wallet_settings_controller_delete_wallet_confirm(),
			.confirmed = [=](Fn<void()> &&close) {
				session->wallet().forgetWallet();
				close();
				box->closeBox();
			},
			.confirmText = tr::lng_wallet_settings_controller_delete_anyway(),
			.cancelText = tr::lng_wallet_settings_controller_cancel(),
			.confirmStyle = &st::attentionBoxButton,
			.title = tr::lng_wallet_settings_controller_delete_wallet_confirm_title(),
		}));
	});
}

} // namespace

void WalletSettingsBox(
		not_null<Ui::GenericBox*> box,
		not_null<Main::Session*> session) {
	box->setTitle(tr::lng_wallet_settings_controller_keys_and_backup());
	box->setWidth(st::boxWidth);

	const auto container = box->verticalLayout();

	AddSecretPhraseSection(container, session);
	AddBackupSection(box, container, session);
	AddDeleteSection(box, container, session);

	box->addButton(tr::lng_wallet_settings_controller_proceed(), [=] {
		box->closeBox();
	});
}

object_ptr<Ui::GenericBox> MakeWalletSettingsBox(
		not_null<Main::Session*> session) {
	return Box(WalletSettingsBox, session);
}

void ShowWalletSettingsBox(not_null<Main::Session*> session) {
	show(MakeWalletSettingsBox(session));
}

} // namespace Ui
