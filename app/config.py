"""Central application configuration.

All values are sourced from environment variables / .env.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: str = "development"
    APP_NAME: str = "BeatHub"
    SECRET_KEY: str = "change-me-in-production"
    SESSION_SECRET: str = ""
    DATABASE_URL: str = "sqlite:///./beathub.db"
    BASE_URL: str = "http://localhost:8000"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    JWT_ALGORITHM: str = "HS256"

    # Immutable BeatHub marketplace rule: 10% platform / 90% creator.
    PLATFORM_COMMISSION_PERCENT: float = 10.0
    PLATFORM_COMMISSION_RATE: float = 10.0

    # Paystack — single customer payment gateway for Kenya.
    # Paystack Checkout supports cards and Kenya M-PESA/mobile money.
    PAYSTACK_SECRET_KEY: str = ""
    PAYSTACK_PUBLIC_KEY: str = ""
    PAYSTACK_BASE_URL: str = "https://api.paystack.co"

    YOUTUBE_CHANNEL_ID: str = "UCj0OSnxkdYsuhMipfKqLKnw"
    DISCORD_INVITE_URL: str = "https://discord.gg/R4m7hkrdn"

    EMAIL_ENABLED: bool = False
    EMAIL_HOST: str = ""
    EMAIL_PORT: int = 587
    EMAIL_USERNAME: str = ""
    EMAIL_PASSWORD: str = ""
    EMAIL_FROM: str = ""

    MEDIA_STORAGE: str = "r2"
    MEDIA_ROOT: str = "media"
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = "beathub"
    R2_PUBLIC_URL: str = ""
    R2_PUBLIC_URL_EXPIRES: int = 3600
    R2_DOWNLOAD_URL_EXPIRES: int = 900
    MAX_UPLOAD_MB: int = 50

    @property
    def r2_enabled(self) -> bool:
        return (
            self.MEDIA_STORAGE.lower() == "r2"
            and bool(self.R2_ACCOUNT_ID)
            and bool(self.R2_ACCESS_KEY_ID)
            and bool(self.R2_SECRET_ACCESS_KEY)
            and bool(self.R2_BUCKET_NAME)
        )

    @property
    def r2_endpoint_url(self) -> str:
        if not self.R2_ACCOUNT_ID:
            return ""
        return f"https://{self.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
