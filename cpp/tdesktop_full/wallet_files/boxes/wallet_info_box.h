// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "ui/layers/box_content.h"

class WalletInfoBox final : public Ui::BoxContent {
public:
	WalletInfoBox(QWidget*);

protected:
	void prepare() override;

};

namespace Ui {

[[nodiscard]] object_ptr<WalletInfoBox> MakeWalletInfoBox();
void ShowWalletInfoBox();

} // namespace Ui
