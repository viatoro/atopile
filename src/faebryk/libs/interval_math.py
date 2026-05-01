# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

"""
Pure-float interval arithmetic.

Provides interval and interval-set operations on plain (lo, hi) float tuples.
Used by both Literals.py (which wraps results in graph nodes) and the discrete
SCC solver (which operates directly on floats in its inner search loop).
"""

import math

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Interval = tuple[float, float]
"""Closed interval [lo, hi], supports ±inf."""

IntervalSet = tuple[Interval, ...]
"""Sorted, non-overlapping tuple of intervals."""

# ---------------------------------------------------------------------------
# Tolerances
# ---------------------------------------------------------------------------

REL_DIGITS = 7  # 99.99999% precision
ABS_DIGITS = 15  # femto
EPSILON_REL = 10 ** -(REL_DIGITS - 1)
EPSILON_ABS = 10**-ABS_DIGITS


def float_eq(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=EPSILON_REL, abs_tol=EPSILON_ABS)


def float_ge(a: float, b: float) -> bool:
    return a >= b or float_eq(a, b)


def float_round(value: float, digits: int = 0) -> float:
    """
    Round a float to a specified number of decimal places using traditional
    "round half up" behavior (0.5 -> 1), rather than Python's default
    banker's rounding (round half to even).
    """
    if value in (math.inf, -math.inf):
        return value
    multiplier = 10**digits
    if digits > 0 and abs(value) > 1e30:
        raise ValueError(
            f"Value {value} is too large to round reliably with digits={digits}"
        )
    # Use floor(x + 0.5) for traditional rounding
    return math.floor(value * multiplier + 0.5) / multiplier


# ---------------------------------------------------------------------------
# Single-interval operations
# ---------------------------------------------------------------------------


def interval_add(a: Interval, b: Interval) -> Interval:
    """Arithmetically adds two intervals."""
    return (a[0] + b[0], a[1] + b[1])


def interval_negate(a: Interval) -> Interval:
    """Arithmetically negates an interval."""
    return (-a[1], -a[0])


def interval_subtract(a: Interval, b: Interval) -> Interval:
    """Arithmetically subtracts an interval from another interval."""
    return interval_add(a, interval_negate(b))


def _guarded_mul(a: float, b: float) -> float:
    """0 × ±inf → 0"""
    if a == 0.0 or b == 0.0:
        return 0.0
    prod = a * b
    assert not math.isnan(prod)
    return prod


def interval_multiply(a: Interval, b: Interval) -> Interval:
    """Arithmetically multiplies two intervals."""
    products = [
        _guarded_mul(a[0], b[0]),
        _guarded_mul(a[0], b[1]),
        _guarded_mul(a[1], b[0]),
        _guarded_mul(a[1], b[1]),
    ]
    return (min(products), max(products))


def interval_invert(a: Interval) -> IntervalSet:
    """
    Arithmetically inverts an interval (1/x).

    Returns 0–2 intervals depending on whether the input crosses zero.
    """
    lo, hi = a
    # [0, 0] → empty
    if lo == 0 == hi:
        return ()
    # Crosses zero → two half-infinite intervals
    if lo < 0 < hi:
        return ((-math.inf, 1.0 / lo), (1.0 / hi, math.inf))
    # Negative up to zero
    if lo < 0 == hi:
        return ((-math.inf, 1.0 / lo),)
    # Zero up to positive
    if lo == 0 < hi:
        return ((1.0 / hi, math.inf),)
    # Strictly one side of zero
    return ((1.0 / hi, 1.0 / lo),)


def interval_divide(a: Interval, b: Interval) -> IntervalSet:
    """Arithmetically divides an interval by another interval."""
    inv = interval_invert(b)
    return intervals_merge([interval_multiply(a, i) for i in inv])


def interval_intersect(a: Interval, b: Interval) -> Interval | None:
    """Set intersects two intervals."""
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    if lo <= hi:
        return (lo, hi)
    return None


def interval_difference(a: Interval, b: Interval) -> IntervalSet:
    """Set difference of two intervals."""
    # no overlap
    if a[1] < b[0] or a[0] > b[1]:
        return (a,)
    # fully covered
    if b[0] <= a[0] and b[1] >= a[1]:
        return ()
    # inner overlap
    if a[0] < b[0] and a[1] > b[1]:
        return ((a[0], b[0]), (b[1], a[1]))
    # right overlap
    if a[0] < b[0]:
        return ((a[0], b[0]),)
    # left overlap
    return ((b[1], a[1]),)


def interval_is_subset(a: Interval, b: Interval) -> bool:
    """Uses plain >= (no tolerance), matching NumericInterval.op_is_subset_of."""
    return a[0] >= b[0] and b[1] >= a[1]


