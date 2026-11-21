import secrets

from app.core.config import env_var
from fastapi import HTTPException, Header


def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != env_var.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")


def verify_cron_key(x_cron_key: str = Header(...)):
    """Guard machine-only endpoints that cost money to call.

    Deliberately NOT the public API key: anything holding X-API-Key can read
    the feed, but only the scheduler can trigger paid OpenAI generation.

    Fails closed — if CRON_KEY is unset the endpoint is unreachable rather than
    open to everyone. Compared in constant time so a wrong key leaks nothing
    about how much of it was right.
    """
    if not env_var.CRON_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "CRON_KEY is not configured on the server, so this endpoint is "
                "disabled."
            ),
        )
    if not secrets.compare_digest(x_cron_key, env_var.CRON_KEY):
        raise HTTPException(status_code=401, detail="Invalid cron key")
