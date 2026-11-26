"""Tests for the anti-hallucination validation of OpenAI output.

Everything the model returns is treated as untrusted: bad links, articles from
outside the window and unknown categories are dropped rather than repaired.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.news.constants import AssetType, Category, Sentiment
from app.news.openai_client import (
    InsightGenerationError,
    _parse_published_at,
    _validate_payload,
    _validate_sources,
)
from app.news.trend import TrendResult
from app.utils.performance import Trend

WINDOW_END = datetime(2026, 11, 19, tzinfo=timezone.utc)
WINDOW_START = WINDOW_END - timedelta(days=7)


def _result(code="EUR", trend=Trend.WEAKER):
    return TrendResult(
        currency_code=code,
        currency_name="Euro",
        asset_type=AssetType.FIAT,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        change_percent=-0.42,
        trend=trend,
    )


def _source(**overrides):
    base = {
        "headline": "Euro slips against the dollar",
        "publisher": "Reuters",
        "source_url": "https://reuters.com/markets/euro-slips",
        "published_at": "2026-11-17T14:30:00Z",
    }
    base.update(overrides)
    return base


def test_a_good_source_survives():
    kept, dropped = _validate_sources([_source()], WINDOW_START, WINDOW_END)
    assert len(kept) == 1
    assert dropped == 0
    assert kept[0].publisher == "Reuters"


@pytest.mark.parametrize(
    "bad",
    [
        {"source_url": ""},                      # no link at all
        {"source_url": "reuters.com/markets"},   # not a URL
        {"source_url": "javascript:alert(1)"},   # not http(s)
        {"source_url": "ftp://files.example"},   # not http(s)
        {"headline": ""},                        # citation with no title
    ],
)
def test_sources_without_a_usable_link_are_dropped(bad):
    kept, dropped = _validate_sources([_source(**bad)], WINDOW_START, WINDOW_END)
    assert kept == []
    assert dropped == 1


def test_articles_from_outside_the_window_are_dropped():
    stale = _source(published_at="2026-01-05T00:00:00Z")
    kept, dropped = _validate_sources([stale], WINDOW_START, WINDOW_END)
    assert kept == []
    assert dropped == 1


def test_a_small_publishing_lag_is_tolerated():
    # One day after the window closes is still plausibly about this window.
    late = _source(published_at="2026-11-20T09:00:00Z")
    kept, _ = _validate_sources([late], WINDOW_START, WINDOW_END)
    assert len(kept) == 1


def test_an_unparseable_date_is_kept_but_recorded_as_unknown():
    kept, dropped = _validate_sources(
        [_source(published_at="sometime last week")], WINDOW_START, WINDOW_END
    )
    assert len(kept) == 1
    assert kept[0].published_at is None
    assert dropped == 0


def test_duplicate_urls_are_collapsed():
    dupe = _source(source_url="https://reuters.com/markets/euro-slips/")
    kept, dropped = _validate_sources([_source(), dupe], WINDOW_START, WINDOW_END)
    assert len(kept) == 1
    assert dropped == 1


def test_sources_are_not_a_list():
    assert _validate_sources("nope", WINDOW_START, WINDOW_END) == ([], 0)


def _payload(**overrides):
    base = {
        "title": "Euro Faces Pressure Against a Stronger US Dollar",
        "category": Category.FOREX.value,
        "sentiment": Sentiment.BEARISH.value,
        "short_summary": "The euro slipped as rate expectations shifted.",
        "long_summary": "Across the week the euro gave up ground ...",
        "related_assets": ["EUR", "USD"],
        "sources": [_source()],
    }
    base.update(overrides)
    return base


def test_valid_payload_passes_through():
    draft = _validate_payload(_payload(), _result(), WINDOW_START, WINDOW_END)
    assert draft.category == Category.FOREX.value
    assert draft.sentiment == Sentiment.BEARISH.value
    assert draft.related_assets == ["EUR", "USD"]
    assert len(draft.sources) == 1


def test_an_invented_category_falls_back_to_the_asset_default():
    draft = _validate_payload(
        _payload(category="Vibes News"), _result(), WINDOW_START, WINDOW_END
    )
    assert draft.category == Category.FOREX.value


def test_an_invented_sentiment_falls_back_to_what_we_measured():
    draft = _validate_payload(
        _payload(sentiment="Spicy"), _result(trend=Trend.WEAKER),
        WINDOW_START, WINDOW_END,
    )
    assert draft.sentiment == Sentiment.BEARISH.value


def test_the_subject_currency_is_always_in_related_assets():
    draft = _validate_payload(
        _payload(related_assets=["USD"]), _result(), WINDOW_START, WINDOW_END
    )
    assert draft.related_assets[0] == "EUR"


@pytest.mark.parametrize("missing", ["title", "short_summary", "long_summary"])
def test_an_empty_title_or_summary_is_rejected(missing):
    with pytest.raises(InsightGenerationError):
        _validate_payload(
            _payload(**{missing: "   "}), _result(), WINDOW_START, WINDOW_END
        )


def test_an_insight_with_no_usable_sources_is_still_accepted():
    """An honest "we found nothing" beats an invented citation."""
    draft = _validate_payload(
        _payload(sources=[_source(source_url="not-a-url")]),
        _result(), WINDOW_START, WINDOW_END,
    )
    assert draft.sources == []
    assert draft.dropped_sources == 1
    assert draft.short_summary  # the write-up survives


@pytest.mark.parametrize(
    "raw,expected_year",
    [("2026-11-17", 2026), ("2026-11-17T14:30:00Z", 2026), ("2026-11-17T14:30:00+02:00", 2026)],
)
def test_published_at_parsing(raw, expected_year):
    parsed = _parse_published_at(raw)
    assert parsed is not None
    assert parsed.year == expected_year
    assert parsed.tzinfo is not None


@pytest.mark.parametrize("raw", [None, "", "last Tuesday", 12345, []])
def test_unparseable_dates_return_none(raw):
    assert _parse_published_at(raw) is None
