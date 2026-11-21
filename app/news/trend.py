"""Works out whether an asset got stronger or weaker against USD.

All percentage maths is delegated to `app/utils/performance.py` so currencies,
crypto and metals share one code path. Nothing here rounds except at the end.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.currency.models import CurrencyRate
from app.utils.performance import (
    Trend,
    change_percent,
    classify,
    fit_slope,
    is_valid_rate,
    round_percent,
    to_strength,
)

from .constants import BASE_CURRENCY, AssetType, Sentiment, get_meta

logger = logging.getLogger(__name__)

# Guards getattr() against anything that isn't a real rate column.
_RESERVED_COLUMNS = {"id", "created_at"}
RATE_COLUMNS: frozenset[str] = frozenset(
    c.name for c in CurrencyRate.__table__.columns if c.name not in _RESERVED_COLUMNS
)


@dataclass
class TrendResult:
    currency_code: str
    currency_name: str
    asset_type: AssetType

    window_start: datetime
    window_end: datetime

    # USD value of one unit (the inverted, human-readable rate).
    start_rate: float | None = None
    end_rate: float | None = None
    start_observed_at: datetime | None = None
    end_observed_at: datetime | None = None

    change_percent: float | None = None   # rounded to 2dp, ready to display
    trend: str = Trend.UNKNOWN
    slope: float | None = None
    points_used: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def is_base(self) -> bool:
        return self.currency_code == BASE_CURRENCY

    @property
    def sentiment(self) -> Sentiment:
        if self.trend == Trend.STRONGER:
            return Sentiment.BULLISH
        if self.trend == Trend.WEAKER:
            return Sentiment.BEARISH
        return Sentiment.NEUTRAL


def _as_utc(ts: datetime | None) -> datetime | None:
    """Force a timestamp to aware UTC.

    Postgres hands back aware datetimes for `DateTime(timezone=True)`, but a
    naive value would silently be read as local time by `.timestamp()`, so we
    normalise rather than trust the driver.
    """
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


async def _fetch_series(
    db: AsyncSession,
    code: str,
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[tuple[datetime, float]], tuple[datetime, float] | None]:
    """Return (points inside the window, the last point at or before the start).

    The second value is what rule 3 calls "the appropriate previous historical
    rate": the asset's value as it stood when the window opened, even if no row
    was written at exactly that moment.
    """
    column = getattr(CurrencyRate, code)

    in_window = (
        await db.execute(
            select(CurrencyRate.created_at, column)
            .where(
                CurrencyRate.created_at >= window_start,
                CurrencyRate.created_at <= window_end,
                column.isnot(None),
            )
            .order_by(CurrencyRate.created_at.asc())
        )
    ).all()

    baseline_row = (
        await db.execute(
            select(CurrencyRate.created_at, column)
            .where(CurrencyRate.created_at < window_start, column.isnot(None))
            .order_by(CurrencyRate.created_at.desc())
            .limit(1)
        )
    ).first()

    points = [(_as_utc(ts), float(v)) for ts, v in in_window if is_valid_rate(v)]
    baseline = None
    if baseline_row and is_valid_rate(baseline_row[1]):
        baseline = (_as_utc(baseline_row[0]), float(baseline_row[1]))

    return points, baseline


async def compute_trend(
    db: AsyncSession,
    code: str,
    window_start: datetime,
    window_end: datetime,
) -> TrendResult:
    """Measure one asset's move against USD over the window."""
    code = (code or "").upper()
    meta = get_meta(code)
    if meta is None:
        raise ValueError(f"Unsupported currency code: {code}")
    name, asset_type = meta

    result = TrendResult(
        currency_code=code,
        currency_name=name,
        asset_type=asset_type,
        window_start=window_start,
        window_end=window_end,
    )

    # USD is the base: 1 USD is always 1 USD. No percentage, no trend.
    if code == BASE_CURRENCY:
        result.trend = Trend.BASE
        result.notes.append("USD is the base currency; no percentage is calculated.")
        return result

    if code not in RATE_COLUMNS:
        raise ValueError(f"No rate column stored for: {code}")

    points, baseline = await _fetch_series(db, code, window_start, window_end)

    if not points:
        result.notes.append("No valid rate rows inside the window.")
        return result

    # Rule 3: latest available rate vs the rate as the window opened. If we have
    # no history from before the window, fall back to the window's own first row.
    end_ts, end_value = points[-1]
    if baseline is not None:
        start_ts, start_value = baseline
    else:
        start_ts, start_value = points[0]
        result.notes.append(
            "No rate recorded before the window opened; compared against the "
            "earliest row inside it instead."
        )

    # Rule 4: invert first, so a positive percent always means "stronger".
    start_strength = to_strength(start_value)
    end_strength = to_strength(end_value)
    if start_strength is None or end_strength is None:
        result.notes.append("Start or end rate was not usable.")
        return result

    result.start_rate = start_strength
    result.end_rate = end_strength
    result.start_observed_at = start_ts
    result.end_observed_at = end_ts
    result.points_used = len(points)

    if start_ts == end_ts:
        result.trend = Trend.FLAT
        result.change_percent = 0.0
        result.notes.append("Only one observation available; treated as no move.")
        return result

    raw_percent = change_percent(start_strength, end_strength)

    # Slope over the inverted series confirms the move held across the window
    # rather than being one spike at either end.
    slope_points = []
    for ts, value in points:
        strength = to_strength(value)
        if strength is not None:
            slope_points.append((ts.timestamp(), strength))
    result.slope = fit_slope(slope_points)

    # Rule 7: the raw figure drives the classification; we round only for display.
    result.trend = classify(raw_percent, result.slope)
    result.change_percent = round_percent(raw_percent)

    if result.trend == Trend.UNKNOWN:
        result.notes.append("Could not compute a reliable percentage.")

    return result
