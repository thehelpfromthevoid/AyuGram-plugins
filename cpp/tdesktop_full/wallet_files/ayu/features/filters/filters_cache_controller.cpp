// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/features/filters/filters_cache_controller.h"

#include "ayu/data/ayu_database.h"
#include "ayu/features/filters/filters_controller.h"
#include "data/data_groups.h"
#include "data/data_peer.h"
#include "data/data_session.h"
#include "history/history.h"
#include "history/history_item.h"
#include "rpl/event_stream.h"

#include <cstddef>
#include <deque>
#include <memory>
#include <mutex>
#include <unordered_set>
#include <utility>

namespace FiltersCacheController {

std::mutex rebuildMutex;
std::mutex cacheMutex;
std::mutex filteredMessagesMutex;

rpl::event_stream<> filtersUpdateStream;

void fireUpdate() {
	filtersUpdateStream.fire({});
}

rpl::producer<> updates() {
	return filtersUpdateStream.events();
}

std::shared_ptr<const Cache> cache;

std::unordered_map<long long, std::unordered_map<int64, bool>> filteredMessages;

// Upper bound on the total number of cache entries. An entry costs around
// fifty bytes together with its hash table bucket and the deque slot below,
// so the bound keeps the cache near ten megabytes instead of growing with
// every message that was ever scrolled through in every opened history.
// Evicting an entry is safe: a missing entry only makes isFiltered return
// nullopt, and the caller recomputes the value.
constexpr auto kMaxFilteredMessages = std::size_t(200000);

// Number of live entries in filteredMessages, maintained by the helpers
// below so that eviction never has to walk every dialog.
std::size_t filteredMessagesCount = 0;

// Insertion order for evicting the oldest entries. Invalidations leave
// stale keys here rather than searching the middle of the deque, which
// would cost a linear pass on every message update. Eviction walks from the
// front and drops the entries that are still live. A key that was
// forgotten and stored again is appended once more, so the deque can hold
// the same key twice, and such a key is then evicted at its earliest
// occurrence rather than at the later store. The eviction order is
// therefore approximate rather than strict FIFO, which is harmless: evicting
// early only makes the caller recompute a value. What matters is that the
// deque stays bounded, which compaction guarantees by dropping the stale
// entries and collapsing duplicates.
using FilteredKey = std::pair<long long, int64>;

struct FilteredKeyHasher {
	std::size_t operator()(const FilteredKey &key) const {
		auto h = std::hash<long long>{}(key.first);
		h ^= std::hash<int64>{}(key.second)
			+ 0x9e3779b97f4a7c15ull
			+ (h << 6)
			+ (h >> 2);
		return h;
	}
};

std::deque<FilteredKey> filteredMessagesOrder;

std::unordered_set<BareId> dialogsWithHiddenBlockedMessages; // purely for show / hide filtered messages

namespace {

// Compaction runs once the recorded keys exceed the live entries by this
// much, so an average removal pays O(1) deque work for it.
constexpr auto kMinFilteredOrderSlack = std::size_t(4096);
constexpr auto kFilteredOrderSlackRatio = std::size_t(4);

// Rebuilds the insertion order from the entries that are still live,
// dropping stale keys and collapsing duplicates to their earliest
// occurrence. Must be called under filteredMessagesMutex.
void compactFilteredOrder() {
	std::unordered_set<FilteredKey, FilteredKeyHasher> seen;
	std::deque<FilteredKey> compacted;
	for (const auto &key : filteredMessagesOrder) {
		const auto dialogIt = filteredMessages.find(key.first);
		if (dialogIt == filteredMessages.end()
			|| !dialogIt->second.contains(key.second)) {
			continue;
		}
		if (!seen.insert(key).second) {
			continue;
		}
		compacted.push_back(key);
	}
	filteredMessagesOrder = std::move(compacted);
}

// Frees the oldest entries until the cache is back under the bound and
// compacts the order once the stale keys piled up. Must be called under
// filteredMessagesMutex.
void trimFilteredOverflow() {
	while (filteredMessagesCount > kMaxFilteredMessages
		&& !filteredMessagesOrder.empty()) {
		const auto key = filteredMessagesOrder.front();
		filteredMessagesOrder.pop_front();

		const auto dialogIt = filteredMessages.find(key.first);
		if (dialogIt == filteredMessages.end()
			|| !dialogIt->second.erase(key.second)) {
			continue;
		}
		--filteredMessagesCount;
		if (dialogIt->second.empty()) {
			filteredMessages.erase(dialogIt);
		}
	}

	const auto slack = kMinFilteredOrderSlack
		+ filteredMessagesCount / kFilteredOrderSlackRatio;
	if (filteredMessagesOrder.size() > filteredMessagesCount + slack) {
		compactFilteredOrder();
	}
}

// Stores one entry, keeping the counter and the insertion order in sync.
// Must be called under filteredMessagesMutex.
void rememberFiltered(long long dialogId, int64 messageId, bool res) {
	const auto [it, inserted] =
		filteredMessages[dialogId].try_emplace(messageId, res);
	if (!inserted) {
		it->second = res;
		return;
	}

	filteredMessagesOrder.emplace_back(dialogId, messageId);
	++filteredMessagesCount;
}

// Drops one entry. The matching insertion order key is left in the deque as
// stale and skipped by trimFilteredOverflow. Must be called under
// filteredMessagesMutex.
void forgetFiltered(long long dialogId, int64 messageId) {
	const auto dialogIt = filteredMessages.find(dialogId);
	if (dialogIt == filteredMessages.end()
		|| !dialogIt->second.erase(messageId)) {
		return;
	}

	--filteredMessagesCount;
	if (dialogIt->second.empty()) {
		filteredMessages.erase(dialogIt);
	}
}

} // namespace

std::shared_ptr<const Cache> buildCache() {
	const auto filters = AyuDatabase::getAllRegexFilters();
	const auto exclusions = AyuDatabase::getAllFiltersExclusions();

	std::vector<HashablePattern> shared;
	std::unordered_map<ID, std::vector<ReversiblePattern>> byDialogId;

	for (const auto &filter : filters) {
		if (!filter.enabled || filter.text.empty()) {
			continue;
		}

		int flags = UREGEX_MULTILINE;
		if (filter.caseInsensitive) flags |= UREGEX_CASE_INSENSITIVE;

		auto status = U_ZERO_ERROR;
		auto pattern = icu::RegexPattern::compile(icu::UnicodeString::fromUTF8(filter.text), flags, status);

		if (!pattern) {
			continue;
		}

		if (filter.dialogId.has_value()) {
			byDialogId[filter.dialogId.value()].push_back({
				std::shared_ptr<icu::RegexPattern>(pattern),
				filter.reversed,
			});
		} else {
			shared.push_back({
				filter.id,
				{
					std::shared_ptr<icu::RegexPattern>(pattern),
					filter.reversed,
				},
			});
		}
	}

	auto exclByDialogId = buildExclusions(exclusions, shared);
	auto result = std::make_shared<Cache>();
	result->sharedPatterns = std::move(shared);
	result->patternsByDialogId = std::move(byDialogId);
	result->exclusionsByDialogId = std::move(exclByDialogId);

	return result;
}

void rebuildCache() {
	std::lock_guard rebuildLock(rebuildMutex);
	auto next = buildCache();
	{
		std::lock_guard cacheLock(cacheMutex);
		std::lock_guard filteredLock(filteredMessagesMutex);
		cache = std::move(next);
		filteredMessages.clear();
		filteredMessagesOrder.clear();
		filteredMessagesCount = 0;
		dialogsWithHiddenBlockedMessages.clear();
	}
}

std::unordered_map<long long, std::unordered_set<HashablePattern, PatternHasher>> buildExclusions(
	const std::vector<RegexFilterGlobalExclusion> &exclusions,
	const std::vector<HashablePattern> &shared) {
	std::unordered_map<long long, std::unordered_set<HashablePattern, PatternHasher>> exclusionsByDialogId;

	for (const auto &exclusion : exclusions) {
		auto &exclusionSet = exclusionsByDialogId[exclusion.dialogId];

		for (const auto &filter : shared) {
			if (filter.id == exclusion.filterId) {
				exclusionSet.insert(filter);
				break;
			}
		}
	}
	return exclusionsByDialogId;
}

std::shared_ptr<const Cache> snapshot() {
	{
		std::lock_guard lock(cacheMutex);
		if (cache) {
			return cache;
		}
	}

	std::lock_guard rebuildLock(rebuildMutex);
	{
		std::lock_guard lock(cacheMutex);
		if (cache) {
			return cache;
		}
	}

	auto next = buildCache();
	std::lock_guard lock(cacheMutex);
	if (!cache) {
		cache = std::move(next);
	}
	return cache;
}

std::optional<bool> isFiltered(not_null<HistoryItem*> item) {
	std::lock_guard lock(filteredMessagesMutex);
	auto dialogIt = filteredMessages.find(item->history()->peer->id.value);

	if (dialogIt == filteredMessages.end()) {
		return std::nullopt;
	}

	const auto it = dialogIt->second.find(item->id.bare);
	if (it == dialogIt->second.end()) {
		return std::nullopt;
	}

	return it->second;
}

bool hasFilteredMessages(not_null<PeerData*> peer) {
	std::lock_guard lock(filteredMessagesMutex);
	if (dialogsWithHiddenBlockedMessages.contains(peer->id.value)) {
		return true;
	}
	const auto dialogIt = filteredMessages.find(peer->id.value);
	if (dialogIt == filteredMessages.end()) {
		return false;
	}
	for (const auto &entry : dialogIt->second) {
		if (entry.second) {
			return true;
		}
	}
	return false;
}

void putHiddenBlockedMessage(not_null<HistoryItem*> item) {
	std::lock_guard lock(filteredMessagesMutex);
	dialogsWithHiddenBlockedMessages.insert(item->history()->peer->id.value);
}

void putFiltered(
		not_null<HistoryItem*> item,
		const Data::Group *group,
		bool res,
		const std::shared_ptr<const Cache> &matchedCache) {
	std::lock_guard cacheLock(cacheMutex);
	if (cache != matchedCache) {
		return;
	}

	std::lock_guard filteredLock(filteredMessagesMutex);
	const auto dialogId = item->history()->peer->id.value;
	rememberFiltered(dialogId, item->id.bare, res);
	if (group && res) {
		for (const auto& groupItem : group->items) {
			rememberFiltered(dialogId, groupItem->id.bare, true);
		}
	}
	trimFilteredOverflow();
}

void invalidateSingle(not_null<HistoryItem*> item) {
	forgetFiltered(item->history()->peer->id.value, item->id.bare);
}

void invalidate(not_null<HistoryItem*> item) {
	std::lock_guard lock(filteredMessagesMutex);
	if (const auto group = item->history()->owner().groups().find(item)) {
		for (const auto& groupItem : group->items) {
			invalidateSingle(groupItem);
		}
	} else {
		invalidateSingle(item);
	}
}

}
