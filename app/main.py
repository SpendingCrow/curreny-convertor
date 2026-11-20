import logging.config
import asyncio
from datetime import datetime, timedelta, timezone
from app.currency.service import upload_to_s3, update_currency_rates
from app.currency.models import CurrencyRate
from app.utils.database import async_session
from fastapi import FastAPI
from app.utils.logging_config import LOGGING_CONFIG
from app.currency.routes import router as currency_router
from app.history.routes import router as history_router
from app.news.routes import router as news_router
from app.news.service import run_daily_generation
from app.core.config import env_var
from app.services.slack_service import send_slack_alert
from app.utils.rate_limit import RateLimitMiddleware
from sqlalchemy import select
# Load the logging config
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Currency Converter API",
    description="Provides pre-signed download URLs for the latest fiat and cryptocurrency exchange rates (150+ fiat currencies and top 50 cryptos, base USD). Rates are refreshed periodically from OpenExchangeRates and CoinMarketCap.",
    version="1.1.0",
)

app.add_middleware(RateLimitMiddleware)
app.include_router(currency_router)
app.include_router(history_router)
app.include_router(news_router)

@app.on_event("startup")
async def start_background_tasks():
    
    async def periodic_s3_upload():
        """First background task - S3 upload every minute"""
        await asyncio.sleep(5)  # Initial delay
        while True:
            try:
                await upload_to_s3()
                logger.info("✅ Background S3 upload completed")
            except Exception as e:
                logger.error(f"❌ S3 upload failed: {e}")
            await asyncio.sleep(env_var.WRITE_FILE_ON_S3)
    
    async def periodic_currency_update():
        """Second background task - Update currency rates every hour"""
        await asyncio.sleep(10)  # Initial delay (different from first task)
        while True:
            try:
                async with async_session() as db:
                    await update_currency_rates(db)
                logger.info("✅ Currency rates update completed")
            except Exception as e:
                logger.error(f"❌ Currency rates update failed: {e}")
            await asyncio.sleep(env_var.FETCH_API_DATA)  # Wait 1 hour
    
    async def periodic_rate_staleness_check():
        """Third background task - alert on Slack if rates are stale for 12h"""
        await asyncio.sleep(60)  # Initial delay
        while True:
            try:
                async with async_session() as db:
                    result = await db.execute(
                        select(CurrencyRate.created_at)
                        .order_by(CurrencyRate.created_at.desc())
                        .limit(1)
                    )
                    row = result.scalar_one_or_none()

                if row is None:
                    logger.warning("No currency rate row found in DB")
                    await send_slack_alert(
                        ":warning: *Currency Converter Alert*\n"
                        "No currency rate records found in the database."
                    )
                else:
                    last_updated = row if row.tzinfo else row.replace(tzinfo=timezone.utc)
                    age = datetime.now(timezone.utc) - last_updated
                    if age > timedelta(hours=12):
                        hours_ago = int(age.total_seconds() // 3600)
                        await send_slack_alert(
                            f":rotating_light: *Currency Converter Alert*\n"
                            f"Currency rates have *not been updated for {hours_ago} hours*.\n"
                            f"Last update: `{last_updated.strftime('%Y-%m-%d %H:%M UTC')}`"
                        )
                        logger.warning(f"Stale rate alert sent — last update {hours_ago}h ago")
                    else:
                        logger.info(f"Rate freshness OK — last update {int(age.total_seconds() // 60)}m ago")
            except Exception as e:
                logger.error(f"Rate staleness check failed: {e}")
            await asyncio.sleep(3600)  # Check every hour

    async def periodic_news_insights():
        """Fourth background task - generate currency news insights once a day.

        Each currency is independent: one failure is logged and the rest carry
        on. Already-generated currencies are skipped, so a restart costs nothing.

        Set NEWS_INSIGHT_INTERVAL=0 to turn this off and drive generation purely
        from an external scheduler hitting POST /news-history/refresh.
        """
        if env_var.NEWS_INSIGHT_INTERVAL <= 0:
            logger.info(
                "⏭️  NEWS_INSIGHT_INTERVAL=0 — in-process news generation "
                "disabled; expecting an external cron to call "
                "POST /news-history/refresh"
            )
            return

        await asyncio.sleep(120)  # Initial delay — let the rate tasks settle first
        while True:
            try:
                if not env_var.OPENAI_API_KEY:
                    logger.warning(
                        "⏭️  OPENAI_API_KEY not set — skipping news insight generation"
                    )
                else:
                    async with async_session() as db:
                        summary = await run_daily_generation(db)
                    logger.info(f"✅ News insights generated: {summary}")
            except Exception as e:
                logger.error(f"❌ News insight generation failed: {e}")
            await asyncio.sleep(env_var.NEWS_INSIGHT_INTERVAL)

    # Start the background tasks
    asyncio.create_task(periodic_s3_upload())
    asyncio.create_task(periodic_currency_update())
    asyncio.create_task(periodic_rate_staleness_check())
    asyncio.create_task(periodic_news_insights())

    logger.info("🚀 Background tasks started successfully")