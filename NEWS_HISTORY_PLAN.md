# News History Endpoint — Plan

**Goal:** For each of our 19 tracked currencies, show *why* it moved up or down against USD
over a time window. We read the trend from our own `currency_rates` table, then ask OpenAI
for the reason, backed by real news articles. The result is stored in the DB and served as a
news feed grouped by category.

**Decisions locked in:**
- Daily background job generates the insights; the endpoint only reads the DB.
- Model: **gpt-5-nano with web search** so the cited articles are real, not recalled.
- **19 currencies:** 12 fiat + 5 crypto + 2 metals. USD excluded from the percentages
  (it is the base) but it still gets one commentary article in the Forex feed.
- `change_percent` is a plain 2-decimal number, e.g. `1.08`, `-0.42`, `0.00`.
- `image_url` stays null for now; the frontend uses its own per-category icons.

---

## Build status — implemented 2026-11-19

All ten steps are written and verified. **68 tests pass**, the app imports and
builds its OpenAPI schema, and the migration applies and rolls back cleanly.

| Step | State |
|---|---|
| 1. `app/utils/performance.py` + tests | done |
| 2. Models + Alembic migration | done — applied and rolled back against a scratch DB |
| 3. `constants.py` + `trend.py` | done — verified against seeded rate rows |
| 4. Config keys + `openai` dependency | done |
| 5. `prompts.py` + `openai_client.py` | written; **not yet run against the live API** |
| 6. `service.py` | done |
| 7. `routes.py` + wired into `main.py` | done — 4 endpoints in the OpenAPI schema |
| 8. Daily background task | done — 4th task in `main.py` |
| 9. `history` on the shared percent service | done — its numbers are unchanged |
| 10. README + Swagger docs | done |

**Two things still outstanding:**

1. **The migration has not been run against your real Postgres.** Docker was not
   running on this machine. Run `make migrate-upgrade` (or `alembic upgrade head`
   inside the container) to create the two tables.
2. **No live OpenAI call has been made.** `OPENAI_API_KEY` is empty in `.env`.
   Until it is set, the daily job logs a warning and skips — everything else runs
   normally. First smoke test once the key is in:
   `curl -X POST -H "X-API-Key: $API_KEY" "http://localhost:8000/news-history/refresh?currency_code=EUR"`

### One change from the plan above

The endpoints take a `days` parameter, so the percentage is recomputed live from
`currency_rates` on every read while the written summary stays as generated. The
response therefore carries **`summary_window_days`** alongside `days`: when they
match (the normal case) the text and the number describe the same window; when
you ask for a different window, the number follows your request and
`summary_window_days` tells you the text did not. This keeps rule 3 — always the
latest available rate — without ever letting a card's words contradict its number.

### Second change — collapsed to two endpoints

The three read endpoints originally planned (`/feed`, `/all`, and single-currency)
returned **the same data** differing only in formatting, so they became one
endpoint with query parameters. See section 1.

The refresh endpoint stayed separate, because merging it into the GET would be a
real bug: a GET must be safe to repeat, and a browser prefetch, CDN retry or
double-click must never be able to spend OpenAI credit or overwrite a stored
card. It also moved from `X-API-Key` to a dedicated **`X-Cron-Key`**, so the key
shipped to the frontend cannot trigger paid generation.


---

## 1. What we are building

A new module `app/news/` with **two endpoints, split by what they cost**.

**Reading — free, fast, public traffic (`X-API-Key`):**

```
GET /news-history                          → every card, flat
GET /news-history?group_by=category        → grouped for the news page (the main one)
GET /news-history?currency_code=EUR        → one card, as a one-element items list
```

All accept `days` (1–365, default 7). One response model covers all three:
`items` and `groups` are mutually exclusive, so the payload is never duplicated,
and a single-currency request is still a list — the frontend reads `items[0]`
and never needs a union type.

`group_by=category` returns exactly the shape the mockup shows: groups — Forex,
Cryptocurrency, Precious Metals — each holding cards with a title, category,
date, summary and the source links underneath. Empty categories are omitted.

**Generating — paid, slow, scheduler only (`X-Cron-Key`):**

```
POST /news-history/refresh                 → full daily batch, background, 202
POST /news-history/refresh?currency_code=EUR → one card, synchronous, 200
```

Fails closed: if `CRON_KEY` is unset the endpoint returns `503` rather than
opening to anyone holding the public key. The batch form returns immediately
because a full pass is ~20 OpenAI calls at 60–120s each.

---

