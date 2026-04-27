"""
zephr.chat — FastAPI Backend
Real-time WebSocket hub + REST API
"""
import asyncio
import json
import random
from contextlib import asynccontextmanager
from typing import Dict, Optional

from redis import asyncio as aioredis
from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    Depends, HTTPException, Request, status
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from auth import verify_telegram_init_data, verify_telegram_init_data_dev
from config import settings
from database import get_db, init_db, get_or_create_user, User, Report, ChatSession
from matching import engine as match_engine, QueueEntry
from moderation import moderator
import bot as telegram_bot
import razorpay_routes

import logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("zephr")


# ── Anon Name Generator ───────────────────────────────────────────────────────
ADJECTIVES = [
    "Cosmic", "Velvet", "Neon", "Silent", "Mystic", "Amber", "Shadow",
    "Crystal", "Lunar", "Jade", "Prism", "Astral", "Arctic", "Crimson",
    "Golden", "Silver", "Storm", "Azure", "Ember", "Frost"
]
NOUNS = [
    "Fox", "Wanderer", "Echo", "Specter", "Moth", "Phoenix", "Drifter",
    "Comet", "Lynx", "Sage", "Cipher", "Nomad", "Falcon", "Raven",
    "Tide", "Gale", "Spark", "Drift", "Flare", "Mist"
]
EMOJIS = ["🦊", "🌙", "🔮", "🦋", "🌊", "🎭", "🌸", "⚡", "🦚", "🌿",
          "🐺", "🦅", "🌀", "🔱", "🌺", "🦁", "🐉", "🦜", "🌙", "🎯"]

def gen_anon_name() -> str:
    return f"{random.choice(ADJECTIVES)} {random.choice(NOUNS)}"

def gen_anon_emoji() -> str:
    return random.choice(EMOJIS)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    await match_engine.connect()
    await moderator.setup()
    
    # Start Telegram bot
    if telegram_bot.bot:
        await telegram_bot.setup_bot()
        if settings.WEBHOOK_URL:
            webhook_url = f"{settings.WEBHOOK_URL}/bot/webhook"
            await telegram_bot.bot.set_webhook(
                url=webhook_url,
                drop_pending_updates=True
            )
            log.info(f"✅ Telegram bot webhook set to {webhook_url}")
        else:
            log.warning("⚠️ WEBHOOK_URL not set - bot running in dev mode")
    
    log.info("🚀 zephr.chat backend started")
    yield
    
    # Shutdown
    if telegram_bot.bot:
        await telegram_bot.bot.delete_webhook()
    await match_engine.disconnect()
    await moderator.teardown()
    log.info("👋 zephr.chat backend stopped")
    


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="zephr.chat API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.BOT_TOKEN == "dev" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for Telegram Mini Apps
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Include Razorpay payment routes
app.include_router(razorpay_routes.router)

# ── Telegram Bot Webhook ──────────────────────────────────────────────────────
@app.post("/bot/webhook")
async def telegram_webhook(request: Request):

    if not telegram_bot.bot:
        raise HTTPException(status_code=503, detail="Bot not configured")
    
    update_data = await request.json()
    from aiogram.types import Update
    update = Update(**update_data)
    await telegram_bot.dp.feed_update(telegram_bot.bot, update)
    return {"ok": True}



