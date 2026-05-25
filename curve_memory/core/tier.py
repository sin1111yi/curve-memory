#!/usr/bin/env python3
"""tier.py — R(t) forgetting curve calculation + TIER mapping

Forgetting curve: R(t) = 0.462 + 0.538 * exp(-t / 2.71)
R(t) ∈ [0.462, 1.0], baseline 46.2% never reaches zero
t = days since last access

Calculator-compatible formula: R = 0.462 + 0.538 * pow(C_E, -t / 2.71)

TIER mapping:
  TIER_5 🔥  R ≥ 0.800  (t ≤ 1)
  TIER_4 📗  R ≥ 0.640  (t ≤ 3)
  TIER_3 📙  R ≥ 0.503  (t ≤ 7)
  TIER_2 📕  R ≥ 0.465  (t ≤ 14)
  TIER_1 📦  R > 0.462  (t < 30)
  ARCHIVE 🗄️ R ≈ 0.462  (t ≥ 30)
"""

import math

# === Constants ===
BASE_RATE = 0.462       # Baseline retention rate R₀
DECAY_RATE = 0.538      # 1 - R₀
TAU = 2.71              # Time constant τ
ARCHIVE_THRESHOLD = 30  # Archive threshold (days)
EPSILON = 0.001         # Baseline tolerance


def forgetting_curve(t: float) -> float:
    """Calculate R(t) forgetting curve value"""
    if t < 0:
        t = 0
    return BASE_RATE + DECAY_RATE * math.exp(-t / TAU)


def r_to_tier_name(r: float) -> str:
    """R(t) → TIER name (with icon)"""
    if r >= 0.800:
        return "TIER_5 🔥"
    elif r >= 0.640:
        return "TIER_4 📗"
    elif r >= 0.503:
        return "TIER_3 📙"
    elif r >= 0.465:
        return "TIER_2 📕"
    elif r > BASE_RATE + EPSILON:
        return "TIER_1 📦"
    else:
        return "ARCHIVE 🗄️"


def r_to_tier_level(r: float) -> int:
    """R(t) → TIER numeric level (5=highest, 0=archived)"""
    if r >= 0.800:
        return 5
    elif r >= 0.640:
        return 4
    elif r >= 0.503:
        return 3
    elif r >= 0.465:
        return 2
    elif r > BASE_RATE + EPSILON:
        return 1
    else:
        return 0


def r_to_tier_abbr(tier_name: str) -> str:
    """TIER name → abbreviation"""
    mapping = {
        "TIER_5 🔥": "T5",
        "TIER_4 📗": "T4",
        "TIER_3 📙": "T3",
        "TIER_2 📕": "T2",
        "TIER_1 📦": "T1",
        "ARCHIVE 🗄️": "ARC",
    }
    return mapping.get(tier_name, "UNK")


def t_to_tier_name(t: int) -> str:
    """Days t → TIER name"""
    r = forgetting_curve(t)
    return r_to_tier_name(r)


def should_archive(t: int) -> bool:
    """Determine if should archive (purely day-driven)"""
    return t >= ARCHIVE_THRESHOLD


def is_mature(access_count: int, t: int) -> bool:
    """Determine if memory is mature (frequent access)"""
    return access_count >= 20 and t <= 3


# === Built-in self-test ===
if __name__ == "__main__":
    print("=== R(t) Forgetting Curve Validation ===")
    test_points = [0, 1, 3, 7, 14, 21, 30, 48, 60]
    for t in test_points:
        r = forgetting_curve(t)
        tier = r_to_tier_name(r)
        print(f"  t={t:2d} → R={r:.6f}  ({tier})")

    print()
    print("=== Unit Tests ===")
    # R(0) = 1.0
    assert abs(forgetting_curve(0) - 1.0) < 0.001, "R(0) != 1.0"
    # R(1) ≈ 0.834
    assert abs(forgetting_curve(1) - 0.834) < 0.01, "R(1) out of range"
    # R(30) ≈ 0.4628
    assert abs(forgetting_curve(30) - 0.4628) < 0.01, "R(30) out of range"
    # R(60) ≈ 0.4620
    assert abs(forgetting_curve(60) - 0.4620) < 0.01, "R(60) out of range"
    # TIER mapping
    assert r_to_tier_name(0.9) == "TIER_5 🔥"
    assert r_to_tier_name(0.55) == "TIER_3 📙"
    assert r_to_tier_level(0.9) == 5
    assert r_to_tier_level(0.462) == 0
    # Archive judgment
    assert should_archive(30) == True
    assert should_archive(29) == False
    # Maturity judgment
    assert is_mature(20, 3) == True
    assert is_mature(20, 4) == False
    assert is_mature(19, 3) == False
    print("  ✅ All passed")
