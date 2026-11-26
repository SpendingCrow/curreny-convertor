"""Tests for the shared percentage service.

The case that matters most: `currency_rates` stores every asset as "units per
1 USD", so a RISING column value means the asset got WEAKER. Getting that
backwards would flip every percentage in the app, so it is pinned here.
"""

import math

import pytest

from app.utils.performance import (
    Trend,
    change_percent,
    classify,
    fit_slope,
    is_valid_rate,
    round_percent,
    to_strength,
)


@pytest.mark.parametrize(
    "value",
    [None, 0, 0.0, -1.5, float("nan"), float("inf"), float("-inf"), "abc", True, [], {}],
)
def test_invalid_rates_are_rejected(value):
    assert is_valid_rate(value) is False


@pytest.mark.parametrize("value", [1, 0.0001, 1e9, "1.5", 0.92])
def test_valid_rates_are_accepted(value):
    assert is_valid_rate(value) is True


def test_to_strength_inverts():
    # EUR column holds euros per dollar; strength is dollars per euro.
    assert to_strength(0.92) == pytest.approx(1 / 0.92)
    assert to_strength(0) is None
    assert to_strength(None) is None


def test_rising_column_means_the_currency_weakened():
    # 0.92 -> 0.94 euros per dollar: it now takes more euros to buy a dollar.
    percent = change_percent(to_strength(0.92), to_strength(0.94))
    assert percent < 0
    assert round_percent(percent) == -2.13


def test_falling_column_means_the_currency_strengthened():
    percent = change_percent(to_strength(0.92), to_strength(0.90))
    assert percent > 0
    assert round_percent(percent) == 2.22


def test_crypto_follows_the_identical_path():
    # Crypto is stored as 1/price, so strength comes back out as the USD price.
    assert to_strength(1 / 95000) == pytest.approx(95000)
    percent = change_percent(to_strength(1 / 95000), to_strength(1 / 97000))
    assert round_percent(percent) == 2.11


@pytest.mark.parametrize(
    "first,last",
    [(0, 5), (-1, 5), (None, 5), (1, None), (1, float("nan")), ("x", 5)],
)
def test_change_percent_guards_bad_input(first, last):
    assert change_percent(first, last) is None


def test_rounding_happens_once_and_never_shows_negative_zero():
    assert round_percent(1.0849) == 1.08
    assert round_percent(-0.0001) == 0.0
    assert not math.copysign(1, round_percent(-0.0001)) < 0
    assert round_percent(None) is None
    assert round_percent(float("nan")) is None


def test_slope_direction():
    assert fit_slope([(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]) > 0
    assert fit_slope([(0.0, 3.0), (1.0, 2.0), (2.0, 1.0)]) < 0
    assert fit_slope([(0.0, 1.0)]) is None          # needs two points
    assert fit_slope([(1.0, 1.0), (1.0, 2.0)]) is None  # no spread in x


def test_classify_requires_percent_and_slope_to_agree():
    assert classify(2.5, 0.1) == Trend.STRONGER
    assert classify(-2.5, -0.1) == Trend.WEAKER
    # A big move that fights its own slope is a spike, not a trend.
    assert classify(2.5, -0.1) == Trend.FLAT
    assert classify(0.05, 0.1) == Trend.FLAT   # under the threshold
    assert classify(2.5, None) == Trend.FLAT   # not enough points for a slope
    assert classify(None, 0.1) == Trend.UNKNOWN


def test_round_rate_handles_the_full_range_of_assets():
    """Rates span 1.00 (stablecoin) to 100,000 (BTC), so significant figures win."""
    from app.utils.performance import round_rate

    # Inverting 1/100000 leaves a float artefact that must not reach the UI.
    assert round_rate(99999.99999999999) == 100000.0
    assert round_rate(1.1111111111111112) == 1.11111
    assert round_rate(4000.0) == 4000.0
    assert round_rate(0.00001234567) == pytest.approx(0.0000123457)
    assert round_rate(0) == 0.0


@pytest.mark.parametrize("bad", [None, "abc", float("nan"), float("inf")])
def test_round_rate_rejects_bad_input(bad):
    from app.utils.performance import round_rate
    assert round_rate(bad) is None