def interval_pow(base: Interval, exp: Interval) -> IntervalSet:
    """base^exp with the same semantics as NumericInterval.op_pow."""
    base_lo, base_hi = base
    exp_lo, exp_hi = exp

    if exp_hi < 0:
        neg_exp = interval_negate(exp)
        pos_result = interval_pow(base, neg_exp)
        return intervals_invert(pos_result)

    if exp_lo < 0:
        raise NotImplementedError("crossing zero in exp not implemented yet")

    if base_lo < 0 and not (exp_lo == exp_hi and exp_lo == int(exp_lo)):
        raise NotImplementedError(
            "cannot raise negative base to fractional exponent (complex result)"
        )

    # see first two guards above
    assert exp_lo >= 0

    def _pow(x: float, y: float) -> float:
        try:
            return x**y
        except OverflowError:
            return math.inf if x > 0 else -math.inf

    a, b = base_lo, base_hi
    c, d = exp_lo, exp_hi

    values = [_pow(a, c), _pow(a, d), _pow(b, c), _pow(b, d)]

    if a < 0 < b:
        # might be 0 exp, so just in case applying exponent
        values.extend((0.0**c, 0.0**d))

        # d odd
        if d % 2 == 1:
            # c < k < d
            k = d - 1
            if k > c:
                values.append(_pow(a, k))

    return ((min(values), max(values)),)


def interval_abs(a: Interval) -> Interval:
    lo, hi = a
    # case 1: crosses zero
    if lo < 0 < hi:
        return (0.0, hi)
    # case 2: negative only
    if lo < 0 and hi < 0:
        return (-hi, -lo)
    # case 3: max = 0 and min < 0
    if lo < 0 and hi == 0:
        return (0.0, -lo)
    assert lo >= 0 and hi >= 0
    return a


def interval_round(a: Interval, ndigits: int = 0) -> Interval:
    return (float_round(a[0], ndigits), float_round(a[1], ndigits))


def interval_log(a: Interval, base: float = math.e) -> Interval:
    lo, hi = a
    if lo <= 0:
        raise ValueError(f"invalid log of interval ({lo}, {hi})")
    return (math.log(lo, base), math.log(hi, base))


def sine_on_interval(interval: Interval) -> Interval:
    """
    Computes the overall sine range on the given x-interval.

    The extreme values occur either at the endpoints or at turning points
    of sine (x = π/2 + π*k).
    """
    start, end = interval
    if start > end:
        raise ValueError("Invalid interval: start must be <= end")
    if math.isinf(start) or math.isinf(end):
        return (-1.0, 1.0)
    if end - start > 2 * math.pi:
        return (-1.0, 1.0)

    # Evaluate sine at the endpoints
    xs: list[float] = [start, end]

    # Include turning points within the interval
    k_start = math.ceil((start - math.pi / 2) / math.pi)
    k_end = math.floor((end - math.pi / 2) / math.pi)
    for k in range(k_start, k_end + 1):
        xs.append(math.pi / 2 + math.pi * k)

    sine_values = [math.sin(x) for x in xs]
    return (min(sine_values), max(sine_values))


def interval_contains(a: Interval, item: float) -> bool:
    return a[0] <= item <= a[1]


# ---------------------------------------------------------------------------
# IntervalSet operations
# ---------------------------------------------------------------------------


def _maybe_merge(a: Interval, b: Interval) -> list[Interval]:
    """Merge two intervals if overlapping/adjacent, else return both sorted."""
    if a[0] <= b[0]:
        left, right = a, b
    else:
        left, right = b, a
    if interval_contains(left, right[0]):
        return [(left[0], max(left[1], right[1]))]
    return [left, right]


def intervals_merge(intervals: list[Interval]) -> IntervalSet:
    """Sort and merge overlapping intervals."""
    non_empty = [iv for iv in intervals if iv[0] <= iv[1]]
    if not non_empty:
        return ()
    sorted_ivs = sorted(non_empty, key=lambda iv: iv[0])
    merged: list[Interval] = []
    current = sorted_ivs[0]
    for iv in sorted_ivs[1:]:
        result = _maybe_merge(current, iv)
        if len(result) == 1:
            current = result[0]
        else:
            merged.append(result[0])
            current = result[1]
    merged.append(current)
    return tuple(merged)


def intervals_add(a: IntervalSet, b: IntervalSet) -> IntervalSet:
    return intervals_merge([interval_add(ai, bi) for ai in a for bi in b])


def intervals_negate(a: IntervalSet) -> IntervalSet:
    return intervals_merge([interval_negate(ai) for ai in a])