## 2. Database — two tables, the ERD extended

The diagram you drew stays. We add the display fields from your field list on top of it,
and keep the original ERD column names so the diagram still reads true.

### Where each of your fields lives

| your field | lands as | table |
|---|---|---|
| title | `title` | currency_insights |
| slug | `slug` | currency_insights |
| category | `category` | currency_insights |
| summary | `short_summary` *(ERD name kept)* | currency_insights |
| content | `long_summary` *(ERD name kept)* | currency_insights |
| image_url | `image_url` | currency_insights |
| sentiment | `sentiment` | currency_insights |
| related_assets | `related_assets` | currency_insights |
| source_name | `publisher` *(ERD name kept)* | news_sources |
| source_url | `source_url` | news_sources |
| published_at | `published_at` | news_sources |

**Why the display fields go on `currency_insights`, not `news_sources`:** look at your
mockup — one card is *"Gold Prices Drop as Stronger Dollar Weighs on Precious Metals /
Metals / November 18, 2026 / summary..."* with *"Reuters+1"* underneath. The card is the
insight we generated; "Reuters+1" is the list of articles backing it. So the card fields
(title, category, sentiment, summary) belong to the insight, and `news_sources` stays what
the ERD says it is: the real articles we cite. That also keeps one card from needing a
duplicated title on every source row.

### `currency_insights`

| column | type | notes |
|---|---|---|
| id | integer, PK | auto |
| currency_code | varchar(10) | `EUR`, `BTC`, `XAU`, `USD` |
| insight_date | date | the day the insight was generated for |
| change_percent | numeric(10, 2), **nullable** | % vs USD, 2 decimals. Null for USD. |
| title | text | the card headline, written by the model |
| slug | varchar(255), unique | from title + code + date, e.g. `gold-prices-drop-xau-2026-11-18` |
| category | varchar(50) | one of the six below |
| sentiment | varchar(20) | `Bullish` / `Bearish` / `Neutral` |
| related_assets | varchar(255) | comma-separated, e.g. `XAU,USD` |
| image_url | text, nullable | always null for now |
| short_summary | text | 1–2 sentences — the card blurb |
| long_summary | text | 1–2 paragraphs — the full write-up |
| created_at | timestamp | defaults to now |

Unique index on `(currency_code, insight_date)` so we never store the same day twice.
Index on `(category, insight_date)` for the feed query.

### `news_sources`

| column | type | notes |
|---|---|---|
| id | integer, PK | auto |
| insight_id | integer, FK → currency_insights.id | `ON DELETE CASCADE` |
| headline | text | the real article title |
| publisher | varchar(255) | e.g. Reuters |
| source_url | text | real link to the article |
| published_at | timestamp | when the article was published |

One insight → many news sources. This is the `Reuters+1` line under each card.

> **On rule 1 ("no database migration required"):** correct for `currency_rates` — we do not
> touch it, the USD-based storage stays exactly as it is. The two *new* news tables are new
> objects, so they still need one Alembic migration to be created. Nothing existing is altered.

> **On `content`:** `long_summary` holds *our own* generated analysis. We never store or
> republish the publisher's article body — that is their copyright. We link to it instead.

---

## 3. Categories

Your six, as a fixed list:

`Forex News` · `Cryptocurrency News` · `Precious Metals News` · `Market Analysis` ·
`Economic News` · `Central Bank Updates`

Default by asset type — fiat → Forex News, crypto → Cryptocurrency News, metal → Precious
Metals News. The model may override with a more specific one from the list when it fits
(a Fed-driven USD piece becoming `Central Bank Updates`, for example). If the model returns
anything outside the six, we fall back to the asset-type default.

`GET /news-history?group_by=category` groups by category and only returns groups that
have cards.

---

## 4. The 19 currencies + USD

All already columns in `currency_rates` — no new data plumbing.

**Fiat — 12:** EUR, GBP, JPY, CNY, CAD, HKD, AUD, SGD, CHF, THB, SEK, PLN
**Crypto — 5:** BTC, ETH, USDT, BNB, USDC
**Metal — 2:** XAU (gold), XAG (silver)

**Plus USD**, handled differently: no percentage (USD against USD is always 0, so a number
there would be meaningless), but it still gets one article per day in the Forex feed —
"what the dollar did and why" — since that is the driver behind most of the other 19 moves.
Its card shows `change_percent: null` and `sentiment` from the model's read of the week.

Kept in `app/news/constants.py` with each code's display name and asset type.

