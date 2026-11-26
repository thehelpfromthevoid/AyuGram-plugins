// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "boxes/wallet_send_box.h"

#include "ayu/ayu_wallet.h"
#include "core/credits_amount.h"
#include "data/components/credits.h"
#include "data/components/wallet.h"
#include "data/data_thread.h"
#include "data/data_user.h"
#include "lang/lang_keys.h"
#include "main/main_session.h"
#include "ui/boxes/emoji_stake_box.h"
#include "ui/controls/ton_common.h"
#include "ui/layers/generic_box.h"
#include "ui/vertical_list.h"
#include "ui/widgets/buttons.h"
#include "ui/widgets/fields/input_field.h"
#include "ui/widgets/labels.h"
#include "ui/wrap/slide_wrap.h"
#include "ui/wrap/vertical_layout.h"
#include "window/window_peer_menu.h"
#include "window/window_session_controller.h"
#include "styles/style_layers.h"
#include "styles/style_widgets.h"

namespace Ui {
namespace {

void RebuildLayout(not_null<VerticalLayout*> container) {
	container->resizeToWidth(container->width());
}

} // namespace

void WalletSendBox(
		not_null<Ui::GenericBox*> box,
		not_null<Window::SessionController*> controller) {
	box->setTitle(tr::lng_wallet_send_controller_title());
	box->setWidth(st::boxWideWidth);

	const auto session = &controller->session();
	const auto container = box->verticalLayout();
	const auto credits = &session->credits();
	const auto wallet = &session->wallet();

	// The picked account. Null until the recipient box reports a choice.
	const auto recipient = std::make_shared<Data::Thread*>(nullptr);

	const auto choose = container->add(
		object_ptr<Ui::RoundButton>(
			container,
			tr::lng_wallet_recipient_controller_title(),
			st::defaultLightButton),
		st::boxRowPadding,
		style::al_top);

	const auto amount = Ui::AddTonInputField(container, {});
	amount->setMaxLength(32);

	const auto comment = container->add(
		object_ptr<Ui::InputField>(
			container,
			st::defaultInputField,
			tr::lng_wallet_send_controller_comment_placeholder()),
		st::boxRowPadding);
	comment->setMaxLength(128);

	// The key is never written to disk, so a freshly started session asks for
	// the phrase right before the first transfer.
	const auto phrase = wallet->secretPhrase().isEmpty()
		? container->add(
			object_ptr<Ui::InputField>(
				container,
				st::defaultInputField,
				tr::lng_wallet_settings_controller_enter_secret_phrase()),
			st::boxRowPadding)
		: nullptr;
	if (phrase) {
		phrase->setMaxLength(256);
	}

	Ui::AddSkip(container);

	const auto error = container->add(
		object_ptr<Ui::SlideWrap<Ui::FlatLabel>>(
			container,
			object_ptr<Ui::FlatLabel>(container, st::defaultFlatLabel)),
		st::boxRowPadding,
		style::al_top);
	error->toggle(false, anim::type::instant);
	error->entity()->setTextColorOverride(st::boxTextFgError->c);

	const auto hideError = [=] {
		error->toggle(false, anim::type::instant);
		RebuildLayout(container);
	};

	const auto showError = [=](const QString &text) {
		error->entity()->setText(text);
		error->toggle(true, anim::type::instant);
		RebuildLayout(container);
	};

	choose->setClickedCallback([=] {
		Window::ShowChooseRecipientBox(
			controller,
			[=](not_null<Data::Thread*> thread) {
				*recipient = thread.get();
				choose->setText(rpl::single(thread->peer()->name()));
				hideError();
				return true;
			});
	});

	const auto button = box->addButton(tr::lng_wallet_controller_send());
	button->setClickedCallback([=] {
		const auto thread = *recipient;
		if (!thread) {
			showError(tr::lng_wallet_recipient_controller_title(tr::now));
			return;
		}
		const auto user = thread->peer()->asUser();
		if (!user || user->isSelf()) {
			// wallet.sendTransfer takes an InputUser, so channels, chats and
			// the own account cannot receive through this path.
			showError(tr::lng_wallet_recipient_controller_invalid_address(
				tr::now));
			return;
		}

		if (phrase) {
			const auto entered = phrase->getLastText().trimmed();
			if (entered.isEmpty()) {
				phrase->showError();
				showError(tr::lng_wallet_controller_secret_phrase_required(
					tr::now));
				return;
			}
			wallet->rememberSecretPhrase(entered);
		}
		if (wallet->secretPhrase().isEmpty()) {
			showError(tr::lng_wallet_controller_secret_phrase_required_to_send(
				tr::now));
			return;
		}

		const auto balance = credits->tonBalance();
		const auto balanceNano = balance.whole() * kOneStarInNano
			+ balance.nano();
		const auto parsed = Ui::ParseTonAmountString(amount->getLastText());
		const auto amountNano = uint64(parsed.value_or(0));
		if (amountNano <= 0 || int64(amountNano) > balanceNano) {
			amount->showError();
			showError(tr::lng_wallet_send_controller_insufficient_funds(
				tr::now));
			return;
		}

		hideError();

		button->setEnabled(false);
		wallet->sendTransfer(
			Data::WalletTransferRequest{
				.recipient = user,
				.amountNano = amountNano,
				.comment = comment->getLastText(),
			},
			crl::guard(box, [=](Data::WalletTransferResult result) {
				if (result.ok) {
					box->closeBox();
					return;
				}
				button->setEnabled(true);
				showError(result.error.isEmpty()
					? tr::lng_wallet_recipient_controller_unknown_error(
						tr::now)
					: result.error);
			}));
	});

	box->setFocusCallback([=] {
		if (phrase) {
			phrase->setFocusFast();
		} else {
			amount->setFocusFast();
		}
	});
}

object_ptr<Ui::GenericBox> MakeWalletSendBox(
		not_null<Window::SessionController*> controller) {
	return Box(WalletSendBox, controller);
}

void ShowWalletSendBox(not_null<Window::SessionController*> controller) {
	controller->uiShow()->show(MakeWalletSendBox(controller));
}

} // namespace Ui
