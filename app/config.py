"""
Central application configuration.
All values are sourced from environment variables / .env.
Never hard-code secrets here.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # CORE
    # ------------------------------------------------------------------

    APP_ENV: str = "development"
    APP_NAME: str = "BeatHub"
    SECRET_KEY: str = "change-me-in-production"

    DATABASE_URL: str = "sqlite:///./beathub.db"
    BASE_URL: str = "http://localhost:8000"

    # ------------------------------------------------------------------
    # AUTH
    # ------------------------------------------------------------------

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    JWT_ALGORITHM: str = "HS256"

    # ------------------------------------------------------------------
    # PLATFORM
    # ------------------------------------------------------------------

    PLATFORM_COMMISSION_PERCENT: float = 10.0

    # ------------------------------------------------------------------
    # M-PESA
    # ------------------------------------------------------------------

    MPESA_ENVIRONMENT: str = "sandbox"
    MPESA_CONSUMER_KEY: str = ""
    MPESA_CONSUMER_SECRET: str = ""
    MPESA_SHORTCODE: str = ""
    MPESA_PASSKEY: str = ""
    MPESA_CALLBACK_URL: str = ""

    # ------------------------------------------------------------------
    # SOCIAL
    # ------------------------------------------------------------------

    YOUTUBE_CHANNEL_ID: str = "UCj0OSnxkdYsuhMipfKqLKnw"
    DISCORD_INVITE_URL: str = "https://discord.gg/R4m7hkrdn"

    # ------------------------------------------------------------------
    # EMAIL
    # ------------------------------------------------------------------

    EMAIL_ENABLED: bool = False
    EMAIL_HOST: str = ""
    EMAIL_PORT: int = 587
    EMAIL_USERNAME: str = ""
    EMAIL_PASSWORD: str = ""
    EMAIL_FROM: str = ""

    # ------------------------------------------------------------------
    # STORAGE
    #
    # Render local filesystem is NOT used for permanent uploads.
    # Files are stored in Cloudflare R2.
    # ------------------------------------------------------------------

    MEDIA_STORAGE: str = "r2"

    # Kept for compatibility with existing code/config.
    # No permanent uploads are written here.
    MEDIA_ROOT: str = "/tmp/beathub-media"

    MAX_UPLOAD_MB: int = 50

    # ------------------------------------------------------------------
    # CLOUDFLARE R2
    # ------------------------------------------------------------------

    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = "beathub"

    # Optional custom/public R2 domain.
    # Leave empty when using presigned URLs.
    R2_PUBLIC_URL: str = ""

    # Presigned URL lifetime for public images/previews.
    R2_PUBLIC_URL_EXPIRES: int = 3600

    # Presigned URL lifetime for downloads.
    R2_DOWNLOAD_URL_EXPIRES: int = 900

    @property
    def mpesa_base_url(self) -> str:
        if self.MPESA_ENVIRONMENT.lower() == "production":
            return "https://api.safaricom.co.ke"

        return "https://sandbox.safaricom.co.ke"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    @property
    def r2_endpoint_url(self) -> str:
        if not self.R2_ACCOUNT_ID:
            return ""

        return (
            f"https://{self.R2_ACCOUNT_ID}"
            ".r2.cloudflarestorage.com"
        )

    @property
    def r2_enabled(self) -> bool:
        return all(
            [
                self.R2_ACCOUNT_ID,
                self.R2_ACCESS_KEY_ID,
                self.R2_SECRET_ACCESS_KEY,
                self.R2_BUCKET_NAME,
            ]
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
