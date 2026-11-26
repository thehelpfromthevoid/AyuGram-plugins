// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include <QByteArray>
#include <QString>

#include <optional>

namespace Ayu::Wallet {

// Whether the Rust wallet layer is linked into this build. Every other
// function in this namespace returns an empty/negative result when it is not,
// so a caller never has to guard on the compile definition itself.
[[nodiscard]] bool Available();

// Parses a TON address in either the raw `workchain:hash` or the
// user-friendly base64url form and returns the raw form. Empty on a malformed
// address.
[[nodiscard]] QString NormalizeAddress(const QString &text);

// The user-friendly base64url form of an address. Empty on a malformed
// address.
[[nodiscard]] QString FriendlyAddress(
	const QString &text,
	bool bounceable,
	bool testnet);

// Expands a 12- or 24-word secret phrase into the 64-byte BIP39 seed the
// signer consumes. Empty when the phrase does not parse.
[[nodiscard]] QByteArray SeedFromMnemonic(const QString &phrase);

struct Transfer {
	QByteArray boc;
	QString bodyHash;
};

// Builds and signs the external message for a wallet v4r2 transfer. The
// returned BOC is what `wallet.sendTransfer` expects in `data_normal`.
// `std::nullopt` means the transfer could not be prepared; the reason is
// logged.
[[nodiscard]] std::optional<Transfer> BuildTransfer(
	const QByteArray &seed,
	quint32 subwalletId,
	quint32 seqno,
	quint32 validUntil,
	const QString &destAddress,
	quint64 amountNano,
	bool bounceable,
	const QString &comment);

} // namespace Ayu::Wallet