# ── Auth Helper ───────────────────────────────────────────────────────────────
def get_telegram_user(request: Request) -> dict:
    """Extract and validate Telegram user from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("tma "):
        raise HTTPException(status_code=401, detail="Missing Telegram auth")

    init_data = auth[4:]

    # Dev mode bypass
    if settings.BOT_TOKEN == "dev":
        user = verify_telegram_init_data_dev(init_data)
    else:
        user = verify_telegram_init_data(init_data)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid Telegram auth")

    return user


# ── REST Endpoints ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "zephr.chat"}


@app.get("/checkout.html")
async def serve_checkout():
    """Serve the checkout page - fetches from GitHub frontend repo"""
    from fastapi.responses import HTMLResponse
    import aiohttp
    
    # Fetch checkout.html from GitHub frontend repo
    github_url = "https://raw.githubusercontent.com/zephrchat-sys/zephr-chat-frontend/main/checkout.html"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(github_url) as response:
                if response.status == 200:
                    html_content = await response.text()
                    log.info(f"✅ Serving checkout.html from GitHub frontend repo")
                    return HTMLResponse(content=html_content)
                else:
                    log.error(f"❌ Failed to fetch checkout.html from GitHub: {response.status}")
                    raise HTTPException(status_code=503, detail="Checkout page temporarily unavailable")
    except Exception as e:
        log.error(f"❌ Error fetching checkout.html: {e}")
        raise HTTPException(status_code=503, detail="Checkout page temporarily unavailable")


@app.get("/api/stats")
async def get_stats():
    """Public stats for the lobby display."""
    stats = await match_engine.get_stats()
    return stats


@app.post("/api/auth")
async def authenticate(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Authenticate user via Telegram initData.
    Returns user profile + VIP status.
    """
    tg_user = get_telegram_user(request)
    user = await get_or_create_user(
        db,
        user_id=tg_user["id"],
        first_name=tg_user.get("first_name"),
        username=tg_user.get("username"),
        language_code=tg_user.get("language_code", "en"),
    )

    if user.is_banned:
        raise HTTPException(status_code=403, detail="Account suspended")

    return {
        "user_id": user.id,
        "is_vip": user.is_vip_active(),
        "vip_expires_at": user.vip_expires_at.isoformat() if user.vip_expires_at else None,
        "referral_code": user.referral_code,
        "match_count": user.match_count,
        "prefs": {
            "language": user.pref_language,
            "age_group": user.pref_age_group,
            "topic": user.pref_topic,
            "gender": user.pref_gender,
            "country": user.pref_country,
        }
    }


