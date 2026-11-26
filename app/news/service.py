"""Generates, stores and serves the currency news insights.

Split of responsibility:
  - The *text* (title, summaries, sources) is generated once a day by the
    background job and stored, so no user request ever waits on OpenAI.
  - The *numbers* are recomputed from `currency_rates` on every read, so a card
    always shows the latest measured move rather than a stale snapshot.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import env_var
from app.utils.performance import Trend, round_rate

from .constants import (
    ALL_SUBJECTS,
    BASE_CURRENCY,
    CATEGORY_ORDER,
    get_meta,
    is_supported,
)
from .models import CurrencyInsight, NewsSource
from .openai_client import InsightGenerationError, generate_insight
from .prompts import PROMPT_VERSION
from .schema import (
    NewsFeedGroup,
    NewsInsightOut,
    NewsResponse,
    NewsSourceOut,
)
from .trend import compute_trend

logger = logging.getLogger(__name__)

MAX_WINDOW_DAYS = 365


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _window(days: int, end: datetime | None = None) -> tuple[datetime, datetime]:
    window_end = end or _now()
    return window_end - timedelta(days=days), window_end


# --------------------------------------------------------------------------
# Generation (background job / forced refresh)
# --------------------------------------------------------------------------


async def generate_and_store(
    db: AsyncSession,
    code: str,
    days: int | None = None,
    force: bool = False,
) -> tuple[CurrencyInsight, bool]:
    """Generate one insight and save it. Replaces today's row when `force`.

    Returns `(insight, generated)`. `generated` is False when today's card
    already existed and was kept, which the caller cannot infer from
    `created_at`: that column defaults to Postgres `now()`, which is the
    *transaction* timestamp, and the session's transaction opens before the
    caller starts timing.
    """
    code = (code or "").strip().upper()
    if not is_supported(code):
        raise ValueError(
            f"Unsupported currency code: {code!r}. Pass one code from: "
            f"{', '.join(ALL_SUBJECTS)}."
        )

    days = days or env_var.NEWS_WINDOW_DAYS
    window_start, window_end = _window(days)
    insight_date = window_end.date()

    existing = (
        await db.execute(
            select(CurrencyInsight).where(
                CurrencyInsight.currency_code == code,
                CurrencyInsight.insight_date == insight_date,
            )
        )
    ).scalar_one_or_none()

    if existing and not force:
        # A prompt bump does not regenerate on its own: that would re-spend on
        # every currency the moment the constant changes. Flagged here so a
        # stale-version row is visible in the logs. Rewriting it takes
        # force=True, which is what POST /news-history/refresh does when it is
        # given a single currency_code.
        if (existing.prompt_version or 1) != PROMPT_VERSION:
            logger.info(
                "%s already has an insight for %s, written by prompt v%s "
                "(current is v%s); skipping. Regenerate it with POST "
                "/news-history/refresh?currency_code=%s.",
                code, insight_date, existing.prompt_version or 1,
                PROMPT_VERSION, code,
            )
        else:
            logger.info(
                "%s already has an insight for %s; skipping.", code, insight_date
            )
        return existing, False

    result = await compute_trend(db, code, window_start, window_end)

    # No usable rate data means we have nothing truthful to ask about.
    if result.trend == Trend.UNKNOWN:
        raise InsightGenerationError(
            f"{code}: no usable rate data in the last {days} days; skipping."
        )

    draft = await generate_insight(result, days)

    # Re-read before writing. The call above takes a minute or more, and the
    # daily job may have stored this very card while we waited — the row we
    # checked at the top is not evidence of anything by now. Without this the
    # INSERT collides with uq_insight_currency_date and the caller gets a 500
    # after the call has already been paid for.
    existing = (
        await db.execute(
            select(CurrencyInsight).where(
                CurrencyInsight.currency_code == code,
                CurrencyInsight.insight_date == insight_date,
            )
        )
    ).scalar_one_or_none()

    if existing and not force:
        # Another generator got there first. Keep its card rather than
        # overwrite it: whoever asked for this one did not ask to replace.
        logger.info(
            "%s: an insight for %s was stored while we were generating; "
            "keeping the stored one.",
            code, insight_date,
        )
        return existing, False

    if existing:
        # Cascade clears the old sources with it.
        await db.execute(
            delete(CurrencyInsight).where(CurrencyInsight.id == existing.id)
        )
        await db.flush()

    insight = CurrencyInsight(
        currency_code=code,
        insight_date=insight_date,
        change_percent=result.change_percent,
        prompt_version=PROMPT_VERSION,
        title=draft.title,
        category=draft.category,
        sentiment=draft.sentiment,
        short_summary=draft.short_summary,
        long_summary=draft.long_summary,
    )
    insight.sources = [
        NewsSource(
            source_number=s.source_number,
            headline=s.headline,
            publisher=s.publisher,
            source_url=s.source_url,
            published_at=s.published_at,
        )
        for s in draft.sources
    ]

    db.add(insight)
    await db.commit()
    await db.refresh(insight)

    logger.info(
        "%s insight stored (prompt v%s, %s, %s%%, %s source(s))",
        code, PROMPT_VERSION, result.trend,
        result.change_percent if result.change_percent is not None else "n/a",
        len(draft.sources),
    )
    return insight, True


async def run_daily_generation(db: AsyncSession, days: int | None = None) -> dict:
    """Generate an insight for every asset in ALL_SUBJECTS, USD included.

    One failure never stops the rest — each subject is independent.
    """
    days = days or env_var.NEWS_WINDOW_DAYS
    created, skipped, failed = 0, 0, []

    for code in ALL_SUBJECTS:
        try:
            _, generated = await generate_and_store(db, code, days=days)
            if generated:
                created += 1
            else:
                skipped += 1
        except Exception as exc:
            await db.rollback()
            failed.append(code)
            logger.error("News insight failed for %s: %s", code, exc)

    summary = {"created": created, "skipped": skipped, "failed": failed, "days": days}
    logger.info("News generation run finished: %s", summary)
    return summary


# --------------------------------------------------------------------------
# Reading (the endpoints)
# --------------------------------------------------------------------------


async def _latest_insights(
    db: AsyncSession, codes: list[str] | None = None
) -> dict[str, CurrencyInsight]:
    """The newest stored insight per currency, keyed by code."""
    stmt = (
        select(CurrencyInsight)
        .order_by(
            CurrencyInsight.currency_code.asc(),
            CurrencyInsight.insight_date.desc(),
            CurrencyInsight.id.desc(),
        )
        .distinct(CurrencyInsight.currency_code)
    )
    if codes:
        stmt = stmt.where(CurrencyInsight.currency_code.in_(codes))

    rows = (await db.execute(stmt)).scalars().all()
    return {row.currency_code: row for row in rows}


async def _to_out(
    db: AsyncSession, insight: CurrencyInsight, days: int
) -> NewsInsightOut:
    """Join a stored insight to a freshly measured move over `days`."""
    window_start, window_end = _window(days)
    result = await compute_trend(db, insight.currency_code, window_start, window_end)
    name, asset_type = get_meta(insight.currency_code)

    return NewsInsightOut(
        currency_code=insight.currency_code,
        currency_name=name,
        asset_type=asset_type.value,
        quote_currency=BASE_CURRENCY,
        prompt_version=insight.prompt_version or 1,
        title=insight.title,
        category=insight.category,
        sentiment=insight.sentiment,
        short_summary=insight.short_summary,
        long_summary=insight.long_summary,
        change_percent=result.change_percent,
        trend=result.trend,
        start_date=window_start.date(),
        end_date=window_end.date(),
        days=days,
        start_rate=round_rate(result.start_rate),
        end_rate=round_rate(result.end_rate),
        summary_window_days=env_var.NEWS_WINDOW_DAYS,
        insight_date=insight.insight_date,
        created_at=insight.created_at,
        sources=[
            NewsSourceOut(
                source_number=s.source_number,
                headline=s.headline,
                publisher=s.publisher,
                source_url=s.source_url,
                published_at=s.published_at,
            )
            for s in insight.sources
        ],
    )


def _validate_days(days: int) -> int:
    if days < 1 or days > MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"days must be between 1 and {MAX_WINDOW_DAYS}.",
        )
    return days


GROUP_BY_CATEGORY = "category"
ALLOWED_GROUP_BY = {GROUP_BY_CATEGORY}


def _group_by_category(items: list[NewsInsightOut]) -> list[NewsFeedGroup]:
    """Bucket cards into categories in display order, dropping empty ones."""
    by_category: dict[str, list[NewsInsightOut]] = {}
    for item in items:
        by_category.setdefault(item.category, []).append(item)

    groups = []
    ordered = [c.value for c in CATEGORY_ORDER]
    # Known categories first, in the designed order; anything unexpected after.
    for category in ordered + [c for c in by_category if c not in ordered]:
        bucket = by_category.get(category)
        if bucket:
            groups.append(NewsFeedGroup(category=category, items=bucket))
    return groups


async def get_news(
    db: AsyncSession,
    days: int,
    currency_code: str | None = None,
    group_by: str | None = None,
) -> NewsResponse:
    """The single read path: whole feed, one currency, grouped or flat.

    A single-currency request returns a one-element `items` list rather than a
    bare object, so the response shape never changes with the query.
    """
    _validate_days(days)

    if group_by is not None and group_by not in ALLOWED_GROUP_BY:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported group_by: {group_by}. "
                f"Allowed: {', '.join(sorted(ALLOWED_GROUP_BY))}."
            ),
        )

    if currency_code and currency_code.strip():
        code = currency_code.strip().upper()
        if not is_supported(code):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported currency code: {code!r}. Pass one code from: "
                    f"{', '.join(ALL_SUBJECTS)}."
                ),
            )
        insight = (await _latest_insights(db, [code])).get(code)
        if insight is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No news insight stored for {code} yet. The generation "
                    "job may not have run."
                ),
            )
        items = [await _to_out(db, insight, days)]
    else:
        insights = await _latest_insights(db)
        items = [
            await _to_out(db, insights[code], days)
            for code in ALL_SUBJECTS
            if code in insights
        ]

    response = NewsResponse(
        days=days, generated_at=_now(), count=len(items), items=items
    )
    if group_by == GROUP_BY_CATEGORY:
        # Grouped and flat are the same payload; send only what was asked for.
        response.groups = _group_by_category(items)
        response.items = None
    return response