**One note:** USDT and USDC are pegged to USD by design, so they will nearly always come
back `Neutral` with a summary about the peg holding. Correct behaviour, not a bug.

The daily job makes **20 OpenAI calls** — 19 currencies + the USD commentary.

---

## 5. The percentage — one shared service

**Rule 2: all percentage maths lives in one place**, `app/utils/performance.py`. Nothing
computes a percent change on its own.

```
is_valid_rate(value)            → not None, finite, > 0          (rule 8)
to_strength(column_value)       → 1 / column_value               (rule 4)
change_percent(first, last)     → (last - first) / first * 100   — raw float, no rounding (rule 7)
fit_slope(points)               → least-squares slope
classify(pct, slope)            → "stronger" | "weaker" | "flat"
```

**Rule 4 — always invert.** Our table stores every currency as *"how many units you get for
1 USD"* (crypto too — [service.py](app/currency/service.py) saves `1 / usd_price`). So a
rising column value means the currency is **weaker**. Every calculation calls `to_strength()`
first, so a positive percent always means stronger. This is the single easiest thing to get
backwards, which is why it is one function that everything shares.

**Rule 6 — same logic for all three types.** Fiat, crypto and metals all run the identical
path. Gold is just `XAU` with an asset-type label on it; there is no special case anywhere.

**Rule 3 — compare latest against the right historical point.** Take the newest row at or
before `end_date`, and the newest row at or before `start_date`. Not "whatever rows happen
to be in the window" — if a currency stopped updating three days ago, we compare against
what we actually have and the response says which timestamps were used.

**Rule 7 — round once, at the end.** Every intermediate value stays full float precision.
Only the final `change_percent` is rounded, to 2 decimals.

**Rule 8 — validation.** Any row where the value is null, zero, negative, NaN or infinite is
skipped before the maths runs. If fewer than two valid points survive the window, we return
the insight with `change_percent: null` and `trend: "unknown"` rather than a wrong number.

**Rule 5 — green/red.** The backend sends the signed number and the `trend` word; the
frontend colours on `change_percent >= 0 ? green : red`. No colour logic in the API, no
sign-parsing in the UI.

**Trend thresholds:** slope sign and start→end sign must agree and `|change_percent| >= 0.10`
to call it `stronger` / `weaker`. Otherwise `flat`.

### One thing I will *not* silently change

[app/history/service.py](app/history/service.py) already computes a `percent_change`, and
your commit `95eb052 fix auto-tune precision` deliberately tuned its rounding to significant
figures so BTC pairs don't collapse to `0.00`. Centralising means history calls the shared
`change_percent()` for the **maths**, but keeps its own auto-precision **formatting**. Its
numbers come out identical to today. Tell me if you'd rather history also move to flat
2-decimals and I'll switch it.

---

## 6. The OpenAI call

Model **gpt-5-nano**, web search on. Three prompt templates in `app/news/prompts.py`, picked by
the trend — plus a fourth for the USD commentary card:

- **STRONGER** — "EUR strengthened 1.08% against USD between <start> and <end>. Explain what drove the gain."
- **WEAKER** — same shape, "explain what drove the fall."
- **FLAT** — "barely moved, explain why it was quiet."
- **USD COMMENTARY** — "summarise what the dollar did this week and why", no percentage supplied.

Every prompt includes, as facts *we* supply (the model never guesses these): currency code
and name, asset type, start date, end date, start rate, end rate, change_percent, slope
direction.

The model returns: `title`, `category`, `sentiment`, `short_summary`, `long_summary`,
`related_assets`, `sources[]`.

**Anti-hallucination, enforced in three places:**

1. **Web search on.** Real current articles, not memory. Hard system rule: *do not cite an
   article you did not retrieve in this call.*
2. **Strict JSON schema.** No free-form text to parse.
3. **We validate before saving.** Drop any source with a missing or non-`http(s)` URL. Drop
   any source whose `published_at` falls outside the window (± 2 days slack). Force
   `category` and `sentiment` into the allowed values. If zero sources survive we still save
   the insight with no sources — we never invent a link to fill the gap.

The model is told plainly: *if you cannot find news explaining the move, say so.* An honest
"no clear driver found" is an acceptable answer.

Failures (timeout, rate limit, bad JSON) retry twice with backoff, then log and skip. One
bad currency never breaks the other 19.

---

## 7. When it runs

A **daily background task** in `main.py`, alongside the existing three. Once a day it loops
the 20 subjects, computes each trend, calls OpenAI, writes `currency_insights` +
`news_sources`. It skips anything that already has a row for today, so a restart does not
double-charge us.

