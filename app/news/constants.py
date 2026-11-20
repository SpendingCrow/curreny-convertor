"""The currencies we generate news insights for, and the feed categories.

Every code here is already a column on `currency_rates`, so adding or removing
one needs no migration and no change to the rate-fetching pipeline.
"""

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

# code -> (display name, asset type)
CURRENCY_META: dict[str, tuple[str, AssetType]] = {
    # --- Fiat (12) ---
    "EUR": ("Euro", AssetType.FIAT),
    "GBP": ("British Pound", AssetType.FIAT),
    "JPY": ("Japanese Yen", AssetType.FIAT),
    "CNY": ("Chinese Yuan", AssetType.FIAT),
    "CAD": ("Canadian Dollar", AssetType.FIAT),
    "HKD": ("Hong Kong Dollar", AssetType.FIAT),
    "AUD": ("Australian Dollar", AssetType.FIAT),
    "SGD": ("Singapore Dollar", AssetType.FIAT),
    "CHF": ("Swiss Franc", AssetType.FIAT),
    "THB": ("Thai Baht", AssetType.FIAT),
    "SEK": ("Swedish Krona", AssetType.FIAT),
    "PLN": ("Polish Zloty", AssetType.FIAT),
    # --- Crypto (5) ---
    "BTC": ("Bitcoin", AssetType.CRYPTO),
    "ETH": ("Ethereum", AssetType.CRYPTO),
    "USDT": ("Tether", AssetType.CRYPTO),
    "BNB": ("BNB", AssetType.CRYPTO),
    "USDC": ("USD Coin", AssetType.CRYPTO),
    # --- Metals (2) ---
    "XAU": ("Gold", AssetType.METAL),
    "XAG": ("Silver", AssetType.METAL),
}

# The 19 currencies that get a measured percentage.
TRACKED_CURRENCIES: tuple[str, ...] = tuple(CURRENCY_META.keys())

# USD is covered too, but with no percentage — hence a separate list.
USD_META: tuple[str, AssetType] = ("US Dollar", AssetType.FIAT)

# Everything the daily job generates an article for: the 19 plus USD.
ALL_SUBJECTS: tuple[str, ...] = TRACKED_CURRENCIES + (BASE_CURRENCY,)

# Stablecoins are pegged to USD by design, so a ~0% move is the expected result
# rather than a data problem. Used to phrase their prompt honestly.
PEGGED_TO_USD: frozenset[str] = frozenset({"USDT", "USDC"})

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


def get_meta(code: str) -> tuple[str, AssetType] | None:
    """Display name and asset type for a code, or None if we don't track it."""
    code = (code or "").upper()
    if code == BASE_CURRENCY:
        return USD_META
    return CURRENCY_META.get(code)


def is_supported(code: str) -> bool:
    return get_meta(code) is not None
