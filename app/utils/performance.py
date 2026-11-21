"""Shared performance/percentage maths.

Every percent-change calculation in the app goes through this module so the
logic cannot drift between currencies, crypto and metals.

The golden rule: `currency_rates` stores every asset as "how many units you get
for 1 USD" — crypto included, since `app/currency/service.py` saves
`1 / usd_price`. So a RISING column value means the asset is WEAKER against the
dollar. Call `to_strength()` before any comparison and a positive percent always
means "got stronger", for every asset type.

Intermediate values are never rounded; only the final displayed percentage is.
"""

import math

# |percent| must reach this before we call a move a real trend.
TREND_THRESHOLD_PERCENT = 0.10

# Displayed percentages use a plain fixed 2 decimals, e.g. 1.08 / -0.42 / 0.00.
PERCENT_DECIMALS = 2

# Rates span a huge range — gold near 4,000 USD, BTC near 100,000, a stablecoin
# near 1.00 — so a fixed number of decimals would either lose precision or print
# noise. Significant figures keep every one of them readable.
RATE_SIG_FIGURES = 6
RATE_DECIMALS_MIN = 2
RATE_DECIMALS_MAX = 10


class Trend:
    STRONGER = "stronger"
    WEAKER = "weaker"
    FLAT = "flat"
    UNKNOWN = "unknown"
    # USD against USD: there is no move to measure, which is not the same thing
    # as a flat week.
    BASE = "base"


def is_valid_rate(value) -> bool:
    """True only for a rate we can safely divide by.

    Rejects None, non-numerics, bools, NaN, +/-inf, zero and negatives.
    """
    if value is None or isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0.0


def to_strength(column_value) -> float | None:
    """Convert a stored 'units per 1 USD' value into a strength figure.

    Inverting means a bigger number is always a stronger asset, which makes the
    sign of every downstream percentage mean the same thing for EUR, BTC and XAU
    alike.
    """
    if not is_valid_rate(column_value):
        return None
    return 1.0 / float(column_value)


def change_percent(first, last) -> float | None:
    """Percent change from `first` to `last`. Raw float — deliberately unrounded.

    Callers round once, at display time.
    """
    if not is_valid_rate(first) or last is None:
        return None
    try:
        last_value = float(last)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(last_value):
        return None

    first_value = float(first)
    result = ((last_value - first_value) / first_value) * 100.0
    return result if math.isfinite(result) else None


def round_percent(percent, decimals: int = PERCENT_DECIMALS) -> float | None:
    """Round a percentage for display. The only place rounding happens."""
    if percent is None or not math.isfinite(percent):
        return None
    # `+ 0.0` normalises -0.0 to 0.0 so the UI never prints "-0.00".
    return round(percent, decimals) + 0.0


def round_rate(value, sig_figures: int = RATE_SIG_FIGURES) -> float | None:
    """Round a rate for display at a fixed number of significant figures.

    Float arithmetic leaves artefacts — inverting 1/100000 gives back
    99999.99999999999 — and those must never reach the UI.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number == 0:
        return 0.0
    magnitude = math.floor(math.log10(abs(number)))
    decimals = max(RATE_DECIMALS_MIN, min(RATE_DECIMALS_MAX, sig_figures - magnitude - 1))
    return round(number, decimals) + 0.0


def fit_slope(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope through (x, y) points.

    Used only for its sign: it confirms the start-to-end move reflects the whole
    window rather than a single spike at one end. x values are recentred on their
    mean so large unix timestamps don't lose precision.
    """
    usable = [
        (float(x), float(y))
        for x, y in points
        if x is not None and y is not None
        and math.isfinite(float(x)) and math.isfinite(float(y))
    ]
    if len(usable) < 2:
        return None

    mean_x = sum(x for x, _ in usable) / len(usable)
    mean_y = sum(y for _, y in usable) / len(usable)

    numerator = sum((x - mean_x) * (y - mean_y) for x, y in usable)
    denominator = sum((x - mean_x) ** 2 for x, _ in usable)
    if denominator == 0:
        return None

    slope = numerator / denominator
    return slope if math.isfinite(slope) else None


def classify(
    percent: float | None,
    slope: float | None,
    threshold: float = TREND_THRESHOLD_PERCENT,
) -> str:
    """Turn a percent change plus a slope into stronger / weaker / flat.

    Both must agree AND the move must clear `threshold`; a move that clears the
    threshold but fights its own slope is a spike, not a trend, so it reads flat.
    """
    if percent is None or not math.isfinite(percent):
        return Trend.UNKNOWN
    if abs(percent) < threshold:
        return Trend.FLAT
    if slope is None:
        return Trend.FLAT
    if percent > 0 and slope > 0:
        return Trend.STRONGER
    if percent < 0 and slope < 0:
        return Trend.WEAKER
    return Trend.FLAT