@app.put("/api/prefs")
async def update_prefs(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Save user filter preferences."""
    tg_user = get_telegram_user(request)
    body = await request.json()

    from sqlalchemy import select, update
    await db.execute(
        update(User).where(User.id == tg_user["id"]).values(
            pref_language=body.get("language", "any"),
            pref_age_group=body.get("age_group", "any"),
            pref_topic=body.get("topic", "random"),
            pref_gender=body.get("gender", "any"),
            pref_country=body.get("country", "any"),
        )
    )
    await db.commit()
    return {"ok": True}


@app.put("/api/profile")
async def update_profile(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Update user's own profile (age, gender, country) - optional fields."""
    tg_user = get_telegram_user(request)
    body = await request.json()

    from sqlalchemy import select, update
    
    # Build update dict with only provided fields
    update_data = {}
    if "age" in body:
        age = body.get("age")
        if age and isinstance(age, int) and 13 <= age <= 100:
            update_data["age"] = age
    if "gender" in body and body.get("gender"):
        update_data["gender"] = body.get("gender")
    if "country" in body and body.get("country"):
        update_data["country"] = body.get("country")
    
    if update_data:
        await db.execute(
            update(User).where(User.id == tg_user["id"]).values(**update_data)
        )
        await db.commit()
    
    return {"ok": True}


@app.post("/api/report")
async def submit_report(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Submit a report against a peer."""
    tg_user = get_telegram_user(request)
    body = await request.json()

    session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")

    # Verify reporter is in session
    session_data = await match_engine.get_session(session_id)
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")

    u1 = int(session_data.get("user1_id", 0))
    u2 = int(session_data.get("user2_id", 0))
    reporter_id = tg_user["id"]

    if reporter_id not in (u1, u2):
        raise HTTPException(status_code=403, detail="Not in this session")

    reported_id = u2 if reporter_id == u1 else u1

    report = Report(
        reporter_id=reporter_id,
        reported_id=reported_id,
        session_id=session_id,
        reason=body.get("reason", "unspecified"),
    )
    db.add(report)

    # Increment report count
    from sqlalchemy import select, update
    await db.execute(
        update(User).where(User.id == reported_id).values(
            report_count=User.report_count + 1
        )
    )
    await db.commit()

    # Auto-ban if too many reports
    result = await db.execute(
        select(User.report_count).where(User.id == reported_id)
    )
    count = result.scalar()
    if count and count >= 5:
        await db.execute(
            update(User).where(User.id == reported_id).values(
                is_banned=True, ban_reason="Auto-banned: 5+ reports"
            )
        )
        await db.commit()
        log.warning(f"Auto-banned user {reported_id} after {count} reports")

    return {"ok": True, "message": "Report submitted. Thank you for keeping zephr safe."}


@app.post("/api/friend-request")
async def send_friend_request(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Send a friend request to the current chat peer."""
    tg_user = get_telegram_user(request)
    body = await request.json()
    
    session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    
    # Get session from Redis to find the peer
    session_data = await match_engine.get_session(session_id)
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")
    
    u1 = int(session_data.get("user1_id", 0))
    u2 = int(session_data.get("user2_id", 0))
    requester_id = tg_user["id"]
    
    if requester_id not in (u1, u2):
        raise HTTPException(status_code=403, detail="Not in this session")
    
    # Determine who the peer is
    recipient_id = u2 if requester_id == u1 else u1
    
    # Check if friend request already exists
    from sqlalchemy import select
    from database import FriendRequest
    
    existing = await db.execute(
        select(FriendRequest).where(
            (FriendRequest.requester_id == requester_id) &
            (FriendRequest.recipient_id == recipient_id) &
            (FriendRequest.status == "pending")
        )
    )
    
    if existing.scalar_one_or_none():
        return {"ok": True, "message": "Friend request already sent"}
    
    # Create friend request
    friend_req = FriendRequest(
        requester_id=requester_id,
        recipient_id=recipient_id,
        session_id=session_id,
        status="pending"
    )
    
    db.add(friend_req)
    await db.commit()
    await db.refresh(friend_req)
    
    log.info(f"👥 Friend request sent from {requester_id} to {recipient_id} in session {session_id}")
    
    return {"ok": True, "message": "Friend request sent", "request_id": friend_req.id}


@app.get("/api/friends")
async def get_friends(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Get user's friend list with online status and last seen."""
    tg_user = get_telegram_user(request)
    telegram_id = tg_user["id"]
    
    # Get friends
    from sqlalchemy import select
    from database import Friend, User
    
    result = await db.execute(
        select(Friend).where(Friend.user_id == telegram_id)
    )
    friends = result.scalars().all()
    
    # Get friend details with online status
    friend_list = []
    for friend_record in friends:
        friend_user_result = await db.execute(
            select(User).where(User.id == friend_record.friend_id)
        )
        friend_data = friend_user_result.scalar_one_or_none()
        if friend_data:
            # Check if friend is online via Redis (user:online:{user_id} key)
            online_check = await match_engine.redis.get(f"zephr:user:online:{friend_data.id}")
            is_online = bool(online_check)
            
            friend_list.append({
                "telegram_id": friend_data.id,
                "first_name": friend_data.first_name,
                "username": friend_data.username,
                "added_at": friend_record.created_at.isoformat(),
                "is_online": is_online,
                "last_seen": friend_data.last_seen.isoformat() if friend_data.last_seen else None
            })
    
    return {"ok": True, "friends": friend_list, "count": len(friend_list)}


@app.delete("/api/friends/{friend_id}")
async def remove_friend(
    friend_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Remove a friend (bidirectional)."""
    tg_user = get_telegram_user(request)
    telegram_id = tg_user["id"]
    
    from sqlalchemy import select, delete
    from database import Friend
    
    # Delete both friendship records (bidirectional)
    await db.execute(
        delete(Friend).where(
            ((Friend.user_id == telegram_id) & (Friend.friend_id == friend_id)) |
            ((Friend.user_id == friend_id) & (Friend.friend_id == telegram_id))
        )
    )
    await db.commit()
    
    log.info(f"👥 Friendship removed: {telegram_id} <-> {friend_id}")
    
    return {"ok": True, "message": "Friend removed"}


@app.post("/api/start-friend-chat")
async def start_friend_chat(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Start a direct chat with a friend."""
    tg_user = get_telegram_user(request)
    telegram_id = tg_user["id"]
    body = await request.json()
    
    friend_id = body.get("friend_id")
    if not friend_id:
        raise HTTPException(status_code=400, detail="friend_id required")
    
    # Verify friendship exists
    from sqlalchemy import select
    from database import Friend
    
    result = await db.execute(
        select(Friend).where(
            (Friend.user_id == telegram_id) & (Friend.friend_id == friend_id)
        )
    )
    friendship = result.scalar_one_or_none()
    
    if not friendship:
        raise HTTPException(status_code=404, detail="Not friends with this user")
    
    # Check if friend is online via Redis
    online_check = await match_engine.redis.get(f"zephr:user:online:{friend_id}")
    is_online = bool(online_check)
    
    if not is_online:
        raise HTTPException(status_code=400, detail="Friend is offline. They need to be online to chat.")
    
    # Create a friend chat session
    import uuid
    session_id = str(uuid.uuid4())
    
    # Get friend's name
    from database import User
    friend_result = await db.execute(select(User).where(User.id == friend_id))
    friend_user = friend_result.scalar_one_or_none()
    
    # Get current user's name
    user_result = await db.execute(select(User).where(User.id == telegram_id))
    current_user = user_result.scalar_one_or_none()
    
    # Store session in Redis
    session_data = {
        "user1_id": str(telegram_id),
        "user2_id": str(friend_id),
        "topic": "Friend Chat",
        "is_friend_chat": True,
        "user1_name": current_user.first_name if current_user else "Friend",
        "user2_name": friend_user.first_name if friend_user else "Friend",
    }
    
    await match_engine.redis.setex(
        f"zephr:session:{session_id}",
        3600,  # 1 hour expiry
        json.dumps(session_data)
    )
    
    # Notify both users via WebSocket
    from datetime import datetime as dt
    
    # Send to user initiating chat
    await match_engine.redis.publish(
        f"zephr:user:{telegram_id}",
        json.dumps({
            "type": "matched",
            "session_id": session_id,
            "peer_anon": friend_user.first_name if friend_user else "Friend",
            "peer_emoji": "👤",
            "topic": "Friend Chat",
        })
    )
    
    # Send to friend
    await match_engine.redis.publish(
        f"zephr:user:{friend_id}",
        json.dumps({
            "type": "matched",
            "session_id": session_id,
            "peer_anon": current_user.first_name if current_user else "Friend",
            "peer_emoji": "👤",
            "topic": "Friend Chat",
        })
    )
    
    log.info(f"👥 Friend chat started: {telegram_id} <-> {friend_id} (session: {session_id})")
    
    return {"ok": True, "session_id": session_id, "message": "Chat started"}


@app.get("/api/friend-requests/pending")
async def get_pending_requests(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Get pending friend requests received by the user."""
    tg_user = get_telegram_user(request)
    telegram_id = tg_user["id"]
    
    # Get pending requests where user is the recipient
    from sqlalchemy import select
    from database import FriendRequest, User
    
    result = await db.execute(
        select(FriendRequest).where(
            (FriendRequest.recipient_id == telegram_id) &
            (FriendRequest.status == "pending")
        ).order_by(FriendRequest.created_at.desc())
    )
    requests = result.scalars().all()
    
    # Get requester details
    request_list = []
    for req in requests:
        requester_result = await db.execute(
            select(User).where(User.id == req.requester_id)
        )
        requester_data = requester_result.scalar_one_or_none()
        if requester_data:
            request_list.append({
                "request_id": req.id,
                "from_user": {
                    "telegram_id": requester_data.id,
                    "first_name": requester_data.first_name,
                    "username": requester_data.username
                },
                "created_at": req.created_at.isoformat()
            })
    
    return {"ok": True, "requests": request_list, "count": len(request_list)}


@app.post("/api/friend-requests/{request_id}/accept")
async def accept_friend_request(
    request_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Accept a friend request."""
    tg_user = get_telegram_user(request)
    telegram_id = tg_user["id"]
    
    # Get friend request
    from sqlalchemy import select, update
    from database import FriendRequest, Friend
    
    result = await db.execute(
        select(FriendRequest).where(
            (FriendRequest.id == request_id) &
            (FriendRequest.recipient_id == telegram_id) &
            (FriendRequest.status == "pending")
        )
    )
    friend_req = result.scalar_one_or_none()
    
    if not friend_req:
        raise HTTPException(status_code=404, detail="Friend request not found")
    
    # Update status
    await db.execute(
        update(FriendRequest).where(
            FriendRequest.id == request_id
        ).values(status="accepted", responded_at=datetime.utcnow())
    )
    
    # Create bidirectional friendship
    friend1 = Friend(user_id=telegram_id, friend_id=friend_req.requester_id)
    friend2 = Friend(user_id=friend_req.requester_id, friend_id=telegram_id)
    
    db.add(friend1)
    db.add(friend2)
    await db.commit()
    
    log.info(f"👥 Friend request {request_id} accepted: {telegram_id} <-> {friend_req.requester_id}")
    
    return {"ok": True, "message": "Friend request accepted"}


@app.post("/api/friend-requests/{request_id}/decline")
async def decline_friend_request(
    request_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Decline a friend request."""
    tg_user = get_telegram_user(request)
    telegram_id = tg_user["id"]
    
    # Get friend request
    from sqlalchemy import select, update
    from database import FriendRequest
    
    result = await db.execute(
        select(FriendRequest).where(
            (FriendRequest.id == request_id) &
            (FriendRequest.recipient_id == telegram_id) &
            (FriendRequest.status == "pending")
        )
    )
    friend_req = result.scalar_one_or_none()
    
    if not friend_req:
        raise HTTPException(status_code=404, detail="Friend request not found")
    
    # Update status
    await db.execute(
        update(FriendRequest).where(
            FriendRequest.id == request_id
        ).values(status="declined", responded_at=datetime.utcnow())
    )
    await db.commit()
    
    log.info(f"👥 Friend request {request_id} declined by {telegram_id}")
    
    return {"ok": True, "message": "Friend request declined"}


@app.post("/api/rate")
async def submit_rating(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Submit post-chat rating."""
    tg_user = get_telegram_user(request)
    body = await request.json()

    session_id = body.get("session_id")
    rating = int(body.get("rating", 0))

    if not session_id or not 1 <= rating <= 5:
        raise HTTPException(status_code=400, detail="Invalid request")

    from sqlalchemy import select, update
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id)
    )
    session_rec = result.scalar_one_or_none()
    if not session_rec:
        return {"ok": True}  # Session may have already been cleaned up

    uid = tg_user["id"]
    if uid == session_rec.user1_id:
        await db.execute(
            update(ChatSession).where(ChatSession.id == session_id).values(user1_rating=rating)
        )
    elif uid == session_rec.user2_id:
        await db.execute(
            update(ChatSession).where(ChatSession.id == session_id).values(user2_rating=rating)
        )
    await db.commit()
    return {"ok": True}


@app.post("/api/upload-media")
async def upload_media(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Handle media uploads and broadcast to peer."""
    tg_user = get_telegram_user(request)
    body = await request.json()
    
    session_id = body.get("session_id")
    file_data = body.get("file_data")  # base64 data URL
    file_type = body.get("file_type", "image/jpeg")
    
    if not session_id or not file_data:
        raise HTTPException(status_code=400, detail="session_id and file_data required")
    
    # Get session to find peer
    session_data = await match_engine.get_session(session_id)
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")
    
    user_id = tg_user["id"]
    u1 = int(session_data.get("user1_id", 0))
    u2 = int(session_data.get("user2_id", 0))
    
    if user_id not in (u1, u2):
        raise HTTPException(status_code=403, detail="Not in this session")
    
    # Determine peer
    peer_id = u2 if user_id == u1 else u1
    
    # Send media message to peer via pub/sub
    await match_engine.redis.publish(
        f"zephr:user:{peer_id}",
        json.dumps({
            "type": "message",
            "session_id": session_id,
            "media_type": file_type,
            "media_data": file_data,
            "timestamp": asyncio.get_event_loop().time()
        })
    )
    
    log.info(f"Media sent from {user_id} to {peer_id} in session {session_id}")
    return {"ok": True, "message": "Media sent"}


# ── WebSocket Manager ─────────────────────────────────────────────────────────

class ConnectionManager:
    """Tracks active WebSocket connections per user."""

    def __init__(self):
        self.connections: Dict[int, WebSocket] = {}
        self._pubsub_tasks: Dict[int, asyncio.Task] = {}

    async def connect(self, user_id: int, websocket: WebSocket):
        await websocket.accept()
        # Disconnect any existing connection for this user
        if user_id in self.connections:
            try:
                await self.connections[user_id].close()
            except Exception:
                pass

        self.connections[user_id] = websocket

        # Start pub/sub listener for this user
        task = asyncio.create_task(self._listen_pubsub(user_id, websocket))
        self._pubsub_tasks[user_id] = task

        await match_engine.set_online(user_id, True)
        log.info(f"User {user_id} connected ({len(self.connections)} total)")

    async def disconnect(self, user_id: int):
        self.connections.pop(user_id, None)

        # Cancel pub/sub listener
        task = self._pubsub_tasks.pop(user_id, None)
        if task:
            task.cancel()

        await match_engine.set_online(user_id, False)
        log.info(f"User {user_id} disconnected ({len(self.connections)} total)")

    async def send(self, user_id: int, data: dict):
        ws = self.connections.get(user_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                await self.disconnect(user_id)

    async def _listen_pubsub(self, user_id: int, websocket: WebSocket):
        """
        Subscribe to user's Redis pub/sub channel and forward
        events to their WebSocket in real-time.
        """
        redis = aioredis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
        pubsub = redis.pubsub()
        channel = f"zephr:user:{user_id}"

        try:
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        await websocket.send_json(data)
                    except Exception:
                        break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"PubSub error for user {user_id}: {e}")
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            await redis.close()


manager = ConnectionManager()


# ── WebSocket Endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws/{init_data_encoded}")
async def websocket_endpoint(
    websocket: WebSocket,
    init_data_encoded: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Single WebSocket endpoint for all real-time communication.
    Client sends JSON messages, server responds via pub/sub.

    Message types from client:
      - join_queue: {type, topic, language, age_group, gender, country}
      - leave_queue: {type}
      - send_message: {type, session_id, text, translate_to?}
      - send_media: {type, session_id, media_type, file_id}
      - leave_session: {type, session_id}
      - heartbeat: {type}

    Message types to client:
      - matched: {type, session_id, peer_anon, peer_emoji, topic}
      - message: {type, session_id, text, timestamp, translated?}
      - peer_left: {type, reason}
      - moderation: {type, action, reason}
      - stats: {type, online, active_chats}
      - error: {type, code, message}
      - pong: {type}
    """
    from urllib.parse import unquote
    init_data = unquote(init_data_encoded)

    # Authenticate
    if settings.BOT_TOKEN == "dev":
        tg_user = verify_telegram_init_data_dev(init_data) or {"id": 999999999, "first_name": "Dev"}
    else:
        tg_user = verify_telegram_init_data(init_data)
        if not tg_user:
            await websocket.close(code=4001, reason="Unauthorized")
            return

    user_id = tg_user["id"]

    # DB user
    user = await get_or_create_user(
        db, user_id=user_id,
        first_name=tg_user.get("first_name"),
        username=tg_user.get("username"),
    )

    if user.is_banned:
        await websocket.close(code=4003, reason="Banned")
        return

    await manager.connect(user_id, websocket)

    # Send initial stats
    stats = await match_engine.get_stats()
    await websocket.send_json({"type": "stats", **stats})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            msg_type = msg.get("type")

            # ── Heartbeat ─────────────────────────────────────
            if msg_type == "heartbeat":
                await match_engine.set_online(user_id, True)
                await websocket.send_json({"type": "pong"})

            # ── Join Queue ────────────────────────────────────
            elif msg_type == "join_queue":
                if not await match_engine.check_rate_limit(user_id, "match"):
                    await websocket.send_json({
                        "type": "error", "code": "rate_limited",
                        "message": "Too many match attempts. Please wait."
                    })
                    continue

                entry = QueueEntry(
                    user_id=user_id,
                    anon_name=gen_anon_name(),
                    anon_emoji=gen_anon_emoji(),
                    language=msg.get("language", "any"),
                    age_group=msg.get("age_group", "any"),
                    topic=msg.get("topic", "random"),
                    gender=msg.get("gender", "any") if user.is_vip_active() else "any",
                    country=msg.get("country", "any") if user.is_vip_active() else "any",
                    is_vip=user.is_vip_active(),
                )

                session = await match_engine.join_queue(entry)
                if session:
                    # Matched immediately
                    is_user1 = session.user1_id == user_id
                    peer_anon = session.user2_anon if is_user1 else session.user1_anon
                    peer_emoji = session.user2_emoji if is_user1 else session.user1_emoji

                    await websocket.send_json({
                        "type": "matched",
                        "session_id": session.session_id,
                        "peer_anon": peer_anon,
                        "peer_emoji": peer_emoji,
                        "topic": session.topic,
                    })
                else:
                    await websocket.send_json({"type": "queued", "topic": entry.topic})

            # ── Leave Queue ───────────────────────────────────
            elif msg_type == "leave_queue":
                await match_engine.leave_queue(user_id, msg.get("topic"))
                await websocket.send_json({"type": "queue_left"})

            # ── Send Message ──────────────────────────────────
            elif msg_type == "send_message":
                if not await match_engine.check_rate_limit(user_id, "message"):
                    await websocket.send_json({
                        "type": "error", "code": "rate_limited",
                        "message": "Slow down! Too many messages."
                    })
                    continue

                session_id = msg.get("session_id")
                text = msg.get("text", "").strip()

                if not text or not session_id:
                    continue

                # Moderate
                text = moderator.sanitize(text)
                mod_result = await moderator.check(text, user_id)

                if mod_result.action == "ban":
                    # Ban user
                    from sqlalchemy import update as sa_update
                    await db.execute(
                        sa_update(User).where(User.id == user_id).values(
                            is_banned=True,
                            ban_reason=mod_result.reason
                        )
                    )
                    await db.commit()
                    await websocket.send_json({
                        "type": "moderation",
                        "action": "ban",
                        "reason": mod_result.reason
                    })
                    await manager.disconnect(user_id)
                    return

                if not mod_result.allowed:
                    await websocket.send_json({
                        "type": "moderation",
                        "action": mod_result.action,
                        "reason": mod_result.reason
                    })
                    continue

                # Relay message
                from datetime import datetime as dt
                message_data = {
                    "text": text,
                    "timestamp": dt.utcnow().isoformat(),
                    "score": mod_result.score,
                }

                sent = await match_engine.send_message(session_id, user_id, message_data)
                if not sent:
                    await websocket.send_json({
                        "type": "error", "code": "session_invalid",
                        "message": "Session has ended"
                    })
                    continue

                # Echo back to sender (confirmed)
                await websocket.send_json({
                    "type": "message_sent",
                    "session_id": session_id,
                    "timestamp": message_data["timestamp"],
                })

                # Warn if needed
                if mod_result.action == "warn":
                    await websocket.send_json({
                        "type": "moderation",
                        "action": "warn",
                        "reason": mod_result.reason
                    })

            # ── Send Media ────────────────────────────────────
            elif msg_type == "send_media":
                session_id = msg.get("session_id")
                media_type = msg.get("media_type", "photo")
                file_id = msg.get("file_id", "")

                if not session_id:
                    continue

                from datetime import datetime as dt
                media_data = {
                    "media_type": media_type,
                    "file_id": file_id,
                    "timestamp": dt.utcnow().isoformat(),
                }
                await match_engine.send_message(session_id, user_id, {
                    **media_data, "is_media": True
                })
                await websocket.send_json({
                    "type": "media_sent",
                    "session_id": session_id,
                    "timestamp": media_data["timestamp"],
                })

            # ── Friend Request ────────────────────────────────
            elif msg_type == "friend_request":
                session_id = msg.get("session_id")
                if not session_id:
                    continue
                
                # Forward to peer via Redis pub/sub
                session_data = await match_engine.get_session(session_id)
                if not session_data:
                    continue
                
                u1 = int(session_data.get("user1_id", 0))
                u2 = int(session_data.get("user2_id", 0))
                
                if user_id not in (u1, u2):
                    continue
                
                # Send to peer
                peer_id = u2 if user_id == u1 else u1
                from datetime import datetime as dt
                await match_engine.redis.publish(
                    f"zephr:user:{peer_id}",
                    json.dumps({
                        "type": "friend_request",
                        "session_id": session_id,
                        "timestamp": dt.utcnow().isoformat()
                    })
                )
                
                log.info(f"👥 Friend request notification sent to {peer_id}")

            # ── Leave Session ─────────────────────────────────
            elif msg_type == "leave_session":
                session_id = await match_engine.leave_session(user_id, reason="user_left")
                if session_id:
                    # Record session end in DB
                    from datetime import datetime as dt
                    session_data = await match_engine.get_session(session_id)
                    await db.execute(
                        ChatSession.__table__.insert().values(
                            id=session_id,
                            user1_id=user_id,
                            user2_id=0,  # Anonymised
                            ended_at=dt.utcnow(),
                        )
                    )
                    await db.commit()

                await websocket.send_json({"type": "session_left"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error(f"WS error for user {user_id}: {e}")
    finally:
        await match_engine.leave_queue(user_id)
        await match_engine.leave_session(user_id, reason="disconnected")
        await manager.disconnect(user_id)


# ── Mount Static Files (Frontend) - MUST BE LAST ──────────────────────────────
# Try multiple possible paths for different deployment environments
import os

possible_frontend_paths = [
    "/app/frontend",                                      # Railway direct path
    os.path.join(os.path.dirname(__file__), "..", "frontend"),  # Relative path
    "/app/zephr-chat/frontend",                          # Alternative Railway path
    os.path.join(os.getcwd(), "frontend"),               # Current working directory
    "../frontend",                                        # Simple relative
]

frontend_dir = None
for path in possible_frontend_paths:
    abs_path = os.path.abspath(path)
    if os.path.exists(abs_path) and os.path.isdir(abs_path):
        frontend_dir = abs_path
        log.info(f"✅ Found frontend directory at: {frontend_dir}")
        break

if frontend_dir:
    try:
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="static")
        log.info(f"✅ Static files mounted and serving from: {frontend_dir}")
        
        # List files in frontend directory for debugging
        files = os.listdir(frontend_dir)
        log.info(f"📁 Frontend files available: {', '.join(files[:10])}")  # First 10 files
    except Exception as e:
        log.error(f"❌ Failed to mount static files: {e}")
else:
    log.warning(f"⚠️ Frontend directory not found. Searched: {possible_frontend_paths}")
    log.warning(f"📍 Current working directory: {os.getcwd()}")
    log.warning(f"📍 Script directory: {os.path.dirname(__file__)}")