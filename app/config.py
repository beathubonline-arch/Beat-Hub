"""
Central application configuration.

All values are sourced from environment variables / .env.
BeatHub uses Cloudflare R2 for persistent media storage.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --------------------------------------------------------------
    # CORE
    # --------------------------------------------------------------

    APP_ENV: str = "development"
    APP_NAME: str = "BeatHub"
    SECRET_KEY: str = "change-me-in-production"

    DATABASE_URL: str = "sqlite:///./beathub.db"
    BASE_URL: str = "http://localhost:8000"

    # --------------------------------------------------------------
    # AUTH
    # --------------------------------------------------------------

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    JWT_ALGORITHM: str = "HS256"

    # --------------------------------------------------------------
    # PLATFORM ECONOMICS
    # --------------------------------------------------------------

    PLATFORM_COMMISSION_PERCENT: float = 10.0
    PLATFORM_COMMISSION_RATE: float = 10.0

    # --------------------------------------------------------------
    # M-PESA
    # --------------------------------------------------------------

    MPESA_ENVIRONMENT: str = "sandbox"
    MPESA_CONSUMER_KEY: str = ""
    MPESA_CONSUMER_SECRET: str = ""
    MPESA_SHORTCODE: str = ""
    MPESA_PASSKEY: str = ""
    MPESA_CALLBACK_URL: str = ""

    @property
    def mpesa_base_url(self) -> str:
        if self.MPESA_ENVIRONMENT.lower() == "production":
            return "https://api.safaricom.co.ke"

        return "https://sandbox.safaricom.co.ke"

    # --------------------------------------------------------------
    # SOCIAL
    # --------------------------------------------------------------

    YOUTUBE_CHANNEL_ID: str = "UCj0OSnxkdYsuhMipfKqLKnw"
    DISCORD_INVITE_URL: str = "https://discord.gg/R4m7hkrdn"

    # --------------------------------------------------------------
    # EMAIL
    # --------------------------------------------------------------

    EMAIL_ENABLED: bool = False
    EMAIL_HOST: str = ""
    EMAIL_PORT: int = 587
    EMAIL_USERNAME: str = ""
    EMAIL_PASSWORD: str = ""
    EMAIL_FROM: str = ""

    # --------------------------------------------------------------
    # STORAGE
    #
    # BeatHub uses Cloudflare R2.
    # --------------------------------------------------------------

    MEDIA_STORAGE: str = "r2"

    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = "beathub"

    # Optional public/custom R2 URL.
    #
    # Leave EMPTY for private R2 buckets.
    # When empty, BeatHub generates temporary presigned URLs.
    #
    R2_PUBLIC_URL: str = ""

    # Artwork URL lifetime.
    R2_PUBLIC_URL_EXPIRES: int = 3600

    # Purchased audio download URL lifetime.
    R2_DOWNLOAD_URL_EXPIRES: int = 900

    MAX_UPLOAD_MB: int = 50

    # --------------------------------------------------------------
    # R2 HELPERS
    # --------------------------------------------------------------

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

        return (
            f"https://{self.R2_ACCOUNT_ID}"
            ".r2.cloudflarestorage.com"
        )

    # --------------------------------------------------------------
    # ENVIRONMENT
    # --------------------------------------------------------------

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
