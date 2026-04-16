"""
zephr.chat — Configuration
All settings loaded from environment variables (.env file)
"""
import os
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # ── Telegram ──────────────────────────────────────────────
    BOT_TOKEN: str                          # From BotFather
    WEBHOOK_URL: Optional[str] = None       # e.g. https://yourdomain.com
    WEBHOOK_PATH: str = "/webhook"
    WEBAPP_URL: str = "https://yourdomain.com"  # Your frontend URL

    # ── Database ──────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://zephr:zephr@localhost:5432/zephrdb"

    # ── Redis ─────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379"

    # ── Security ──────────────────────────────────────────────
    SECRET_KEY: str = "change-this-to-a-random-64-char-string"

    # ── AI Moderation ─────────────────────────────────────────
    PERSPECTIVE_API_KEY: Optional[str] = None   # Google Perspective API (free)
    TOXICITY_THRESHOLD: float = 0.75            # Block above this score

    # ── Session ───────────────────────────────────────────────
    SESSION_TTL: int = 3600          # 1 hour max chat session
    QUEUE_TTL: int = 300             # 5 min in queue before auto-remove
    MAX_MSG_LENGTH: int = 2000

    # ── VIP ───────────────────────────────────────────────────
    VIP_MONTHLY_PRICE: int = 499     # $4.99 in cents (Telegram Stars: ~100 stars)
    VIP_QUARTERLY_PRICE: int = 999   # $9.99
    VIP_TRIAL_DAYS: int = 3

    # ── Rate Limiting ─────────────────────────────────────────
    RATE_LIMIT_MESSAGES: int = 30    # per minute
    RATE_LIMIT_MATCHES: int = 20     # per hour

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
