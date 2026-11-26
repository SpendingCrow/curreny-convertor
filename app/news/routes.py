"""Two endpoints, split by what they cost.

  GET  /news-history          — free, fast, reads the database. Public traffic.
  POST /news-history/refresh  — paid, slow, calls OpenAI. Scheduler only.

The split is deliberate and not cosmetic: a GET must be safe to repeat, and a
browser prefetch or CDN retry must never be able to spend money or overwrite a
stored card. The two also carry different credentials, so the key you ship to
the frontend cannot trigger generation.

Both endpoints take their rates from our own `currency_rates` table. Nothing in
this module fetches an exchange rate from a third party, and the language model
is never asked for one — it receives our measured figures as ground truth and
may not state any others. OpenAI supplies words; the database supplies numbers.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import env_var
from app.utils.database import async_session
from app.utils.security import verify_api_key, verify_cron_key

from .constants import ALL_SUBJECTS
from .openai_client import InsightGenerationError
from .prompts import PROMPT_VERSION
from .schema import (
    NewsErrorDetail,
    NewsRefreshResponse,
    NewsResponse,
)
from .service import (
    GROUP_BY_CATEGORY,
    generate_and_store,
    get_news,
    run_daily_generation,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/news-history", tags=["news history"])


async def _db():
    async with async_session() as session:
        yield session


def _days_query() -> int:
    return env_var.NEWS_WINDOW_DAYS


_DAYS = Query(
    default_factory=_days_query,
    ge=1,
    le=365,
    description=(
        "How many days back to measure the move. Defaults to the window the "
        "summaries are written for (7). Changing it updates the numbers; the "
        "written text still describes the generated window, reported back as "
        "`summary_window_days`."
    ),
)

_SUPPORTED = ", ".join(ALL_SUBJECTS)


@router.get(
    "",
    response_model=NewsResponse,
    response_model_exclude_none=False,
    status_code=200,
    summary="Currency news cards — whole feed, grouped, or a single currency",
    description=(
        "The one read endpoint for the news feature. What you get back depends "
        "on the query:\n\n"
        "- `GET /news-history` — every stored card as a flat list, in fixed "
        "order (fiat, then crypto, then metals, then USD).\n"
        "- `GET /news-history?group_by=category` — the same cards grouped into "
        "Forex News, Cryptocurrency News, Precious Metals News and so on, in "
        "display order. Empty categories are omitted. **Render your news page "
        "from this.**\n"
        "- `GET /news-history?currency_code=EUR` — one card, returned as a "
        "one-element `items` list so the response shape never changes.\n\n"
        "`items` and `groups` are mutually exclusive: exactly one is populated "
        "and the other is `null`, so the payload is never sent twice.\n\n"
        "### Where each field comes from\n\n"
        "A card is two things from two different sources, and it matters which "
        "you trust for what:\n\n"
        "| Fields | Source | Freshness |\n"
        "| --- | --- | --- |\n"
        "| `change_percent`, `trend`, `start_rate`, `end_rate` | **our own "
        "`currency_rates` table** | recomputed on **every** request |\n"
        "| `title`, `short_summary`, `long_summary`, `sources` | news articles, "
        "written once by the daily job | as of `created_at` |\n\n"
        "No exchange rate is ever fetched from a third party at read time, and "
        "the language model is never asked for a price — it is given our "
        "measured figures as ground truth and forbidden from stating any "
        "others. So the numbers on a card always reflect the latest rates in "
        "our database, even when the text beside them was written earlier.\n\n"
        "### Reading `change_percent`\n\n"
        "The asset's move against USD over the window, as a plain 2-decimal "
        "number. Positive means the asset got **stronger** against the dollar "
        "(show green); negative means it got weaker (show red). `null` means "
        "either no reliable rate data, or that the card is the USD card — USD "
        "is the base currency, so it has no percentage. Show no percentage at "
        "all rather than a zero.\n\n"
        "### `days` vs `summary_window_days`\n\n"
        "`days` changes the window the **numbers** are measured over. The text "
        "still describes the window it was generated for, which comes back as "
        "`summary_window_days`. When the two differ, trust the number and treat "
        "the prose as context for the default window.\n\n"
        f"Supported codes: {_SUPPORTED}."
    ),
    responses={
        200: {"model": NewsResponse},
        400: {
            "model": NewsErrorDetail,
            "description": "Unsupported currency, bad group_by, or invalid window",
        },
        401: {"model": NewsErrorDetail, "description": "Invalid API key"},
        404: {
            "model": NewsErrorDetail,
            "description": "Named currency has no insight generated yet",
        },
        422: {"description": "X-API-Key header missing, or malformed query params"},
    },
)
async def read_news(
    currency_code: str | None = Query(
        None,
        min_length=1,
        max_length=32,
        example="EUR",
        description=(
            "**One** asset code — for example `EUR`. Omit for the whole feed. "
            "Anything unrecognised returns 400 with the supported list, so a "
            "typo is never a schema error."
        ),
    ),
    group_by: str | None = Query(
        None,
        example=GROUP_BY_CATEGORY,
        description=(
            "Set to `category` to group the cards for the news page. Omit for "
            "a flat list."
        ),
    ),
    days: int = _DAYS,
    db: AsyncSession = Depends(_db),
    _: str = Depends(verify_api_key),
):
    return await get_news(
        db, days=days, currency_code=currency_code, group_by=group_by
    )


async def _run_batch_in_background(days: int) -> None:
    """Full generation run on its own session, detached from the request."""
    try:
        async with async_session() as db:
            summary = await run_daily_generation(db, days=days)
        logger.info("✅ Cron-triggered news generation finished: %s", summary)
    except Exception as exc:
        logger.error("❌ Cron-triggered news generation failed: %s", exc)


@router.post(
    "/refresh",
    response_model=NewsRefreshResponse,
    status_code=200,
    summary="Generate news insights (scheduler only)",
    description=(
        "Writes the **text** of the news cards. **Machine endpoint — "
        "authenticates with `X-Cron-Key`, not `X-API-Key`**, so the key your "
        "frontend ships cannot spend money here. Disabled with a 503 if "
        "`CRON_KEY` is not configured on the server.\n\n"
        "- **No `currency_code`** — the daily batch for all currencies. Runs in "
        "the background and returns `202 accepted` immediately, because a full "
        "pass takes many minutes and would otherwise time out. Watch the logs "
        "for the outcome.\n"
        "- **With `currency_code`** — regenerates that one card synchronously "
        "and returns the result. Takes 60–120 seconds, since it waits on a live "
        "OpenAI call with web search.\n\n"
        "Already-generated cards for today are skipped unless a single "
        "`currency_code` is given, which always forces a fresh call. Point "
        "your scheduler at the no-argument form once a day.\n\n"
        "### What this does, step by step\n\n"
        "1. Reads the last `days` of rates for the asset **from our own "
        "`currency_rates` table**. No third-party rate API is called here or "
        "anywhere else in this module.\n"
        "2. Fits a slope across those rates to classify the move as "
        "`stronger`, `weaker` or `flat`, so one spike cannot look like a "
        "trend. Everything is measured against USD.\n"
        "3. Picks the prompt matching that direction and injects **our** "
        "measured figures into it as ground truth. The model is explicitly "
        "forbidden from stating any price or percentage we did not give it, so "
        "it never supplies rate data.\n"
        "4. Calls OpenAI with web search, which must find real articles "
        "published inside the window before answering, and returns a fixed "
        "JSON structure.\n"
        "5. Validates every citation. Non-HTTP links, duplicates and articles "
        "dated outside the window are dropped rather than repaired. A card "
        "with no usable sources is stored with none — a citation is never "
        "invented to make a card look complete.\n"
        "6. Stores the text and its sources. The percentage is **not** frozen "
        "here: `GET /news-history` recomputes it from the rate table on every "
        "read.\n\n"
        "Skipping this endpoint entirely is safe — the app also runs the same "
        "job in-process once a day. Set `NEWS_INSIGHT_INTERVAL=0` to turn that "
        "off when an external scheduler owns generation."
    ),
    responses={
        200: {"model": NewsRefreshResponse, "description": "Single currency regenerated"},
        202: {"model": NewsRefreshResponse, "description": "Batch run accepted"},
        400: {"model": NewsErrorDetail, "description": "Unsupported currency"},
        401: {"model": NewsErrorDetail, "description": "Invalid cron key"},
        422: {"description": "X-Cron-Key header missing"},
        500: {
            "model": NewsErrorDetail,
            "description": (
                "Unexpected failure (database or bug). The detail names the "
                "exception; the traceback is in the server logs."
            ),
        },
        503: {
            "model": NewsErrorDetail,
            "description": "CRON_KEY not configured, or generation failed",
        },
    },
)
async def refresh_news(
    response: Response,
    currency_code: str | None = Query(
        None,
        min_length=1,
        max_length=32,
        example="EUR",
        description=(
            "Regenerate only **one** asset, synchronously — for example `EUR`. "
            "Omit to run the full daily batch in the background."
        ),
    ),
    days: int = _DAYS,
    db: AsyncSession = Depends(_db),
    _: str = Depends(verify_cron_key),
):
    if currency_code is None:
        # Fire and forget: a full pass is one OpenAI call per subject and will
        # outlive any sensible HTTP timeout.
        asyncio.create_task(_run_batch_in_background(days))
        response.status_code = status.HTTP_202_ACCEPTED
        return NewsRefreshResponse(
            status="accepted",
            detail=(
                f"Generation started in the background for {len(ALL_SUBJECTS)} "
                "subjects. Check the server logs for the result."
            ),
            days=days,
            prompt_version=PROMPT_VERSION,
        )

    code = currency_code.strip().upper()
    try:
        _, generated = await generate_and_store(db, code, days=days, force=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except InsightGenerationError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        # Anything reaching here is a bug or an infrastructure fault, not a bad
        # request: the OpenAI path already converts its own failures into
        # InsightGenerationError. FastAPI would return a bare 500 with an empty
        # body, which tells the caller nothing and leaves the cause only in the
        # logs. Name the failure in the response and keep the traceback.
        await db.rollback()
        logger.exception("Unexpected failure generating the %s insight", code)
        raise HTTPException(
            status_code=500,
            detail=(
                f"Unexpected {type(exc).__name__} while generating the {code} "
                f"insight: {exc}"
            ),
        )

    return NewsRefreshResponse(
        status="completed",
        detail=(
            f"Regenerated the {code} insight with prompt v{PROMPT_VERSION}."
            if generated
            else (
                f"Kept the stored {code} insight: another generation run wrote "
                "it while this one was waiting on OpenAI."
            )
        ),
        days=days,
        prompt_version=PROMPT_VERSION,
        currency_code=code,
        created=1 if generated else 0,
        skipped=0 if generated else 1,
        failed=[],
    )
