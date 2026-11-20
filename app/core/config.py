import os
from pydantic import BaseModel, Field, model_validator
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class GeneralConfig(BaseModel):
    # Environment
    ENV_STATE: str = Field(default="dev")
    API_URL: str = Field(default="http://localhost:8000")
    API_KEY: str | None = None

    # Database Settings
    POSTGRES_NAME: str | None = Field(default=None)
    POSTGRES_USER: str | None = Field(default=None)
    POSTGRES_PASSWORD: str | None = Field(default=None)
    POSTGRES_DATA_PATH: str | None = Field(default=None)
    DATABASE_URL: str | None = Field(default=None)
    

    # DigitalOcean Spaces (S3)
    DO_SPACES_ENDPOINT: str | None = None
    DO_SPACES_REGION: str | None = None
    DO_SPACES_KEY: str | None = None
    DO_SPACES_SECRET: str | None = None
    DO_SPACES_BUCKET: str | None = None
    DO_SPACES_CDN_URL: str | None = None

    # API Keys
    OPENEXCHANGERATES_API_KEY: str | None = None
    COINMARKETCAP_API_KEY: str | None = None
    OPENEXCHANGERATES_CRON_KEY: str | None = None

    # Shared secret for machine-only endpoints (the news refresh job). Sent as
    # the X-Cron-Key header. Deliberately separate from API_KEY: the public key
    # must never be able to trigger paid OpenAI work. Unset = endpoint disabled.
    CRON_KEY: str | None = None

    # OpenAI — powers the currency news insights
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = Field(default="gpt-5-nano")
    # Seconds between in-process news generation runs (default: once a day).
    # Set to 0 to disable the in-process job and drive generation purely from an
    # external cron hitting POST /news-history/refresh.
    NEWS_INSIGHT_INTERVAL: int = Field(default=86400)
    # How many days back each insight looks when measuring the move
    NEWS_WINDOW_DAYS: int = Field(default=7)
    # Seconds to wait on a single OpenAI call before giving up
    NEWS_OPENAI_TIMEOUT: int = Field(default=180)

    WRITE_FILE_ON_S3: int = Field(default=60)
    FETCH_API_DATA: int = Field(default=60)

    # Rate limiting (per client IP, sliding window)
    RATE_LIMIT_SOFT: int = Field(default=10)
    RATE_LIMIT_HARD: int = Field(default=20)
    RATE_LIMIT_WINDOW_SECONDS: int = Field(default=60)
    RATE_LIMIT_SOFT_DELAY_MS: int = Field(default=500)
    # Cap on distinct IPs tracked in memory; oldest are evicted past this.
    RATE_LIMIT_MAX_TRACKED_IPS: int = Field(default=10000)
    # Comma-separated IPs/CIDRs allowed to set X-Forwarded-For (e.g. your
    # reverse proxy). Empty = never trust XFF, always use the socket peer.
    TRUSTED_PROXIES: str = Field(default="")

    # Slack
    SLACK_WEBHOOK_URL: str | None = None

    # Interval for Jobs in minutes

    @model_validator(mode="after")
    def _validate_rate_limits(self):
        if self.RATE_LIMIT_SOFT >= self.RATE_LIMIT_HARD:
            raise ValueError(
                f"RATE_LIMIT_SOFT ({self.RATE_LIMIT_SOFT}) must be < "
                f"RATE_LIMIT_HARD ({self.RATE_LIMIT_HARD}); otherwise the "
                "soft-throttle path is unreachable."
            )
        return self

    def __init__(self, **kwargs):
        # Load from environment variables
        env_values = {}
        for field_name in self.__fields__.keys():
            env_value = os.getenv(field_name)
            if env_value is not None:
                env_values[field_name] = env_value
                    
        # Merge with any passed kwargs
        env_values.update(kwargs)
        super().__init__(**env_values)


# Singleton instance
env_var = GeneralConfig()