def intervals_subtract(a: IntervalSet, b: IntervalSet) -> IntervalSet:
    return intervals_add(a, intervals_negate(b))


def intervals_multiply(a: IntervalSet, b: IntervalSet) -> IntervalSet:
    return intervals_merge([interval_multiply(ai, bi) for ai in a for bi in b])


def intervals_invert(a: IntervalSet) -> IntervalSet:
    out: list[Interval] = []
    for ai in a:
        out.extend(interval_invert(ai))
    return intervals_merge(out)


def intervals_divide(a: IntervalSet, b: IntervalSet) -> IntervalSet:
    return intervals_multiply(a, intervals_invert(b))


def intervals_pow(a: IntervalSet, b: IntervalSet) -> IntervalSet:
    out: list[Interval] = []
    for ai in a:
        for bi in b:
            out.extend(interval_pow(ai, bi))
    return intervals_merge(out)


def intervals_intersect(a: IntervalSet, b: IntervalSet) -> IntervalSet:
    """Two-pointer intersection of sorted interval sets."""
    result: list[Interval] = []
    s, o = 0, 0
    while s < len(a) and o < len(b):
        ai, bi = a[s], b[o]
        ix = interval_intersect(ai, bi)
        if ix is not None:
            result.append(ix)
        if ai[1] < bi[1]:
            s += 1
        elif bi[1] < ai[1]:
            o += 1
        else:
            s += 1
            o += 1
    return tuple(result)


def intervals_union(a: IntervalSet, b: IntervalSet) -> IntervalSet:
    return intervals_merge(list(a) + list(b))


def intervals_difference(a: IntervalSet, b: IntervalSet) -> IntervalSet:
    """a \\ b — iteratively subtract each interval in b."""
    out = a
    for bi in b:
        next_out: list[Interval] = []
        for ai in out:
            next_out.extend(interval_difference(ai, bi))
        out = tuple(next_out)
    return intervals_merge(list(out))


def intervals_symmetric_difference(a: IntervalSet, b: IntervalSet) -> IntervalSet:
    return intervals_difference(intervals_union(a, b), intervals_intersect(a, b))


def intervals_is_subset(a: IntervalSet, b: IntervalSet) -> bool:
    """Every point in a is contained in b."""
    return not intervals_difference(a, b)


def intervals_is_empty(a: IntervalSet) -> bool:
    return len(a) == 0


def intervals_abs(a: IntervalSet) -> IntervalSet:
    return intervals_merge([interval_abs(ai) for ai in a])


def intervals_round(a: IntervalSet, ndigits: int = 0) -> IntervalSet:
    return intervals_merge([interval_round(ai, ndigits) for ai in a])


def intervals_log(a: IntervalSet, base: float = math.e) -> IntervalSet:
    return intervals_merge([interval_log(ai, base) for ai in a])


def intervals_sin(a: IntervalSet) -> IntervalSet:
    return intervals_merge([sine_on_interval(ai) for ai in a])


# ---------------------------------------------------------------------------
# Comparison operations → bool | None (None = indeterminate)
# ---------------------------------------------------------------------------


def intervals_min(a: IntervalSet) -> float:
    return a[0][0]


def intervals_max(a: IntervalSet) -> float:
    return a[-1][1]


def intervals_ge(a: IntervalSet, b: IntervalSet) -> bool | None:
    """a >= b: True if definitely, False if definitely not, None if uncertain.

    Uses float_ge (tolerant >=) for the "definitely true" check,
    matching NumericSet.op_ge_intervals.
    """
    if not a or not b:
        return None
    if float_ge(intervals_min(a), intervals_max(b)):
        return True
    if intervals_max(a) < intervals_min(b):
        return False
    return None


def intervals_gt(a: IntervalSet, b: IntervalSet) -> bool | None:
    """Uses strict > and <=, matching NumericSet.op_gt_intervals."""
    if not a or not b:
        return None
    if intervals_min(a) > intervals_max(b):
        return True
    if intervals_max(a) <= intervals_min(b):
        return False
    return None


def intervals_le(a: IntervalSet, b: IntervalSet) -> bool | None:
    """Uses float_ge (tolerant >=) for the "definitely true" check,
    matching NumericSet.op_le_intervals."""
    if not a or not b:
        return None
    if float_ge(intervals_min(b), intervals_max(a)):
        return True
    if intervals_min(a) > intervals_max(b):
        return False
    return None


def intervals_lt(a: IntervalSet, b: IntervalSet) -> bool | None:
    """Uses strict < and >=, matching NumericSet.op_lt_intervals."""
    if not a or not b:
        return None
    if intervals_max(a) < intervals_min(b):
        return True
    if intervals_min(a) >= intervals_max(b):
        return False
    return None
