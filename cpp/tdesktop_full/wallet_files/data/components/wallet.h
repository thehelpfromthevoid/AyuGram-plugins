/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#pragma once

#include "base/basic_types.h"

#include <optional>
#include <QString>

namespace Main {
class Session;
} // namespace Main

// UserData is a global-scope class in this tree (see data/data_user.h), so the
// forward declaration must be global as well: a Data::UserData declaration
// would shadow the real type inside namespace Data for every translation unit
// that includes this header before data/data_user.h.
class UserData;

namespace Data {

enum class WalletTransactionPeerType {
	User,
	Address,
	Onramp,
	Channel,
	TelegramGift,
	OnchainGift,
	Unsupported,
};

struct WalletTransactionPeer {
	WalletTransactionPeerType type = WalletTransactionPeerType::Unsupported;
	PeerId peer;
	QString address;
	QString domain;
	QString providerName;
	bool ownProfile = false;

	friend inline bool operator==(
		const WalletTransactionPeer &,
		const WalletTransactionPeer &) = default;
};

struct WalletTransaction {
	QString id;
	QString msgHash;
	CreditsAmount amount = CreditsAmount(0, CreditsType::Ton);
	CreditsAmount fee = CreditsAmount(0, CreditsType::Ton);
	TimeId date = 0;
	WalletTransactionPeer peer;
	QString comment;
	QString txHash;
	bool incoming = false;
	bool gasless = false;
	bool failed = false;
	bool keyChange = false;
	bool commentEncrypted = false;
	bool suspectedSpam = false;

	// wallet.nftItem of the transaction, when the transfer moved a
	// non-fungible gift. Empty for ordinary transfers.
	struct Nft {
		QString collectionAddress;
		QString address;
		QString ownerAddress;
		QString index;
		QString name;
		QString description;

		friend inline bool operator==(const Nft &, const Nft &)
			= default;
	};
	std::optional<Nft> nft;

	friend inline bool operator==(
		const WalletTransaction &,
		const WalletTransaction &) = default;
};

struct WalletNft {
	QString collectionAddress;
	QString address;
	QString ownerAddress;
	QString index;
	QString name;
	QString description;

	struct Attribute {
		QString traitType;
		QString value;

		friend inline bool operator==(
			const Attribute &,
			const Attribute &) = default;
	};
	std::vector<Attribute> attributes;

	friend inline bool operator==(const WalletNft &, const WalletNft &)
		= default;
};


struct WalletGaslessInfo {
	bool available = false;
	int left = 0;
	TimeId resetAt = 0;
	CreditsAmount minAmount = CreditsAmount(0, CreditsType::Ton);
	QString relayerAddress;

	friend inline bool operator==(
		const WalletGaslessInfo &,
		const WalletGaslessInfo &) = default;
};

struct WalletTonConnectManifest {
	QString url;
	QString name;

	friend inline bool operator==(
		const WalletTonConnectManifest &,
		const WalletTonConnectManifest &) = default;
};

struct WalletTonConnectSession {
	uint64 id = 0;
	QString dappClientId;
	QString clientId;
	QByteArray nonce;
	bool pending = false;
	bool closing = false;
	bool closed = false;
	bool hasManifest = false;
	WalletTonConnectManifest manifest;
	int manifestError = 0;
	TimeId date = 0;

	friend inline bool operator==(
		const WalletTonConnectSession &,
		const WalletTonConnectSession &) = default;
};

enum class WalletTransactionsState {
	Loading,
	Ready,
	Failed,
};

struct WalletState {
	bool exists = false;
	bool creating = false;
	QString address;
	QByteArray publicKey;
	bool backupEnabled = false;
	bool canExportPhrase = false;
	bool canEnableBackup = false;

	friend inline bool operator==(const WalletState &, const WalletState &)
		= default;
};

struct WalletTransferRequest {
	// The Telegram account the money goes to. wallet.sendTransfer takes an
	// InputUser, so a bare TON address cannot be submitted: the wallet backend
	// resolves the recipient's address itself.
	not_null<UserData*> recipient;
	uint64 amountNano = 0;
	QString comment;
};

struct WalletTransferResult {
	bool ok = false;
	QString error;
};

// The TON (Gram) wallet of the current account: the state that
// wallet.getState and updateWalletState report, the transactions that came
// with updateSentWalletTransaction, the gasless transfer info and the
// TonConnect sessions.
//
// The address, the key flags, the transactions, the gasless info and the
// TonConnect sessions live here. The TON balance is owned by Data::Credits
// (Credits::tonBalance) and is only forwarded there from the wallet state.
class Wallet final {
public:
	explicit Wallet(not_null<Main::Session*> session);
	~Wallet();

	void load(bool force = false);
	[[nodiscard]] bool loaded() const;
	[[nodiscard]] rpl::producer<bool> loadedValue() const;

