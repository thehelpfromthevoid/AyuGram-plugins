// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/ayu_power.h"

#include <cstddef>
#include <cstdio>

#if AYU_RUST_ENABLED
// The fast layer's C ABI. Declared here rather than in a shared header because
// this is the only translation unit that consumes it: a Rust-side signature
// change then breaks the one file that has to be updated, instead of every
// includer of a public header.
extern "C" int ayu_power_should_enable(
	const char *line,
	std::size_t line_len,
	int battery_percent);
#endif

namespace Ayu::Power {

bool ShouldEnable(int batteryPercent, int threshold) {
	// An unknown reading must never trigger the mode: the rule below compares a
	// percentage, and there is no percentage to compare. This guard also keeps
	// the callers from having to know how the Rust side treats a negative
	// argument (it clamps to 0, which would satisfy a positive threshold).
	if (batteryPercent < 0) {
		return false;
	}
	const auto clamped = (threshold < 0)
		? 0
		: (threshold > kMaxLowPowerThreshold
			? kMaxLowPowerThreshold
			: threshold);
	if (clamped <= 0) {
		// 0 is the beta's "never switch automatically", and the default state.
		return false;
	}

#if AYU_RUST_ENABLED
	char line[32];
	const auto length = std::snprintf(
		line,
		sizeof(line),
		"low_power=%d",
		clamped);
	if (length <= 0 || std::size_t(length) >= sizeof(line)) {
		return false;
	}
	return ayu_power_should_enable(line, std::size_t(length), batteryPercent) == 1;
#else
	// Same rule as `Settings::should_enable_at` in rust/ayu-core/src/power.rs:
	// strictly below the threshold, and only for a positive threshold.
	return batteryPercent < clamped;
#endif
}

} // namespace Ayu::Power
