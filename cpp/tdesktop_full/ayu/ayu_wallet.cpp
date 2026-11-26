// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/ayu_wallet.h"

#include "logs.h"

#include <cstddef>

#if AYU_RUST_ENABLED
// The fast layer's C ABI. Declared here rather than in a shared header because
// this is the only translation unit that consumes it: a Rust-side signature
// change then breaks the one file that has to be updated, instead of every
// includer of a public header.
extern "C" int ayu_ton_seed_from_mnemonic(
	const char *phrase,
	std::size_t phrase_len,
	unsigned char *out_seed,
	std::size_t out_seed_cap);
extern "C" int ayu_ton_parse_address(
	const char *text,
	std::size_t text_len,
	char *out,
	std::size_t out_cap,
	std::size_t *out_len);
extern "C" int ayu_ton_address_friendly(
	const char *text,
	std::size_t text_len,
	int bounceable,
	int testnet,
	char *out,
	std::size_t out_cap,
	std::size_t *out_len);
extern "C" char *ayu_ton_build_transfer(
	const unsigned char *seed,
	std::size_t seed_len,
	unsigned int subwallet_id,
	unsigned int seqno,
	unsigned int valid_until,
	const char *dest,
	std::size_t dest_len,
	unsigned long long amount_nano,
	int bounceable,
	const char *comment,
	std::size_t comment_len,
	std::size_t *out_len,
	char *out_hash,
	std::size_t out_hash_cap,
	std::size_t *out_hash_len);
extern "C" const char *ayu_last_error();
extern "C" void ayu_bytes_free(char *pointer, std::size_t len);
#endif

namespace Ayu::Wallet {
namespace {

#if AYU_RUST_ENABLED
[[nodiscard]] QString LastError() {
	const auto error = ayu_last_error();
	return error ? QString::fromUtf8(error) : QString();
}

// A TON address never exceeds a few dozen characters; the raw form is 66.
// Anything longer is not an address and is rejected before the call.
constexpr auto kMaxAddressLength = 128;
#endif

} // namespace

bool Available() {
#if AYU_RUST_ENABLED
	return true;
#else
	return false;
#endif
}

QString NormalizeAddress(const QString &text) {
	if (text.isEmpty()) {
		return QString();
	}
#if AYU_RUST_ENABLED
	const auto utf8 = text.toUtf8();
	if (utf8.isEmpty() || utf8.size() > kMaxAddressLength) {
		return QString();
	}
	char buffer[kMaxAddressLength];
	auto length = std::size_t(0);
	if (ayu_ton_parse_address(
			utf8.constData(),
			std::size_t(utf8.size()),
			buffer,
			sizeof(buffer),
			&length) != 0) {
		LOG(("Wallet Error: Couldn't parse address, error: %1"
			).arg(LastError()));
		return QString();
	}
	return QString::fromUtf8(buffer, int(length));
#else
	return QString();
#endif
}

QString FriendlyAddress(
		const QString &text,
		bool bounceable,
		bool testnet) {
	if (text.isEmpty()) {
		return QString();
	}
#if AYU_RUST_ENABLED
	const auto utf8 = text.toUtf8();
	if (utf8.isEmpty() || utf8.size() > kMaxAddressLength) {
		return QString();
	}
	char buffer[kMaxAddressLength];
	auto length = std::size_t(0);
	if (ayu_ton_address_friendly(
			utf8.constData(),
			std::size_t(utf8.size()),
			bounceable ? 1 : 0,
			testnet ? 1 : 0,
			buffer,
			sizeof(buffer),
			&length) != 0) {
		LOG(("Wallet Error: Couldn't format address, error: %1"
			).arg(LastError()));
		return QString();
	}
	return QString::fromUtf8(buffer, int(length));
#else
	return QString();
#endif
}

QByteArray SeedFromMnemonic(const QString &phrase) {
	if (phrase.isEmpty()) {
		return QByteArray();
	}
#if AYU_RUST_ENABLED
	const auto utf8 = phrase.toUtf8();
	auto seed = QByteArray(64, Qt::Uninitialized);
	if (ayu_ton_seed_from_mnemonic(
			utf8.constData(),
			std::size_t(utf8.size()),
			reinterpret_cast<unsigned char*>(seed.data()),
			std::size_t(seed.size())) != 0) {
		LOG(("Wallet Error: Couldn't expand secret phrase, error: %1"
			).arg(LastError()));
		return QByteArray();
	}
	return seed;
#else
	return QByteArray();
#endif
}

std::optional<Transfer> BuildTransfer(
		const QByteArray &seed,
		quint32 subwalletId,
		quint32 seqno,
		quint32 validUntil,
		const QString &destAddress,
		quint64 amountNano,
		bool bounceable,
		const QString &comment) {
	if (seed.size() != 64 || destAddress.isEmpty()) {
		return std::nullopt;
	}
#if AYU_RUST_ENABLED
	const auto dest = destAddress.toUtf8();
	const auto commentUtf8 = comment.toUtf8();
	char hashBuffer[65];
	auto length = std::size_t(0);
	auto hashLength = std::size_t(0);
	const auto pointer = ayu_ton_build_transfer(
		reinterpret_cast<const unsigned char*>(seed.constData()),
		std::size_t(seed.size()),
		unsigned int(subwalletId),
		unsigned int(seqno),
		unsigned int(validUntil),
		dest.constData(),
		std::size_t(dest.size()),
		unsigned long long(amountNano),
		bounceable ? 1 : 0,
		comment.isEmpty() ? nullptr : commentUtf8.constData(),
		comment.isEmpty() ? std::size_t(0) : std::size_t(commentUtf8.size()),
		&length,
		hashBuffer,
		sizeof(hashBuffer),
		&hashLength);
	if (!pointer || !length) {
		LOG(("Wallet Error: Couldn't build transfer, error: %1"
			).arg(LastError()));
		return std::nullopt;
	}
	auto result = Transfer{
		.boc = QByteArray(pointer, int(length)),
		.bodyHash = QString::fromUtf8(hashBuffer, int(hashLength)),
	};
	ayu_bytes_free(pointer, length);
	return result;
#else
	return std::nullopt;
#endif
}

} // namespace Ayu::Wallet