	[[nodiscard]] const WalletState &state() const;
	[[nodiscard]] rpl::producer<WalletState> stateValue() const;

	[[nodiscard]] const std::vector<WalletTransaction> &transactions() const;
	[[nodiscard]] rpl::producer<std::vector<WalletTransaction>> transactionsValue() const;

	void loadTransactions(bool force = false);
	void loadMoreTransactions();
	void retryTransactions();
	[[nodiscard]] bool hasMoreTransactions() const;
	[[nodiscard]] rpl::producer<bool> hasMoreTransactionsValue() const;
	[[nodiscard]] WalletTransactionsState transactionsState() const;
	[[nodiscard]] rpl::producer<WalletTransactionsState> transactionsStateValue() const;

	[[nodiscard]] const WalletGaslessInfo &gasless() const;
	[[nodiscard]] rpl::producer<WalletGaslessInfo> gaslessValue() const;

	[[nodiscard]] const std::vector<WalletTonConnectSession> &tonConnectSessions() const;
	[[nodiscard]] rpl::producer<std::vector<WalletTonConnectSession>> tonConnectSessionsValue() const;
	void disconnectTonConnectSession(uint64 id);

	[[nodiscard]] const std::vector<WalletNft> &nfts() const;
	[[nodiscard]] rpl::producer<std::vector<WalletNft>> nftsValue() const;
	void loadNfts(bool force = false);
	void loadMoreNfts();
	[[nodiscard]] bool hasMoreNfts() const;
	[[nodiscard]] rpl::producer<bool> hasMoreNftsValue() const;

	void apply(const MTPDupdateWalletState &data);
	void apply(const MTPDupdateSentWalletTransaction &data);
	void apply(const MTPDupdateWalletGaslessInfo &data);
	void apply(const MTPDupdateWalletTonConnectSession &data);
	void apply(const MTPDupdateWalletTonConnectPendingDisconnect &data);

	// Signs and submits a TON transfer with the wallet secret phrase stored
	// on this device. The secret phrase lives locally, encrypted with the
	// account local key; nothing about it leaves the process.
	void sendTransfer(WalletTransferRequest request, Fn<void(WalletTransferResult)> done);

	// The 12- or 24-word secret phrase kept on this device, empty when it was
	// never imported or was forgotten. Only the wallet settings UI reads it.
	[[nodiscard]] QString secretPhrase() const;
	void rememberSecretPhrase(const QString &phrase);
	void forgetWallet();

	[[nodiscard]] bool backupEnabled() const;
	void setBackupEnabled(bool enabled, Fn<void(bool, QString)> done);

private:
	void requestTransactions(const QString &offset);
	void requestNfts(const QString &offset);
	void applyState(const MTPWalletState &state);
	void applyTransaction(WalletTransaction transaction);
	void applyTonConnectSession(const MTPTonConnectSession &session);

	const not_null<Main::Session*> _session;

	WalletState _state;
	rpl::variable<WalletState> _stateValue;

	std::vector<WalletTransaction> _transactions;
	rpl::variable<std::vector<WalletTransaction>> _transactionsValue;

	WalletGaslessInfo _gasless;
	rpl::variable<WalletGaslessInfo> _gaslessValue;

	std::vector<WalletTonConnectSession> _tonConnectSessions;
	rpl::variable<std::vector<WalletTonConnectSession>> _tonConnectSessionsValue;

	rpl::variable<bool> _loaded = false;
	mtpRequestId _requestId = 0;
	crl::time _lastLoaded = 0;

	QString _transactionsOffset;
	mtpRequestId _transactionsRequestId = 0;
	bool _transactionsLoaded = false;
	bool _transactionsLoadMore = false;
	WalletTransactionsState _transactionsState = WalletTransactionsState::Loading;
	rpl::variable<WalletTransactionsState> _transactionsStateValue
		= WalletTransactionsState::Loading;
	rpl::variable<bool> _hasMoreTransactions = false;

	std::vector<WalletNft> _nfts;
	rpl::variable<std::vector<WalletNft>> _nftsValue;
	mtpRequestId _nftsRequestId = 0;
	QString _nftsOffset;
	bool _nftsLoaded = false;
	rpl::variable<bool> _hasMoreNfts = false;

	// A transfer in flight: the recipient and payload are kept from the seqno
	// lookup until wallet.sendTransfer answers, so two taps cannot race.
	mtpRequestId _seqnoRequestId = 0;
	mtpRequestId _transferRequestId = 0;
	bool _sending = false;
	Fn<void(WalletTransferResult)> _sendingDone;

	// The secret phrase never touches the disk: it lives here for as long as
	// the process runs and is dropped with it, so a new session asks for it
	// again. Empty means "not entered on this device".
	QString _phrase;

};

} // namespace Data
