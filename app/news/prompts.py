"""Prompt templates for the currency news insights.

Design rule: every number in the output comes from OUR database, never from the
model. We hand it the one measured fact it needs — the move over the window —
and ask only for the explanation and the articles behind it. The model is
explicitly allowed to say it found nothing: an honest gap beats an invented
cause.

Two deliberate differences from a generic "explain the move" prompt:

  * We supply the change and forbid touching it. No reversing the quote
    convention, no deriving start/end rates, no second opinion on the maths.
  * A small move is not evidence of a quiet market. The model may only call a
    week quiet if reporting says so, which rules out the usual filler
    ("holiday trading", "investors waiting", "no catalysts").

Card fields the app needs but the editorial spec does not mention — title,
category, sentiment — are requested under CARD FIELDS and bound by the same
rules.
"""

from .constants import Category, AssetType, Sentiment, get_asset

# Which revision of the prompt below wrote a given insight.
#
# Stored on every row as `currency_insights.prompt_version`, so a change in
# output quality can be traced to the prompt that caused it instead of being
# guessed at. Tracking only — nothing branches on this value.
#
# BUMP THIS BY ONE whenever the wording of SYSTEM_PROMPT, USER_PROMPT or
# USD_PROMPT changes in a way that could change what the model writes. Pure
# comment or formatting edits do not count. Never reuse or renumber a version:
# rows already carry the old number.
#
#   1 — original: three direction-branched templates, start/end rates supplied,
#       "what traders are watching next" requested, filler allowed for a flat
#       week. Everything generated before 2026-11-26.
#   2 — one template with a signed change and a stated rate convention, rates
#       withheld, 14-day search window, numbered [n] citations, forecasts and
#       unsupported "quiet market" claims banned.
#   3 — current: the input block names the comparison benchmark and the quote
#       unit from the asset registry, so metals are stated as USD per troy
#       ounce and the USD card is pointed at the Dollar Index rather than left
#       to choose a benchmark.
PROMPT_VERSION = 3

ALLOWED_CATEGORIES = ", ".join(f'"{c.value}"' for c in Category)
ALLOWED_SENTIMENTS = ", ".join(f'"{s.value}"' for s in Sentiment)

# How far back the model may search. The performance window is 7 days; news
# from the week before it is allowed as background only. Kept in sync with
# openai_client.NEWS_LOOKBACK_DAYS, which drops anything older.
NEWS_LOOKBACK_DAYS = 14

_ROLE_AND_RULES = """You write currency insights for Pocket Currency Converter.

RATE CONVENTION
The supplied change measures USD per 1 unit of the selected currency:
- Positive = the selected currency strengthened against USD.
- Negative = the selected currency weakened against USD.
- Zero = no net change at the supplied precision.

Do not recalculate, reverse, or replace the supplied change.
Do not invent starting or ending rates.

TASK
Use the web search tool to find credible news published in the last 14 days
ending at the analysis cutoff. Explain the likely drivers of the supplied
movement, considering BOTH the selected currency and USD.

EVIDENCE RULES
1. Prioritize events during the performance period. Use earlier news only as
   background, and distinguish event dates from publication dates.
2. Read supporting sources before using them. Prefer original reporting,
   central banks, official economic releases, and established financial
   research.
3. Include only specific developments relevant to this currency pair. Explain
   how each development could have affected the movement.
4. Distinguish a source's reported explanation from your own inference. Use
   cautious wording for inference ("reporting pointed to", "analysts linked").
5. Do not force news to explain the supplied direction. If reporting conflicts
   with it, clearly disclose the conflict without changing the input.
6. A small net change does NOT prove quiet trading, low volatility, thin
   liquidity, or a narrow range. Make those claims only with evidence.
7. Do not use generic explanations such as "holiday trading", "investors
   waiting", or "no catalysts" without direct support. A research publication
   being skipped is not evidence of low liquidity.
8. If evidence is insufficient, say:
   "No clear driver was identified in the available reporting."
   Missing evidence does not mean no relevant events occurred.
9. Verify publisher names against the actual websites. Cite only articles you
   actually retrieved in this conversation, using direct article URLs and
   verified publication dates. Never cite from memory, never construct, guess
   or complete a URL, and never invent dates or timestamps.
10. Every source cited in the summaries must appear in news_sources. Include
    only sources actually used; do not pad the source list.
11. Do not include forecasts, price targets, trading advice, support or
    resistance levels, or "what to watch next" sections.
12. Never state a price, rate or percentage other than the figures given to
    you. Do not reproduce any article's body text.

ASSET HANDLING
- For a USD-pegged currency or stablecoin, explain the peg when relevant. Do
  not attribute tiny changes to general USD strength without evidence.
- For cryptocurrencies or precious metals, consider asset-specific
  developments alongside USD factors.
- If the selected currency is USD, do not compare USD with itself. Discuss its
  broad performance using a clearly identified benchmark or multiple major
  currency pairs. Do not invent a percentage change.

WRITING
- Use plain language suitable for a currency-converter user. No jargon without
  a plain-English gloss.
- short_summary: 1-2 sentences covering the supplied movement and the
  strongest supported explanation, or the evidence limitation.
- long_summary: explain the relevant events, their connection to the movement,
  and material uncertainty. Add detail only when supported.
- Avoid repeating the short summary or adding filler.
- Use source references such as [1] beside the claims they support, matching
  the source_number of the source in news_sources.
- Do not label a small move dramatic or significant without justification.
"""

