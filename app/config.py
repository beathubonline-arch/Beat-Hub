"""
Central application configuration.
All values are sourced from environment variables / .env.
Never hard-code secrets here.
"""
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core
    APP_ENV: str = "development"
    APP_NAME: str = "BeatHub"
    SECRET_KEY: str = "change-me-in-production"
    DATABASE_URL: str = "sqlite:///./beathub.db"
    BASE_URL: str = "http://localhost:8000"

    # Auth
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    JWT_ALGORITHM: str = "HS256"

    # Platform economics
    PLATFORM_COMMISSION_PERCENT: float = 10.0

    # M-Pesa Daraja
    MPESA_ENVIRONMENT: str = "sandbox"  # sandbox | production
    MPESA_CONSUMER_KEY: str = ""
    MPESA_CONSUMER_SECRET: str = ""
    MPESA_SHORTCODE: str = ""
    MPESA_PASSKEY: str = ""
    MPESA_CALLBACK_URL: str = ""

    # Social
    YOUTUBE_CHANNEL_ID: str = "UCj0OSnxkdYsuhMipfKqLKnw"
    DISCORD_INVITE_URL: str = ""

    # Email (optional)
    EMAIL_ENABLED: bool = False
    EMAIL_HOST: str = ""
    EMAIL_PORT: int = 587
    EMAIL_USERNAME: str = ""
    EMAIL_PASSWORD: str = ""
    EMAIL_FROM: str = ""

    # Storage
    MEDIA_STORAGE: str = "local"  # local | s3 (future)
    MEDIA_ROOT: str = "media"
    MAX_UPLOAD_MB: int = 50

    @property
    def mpesa_base_url(self) -> str:
        if self.MPESA_ENVIRONMENT == "production":
            return "https://api.safaricom.co.ke"
        return "https://sandbox.safaricom.co.ke"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
