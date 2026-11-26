// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "base/object_ptr.h"

namespace Main {
class Session;
} // namespace Main

namespace Ui {

class GenericBox;

// The wallet's "Keys & Backup" settings: reveals the secret phrase stored on
// this device, enables or disables the distributed encrypted key backup and
// deletes the wallet together with its keys. Only Data::Wallet is read and
// written here; the enable/disable request itself is made by
// Wallet::setBackupEnabled.
[[nodiscard]] object_ptr<Ui::GenericBox> MakeWalletSettingsBox(
	not_null<Main::Session*> session);

void ShowWalletSettingsBox(not_null<Main::Session*> session);

} // namespace Ui
