from datetime import date, datetime

from pydantic import BaseModel, Field


class NewsSourceOut(BaseModel):
    source_number: int | None = Field(
        None,
        description=(
            "1-based citation number matching the [n] markers in the "
            "summaries. Null on insights generated before the markers "
            "existed. Sources are returned in this order."
        ),
    )
    headline: str = Field(..., description="Title of the real article we are citing")
    publisher: str | None = Field(None, description="Publisher name, e.g. Reuters")
    source_url: str = Field(..., description="Link to the original article")
    published_at: datetime | None = Field(
        None, description="When the article was published (UTC)"
    )


class NewsInsightOut(BaseModel):
    currency_code: str = Field(..., description="Asset code, e.g. EUR, BTC, XAU")
    currency_name: str = Field(..., description="Display name, e.g. Euro")
    asset_type: str = Field(..., description="fiat, crypto, or metal")
    quote_currency: str = Field("USD", description="Everything is measured against USD")

    prompt_version: int = Field(
        ...,
        description=(
            "Which revision of the generation prompt wrote this card's text. "
            "Internal tracking: it lets a change in wording or source quality "
            "be attributed to a prompt change rather than guessed at. Rows "
            "generated before this was tracked report 1."
        ),
    )

    title: str = Field(..., description="Card headline")
    category: str = Field(..., description="Feed category, e.g. 'Forex News'")
    sentiment: str = Field(
        ...,
        description=(
            "Bullish, Bearish, or Neutral — the model's reading of the "
            "reporting, which looks forward. It may disagree with "
            "`change_percent`, which looks back; the percentage is the measured "
            "fact."
        ),
    )
    short_summary: str = Field(..., description="1-2 sentence card blurb")
    long_summary: str = Field(..., description="Full write-up, our own words")

    change_percent: float | None = Field(
        None,
        description=(
            "Move against USD over the requested window, 2 decimals. Measured "
            "by us from `currency_rates` and recomputed on every request, so it "
            "is never stale even when the summary text is older. Positive means "
            "the asset got stronger. Null for USD (the base) or when we lack "
            "reliable data — show no percentage rather than a zero."
        ),
    )
    trend: str = Field(
        ...,
        description=(
            "stronger, weaker, flat, or unknown — our own slope fit over the "
            "window's rates, not the model's opinion. `base` for USD."
        ),
    )

    start_date: date = Field(..., description="Window start (UTC)")
    end_date: date = Field(..., description="Window end (UTC)")
    days: int = Field(..., description="Length of the measured window in days")
    start_rate: float | None = Field(
        None,
        description=(
            "USD value of 1 unit at the window start, from our own "
            "`currency_rates` table — never from a third party or the model."
        ),
    )
    end_rate: float | None = Field(
        None,
        description=(
            "USD value of 1 unit at the window end, from our own "
            "`currency_rates` table — never from a third party or the model."
        ),
    )

    summary_window_days: int = Field(
        ...,
        description=(
            "The window the written summary was generated for. Equal to `days` "
            "in the normal case; if you request a different window the numbers "
            "update but the text still describes this one."
        ),
    )
    insight_date: date = Field(..., description="Day this insight was generated for")
    created_at: datetime = Field(..., description="When the insight was stored")

    sources: list[NewsSourceOut] = Field(
        default_factory=list,
        description=(
            "Articles backing the summary. Empty means no reporting survived "
            "validation — we never invent a citation to fill the gap."
        ),
    )


class NewsFeedGroup(BaseModel):
    category: str = Field(..., description="Feed category heading")
    items: list[NewsInsightOut] = Field(default_factory=list)


class NewsResponse(BaseModel):
    """One shape for every read, whatever the query asks for.

    `items` and `groups` are mutually exclusive so the payload is never sent
    twice: ask for `group_by=category` and you get `groups`, otherwise `items`.
    A single-currency request is just a one-element `items` list, which keeps
    the frontend off a union type.
    """

    days: int = Field(..., description="Window the percentages were measured over")
    generated_at: datetime = Field(..., description="When this response was built")
    count: int = Field(..., description="Total number of cards in this response")
    items: list[NewsInsightOut] | None = Field(
        None,
        description=(
            "Flat list of cards in fixed order (fiat, crypto, metals, USD). "
            "Null when `group_by=category` was requested."
        ),
    )
    groups: list[NewsFeedGroup] | None = Field(
        None,
        description=(
            "Cards grouped by category, in display order, omitting empty "
            "categories. Only present when `group_by=category`."
        ),
    )


class NewsRefreshResponse(BaseModel):
    """Result of a cron-triggered generation run."""

    status: str = Field(
        ...,
        description=(
            "'completed' for a single currency (run synchronously), or "
            "'accepted' for a full batch (running in the background)."
        ),
    )
    detail: str = Field(..., description="Human-readable outcome")
    days: int = Field(..., description="Window the insights were generated for")
    prompt_version: int = Field(
        ...,
        description=(
            "The prompt revision this run generated with — the value stored on "
            "every row it wrote."
        ),
    )
    currency_code: str | None = Field(
        None, description="Set when a single currency was regenerated"
    )
    created: int | None = Field(None, description="Insights generated this run")
    skipped: int | None = Field(
        None, description="Currencies that already had today's card"
    )
    failed: list[str] | None = Field(
        None, description="Currency codes that could not be generated"
    )


class NewsErrorDetail(BaseModel):
    detail: str = Field(..., description="Human-readable error message")
