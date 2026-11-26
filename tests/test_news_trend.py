"""End-to-end trend tests against a real database.

Uses SQLite so the suite runs anywhere; the queries involved are plain
SELECT/WHERE/ORDER BY and behave identically on Postgres.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.currency.models import CurrencyRate
from app.news import models as _news_models  # noqa: F401 — registers the tables
from app.news.service import slugify
from app.news.trend import compute_trend

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
WINDOW_START = NOW - timedelta(days=7)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        # Eleven daily rows, all stored the way the app stores them:
        # "units per 1 USD".
        #   EUR 0.92 -> 0.94   more euros per dollar  => euro WEAKER
        #   BTC stored as 1/price, price 90k -> 99k   => BTC STRONGER
        #   JPY unchanged, CHF never populated
        session.add_all([
            CurrencyRate(
                USD=1.0,
                EUR=0.92 + (0.02 * i / 10),
                BTC=1.0 / (90000 + (9000 * i / 10)),
                JPY=155.0,
                XAU=1 / 4000.0,
                CHF=None,
                created_at=NOW - timedelta(days=10 - i),
            )
            for i in range(11)
        ])
        await session.commit()
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_rising_eur_column_reports_a_weaker_euro(db):
    result = await compute_trend(db, "EUR", WINDOW_START, NOW)
    assert result.trend == "weaker"
    assert result.change_percent < 0
    # start_rate is the inverted, human-readable figure: USD per euro.
    assert result.start_rate > 1


@pytest.mark.asyncio
async def test_crypto_runs_the_same_path_as_fiat(db):
    result = await compute_trend(db, "BTC", WINDOW_START, NOW)
    assert result.trend == "stronger"
    assert result.change_percent > 0
    # Inverting 1/price gives the price back, so this reads in dollars.
    assert 90000 < result.start_rate < 99000


@pytest.mark.asyncio
async def test_metals_run_the_same_path_too(db):
    result = await compute_trend(db, "XAU", WINDOW_START, NOW)
    assert round(result.start_rate) == 4000
    assert result.trend == "flat"


@pytest.mark.asyncio
async def test_an_unchanged_rate_is_flat(db):
    result = await compute_trend(db, "JPY", WINDOW_START, NOW)
    assert result.trend == "flat"
    assert result.change_percent == 0.0


@pytest.mark.asyncio
async def test_usd_is_the_base_and_has_no_percentage(db):
    result = await compute_trend(db, "USD", WINDOW_START, NOW)
    assert result.trend == "base"
    assert result.change_percent is None
    assert result.notes  # says why, rather than silently returning zero


@pytest.mark.asyncio
async def test_baseline_is_taken_from_before_the_window(db):
    """Rule 3: compare against the rate as the window opened, not the first row inside it."""
    result = await compute_trend(db, "EUR", WINDOW_START, NOW)
    assert result.start_observed_at < WINDOW_START
    assert result.notes == []


@pytest.mark.asyncio
async def test_falls_back_when_there_is_no_earlier_history(db):
    start = NOW - timedelta(days=30)  # older than any row we stored
    result = await compute_trend(db, "EUR", start, NOW)
    assert result.start_observed_at >= start
    assert any("earliest row" in note for note in result.notes)


@pytest.mark.asyncio
async def test_a_column_with_no_data_reports_unknown_not_zero(db):
    result = await compute_trend(db, "CHF", WINDOW_START, NOW)
    assert result.trend == "unknown"
    assert result.change_percent is None
    assert result.notes


@pytest.mark.asyncio
async def test_an_untracked_code_is_rejected(db):
    with pytest.raises(ValueError, match="Unsupported"):
        await compute_trend(db, "ZZZ", WINDOW_START, NOW)


@pytest.mark.parametrize(
    "title,code,expected",
    [
        (
            "Euro Faces Pressure Against a Stronger US Dollar",
            "EUR",
            "euro-faces-pressure-against-a-stronger-us-dollar-eur-2026-11-19",
        ),
        ("Gold Prices Drop: Dollar's Rise!", "XAU", "gold-prices-drop-dollar-s-rise-xau-2026-11-19"),
        ("", "BTC", "insight-btc-2026-11-19"),
    ],
)
def test_slugify(title, code, expected):
    assert slugify(title, code, NOW.date()) == expected
