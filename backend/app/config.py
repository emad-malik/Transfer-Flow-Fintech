"""Settings loaded from environment. Nothing below reaches into the DB or HTTP layer."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = (
        "postgresql+psycopg://meowpay:meowpay@localhost:5432/meowpay"
    )

    # Per-transfer cap, in minor units (whiskers). 100 whiskers = 1 treat.
    # This is a fat-finger guard, not a real risk control (see README "Deliberately out of
    # scope"). 1,000,000 whiskers = 10,000 treats is an arbitrary but documented ceiling;
    # it is a config value on purpose so it can be tuned per deployment without a code change.
    transfer_max_amount_minor: int = 1_000_000

    # Default daily_limit_minor applied when POST /v1/accounts omits one.
    default_daily_limit_minor: int = 500_000

    currency: str = "TREATS"

    cors_origins: list[str] = ["http://localhost:3000"]


settings = Settings()
