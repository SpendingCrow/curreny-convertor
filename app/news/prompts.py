"""Prompt templates for the currency news insights.

Design rule: every number in the output comes from OUR database, never from the
model. We hand it the measured facts and ask only for the explanation and the
articles behind it. The model is explicitly allowed to say it found nothing —
an honest gap beats an invented cause.
"""

from .constants import PEGGED_TO_USD, Category, AssetType, Sentiment

ALLOWED_CATEGORIES = ", ".join(f'"{c.value}"' for c in Category)
ALLOWED_SENTIMENTS = ", ".join(f'"{s.value}"' for s in Sentiment)

SYSTEM_PROMPT = f"""You are a financial news analyst writing short, factual market \
explainers for a currency app.

HARD RULES — these override everything else:
1. Use the web search tool to find real, published news articles before you answer. \
Only cite an article you actually retrieved in this conversation. Never cite an \
article from memory, and never construct, guess or complete a URL.
2. Never state a price, rate or percentage other than the figures given to you below. \
The measured numbers come from the caller's own database and are already correct.
3. If your search does not turn up reporting that explains the move, say so plainly in \
the summaries and return an empty "sources" array. "No single clear driver was \
reported this week" is a correct and acceptable answer. Do not fill the gap with \
plausible-sounding speculation.
4. Do not reproduce any article's body text. Write your own words and link to the source.
5. Attribute causes only where reporting supports them. Where the link is uncertain, \
write "analysts linked" or "reporting pointed to" rather than asserting it as fact.

STYLE:
- "title": a news-style headline, 6-12 words, no clickbait, no ticker symbols in caps \
runs. Example: "Euro Faces Pressure Against a Stronger US Dollar".
- "short_summary": 1-2 sentences, max 40 words. This is the card blurb.
- "long_summary": 2-4 short paragraphs explaining the drivers and what to watch next.
- "category": exactly one of {ALLOWED_CATEGORIES}.
- "sentiment": exactly one of {ALLOWED_SENTIMENTS}, from the asset's point of view.
- Write for a general reader. No jargon without a plain-English gloss.
"""

# The caller fills these in from measured data; the model never computes them.
_FACTS_BLOCK = """MEASURED FACTS (from our own rate database — treat as ground truth):
- Asset: {currency_name} ({currency_code}), type: {asset_type}
- Measured against: USD
- Window: {start_date} to {end_date} ({days} days)
- Value at window start: 1 {currency_code} = {start_rate} USD
- Value at window end:   1 {currency_code} = {end_rate} USD
- Change: {change_percent}% ({direction_word})
- Trend across the window: {trend}
"""

STRONGER_PROMPT = (
    _FACTS_BLOCK
    + """
{currency_name} STRENGTHENED {abs_percent}% against the US dollar over this window.

Search for news published between {start_date} and {end_date} that explains the gain. \
Look for: central bank decisions and guidance, inflation and jobs data, trade or \
commodity flows, political events, and anything specific to {currency_name}. Also \
consider whether the move was really about dollar weakness rather than \
{currency_name} strength — say so if the reporting points that way.

Explain what drove the gain, and what traders are watching next.
"""
)

WEAKER_PROMPT = (
    _FACTS_BLOCK
    + """
{currency_name} WEAKENED {abs_percent}% against the US dollar over this window.

Search for news published between {start_date} and {end_date} that explains the fall. \
Look for: central bank decisions and guidance, inflation and jobs data, trade or \
commodity flows, political events, and anything specific to {currency_name}. Also \
consider whether the move was really about dollar strength rather than \
{currency_name} weakness — say so if the reporting points that way.

Explain what drove the decline, and what traders are watching next.
"""
)

FLAT_PROMPT = (
    _FACTS_BLOCK
    + """
{currency_name} barely moved against the US dollar over this window ({change_percent}%).

Search for news published between {start_date} and {end_date} covering \
{currency_name}. Explain why it was quiet: offsetting forces, thin holiday trading, \
markets waiting on an upcoming data release or central bank meeting, or simply no \
major catalyst. {peg_note}

A quiet week is a legitimate story. Do not manufacture drama.
"""
)

USD_COMMENTARY_PROMPT = """MEASURED FACTS:
- Asset: US Dollar (USD)
- Window: {start_date} to {end_date} ({days} days)
- No percentage is calculated for USD: it is the base currency every other asset in \
this app is measured against, so USD against USD is always zero.

Write the dollar's own story for this window. Search for news published between \
{start_date} and {end_date} on the US dollar: Federal Reserve policy and commentary, \
US inflation, jobs and growth data, Treasury yields, and the dollar's performance \
against major currencies.

Do not state a percentage move for the dollar — we have not measured one. You may \
describe direction in words ("firmed", "eased", "held steady") where reporting \
supports it.

Suggested category: "Central Bank Updates" if the week was Fed-driven, otherwise \
"Forex News" or "Economic News". Set "sentiment" from the dollar's point of view.
"""


def _direction_word(trend: str) -> str:
    return {
        "stronger": "stronger against USD",
        "weaker": "weaker against USD",
        "flat": "broadly unchanged against USD",
    }.get(trend, "unclear")


def build_user_prompt(result, days: int) -> str:
    """Pick the right template for a TrendResult and fill in the measured facts."""
    from .constants import BASE_CURRENCY

    start_date = result.window_start.date().isoformat()
    end_date = result.window_end.date().isoformat()

    if result.currency_code == BASE_CURRENCY:
        return USD_COMMENTARY_PROMPT.format(
            start_date=start_date, end_date=end_date, days=days
        )

    percent = result.change_percent
    facts = {
        "currency_code": result.currency_code,
        "currency_name": result.currency_name,
        "asset_type": result.asset_type.value
        if isinstance(result.asset_type, AssetType)
        else str(result.asset_type),
        "start_date": start_date,
        "end_date": end_date,
        "days": days,
        "start_rate": f"{result.start_rate:,.6g}" if result.start_rate else "unavailable",
        "end_rate": f"{result.end_rate:,.6g}" if result.end_rate else "unavailable",
        "change_percent": f"{percent:.2f}" if percent is not None else "unavailable",
        "abs_percent": f"{abs(percent):.2f}" if percent is not None else "unavailable",
        "direction_word": _direction_word(result.trend),
        "trend": result.trend,
    }

    if result.trend == "stronger":
        return STRONGER_PROMPT.format(**facts)
    if result.trend == "weaker":
        return WEAKER_PROMPT.format(**facts)

    peg_note = ""
    if result.currency_code in PEGGED_TO_USD:
        peg_note = (
            f"Note: {result.currency_name} is a stablecoin pegged to the US dollar, so "
            "a near-zero move is the intended behaviour. Focus on whether the peg held, "
            "reserve backing, regulation and issuance volumes."
        )
    return FLAT_PROMPT.format(peg_note=peg_note, **facts)
