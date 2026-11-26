"""Tests for the two-endpoint news surface.

Two things matter here and are easy to regress:
  1. The read endpoint returns ONE shape. `items` and `groups` are mutually
     exclusive, and a single-currency request is still a list.
  2. The public API key can never trigger paid OpenAI generation, and an
     unconfigured cron key disables the endpoint rather than opening it.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.core.config import env_var
from app.news.constants import Category
from app.news.schema import NewsFeedGroup, NewsInsightOut
from app.news.service import (
    ALLOWED_GROUP_BY,
    GROUP_BY_CATEGORY,
    _group_by_category,
)
from app.utils.security import verify_api_key, verify_cron_key


# --------------------------------------------------------------------------
# Cron auth — the money guard
# --------------------------------------------------------------------------


def test_unset_cron_key_disables_the_endpoint_rather_than_opening_it():
    """Fail closed. An unset secret must not mean 'no secret required'."""
    with patch.object(env_var, "CRON_KEY", None):
        with pytest.raises(HTTPException) as exc:
            verify_cron_key("anything-at-all")
    assert exc.value.status_code == 503
    assert "not configured" in exc.value.detail


def test_empty_cron_key_also_disables_the_endpoint():
    with patch.object(env_var, "CRON_KEY", ""):
        with pytest.raises(HTTPException) as exc:
            verify_cron_key("")
    assert exc.value.status_code == 503


def test_correct_cron_key_is_accepted():
    with patch.object(env_var, "CRON_KEY", "secret-cron"):
        assert verify_cron_key("secret-cron") is None


def test_wrong_cron_key_is_rejected():
    with patch.object(env_var, "CRON_KEY", "secret-cron"):
        with pytest.raises(HTTPException) as exc:
            verify_cron_key("secret-cro")
    assert exc.value.status_code == 401


def test_public_api_key_cannot_trigger_generation():
    """The key shipped to the frontend must not reach the paid endpoint."""
    with patch.object(env_var, "CRON_KEY", "secret-cron"), patch.object(
        env_var, "API_KEY", "public-api-key"
    ):
        with pytest.raises(HTTPException) as exc:
            verify_cron_key("public-api-key")
    assert exc.value.status_code == 401


def test_cron_key_cannot_read_the_feed():
    """And the reverse: the cron secret is not a reading credential."""
    with patch.object(env_var, "API_KEY", "public-api-key"):
        with pytest.raises(HTTPException) as exc:
            verify_api_key("secret-cron")
    assert exc.value.status_code == 401


# --------------------------------------------------------------------------
# Grouping
# --------------------------------------------------------------------------


def _card(code: str, category: str) -> NewsInsightOut:
    return NewsInsightOut(
        currency_code=code,
        currency_name=code,
        asset_type="fiat",
        title=f"{code} moved",
        slug=f"{code.lower()}-moved",
        category=category,
        sentiment="Neutral",
        short_summary="s",
        long_summary="l",
        trend="flat",
        start_date="2026-11-12",
        end_date="2026-11-19",
        days=7,
        summary_window_days=7,
        insight_date="2026-11-19",
        created_at="2026-11-19T00:00:00Z",
    )


def test_groups_follow_the_designed_display_order():
    """Crypto must not jump ahead of forex just because it was stored first."""
    cards = [
        _card("BTC", Category.CRYPTO.value),
        _card("XAU", Category.METALS.value),
        _card("EUR", Category.FOREX.value),
    ]
    groups = _group_by_category(cards)
    assert [g.category for g in groups] == [
        Category.FOREX.value,
        Category.CRYPTO.value,
        Category.METALS.value,
    ]


def test_empty_categories_are_omitted():
    groups = _group_by_category([_card("EUR", Category.FOREX.value)])
    assert len(groups) == 1
    assert groups[0].category == Category.FOREX.value


def test_cards_in_the_same_category_stay_together_in_input_order():
    cards = [
        _card("EUR", Category.FOREX.value),
        _card("GBP", Category.FOREX.value),
        _card("JPY", Category.FOREX.value),
    ]
    groups = _group_by_category(cards)
    assert len(groups) == 1
    assert [i.currency_code for i in groups[0].items] == ["EUR", "GBP", "JPY"]


def test_unexpected_category_is_kept_but_sorted_last():
    """We never drop a card just because its category is unfamiliar."""
    cards = [
        _card("XYZ", "Some Future Category"),
        _card("EUR", Category.FOREX.value),
    ]
    groups = _group_by_category(cards)
    assert [g.category for g in groups] == [
        Category.FOREX.value,
        "Some Future Category",
    ]


def test_no_cards_means_no_groups():
    assert _group_by_category([]) == []


def test_category_is_the_only_supported_grouping():
    """Guards the 400 the endpoint returns for anything else."""
    assert ALLOWED_GROUP_BY == {GROUP_BY_CATEGORY}
    assert GROUP_BY_CATEGORY == "category"


def test_grouping_preserves_every_card():
    cards = [
        _card("EUR", Category.FOREX.value),
        _card("BTC", Category.CRYPTO.value),
        _card("XAU", Category.METALS.value),
        _card("GBP", Category.FOREX.value),
    ]
    groups = _group_by_category(cards)
    regrouped = [i.currency_code for g in groups for i in g.items]
    assert sorted(regrouped) == sorted(c.currency_code for c in cards)
    assert isinstance(groups[0], NewsFeedGroup)
