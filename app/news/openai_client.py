"""Calls OpenAI for a currency insight, then distrusts what comes back.

Three layers keep the output honest:
  1. Web search is on, and the system prompt forbids citing anything not
     retrieved in the call.
  2. A strict JSON schema, so there is no prose to parse.
  3. This module validates every field before it reaches the database — bad URLs
     and out-of-window articles are dropped, not cleaned up or guessed at.

If nothing survives validation we save the insight with zero sources. We never
invent a link to make a card look complete.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.config import env_var

from .constants import DEFAULT_CATEGORY, Category, Sentiment
from .prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2, 8)

# An article dated a little outside the window can still be the reason for a
# move (reporting lags, timezones), but anything further out is not about this
# window and gets dropped.
PUBLISH_DATE_SLACK = timedelta(days=2)

MAX_SOURCES = 6
MAX_RELATED_ASSETS = 4

_ALLOWED_CATEGORIES = {c.value for c in Category}
_ALLOWED_SENTIMENTS = {s.value for s in Sentiment}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "News-style headline, 6-12 words"},
        "category": {"type": "string", "enum": sorted(_ALLOWED_CATEGORIES)},
        "sentiment": {"type": "string", "enum": sorted(_ALLOWED_SENTIMENTS)},
        "short_summary": {"type": "string", "description": "1-2 sentences, max 40 words"},
        "long_summary": {"type": "string", "description": "2-4 short paragraphs"},
        "related_assets": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Uppercase asset codes the story touches",
        },
        "sources": {
            "type": "array",
            "description": "Only articles actually retrieved via web search. May be empty.",
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "publisher": {"type": "string"},
                    "source_url": {"type": "string"},
                    "published_at": {
                        "type": "string",
                        "description": "ISO 8601 date or datetime",
                    },
                },
                "required": ["headline", "publisher", "source_url", "published_at"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "title",
        "category",
        "sentiment",
        "short_summary",
        "long_summary",
        "related_assets",
        "sources",
    ],
    "additionalProperties": False,
}


class InsightGenerationError(RuntimeError):
    """Raised when we could not get a usable insight out of OpenAI."""


@dataclass
class SourceDraft:
    headline: str
    publisher: str | None
    source_url: str
    published_at: datetime | None


@dataclass
class InsightDraft:
    title: str
    category: str
    sentiment: str
    short_summary: str
    long_summary: str
    related_assets: list[str] = field(default_factory=list)
    sources: list[SourceDraft] = field(default_factory=list)
    dropped_sources: int = 0


def _client():
    """Build an async OpenAI client, failing loudly if the key is missing."""
    if not env_var.OPENAI_API_KEY:
        raise InsightGenerationError(
            "OPENAI_API_KEY is not set; cannot generate news insights."
        )
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise InsightGenerationError(
            "The `openai` package is not installed. Run: pip install openai"
        ) from exc
    return AsyncOpenAI(
        api_key=env_var.OPENAI_API_KEY,
        timeout=env_var.NEWS_OPENAI_TIMEOUT,
    )


def _parse_published_at(raw) -> datetime | None:
    """Parse an ISO date/datetime from the model. Returns None if unparseable."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        # Fall back to a bare date, which is what most publishers expose.
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except (ValueError, IndexError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_sources(
    raw_sources,
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[SourceDraft], int]:
    """Keep only sources with a real URL and a plausible publication date."""
    kept: list[SourceDraft] = []
    seen_urls: set[str] = set()
    dropped = 0

    if not isinstance(raw_sources, list):
        return [], 0

    earliest = window_start - PUBLISH_DATE_SLACK
    latest = window_end + PUBLISH_DATE_SLACK

    for item in raw_sources:
        if not isinstance(item, dict):
            dropped += 1
            continue

        url = (item.get("source_url") or "").strip()
        headline = (item.get("headline") or "").strip()

        # A citation without a working link is not a citation.
        if not url.lower().startswith(("http://", "https://")) or not headline:
            dropped += 1
            continue

        normalised = url.rstrip("/").lower()
        if normalised in seen_urls:
            dropped += 1
            continue

        published_at = _parse_published_at(item.get("published_at"))
        # An unparseable date is tolerated; a date clearly outside the window
        # means the article is about something else.
        if published_at is not None and not (earliest <= published_at <= latest):
            dropped += 1
            continue

        seen_urls.add(normalised)
        publisher = (item.get("publisher") or "").strip() or None
        kept.append(
            SourceDraft(
                headline=headline[:500],
                publisher=publisher[:255] if publisher else None,
                source_url=url,
                published_at=published_at,
            )
        )
        if len(kept) >= MAX_SOURCES:
            break

    return kept, dropped


def _validate_payload(payload: dict, result, window_start, window_end) -> InsightDraft:
    """Coerce the model's JSON into something safe to store."""
    title = (payload.get("title") or "").strip()
    short_summary = (payload.get("short_summary") or "").strip()
    long_summary = (payload.get("long_summary") or "").strip()

    if not title or not short_summary or not long_summary:
        raise InsightGenerationError("Model returned an empty title or summary.")

    # Anything outside our allowed list falls back to the asset-type default
    # rather than creating a new category by accident.
    category = (payload.get("category") or "").strip()
    if category not in _ALLOWED_CATEGORIES:
        category = DEFAULT_CATEGORY.get(result.asset_type, Category.FOREX).value

    sentiment = (payload.get("sentiment") or "").strip()
    if sentiment not in _ALLOWED_SENTIMENTS:
        # Fall back to what we measured ourselves.
        sentiment = result.sentiment.value

    related = payload.get("related_assets")
    related_assets: list[str] = []
    if isinstance(related, list):
        for code in related:
            if isinstance(code, str) and code.strip():
                cleaned = code.strip().upper()[:12]
                if cleaned not in related_assets:
                    related_assets.append(cleaned)
    if result.currency_code not in related_assets:
        related_assets.insert(0, result.currency_code)
    related_assets = related_assets[:MAX_RELATED_ASSETS]

    sources, dropped = _validate_sources(
        payload.get("sources"), window_start, window_end
    )
    if dropped:
        logger.info(
            "Dropped %s unusable source(s) for %s", dropped, result.currency_code
        )

    return InsightDraft(
        title=title[:300],
        category=category,
        sentiment=sentiment,
        short_summary=short_summary,
        long_summary=long_summary,
        related_assets=related_assets,
        sources=sources,
        dropped_sources=dropped,
    )


async def _call_openai(client, user_prompt: str, tool_type: str) -> str:
    """One Responses API call with web search and a strict JSON schema."""
    response = await client.responses.create(
        model=env_var.OPENAI_MODEL,
        instructions=SYSTEM_PROMPT,
        input=user_prompt,
        tools=[{"type": tool_type}],
        text={
            "format": {
                "type": "json_schema",
                "name": "currency_insight",
                "schema": RESPONSE_SCHEMA,
                "strict": True,
            }
        },
    )
    return response.output_text


async def generate_insight(result, days: int) -> InsightDraft:
    """Ask OpenAI why an asset moved, and return only what survives validation.

    Retries transient failures; raises InsightGenerationError when every attempt
    fails, so the caller can skip this currency without losing the others.
    """
    client = _client()
    user_prompt = build_user_prompt(result, days)

    # The tool was named `web_search_preview` before it was promoted; try the
    # current name first and fall back so either SDK version works.
    tool_types = ["web_search", "web_search_preview"]
    last_error: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        tool_type = tool_types[min(attempt, len(tool_types) - 1)]
        try:
            raw = await _call_openai(client, user_prompt, tool_type)
            payload = json.loads(raw)
            draft = _validate_payload(
                payload, result, result.window_start, result.window_end
            )
            if not draft.sources:
                logger.warning(
                    "%s: no verifiable sources survived validation; saving the "
                    "insight without citations.",
                    result.currency_code,
                )
            return draft

        except json.JSONDecodeError as exc:
            last_error = exc
            logger.warning(
                "%s: OpenAI returned unparseable JSON (attempt %s/%s)",
                result.currency_code, attempt + 1, MAX_ATTEMPTS,
            )
        except InsightGenerationError as exc:
            last_error = exc
            logger.warning(
                "%s: invalid insight payload (attempt %s/%s): %s",
                result.currency_code, attempt + 1, MAX_ATTEMPTS, exc,
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "%s: OpenAI call failed (attempt %s/%s): %s",
                result.currency_code, attempt + 1, MAX_ATTEMPTS, exc,
            )

        if attempt < MAX_ATTEMPTS - 1:
            await asyncio.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])

    raise InsightGenerationError(
        f"Could not generate an insight for {result.currency_code}: {last_error}"
    )
