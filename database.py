"""
zephr.chat — Database Models (SQLAlchemy Async + PostgreSQL)
Stores ONLY what's needed. Chat content is NEVER persisted.
"""
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import (
    Column, BigInteger, Integer, String, Boolean,
    DateTime, Float, Text, Index, func
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from config import settings


# ── Engine & Session ──────────────────────────────────────────────────────────
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=False,
)
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


# ── Models ────────────────────────────────────────────────────────────────────

class User(Base):
    """
    Telegram user record.
    We store the bare minimum — no messages, no chat history.
    """
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True)           # Telegram user ID
    username = Column(String(64), nullable=True)        # @handle (optional)
    first_name = Column(String(64), nullable=True)
    language_code = Column(String(8), default="en")
    is_vip = Column(Boolean, default=False)
    vip_expires_at = Column(DateTime, nullable=True)
    is_banned = Column(Boolean, default=False)
    ban_reason = Column(String(256), nullable=True)
    banned_at = Column(DateTime, nullable=True)
    report_count = Column(Integer, default=0)
    match_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())
    last_seen = Column(DateTime, default=func.now(), onupdate=func.now())

    # Filters (saved preferences)
    pref_language = Column(String(8), default="any")
    pref_age_group = Column(String(16), default="any")
    pref_topic = Column(String(32), default="random")
    # VIP-only filters
    pref_gender = Column(String(16), default="any")
    pref_country = Column(String(8), default="any")

    # Referral
    referral_code = Column(String(16), unique=True, nullable=True)
    referred_by = Column(BigInteger, nullable=True)
    referral_count = Column(Integer, default=0)

    def is_vip_active(self) -> bool:
        if not self.is_vip:
            return False
        if self.vip_expires_at and self.vip_expires_at < datetime.utcnow():
            return False
        return True


class ChatSession(Base):
    """
    Tracks completed chat sessions for analytics.
    NO message content is stored.
    """
    __tablename__ = "chat_sessions"

    id = Column(String(36), primary_key=True)        # UUID
    user1_id = Column(BigInteger, nullable=False)
    user2_id = Column(BigInteger, nullable=False)
    topic = Column(String(32), default="random")
    started_at = Column(DateTime, default=func.now())
    ended_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    message_count = Column(Integer, default=0)
    ended_by = Column(String(16), nullable=True)     # "user1", "user2", "timeout", "ban"
    user1_rating = Column(Integer, nullable=True)    # 1-5 stars
    user2_rating = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_sessions_user1", "user1_id"),
        Index("ix_sessions_user2", "user2_id"),
        Index("ix_sessions_started", "started_at"),
    )


class Report(Base):
    """
    User reports — fed into moderation queue.
    """
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    reporter_id = Column(BigInteger, nullable=False)
    reported_id = Column(BigInteger, nullable=False)
    session_id = Column(String(36), nullable=True)
    reason = Column(String(128), default="unspecified")
    context = Column(Text, nullable=True)               # Last few message snippets (no PII)
    ai_toxicity_score = Column(Float, nullable=True)
    reviewed = Column(Boolean, default=False)
    action_taken = Column(String(64), nullable=True)    # "banned", "warned", "dismissed"
    created_at = Column(DateTime, default=func.now())


class BannedPhrase(Base):
    """
    Hardcoded ban phrases for instant blocking.
    Managed via admin or BotFather commands.
    """
    __tablename__ = "banned_phrases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phrase = Column(String(256), unique=True)
    severity = Column(String(16), default="warn")     # "warn" | "block" | "ban"
    created_at = Column(DateTime, default=func.now())


class VIPPayment(Base):
    """
    Tracks VIP payment records from Telegram Payments.
    """
    __tablename__ = "vip_payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    telegram_charge_id = Column(String(128), unique=True)
    provider_charge_id = Column(String(128), nullable=True)
    amount = Column(Integer)                    # In cents
    currency = Column(String(8), default="USD")
    plan = Column(String(16))                   # "monthly" | "quarterly" | "trial"
    created_at = Column(DateTime, default=func.now())


# ── DB Helpers ────────────────────────────────────────────────────────────────

async def get_db():
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Create all tables on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Database tables created")


async def get_or_create_user(session: AsyncSession, user_id: int, **kwargs) -> User:
    """Get existing user or create new one from Telegram data."""
    from sqlalchemy import select
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        import secrets
        user = User(
            id=user_id,
            referral_code=secrets.token_urlsafe(8),
            **kwargs
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    else:
        # Update last seen and any changed fields
        for k, v in kwargs.items():
            if hasattr(user, k) and v is not None:
                setattr(user, k, v)
        user.last_seen = datetime.utcnow()
        await session.commit()

    return user
