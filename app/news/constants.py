"""The assets we generate news insights for, and the feed categories.

Phase one covers 12 assets plus USD. Everything downstream is derived from the
`ASSETS` tuple below, so adding an asset is one line here and nothing else:

    Asset("NZD", "New Zealand Dollar", AssetType.FIAT),

The code must already be a column on `currency_rates` — every code here is, so
adding one needs no migration and no change to the rate-fetching pipeline.
Check `app/currency/models.py` before adding a code that is not in that table;
a missing column means `compute_trend` finds no data and the asset is skipped.
"""

from dataclasses import dataclass
from enum import Enum


class AssetType(str, Enum):
    FIAT = "fiat"
    CRYPTO = "crypto"
    METAL = "metal"


class Category(str, Enum):
    FOREX = "Forex News"
    CRYPTO = "Cryptocurrency News"
    METALS = "Precious Metals News"
    MARKET_ANALYSIS = "Market Analysis"
    ECONOMIC = "Economic News"
    CENTRAL_BANK = "Central Bank Updates"


class Sentiment(str, Enum):
    BULLISH = "Bullish"
    BEARISH = "Bearish"
    NEUTRAL = "Neutral"


# The base currency. It gets a commentary article in the Forex feed but never a
# percentage — USD against USD is always zero, so a number there would be noise.
BASE_CURRENCY = "USD"


@dataclass(frozen=True)
class Asset:
    """One tracked asset, and everything the generator needs to know about it."""

    code: str
    name: str
    asset_type: AssetType

    # What the move is measured against, phrased for the prompt. "USD" for
    # everything we measure a percentage for; USD itself has no self-comparison
    # and uses a basket instead.
    benchmark: str = "USD"

    # The unit one quote covers, where "1 unit" is not self-evident. Metals are
    # priced per troy ounce; a currency or a coin is just one of itself, so it
    # stays None.
    unit: str | None = None

    # True for a currency or token that is pegged to USD by design, so a ~0%
    # move is the expected result rather than a market signal. Nothing in phase
    # one is pegged; set it on a stablecoin (e.g. USDT, USDC) when one is added
    # and the prompt picks up the peg note automatically.
    pegged_to_usd: bool = False


# --------------------------------------------------------------------------
# The registry — phase one
# --------------------------------------------------------------------------
# Declaration order is feed order: fiat, then crypto, then metals, with USD
# appended last. Keep the grouping if you add to it, because `GET /news-history`
# returns cards in exactly this order.
ASSETS: tuple[Asset, ...] = (
    # --- Fiat (7) ---
    Asset("EUR", "Euro", AssetType.FIAT),
    Asset("GBP", "British Pound", AssetType.FIAT),
    Asset("JPY", "Japanese Yen", AssetType.FIAT),
    Asset("CHF", "Swiss Franc", AssetType.FIAT),
    Asset("CAD", "Canadian Dollar", AssetType.FIAT),
    Asset("AUD", "Australian Dollar", AssetType.FIAT),
    Asset("CNY", "Chinese Yuan", AssetType.FIAT),
    # --- Crypto (3) ---
    Asset("BTC", "Bitcoin", AssetType.CRYPTO),
    Asset("ETH", "Ethereum", AssetType.CRYPTO),
    Asset("BNB", "BNB", AssetType.CRYPTO),
    # --- Precious metals (2) ---
    Asset("XAU", "Gold", AssetType.METAL, unit="troy ounce"),
    Asset("XAG", "Silver", AssetType.METAL, unit="troy ounce"),
)

# USD is handled apart from the rest: it gets a commentary card with no
# percentage, so it is compared against a basket rather than against itself.
USD_ASSET = Asset(
    BASE_CURRENCY,
    "US Dollar",
    AssetType.FIAT,
    benchmark="the US Dollar Index (DXY)",
)


# --------------------------------------------------------------------------
# Everything below is derived — do not edit these by hand
# --------------------------------------------------------------------------

# Codes that get a measured percentage.
TRACKED_CURRENCIES: tuple[str, ...] = tuple(a.code for a in ASSETS)

# Everything the daily job generates an article for: the tracked set plus USD.
ALL_SUBJECTS: tuple[str, ...] = TRACKED_CURRENCIES + (BASE_CURRENCY,)

_BY_CODE: dict[str, Asset] = {a.code: a for a in ASSETS}
_BY_CODE[BASE_CURRENCY] = USD_ASSET

# code -> (display name, asset type). Kept for callers that only need the pair.
CURRENCY_META: dict[str, tuple[str, AssetType]] = {
    a.code: (a.name, a.asset_type) for a in ASSETS
}

USD_META: tuple[str, AssetType] = (USD_ASSET.name, USD_ASSET.asset_type)

# Empty in phase one. Derived, so adding a pegged asset to ASSETS is enough.
PEGGED_TO_USD: frozenset[str] = frozenset(
    a.code for a in ASSETS if a.pegged_to_usd
)

# Default feed category per asset type. The model may pick a more specific
# category from `Category`; anything outside it falls back to these.
DEFAULT_CATEGORY: dict[AssetType, Category] = {
    AssetType.FIAT: Category.FOREX,
    AssetType.CRYPTO: Category.CRYPTO,
    AssetType.METAL: Category.METALS,
}

# Order the feed groups appear in, matching the designed layout.
CATEGORY_ORDER: tuple[Category, ...] = (
    Category.FOREX,
    Category.CRYPTO,
    Category.METALS,
    Category.CENTRAL_BANK,
    Category.ECONOMIC,
    Category.MARKET_ANALYSIS,
)


def get_asset(code: str) -> Asset | None:
    """The full Asset for a code (USD included), or None if we don't track it."""
    return _BY_CODE.get((code or "").upper())


def get_meta(code: str) -> tuple[str, AssetType] | None:
    """Display name and asset type for a code, or None if we don't track it."""
    asset = get_asset(code)
    return (asset.name, asset.asset_type) if asset else None


def is_supported(code: str) -> bool:
    return get_asset(code) is not None
