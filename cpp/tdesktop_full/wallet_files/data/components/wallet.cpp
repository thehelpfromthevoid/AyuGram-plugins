/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#include "data/components/wallet.h"

#include "apiwrap.h"
#include "ayu/ayu_wallet.h"
#include "base/random.h"
#include "base/unixtime.h"
#include "data/components/credits.h"
#include "data/data_peer_id.h"
#include "data/data_user.h"
#include "logs.h"
#include "main/main_session.h"
#include "mtproto/core_types.h"
#include "mtproto/mtproto_config.h"
#include "storage/storage_account.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>

namespace Data {
namespace {

constexpr auto kReloadThreshold = 60 * crl::time(1000);
constexpr auto kOneTonInNano = int64(1'000'000'000);

[[nodiscard]] CreditsAmount TonAmountFromTL(uint64 value) {
	const auto isNegative = (static_cast<int64>(value) < 0);
	const auto absValue = isNegative ? (~value + 1) : value;
	const auto result = CreditsAmount(
		int64(absValue / kOneTonInNano),
		int64(absValue % kOneTonInNano),
		CreditsType::Ton);
	return isNegative
		? CreditsAmount(0, CreditsType::Ton) - result
		: result;
}

[[nodiscard]] WalletTransactionPeer ParseTransactionPeer(
		const MTPWalletTransactionPeer &peer) {
	auto result = WalletTransactionPeer();
	peer.match([&](const MTPDwalletTransactionPeerUser &data) {
		result.type = WalletTransactionPeerType::User;
		result.peer = peerFromUser(UserId(data.vuser_id().v));
		result.address = qs(data.vaddress());
		result.domain = qs(data.vdomain().value_or_empty());
	}, [&](const MTPDwalletTransactionPeerAddress &data) {
		result.type = WalletTransactionPeerType::Address;
		result.address = qs(data.vaddress());
		result.domain = qs(data.vdomain().value_or_empty());
	}, [&](const MTPDwalletTransactionPeerOnramp &data) {
		result.type = WalletTransactionPeerType::Onramp;
		result.address = qs(data.vaddress());
		result.domain = qs(data.vdomain().value_or_empty());
		result.providerName = qs(data.vprovider_name());
	}, [&](const MTPDwalletTransactionPeerChannel &data) {
		result.type = WalletTransactionPeerType::Channel;
		result.peer = peerFromChannel(ChannelId(data.vchannel_id().v));
		result.address = qs(data.vaddress());
		result.domain = qs(data.vdomain().value_or_empty());
	}, [&](const MTPDwalletTransactionPeerTelegramGift &data) {
		result.type = WalletTransactionPeerType::TelegramGift;
		result.ownProfile = data.is_own_profile();
	}, [&](const MTPDwalletTransactionPeerOnchainGift &) {
		result.type = WalletTransactionPeerType::OnchainGift;
	}, [&](const MTPDwalletTransactionPeerUnsupported &) {
		result.type = WalletTransactionPeerType::Unsupported;
	});
	return result;
}

[[nodiscard]] WalletTransaction ParseTransaction(
		const MTPDwalletTransaction &data,
		const QString &msgHash) {
	auto result = WalletTransaction();
	result.id = qs(data.vid());
	result.msgHash = msgHash;
	result.amount = TonAmountFromTL(data.vamount().v);
	result.fee = TonAmountFromTL(data.vfee().v);
	result.date = data.vdate().v;
	result.peer = ParseTransactionPeer(data.vpeer());
	result.comment = qs(data.vcomment().value_or_empty());
	result.txHash = qs(data.vtx_hash().value_or_empty());
	result.incoming = data.is_incoming();
	result.gasless = data.is_gasless();
	result.failed = data.is_failed();
	result.keyChange = data.is_key_change();
	result.commentEncrypted = data.is_comment_encrypted();
	result.suspectedSpam = data.is_suspected_spam();
	if (const auto nft = data.vnft()) {
		const auto &fields = nft->data();
		result.nft = WalletTransaction::Nft{
			.collectionAddress = qs(
				fields.vcollection_address().value_or_empty()),
			.address = qs(fields.vaddress()),
			.ownerAddress = qs(fields.vowner_address()),
			.index = qs(fields.vindex()),
			.name = qs(fields.vname().value_or_empty()),
			.description = qs(fields.vdescription().value_or_empty()),
		};
	}
	return result;
}

[[nodiscard]] WalletTonConnectSession ParseTonConnectSession(
		const MTPDtonConnectSession &data) {
	auto result = WalletTonConnectSession();
	result.id = data.vid().v;
	result.dappClientId = qs(data.vdapp_client_id());
	result.clientId = qs(data.vclient_id().value_or_empty());
	result.nonce = data.vnonce().v;
	if (const auto manifest = data.vmanifest()) {
		result.hasManifest = true;
		result.manifest.url = qs(manifest->data().vurl());
		result.manifest.name = qs(manifest->data().vname());
	}
	result.manifestError = data.vmanifest_error().value_or_empty();
	result.date = data.vdate().v;
	result.pending = data.is_pending();
	result.closing = data.is_closing();
	result.closed = data.is_closed();
	return result;
}

[[nodiscard]] WalletNft ParseNft(const MTPDwallet_nftItem &data) {
	auto result = WalletNft();
	result.collectionAddress = qs(
		data.vcollection_address().value_or_empty());
	result.address = qs(data.vaddress());
	result.ownerAddress = qs(data.vowner_address());
	result.index = qs(data.vindex());
	result.name = qs(data.vname().value_or_empty());
	result.description = qs(data.vdescription().value_or_empty());
	if (const auto attributes = data.vattributes()) {
		for (const auto &attribute : attributes->v) {
			const auto &fields = attribute.data();
			result.attributes.push_back(WalletNft::Attribute{
				.traitType = qs(fields.vtrait_type()),
				.value = qs(fields.vvalue()),
			});
		}
	}
	return result;
}

// The wallet key never touches the disk: the phrase lives in the Wallet for as
// long as the process runs and is dropped with it, so a new session always asks
// for it again (the l10n says the same).

// Reads a number out of the toncenter JSON payload. toncenter answers either
// with a bare value or with `{"result": ...}`, so both shapes are accepted.
[[nodiscard]] std::optional<uint64> JsonUint64(const QJsonValue &value) {
	if (value.isDouble()) {
		return uint64(value.toDouble());
	} else if (value.isString()) {
		const auto text = value.toString();
		auto ok = false;
		if (text.startsWith(u"0x"_q, Qt::CaseInsensitive)) {
			const auto parsed = text.mid(2).toULongLong(&ok, 16);
			return ok ? std::make_optional(uint64(parsed)) : std::nullopt;
		}
		const auto parsed = text.toULongLong(&ok);
		return ok ? std::make_optional(uint64(parsed)) : std::nullopt;
	}
	return std::nullopt;
}

// The `seqno` get-method answers with `{"result":{"stack":[["num","0x157"]]}}`;
// a bare `{"result":{"seqno":N}}` is accepted too, so the caller does not
// depend on which endpoint answered.
[[nodiscard]] std::optional<uint64> SeqnoFromJson(const QByteArray &json) {
	auto error = QJsonParseError();
	const auto document = QJsonDocument::fromJson(json, &error);
	if (error.error != QJsonParseError::NoError || !document.isObject()) {
		return std::nullopt;
	}
	const auto root = document.object();
	const auto result = root.value(u"result"_q);
	const auto source = result.isObject() ? result.toObject() : root;
	if (const auto seqno = JsonUint64(source.value(u"seqno"_q))) {
		return seqno;
	}
	if (const auto stack = source.value(u"stack"_q); stack.isArray()) {
		for (const auto &entry : stack.toArray()) {
			// A toncenter stack entry is the pair ["num","0x157"]; the older
			// {"type":"num","value":"0x157"} object is accepted as well.
			const auto value = entry.isArray()
				? entry.toArray().at(1)
				: entry.toObject().value(u"value"_q);
			if (const auto parsed = JsonUint64(value)) {
				return parsed;
			}
		}
	}
	return std::nullopt;
}

[[nodiscard]] QByteArray DataJsonString(const MTPDataJSON &data) {
	return data.data().vdata().v;
}

} // namespace

Wallet::Wallet(not_null<Main::Session*> session)
: _session(session) {
}

Wallet::~Wallet() = default;

void Wallet::load(bool force) {
	if (_requestId
		|| (!force
			&& _lastLoaded
			&& _lastLoaded + kReloadThreshold > crl::now())) {
		return;
	}
	_requestId = _session->api().request(
		MTPwallet_GetState()
	).done([=](const MTPWalletState &result) {
		_requestId = 0;
		_lastLoaded = crl::now();
		applyState(result);
		_loaded = true;
	}).fail([=](const MTP::Error &error) {
		_requestId = 0;
		_lastLoaded = crl::now();
		LOG(("API Error: Couldn't get wallet state, error: %1"
			).arg(error.type()));
		_loaded = true;
	}).send();
}

bool Wallet::loaded() const {
	return _loaded.current();
}

rpl::producer<bool> Wallet::loadedValue() const {
	return _loaded.value();
}

const WalletState &Wallet::state() const {
	return _state;
}

rpl::producer<WalletState> Wallet::stateValue() const {
	return _stateValue.value();
}

const std::vector<WalletTransaction> &Wallet::transactions() const {
	return _transactions;
}

rpl::producer<std::vector<WalletTransaction>> Wallet::transactionsValue() const {
	return _transactionsValue.value();
}

void Wallet::loadTransactions(bool force) {
	if (_transactionsRequestId
		|| (!force && _transactionsLoaded)) {
		return;
	}
	_transactionsLoadMore = false;
	_transactionsStateValue = _transactionsState
		= WalletTransactionsState::Loading;
	requestTransactions(QString());
}

void Wallet::loadMoreTransactions() {
	if (_transactionsRequestId
		|| !_transactionsLoaded
		|| !_hasMoreTransactions.current()) {
		return;
	}
	_transactionsLoadMore = true;
	requestTransactions(_transactionsOffset);
}

void Wallet::retryTransactions() {
	if (_transactionsRequestId) {
		return;
	}
	_transactionsLoaded = false;
	loadTransactions(true);
}

bool Wallet::hasMoreTransactions() const {
	return _hasMoreTransactions.current();
}

rpl::producer<bool> Wallet::hasMoreTransactionsValue() const {
	return _hasMoreTransactions.value();
}

WalletTransactionsState Wallet::transactionsState() const {
	return _transactionsState;
}

rpl::producer<WalletTransactionsState> Wallet::transactionsStateValue() const {
	return _transactionsStateValue.value();
}

void Wallet::requestTransactions(const QString &offset) {
	constexpr auto kTransactionsLimit = 20;
	_transactionsRequestId = _session->api().request(
		MTPwallet_GetTransactions(
			MTP_flags(
				MTPwallet_GetTransactions::Flag::f_inbound
				| MTPwallet_GetTransactions::Flag::f_outbound),
			MTP_string(offset),
			MTP_int(kTransactionsLimit))
	).done([=](const MTPwallet_Transactions &result) {
		_transactionsRequestId = 0;
		_transactionsLoaded = true;
		_transactionsLoadMore = false;
		const auto &data = result.data();
		const auto &list = data.vtransactions().v;
		if (offset.isEmpty()) {
			_transactions.clear();
		}
		for (const auto &transaction : list) {
			applyTransaction(ParseTransaction(
				transaction.data(),
				QString()));
		}
		const auto next = data.vnext_offset();
		_transactionsOffset = qs(next.value_or_empty());
		_hasMoreTransactions = !_transactionsOffset.isEmpty();
		_transactionsStateValue = _transactionsState
			= WalletTransactionsState::Ready;
	}).fail([=](const MTP::Error &error) {
		_transactionsRequestId = 0;
		if (!_transactionsLoadMore) {
			_transactionsLoaded = false;
			_transactionsStateValue = _transactionsState
				= WalletTransactionsState::Failed;
		}
		LOG(("API Error: Couldn't get wallet transactions, error: %1"
			).arg(error.type()));
	}).send();
}

void Wallet::disconnectTonConnectSession(uint64 id) {
	_session->api().request(MTPwallet_TonConnectCloseSession(
		MTP_flags(MTPwallet_TonConnectCloseSession::Flag(0)),
		MTP_long(id),
		MTP_bytes()
	)).done([=](const MTPBool &) {
	}).fail([=](const MTP::Error &error) {
		LOG(("API Error: Couldn't close wallet TonConnect session, error: %1"
			).arg(error.type()));
	}).send();
}

void Wallet::loadNfts(bool force) {
	if (_nftsRequestId || (!force && _nftsLoaded)) {
		return;
	}
	requestNfts(QString());
}

void Wallet::loadMoreNfts() {
	if (_nftsRequestId || !_nftsLoaded || !_hasMoreNfts.current()) {
		return;
	}
	requestNfts(_nftsOffset);
}

bool Wallet::hasMoreNfts() const {
	return _hasMoreNfts.current();
}

rpl::producer<bool> Wallet::hasMoreNftsValue() const {
	return _hasMoreNfts.value();
}

const std::vector<WalletNft> &Wallet::nfts() const {
	return _nfts;
}

rpl::producer<std::vector<WalletNft>> Wallet::nftsValue() const {
	return _nftsValue.value();
}

void Wallet::requestNfts(const QString &offset) {
	constexpr auto kNftsLimit = 20;
	_nftsRequestId = _session->api().request(
		MTPwallet_GetNfts(
			MTP_string(offset),
			MTP_int(kNftsLimit))
	).done([=](const MTPwallet_NftItems &result) {
		_nftsRequestId = 0;
		_nftsLoaded = true;
		const auto &data = result.data();
		if (offset.isEmpty()) {
			_nfts.clear();
		}
		for (const auto &item : data.vitems().v) {
			_nfts.push_back(ParseNft(item.data()));
		}
		const auto next = data.vnext_offset();
		_nftsOffset = qs(next.value_or_empty());
		_hasMoreNfts = !_nftsOffset.isEmpty();
		_nftsValue = _nfts;
	}).fail([=](const MTP::Error &error) {
		_nftsRequestId = 0;
		_nftsLoaded = true;
		LOG(("API Error: Couldn't get wallet NFTs, error: %1"
			).arg(error.type()));
	}).send();
}

const WalletGaslessInfo &Wallet::gasless() const {
	return _gasless;
}

rpl::producer<WalletGaslessInfo> Wallet::gaslessValue() const {
	return _gaslessValue.value();
}

const std::vector<WalletTonConnectSession> &Wallet::tonConnectSessions() const {
	return _tonConnectSessions;
}

rpl::producer<std::vector<WalletTonConnectSession>>
Wallet::tonConnectSessionsValue() const {
	return _tonConnectSessionsValue.value();
}

void Wallet::apply(const MTPDupdateWalletState &data) {
	applyState(data.vstate());
}

void Wallet::apply(const MTPDupdateSentWalletTransaction &data) {
	if (const auto transaction = data.vtransaction()) {
		applyTransaction(ParseTransaction(
			transaction->data(),
			qs(data.vmsg_hash())));
	}
}

void Wallet::apply(const MTPDupdateWalletGaslessInfo &data) {
	_gasless.available = data.is_available();
	_gasless.left = data.vleft().v;
	_gasless.resetAt = data.vreset_at().v;
	_gasless.minAmount = TonAmountFromTL(data.vmin_amount().v);
	_gasless.relayerAddress = qs(data.vrelayer_address());
	_gaslessValue = _gasless;
}

void Wallet::apply(const MTPDupdateWalletTonConnectSession &data) {
	applyTonConnectSession(data.vsession());
}

void Wallet::apply(const MTPDupdateWalletTonConnectPendingDisconnect &data) {
	for (const auto &sessionId : data.vsession_ids().v) {
		const auto i = ranges::find(
			_tonConnectSessions,
			sessionId.v,
			&WalletTonConnectSession::id);
		if (i != end(_tonConnectSessions)) {
			_tonConnectSessions.erase(i);
		}
	}
	_tonConnectSessionsValue = _tonConnectSessions;
}

void Wallet::applyState(const MTPWalletState &state) {
	state.match([&](const MTPDwalletState &data) {
		_state.exists = true;
		_state.creating = false;
		_state.address = qs(data.vaddress());
		_state.publicKey = data.vpublic_key().v;
		_session->credits().apply(TonAmountFromTL(data.vbalance().v));
		_state.backupEnabled = data.is_backup_enabled();
		_state.canExportPhrase = data.is_can_export_phrase();
		_state.canEnableBackup = data.is_can_enable_backup();
	}, [&](const MTPDwalletStateEmpty &data) {
		_state = WalletState();
		_state.creating = data.is_creating();
		_session->credits().apply(CreditsAmount(0, CreditsType::Ton));
	});
	_stateValue = _state;
}

void Wallet::applyTransaction(WalletTransaction transaction) {
	const auto i = ranges::find(
		_transactions,
		transaction.id,
		&WalletTransaction::id);
	if (i != end(_transactions)) {
		*i = std::move(transaction);
	} else {
		_transactions.push_back(std::move(transaction));
	}
	_transactionsValue = _transactions;
}

void Wallet::applyTonConnectSession(const MTPTonConnectSession &session) {
	auto parsed = ParseTonConnectSession(session.data());
	const auto i = ranges::find(
		_tonConnectSessions,
		parsed.id,
		&WalletTonConnectSession::id);
	if (i != end(_tonConnectSessions)) {
		*i = std::move(parsed);
	} else {
		_tonConnectSessions.push_back(std::move(parsed));
	}
	_tonConnectSessionsValue = _tonConnectSessions;
}

QString Wallet::secretPhrase() const {
	return _phrase;
}

void Wallet::rememberSecretPhrase(const QString &phrase) {
	_phrase = phrase.trimmed();
}

void Wallet::forgetWallet() {
	// The phrase is only ever kept in memory, so forgetting the wallet is
	// dropping the key material and re-reading the on-chain state.
	_phrase.clear();
	load(true);
}

bool Wallet::backupEnabled() const {
	return _state.backupEnabled;
}

void Wallet::setBackupEnabled(bool enabled, Fn<void(bool, QString)> done) {
	if (enabled) {
		_session->api().request(MTPwallet_EnableBackup(
			MTP_flags(MTPwallet_EnableBackup::Flag(0)),
			MTP_vector<MTPbytes>(0),
			MTP_bytes(),
			MTPWalletOwnershipProof()
		)).done([=](const MTPWalletState &result) {
			applyState(result);
			done(true, QString());
		}).fail([=](const MTP::Error &error) {
			LOG(("API Error: Couldn't enable the wallet backup, error: %1"
				).arg(error.type()));
			done(false, error.type());
		}).send();
		return;
	}
	_session->api().request(MTPwallet_DisableBackup(
		MTP_flags(MTPwallet_DisableBackup::Flag(0)),
		MTPInputCheckPasswordSRP(),
		MTP_bytes(),
		MTPWalletOwnershipProof()
	)).done([=](const MTPWalletState &result) {
		applyState(result);
		done(true, QString());
	}).fail([=](const MTP::Error &error) {
		LOG(("API Error: Couldn't disable the wallet backup, error: %1"
			).arg(error.type()));
		done(false, error.type());
	}).send();
}

void Wallet::sendTransfer(
		WalletTransferRequest request,
		Fn<void(WalletTransferResult)> done) {
	if (_sending) {
		done(WalletTransferResult{ .ok = false });
		return;
	}
	const auto phrase = _phrase.trimmed();
	if (phrase.isEmpty()) {
		LOG(("Wallet Error: Send without a secret phrase on this device."));
		done(WalletTransferResult{ .ok = false });
		return;
	}
	_sending = true;
	_sendingDone = std::move(done);

	const auto recipient = request.recipient;
	const auto amountNano = request.amountNano;
	const auto comment = request.comment;

	const auto finish = [=](bool ok, const QString &error) {
		_sending = false;
		_seqnoRequestId = 0;
		_transferRequestId = 0;
		const auto callback = std::move(_sendingDone);
		_sendingDone = nullptr;
		if (callback) {
			callback(WalletTransferResult{ .ok = ok, .error = error });
		}
	};

	const auto submit = [=](const QString &address, uint64 seqno) {
		const auto seed = Ayu::Wallet::SeedFromMnemonic(phrase);
		if (seed.isEmpty()) {
			LOG(("Wallet Error: Couldn't expand the secret phrase."));
			finish(false, QString());
			return;
		}
		// The default v4r2 subwallet of workchain 0.
		constexpr auto kSubwalletId = 698983191u;
		constexpr auto kValidFor = 60;
		const auto validUntil = uint32(base::unixtime::now()) + kValidFor;
		const auto transfer = Ayu::Wallet::BuildTransfer(
			seed,
			kSubwalletId,
			uint32(seqno),
			validUntil,
			address,
			amountNano,
			true,
			comment);
		if (!transfer) {
			LOG(("Wallet Error: Couldn't sign the transfer."));
			finish(false, QString());
			return;
		}
		_transferRequestId = _session->api().request(
			MTPwallet_SendTransfer(
				MTP_flags(MTPwallet_SendTransfer::Flag(0)),
				MTP_bytes(transfer->boc),
				MTP_bytes(),
				recipient->inputUser(),
				MTP_long(base::RandomValue<uint64>()))
		).done([=](const MTPUpdates &result) {
			_session->api().applyUpdates(result);
			finish(true, QString());
		}).fail([=](const MTP::Error &error) {
			LOG(("API Error: Couldn't send the wallet transfer, error: %1"
				).arg(error.type()));
			finish(false, error.type());
		}).send();
	};

	const auto requestSeqno = [=](const QString &address) {
		// Wallet v4r2 signs the seqno of its own state, so it has to come from
		// the chain. Telegram proxies the toncenter call, and answers with the
		// raw toncenter JSON. The seqno is the top of the `seqno` get-method
		// stack, the same call the engine makes.
		const auto payload = QJsonDocument(QJsonObject{
			{ u"jsonrpc"_q, u"2.0"_q },
			{ u"id"_q, 1 },
			{ u"method"_q, u"runGetMethod"_q },
			{ u"params"_q, QJsonObject{
				{ u"address"_q, address },
				{ u"method"_q, u"seqno"_q },
				{ u"stack"_q, QJsonArray() },
			} },
		}).toJson(QJsonDocument::Compact);
		using Flag = MTPtoncenter_performApiRequest::Flag;
		const auto dcId = _session->serverConfig().webFileDcId;
		_seqnoRequestId = _session->api().request(
			MTPtoncenter_PerformApiRequest(
				MTP_flags(Flag::f_post | Flag::f_payload),
				MTP_string(u"/api/v2/jsonRPC"_q),
				MTP_string(),
				MTP_string(QString::fromUtf8(payload)))
		).toDC(
			(dcId > 0 && dcId < MTP::kDcShift)
				? MTP::ShiftDcId(dcId, MTP::kToncenterDcShift)
				: 0
		).done([=](const MTPtoncenter_ApiResponse &result) {
			const auto json = result.match(
				[](const MTPDtoncenter_apiResponse &data) {
					return DataJsonString(data.vresponse());
				});
			LOG(("Wallet: toncenter answered: %1"
				).arg(QString::fromUtf8(json)));
			const auto seqno = SeqnoFromJson(json);
			if (!seqno) {
				LOG(("Wallet Error: No seqno in the toncenter answer."));
				finish(false, QString());
				return;
			}
			submit(address, *seqno);
		}).fail([=](const MTP::Error &error) {
			LOG(("API Error: Couldn't get the wallet seqno, error: %1"
				).arg(error.type()));
			finish(false, error.type());
		}).send();
	};

	using Flag = MTPwallet_GetUserAddresses::Flag;
	_session->api().request(MTPwallet_GetUserAddresses(
		MTP_flags(Flag::f_force),
		MTP_vector<MTPInputUser>(1, recipient->inputUser()),
		MTP_vector<MTPstring>(0)
	)).done([=](const MTPwallet_UserAddresses &result) {
		// The recipient address is what the transfer has to point at; the
		// server resolves it from the account the money is sent to.
		auto address = QString();
		for (const auto &row : result.data().vaddresses().v) {
			const auto parsed = qs(row.data().vaddress());
			if (!parsed.isEmpty()) {
				address = parsed;
				break;
			}
		}
		if (address.isEmpty()) {
			LOG(("Wallet Error: No address for the transfer recipient."));
			finish(false, QString());
			return;
		}
		requestSeqno(address);
	}).fail([=](const MTP::Error &error) {
		LOG(("API Error: Couldn't get the recipient address, error: %1"
			).arg(error.type()));
		finish(false, error.type());
	}).send();
}

} // namespace Data
