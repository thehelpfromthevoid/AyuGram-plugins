// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "base/object_ptr.h"

namespace Window {
class SessionController;
} // namespace Window

namespace Ui {

class GenericBox;

// Collects a recipient, an amount in grams and an optional comment (plus the
// secret phrase, when this device does not hold one yet), then hands the
// transfer to Data::Wallet::sendTransfer. The recipient is picked through the
// regular "choose recipient" box, because wallet.sendTransfer takes an account
// and resolves its address server-side.
void WalletSendBox(
	not_null<Ui::GenericBox*> box,
	not_null<Window::SessionController*> controller);

[[nodiscard]] object_ptr<Ui::GenericBox> MakeWalletSendBox(
	not_null<Window::SessionController*> controller);

void ShowWalletSendBox(not_null<Window::SessionController*> controller);

} // namespace Ui
