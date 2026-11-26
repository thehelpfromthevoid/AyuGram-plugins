// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

// The Power Saving battery threshold, carried over from the macOS 13.2 beta
// (`LiteMode.LowPower`, see rust/ayu-core/src/power.rs). The beta turns the
// mode on automatically once the charge drops below a configured percentage;
// this header is the C++ side of that rule.
//
// The comparison itself lives in Rust: `Ayu::Power::ShouldEnable` calls
// `ayu_power_should_enable` from the `ayu_fast` static library, so the bounds
// and the strictness of the test exist in exactly one place and are covered by
// the Rust tests. When the client is built without the Rust layer
// (`AYU_RUST_OPTIONAL=ON`) the same rule is applied inline, so the call site
// never has to know which one it is.
namespace Ayu::Power {

// The beta refuses a threshold outside 0..=100, where 0 means "never switch
// automatically". Mirrors `power::MAX_THRESHOLD`.
inline constexpr int kMaxLowPowerThreshold = 100;

// Whether Power Saving should switch itself on at this charge level.
//
// `batteryPercent` is the platform reading, `-1` when the platform does not
// report one; `threshold` is the value from settings, 0 meaning never. A
// negative reading never triggers: the Rust parser clamps a non-positive
// argument to 0, which would otherwise satisfy any positive threshold.
[[nodiscard]] bool ShouldEnable(int batteryPercent, int threshold);

} // namespace Ayu::Power
