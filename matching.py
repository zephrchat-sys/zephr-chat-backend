"""
zephr.chat — Matching Engine (Redis)
Pairs users in <1 second using Redis queues and pub/sub.
Sessions live in Redis only — never persisted to DB.
"""
import asyncio
import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

from redis import asyncio as aioredis

from config import settings


# ── Redis Keys ────────────────────────────────────────────────────────────────
QUEUE_KEY = "zephr:queue:{topic}"           # Sorted set per topic
SESSION_KEY = "zephr:session:{session_id}"  # Hash: session data
USER_SESSION_KEY = "zephr:user:{user_id}:session"  # String: active session_id
PUBSUB_CHANNEL = "zephr:chat:{session_id}"  # Pub/Sub channel per session
STATS_KEY = "zephr:stats"                   # Hash: global stats
RATE_MSG_KEY = "zephr:rate:msg:{user_id}"   # Message rate limiter
RATE_MATCH_KEY = "zephr:rate:match:{user_id}"  # Match rate limiter


@dataclass
class QueueEntry:
    user_id: int
    anon_name: str
    anon_emoji: str
    language: str = "any"
    age_group: str = "any"
    topic: str = "random"
    gender: str = "any"          # VIP only - FILTER/PREFERENCE (what they want to match with)
    country: str = "any"         # VIP only - FILTER/PREFERENCE (what they want to match with)
    user_gender: str = "any"     # User's actual gender (for others to filter)
    user_country: str = "any"    # User's actual country (for others to filter)
    user_age_group: str = "any"  # User's actual age group (for others to filter)
    is_vip: bool = False
    joined_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Session:
    session_id: str
    user1_id: int
    user2_id: int
    user1_anon: str
    user2_anon: str
    user1_emoji: str
    user2_emoji: str
    topic: str
    started_at: str
    msg_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class MatchingEngine:
    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None

    async def connect(self):
        self._redis = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
        print("✅ Redis connected")

    async def disconnect(self):
        if self._redis:
            await self._redis.close()

    @property
    def redis(self) -> aioredis.Redis:
        if not self._redis:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return self._redis

    # ── Queue Management ──────────────────────────────────────────────────────

    async def join_queue(self, entry: QueueEntry) -> Optional[Session]:
        """
        Add user to matching queue.
        Returns a Session immediately if a compatible partner is found,
        otherwise returns None (caller should wait via pub/sub).
        """
        import time

        # Check if user is already in a session
        existing = await self.get_user_session(entry.user_id)
        if existing:
            await self.leave_session(entry.user_id)

        entry.joined_at = time.time()
        topic_key = QUEUE_KEY.format(topic=entry.topic)

        # Try to find a match first (VIP gets priority scan)
        partner_data = await self._find_compatible_partner(entry, topic_key)

        if partner_data:
            # Remove partner from queue
            partner = QueueEntry(**partner_data)
            await self.redis.zrem(topic_key, json.dumps(partner_data))

            # Also try removing from "any" topic if different
            if entry.topic != "random":
                await self.redis.zrem(QUEUE_KEY.format(topic="random"), json.dumps(partner_data))

            # Create session
            session = await self._create_session(entry, partner)
            return session

        # No match — add to queue
        score = entry.joined_at if not entry.is_vip else entry.joined_at - 1000  # VIP priority
        await self.redis.zadd(topic_key, {json.dumps(entry.to_dict()): score})
        await self.redis.expire(topic_key, settings.QUEUE_TTL * 10)

        # Also add to "any" topic queue for cross-matching
        if entry.topic != "random":
            await self.redis.zadd(QUEUE_KEY.format(topic="random"), {json.dumps(entry.to_dict()): score})

        # Update global stats
        await self._increment_stat("queued")
        return None

    async def leave_queue(self, user_id: int, topic: str = None):
        """Remove user from all queues."""
        topics = [topic] if topic else ["random", "tech", "language", "vibes", "deep", "gaming"]
        for t in topics:
            key = QUEUE_KEY.format(topic=t)
            # Scan and remove entries with this user_id
            members = await self.redis.zrange(key, 0, -1)
            for member in members:
                try:
                    data = json.loads(member)
                    if data.get("user_id") == user_id:
                        await self.redis.zrem(key, member)
                except Exception:
                    pass

    async def _find_compatible_partner(
        self, entry: QueueEntry, topic_key: str
    ) -> Optional[dict]:
        """
        Find oldest waiting user compatible with given filters.
        VIP users can filter by gender/country.
        """
        members = await self.redis.zrange(topic_key, 0, -1)  # Oldest first

        for member in members:
            try:
                candidate = json.loads(member)
            except Exception:
                continue

            # Skip self
            if candidate["user_id"] == entry.user_id:
                continue

            # Language filter
            if entry.language != "any" and candidate["language"] != "any":
                if entry.language != candidate["language"]:
                    continue

            # Age filter - check if candidate's ACTUAL age group matches your preference
            if entry.age_group != "any":
                candidate_actual_age = candidate.get("user_age_group", "any")
                if candidate_actual_age != "any" and candidate_actual_age != entry.age_group:
                    continue
            
            # Reverse check: Does candidate's age filter match YOUR actual age group?
            candidate_age_filter = candidate.get("age_group", "any")
            if candidate_age_filter != "any":
                your_actual_age = entry.user_age_group
                if your_actual_age != "any" and your_actual_age != candidate_age_filter:
                    continue

            # VIP gender filter - check if candidate's ACTUAL gender matches your preference
            if entry.is_vip and entry.gender != "any":
                candidate_actual_gender = candidate.get("user_gender", "any")
                if candidate_actual_gender != "any" and candidate_actual_gender != entry.gender:
                    continue

            # VIP country filter - check if candidate's ACTUAL country matches your preference  
            if entry.is_vip and entry.country != "any":
                candidate_actual_country = candidate.get("user_country", "any")
                if candidate_actual_country != "any" and candidate_actual_country != entry.country:
                    continue
            
            # Reverse check: Does candidate's filter match YOUR actual attributes?
            if candidate.get("is_vip"):
                # If candidate filtered for gender, check if YOUR gender matches their filter
                candidate_gender_filter = candidate.get("gender", "any")
                if candidate_gender_filter != "any":
                    your_actual_gender = entry.user_gender
                    if your_actual_gender != "any" and your_actual_gender != candidate_gender_filter:
                        continue
                
                # If candidate filtered for country, check if YOUR country matches their filter
                candidate_country_filter = candidate.get("country", "any")
                if candidate_country_filter != "any":
                    your_actual_country = entry.user_country
                    if your_actual_country != "any" and your_actual_country != candidate_country_filter:
                        continue

            return candidate

        return None

    # ── Session Management ────────────────────────────────────────────────────

    async def _create_session(self, user1: QueueEntry, user2: QueueEntry) -> Session:
        """Create a new chat session between two users."""
        session_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        session = Session(
            session_id=session_id,
            user1_id=user1.user_id,
            user2_id=user2.user_id,
            user1_anon=user1.anon_name,
            user2_anon=user2.anon_name,
            user1_emoji=user1.anon_emoji,
            user2_emoji=user2.anon_emoji,
            topic=user1.topic,
            started_at=now,
        )

        # Store session
        key = SESSION_KEY.format(session_id=session_id)
        await self.redis.hset(key, mapping=session.to_dict())
        await self.redis.expire(key, settings.SESSION_TTL)

        # Map each user to their session
        for uid in [user1.user_id, user2.user_id]:
            ukey = USER_SESSION_KEY.format(user_id=uid)
            await self.redis.set(ukey, session_id, ex=settings.SESSION_TTL)

        # Publish match event to both users' channels
        match_event = {
            "type": "matched",
            "session_id": session_id,
            "topic": session.topic,
        }
        await self.redis.publish(f"zephr:user:{user1.user_id}", json.dumps({
            **match_event,
            "peer_anon": user2.anon_name,
            "peer_emoji": user2.anon_emoji,
        }))
        await self.redis.publish(f"zephr:user:{user2.user_id}", json.dumps({
            **match_event,
            "peer_anon": user1.anon_name,
            "peer_emoji": user1.anon_emoji,
        }))

        await self._increment_stat("active_chats")
        await self._increment_stat("total_matches")
        return session

    async def get_session(self, session_id: str) -> Optional[dict]:
        key = SESSION_KEY.format(session_id=session_id)
        data = await self.redis.hgetall(key)
        return data if data else None

    async def get_user_session(self, user_id: int) -> Optional[str]:
        key = USER_SESSION_KEY.format(user_id=user_id)
        return await self.redis.get(key)

    async def increment_message_count(self, session_id: str):
        key = SESSION_KEY.format(session_id=session_id)
        await self.redis.hincrby(key, "msg_count", 1)

    async def leave_session(self, user_id: int, reason: str = "left") -> Optional[str]:
        """End user's current session. Notifies peer."""
        session_id = await self.get_user_session(user_id)
        if not session_id:
            return None

        session_data = await self.get_session(session_id)
        if not session_data:
            return None

        # Determine peer
        u1 = int(session_data.get("user1_id", 0))
        u2 = int(session_data.get("user2_id", 0))
        peer_id = u2 if user_id == u1 else u1

        # Notify peer
        event = json.dumps({"type": "peer_left", "reason": reason})
        await self.redis.publish(f"zephr:user:{peer_id}", event)

        # Cleanup
        await self.redis.delete(USER_SESSION_KEY.format(user_id=user_id))
        await self.redis.delete(USER_SESSION_KEY.format(user_id=peer_id))
        await self.redis.delete(SESSION_KEY.format(session_id=session_id))

        await self._decrement_stat("active_chats")
        return session_id

    # ── Message Relay ─────────────────────────────────────────────────────────

    async def send_message(
        self,
        session_id: str,
        sender_id: int,
        message: dict,
    ) -> bool:
        """
        Relay a message from sender to peer via pub/sub.
        Returns False if session not found or sender not in session.
        """
        session_data = await self.get_session(session_id)
        if not session_data:
            return False

        u1 = int(session_data.get("user1_id", 0))
        u2 = int(session_data.get("user2_id", 0))

        if sender_id not in (u1, u2):
            return False

        peer_id = u2 if sender_id == u1 else u1

        event = json.dumps({
            "type": "message",
            "session_id": session_id,
            **message,
        })
        await self.redis.publish(f"zephr:user:{peer_id}", event)
        await self.increment_message_count(session_id)
        return True

    # ── Stats ─────────────────────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        data = await self.redis.hgetall(STATS_KEY)
        # Count users in all queues
        queued = 0
        for topic in ["random", "tech", "language", "vibes", "deep", "gaming"]:
            queued += await self.redis.zcard(QUEUE_KEY.format(topic=topic))

        return {
            "online": int(data.get("online", 0)),
            "active_chats": int(data.get("active_chats", 0)),
            "total_matches": int(data.get("total_matches", 0)),
            "queued": queued,
        }

    async def set_online(self, user_id: int, online: bool):
        key = f"zephr:online:{user_id}"
        if online:
            await self.redis.set(key, "1", ex=120)  # 2 min TTL, refreshed by heartbeat
            await self.redis.hincrby(STATS_KEY, "online", 1)
        else:
            existed = await self.redis.delete(key)
            if existed:
                await self.redis.hincrby(STATS_KEY, "online", -1)

    async def _increment_stat(self, field: str):
        await self.redis.hincrby(STATS_KEY, field, 1)

    async def _decrement_stat(self, field: str):
        await self.redis.hincrby(STATS_KEY, field, -1)

    # ── Rate Limiting ─────────────────────────────────────────────────────────

    async def check_rate_limit(self, user_id: int, action: str) -> bool:
        """Returns True if allowed, False if rate limited."""
        if action == "message":
            key = RATE_MSG_KEY.format(user_id=user_id)
            limit = settings.RATE_LIMIT_MESSAGES
            ttl = 60
        else:
            key = RATE_MATCH_KEY.format(user_id=user_id)
            limit = settings.RATE_LIMIT_MATCHES
            ttl = 3600

        count = await self.redis.incr(key)
        if count == 1:
            await self.redis.expire(key, ttl)
        return count <= limit


# ── Singleton ─────────────────────────────────────────────────────────────────
engine = MatchingEngine()