`GET /news-history` only **reads the DB**: fast responses, no OpenAI latency for users,
fixed cost of 20 calls/day, and the existing rate limiter is enough protection.

`POST /news-history/refresh` triggers generation, and is the only endpoint that costs
money. It takes `X-Cron-Key`, not `X-API-Key`. Point an external scheduler at the
no-argument form once a day; with a `currency_code` it regenerates one card synchronously.
Set `NEWS_INSIGHT_INTERVAL=0` to disable the in-process job when an external cron owns
generation.

---

## 8. Response shape

`GET /news-history?group_by=category&days=7`:

```json
{
  "days": 7,
  "generated_at": "2026-11-19T06:00:00Z",
  "groups": [
    {
      "category": "Forex News",
      "items": [
        {
          "currency_code": "EUR",
          "currency_name": "Euro",
          "asset_type": "fiat",
          "title": "Euro Faces Pressure Against a Stronger US Dollar",
          "slug": "euro-faces-pressure-eur-2026-11-18",
          "category": "Forex News",
          "sentiment": "Bearish",
          "related_assets": ["EUR", "USD"],
          "image_url": null,
          "short_summary": "The euro faces pressure as investors reassess interest rate expectations...",
          "long_summary": "Across the week ...",
          "change_percent": -0.42,
          "trend": "weaker",
          "start_date": "2026-11-12",
          "end_date": "2026-11-19",
          "start_rate": 1.0812,
          "end_rate": 1.0767,
          "insight_date": "2026-11-19",
          "created_at": "2026-11-19T06:00:00Z",
          "sources": [
            {
              "headline": "...",
              "publisher": "Reuters",
              "source_url": "https://...",
              "published_at": "2026-11-17T14:30:00Z"
            }
          ]
        }
      ]
    },
    { "category": "Cryptocurrency News", "items": [ ... ] },
    { "category": "Precious Metals News", "items": [ ... ] }
  ]
}
```

`change_percent` prints directly next to a `%` — `-0.42` renders as "-0.42%", green when
`>= 0`, red when below. `null` means we had no reliable data (or it is the USD card), and
the UI shows no percentage at all rather than a zero.

Errors on the read endpoint: `400` unsupported currency, unknown `group_by`, or bad
window · `401` bad API key · `404` the named currency has no insight stored yet · `422`
missing `X-API-Key`.

Errors on the refresh endpoint: `400` unsupported currency · `401` bad cron key · `422`
missing `X-Cron-Key` · `503` `CRON_KEY` not configured, or OpenAI unreachable.

---

## 9. Files

**New**
- `app/utils/performance.py` — the shared percentage service (rules 2, 4, 7, 8)
- `app/news/__init__.py`
- `app/news/models.py` — `CurrencyInsight`, `NewsSource`
- `app/news/schema.py` — Pydantic response models
- `app/news/constants.py` — the 19 codes + USD, names, asset types, categories
- `app/news/trend.py` — window selection + trend classification, on top of `performance.py`
- `app/news/prompts.py` — the four prompt templates
- `app/news/openai_client.py` — the call, JSON schema, validation, retry
- `app/news/service.py` — generate, save, read back
- `app/news/routes.py` — the three endpoints
- `alembic/versions/<id>_create_news_tables.py`

**Changed**
- `app/main.py` — register the router + the daily task
- `app/core/config.py` — `OPENAI_API_KEY`, `OPENAI_MODEL`, `NEWS_INSIGHT_INTERVAL`, `NEWS_WINDOW_DAYS`
- `app/history/service.py` — call the shared percent maths (numbers unchanged)
- `.env` — the new keys
- `requirements.txt` / `pyproject.toml` — add `openai`
- `README.md` — document the endpoints

---

## 10. Build order

1. `app/utils/performance.py` + unit tests for the invert/zero/negative/missing cases.
2. Models + Alembic migration; run it, confirm the tables exist.
3. `constants.py` + `trend.py`; verify stronger/weaker against real rows in your DB.
4. Config keys + the `openai` dependency.
5. `prompts.py` + `openai_client.py`; run one currency by hand, eyeball the sources.
6. `service.py` — generate and save.
7. `routes.py` + wire into `main.py`.
8. The daily background task.
9. Point `history` at the shared service, confirm its numbers are unchanged.
10. README + Swagger docs.

Steps 1–4 and 7 need no OpenAI key, so the percentage logic gets proven against real data
before a single paid call fires.
