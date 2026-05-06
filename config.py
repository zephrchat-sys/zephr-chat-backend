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

    # ── Razorpay Payment ──────────────────────────────────────
    # Get these from Razorpay Dashboard → Settings → API Keys
    RAZORPAY_KEY_ID: str = ""               # e.g., rzp_test_AbCdEfGhIjKlMnOp
    RAZORPAY_KEY_SECRET: str = ""           # Keep this PRIVATE!
    RAZORPAY_WEBHOOK_SECRET: str = ""       # For webhook verification
    
    # Payment Mode: "test" or "live"
    RAZORPAY_MODE: str = "test"             # Switch to "live" for production

    # ── Database ──────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://zephr:zephr@localhost:5432/zephrdb"

    # ── Redis ─────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379"

    # ── Security ──────────────────────────────────────────────
    SECRET_KEY: str = "change-this-to-a-random-64-char-string"

    # ── AI Moderation ─────────────────────────────────────────
    PERSPECTIVE_API_KEY: Optional[str] = None   # Google Perspective API (free)
    TOXICITY_THRESHOLD: float = 0.75            # Block above this score

    # ── AI Bot ────────────────────────────────────────────────
    ANTHROPIC_API_KEY: Optional[str] = None     # Claude API key for AI bot

    # ── Session ───────────────────────────────────────────────
    SESSION_TTL: int = 3600          # 1 hour max chat session
    QUEUE_TTL: int = 300             # 5 min in queue before auto-remove
    MAX_MSG_LENGTH: int = 2000

    # ── VIP ───────────────────────────────────────────────────
    # Pricing in smallest currency unit (paise for INR, cents for USD)
    VIP_MONTHLY_PRICE_INR: int = 41500   # ₹415
    VIP_QUARTERLY_PRICE_INR: int = 83000 # ₹830
    VIP_MONTHLY_PRICE_USD: int = 499     # $4.99
    VIP_QUARTERLY_PRICE_USD: int = 999   # $9.99
    VIP_TRIAL_DAYS: int = 3

    # ── Rate Limiting ─────────────────────────────────────────
    RATE_LIMIT_MESSAGES: int = 30    # per minute
    RATE_LIMIT_MATCHES: int = 20     # per hour

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