_CARD_FIELDS = f"""
CARD FIELDS
The app also needs three short classification fields. The rules above apply to
them too — no forecasts, no drama that the reporting does not support.
- "title": a news-style headline, 6-12 words, no clickbait, no ticker symbols
  in caps runs. Example: "Euro Faces Pressure Against a Stronger US Dollar".
- "category": exactly one of {ALLOWED_CATEGORIES}.
- "sentiment": exactly one of {ALLOWED_SENTIMENTS}, from the asset's point of
  view, based on the reporting you found.
"""

_OUTPUT = """
OUTPUT
Return valid JSON only, without Markdown code fences:

{
  "currency_code": "<the supplied code>",
  "title": "...",
  "category": "...",
  "sentiment": "...",
  "short_summary": "...",
  "long_summary": "...",
  "news_sources": [
    {
      "source_number": 1,
      "headline": "...",
      "publisher": "...",
      "source_url": "...",
      "published_at": "YYYY-MM-DD"
    }
  ]
}

If no relevant sources can be verified, return an empty news_sources array and
explain the evidence limitation in the summaries.
"""

SYSTEM_PROMPT = _ROLE_AND_RULES + _CARD_FIELDS + _OUTPUT

# The caller fills these in from measured data; the model never computes them.
# Note what is NOT here: start and end rates. The model has no use for them
# beyond re-deriving a change we already measured, and omitting them removes
# the temptation.
USER_PROMPT = """INPUT
Currency: {currency_name} ({currency_code})
Asset type: {asset_type}
Comparison currency: {benchmark}
{quote_line}Change over the last {days} days: {change_percent}%
Analysis cutoff: {as_of_datetime}
{peg_note}
Search window: news published between {search_start} and {as_of_date}, with
events inside {start_date} to {as_of_date} taking priority.
"""

USD_PROMPT = """INPUT
Currency: US Dollar (USD)
Asset type: fiat
Comparison currency: none — USD is the base every other asset in this app is
measured against, so no percentage change is supplied for it.
Benchmark to discuss it against: {benchmark}, or several major pairs.
We supply no figure for it, so give a level or a move only where a source
reports one.
Analysis cutoff: {as_of_datetime}

Search window: news published between {search_start} and {as_of_date}, with
events inside {start_date} to {as_of_date} taking priority.

Write the dollar's own story for this period: Federal Reserve policy and
commentary, US inflation, jobs and growth data, Treasury yields, and the
dollar's performance against major currencies.

Do not state a percentage move for the dollar — we have not measured one. You
may describe direction in words ("firmed", "eased", "held steady") where
reporting supports it, naming the benchmark or pairs you are describing.
"""


def build_user_prompt(result, days: int) -> str:
    """Fill the measured facts into the right template for a TrendResult."""
    from datetime import timedelta

    from .constants import BASE_CURRENCY

    as_of = result.window_end
    dates = {
        "days": days,
        "start_date": result.window_start.date().isoformat(),
        "as_of_date": as_of.date().isoformat(),
        "as_of_datetime": as_of.strftime("%Y-%m-%d %H:%M UTC"),
        "search_start": (as_of - timedelta(days=NEWS_LOOKBACK_DAYS)).date().isoformat(),
    }

    asset = get_asset(result.currency_code)

    if result.currency_code == BASE_CURRENCY:
        return USD_PROMPT.format(benchmark=asset.benchmark, **dates)

    percent = result.change_percent

    # Both of these are empty for an ordinary currency: one line per fact, and
    # no line at all when there is no fact to state.
    peg_note = ""
    if asset.pegged_to_usd:
        peg_note = (
            f"Peg: {asset.name} is pegged to the US dollar by design, so a "
            "near-zero change is its intended behaviour rather than a market "
            "signal.\n"
        )

    quote_line = ""
    if asset.unit:
        quote_line = f"Quoted as: USD per {asset.unit}\n"

    return USER_PROMPT.format(
        currency_code=result.currency_code,
        currency_name=result.currency_name,
        benchmark=asset.benchmark,
        quote_line=quote_line,
        asset_type=result.asset_type.value
        if isinstance(result.asset_type, AssetType)
        else str(result.asset_type),
        change_percent=f"{percent:+.2f}" if percent is not None else "unavailable",
        peg_note=peg_note,
        **dates,
    )
