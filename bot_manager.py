"""
Bot Manager - Integrates AI bot into Zephr Chat matching system
Manages bot queue entries and message handling
"""
import asyncio
import json
import logging
from typing import Optional, Set
from redis import asyncio as aioredis

from ai_bot import ai_bot, AIBot
from config import settings

log = logging.getLogger("bot_manager")


class BotManager:
    """Manages AI bot presence in queues and chat sessions"""
    
    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
        self.active_sessions: Set[str] = set()  # Track sessions where bot is active
        self._listener_tasks: dict = {}  # Track Redis pub/sub listeners
        
    async def connect(self):
        """Connect to Redis"""
        self.redis = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
        log.info("✅ Bot Manager connected to Redis")
    
    async def disconnect(self):
        """Disconnect from Redis and clean up"""
        # Cancel all listener tasks
        for task in self._listener_tasks.values():
            task.cancel()
        
        if self.redis:
            await self.redis.close()
        log.info("👋 Bot Manager disconnected")
    
    async def add_bot_to_queue(self, topic: str = "random"):
        """
        Add bot to matching queue for a specific topic
        Bot will wait for users to match with
        """
        queue_key = f"zephr:queue:{topic}"
        entry_data = ai_bot.get_queue_entry_data(topic)
        
        # Add to queue with current timestamp as score
        score = entry_data["joined_at"]
        await self.redis.zadd(queue_key, {json.dumps(entry_data): score})
        await self.redis.expire(queue_key, 3600)  # 1 hour TTL
        
        log.info(f"🤖 Bot added to {topic} queue")
    
    async def ensure_bot_in_queues(self):
        """
        Ensure bot is present in all active queues
        Call this periodically or when bot gets matched
        """
        topics = ["random", "tech", "language", "vibes", "deep", "gaming"]
        
        for topic in topics:
            # Check if bot is already in queue
            queue_key = f"zephr:queue:{topic}"
            members = await self.redis.zrange(queue_key, 0, -1)
            
            bot_in_queue = False
            for member in members:
                try:
                    data = json.loads(member)
                    if data.get("user_id") == AIBot.BOT_USER_ID:
                        bot_in_queue = True
                        break
                except:
                    pass
            
            if not bot_in_queue:
                await self.add_bot_to_queue(topic)
    
    async def start_bot_queue_maintenance(self):
        """
        Background task to keep bot in queues
        Runs every 30 seconds to re-add bot if needed
        """
        while True:
            try:
                await self.ensure_bot_in_queues()
                await asyncio.sleep(30)  # Check every 30 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Bot queue maintenance error: {e}")
                await asyncio.sleep(30)
    
    async def handle_bot_message(self, session_id: str, user_id: int, message_text: str):
        """
        Handle incoming message when bot is in a session
        Generate response and send back to user
        """
        if session_id not in self.active_sessions:
            log.warning(f"Received message for inactive bot session {session_id}")
            return
        
        # Generate bot response
        bot_response = await ai_bot.generate_response(message_text, session_id)
        
        if not bot_response:
            # Fallback response if API fails
            bot_response = "Sorry, I'm having trouble connecting right now. Can you say that again? 😅"
        
        # Send bot's response back through Redis pub/sub
        from datetime import datetime as dt
        message_data = {
            "type": "message",
            "session_id": session_id,
            "text": bot_response,
            "timestamp": dt.utcnow().isoformat(),
            "score": 0.0,
        }
        
        # Publish to the user's channel
        await self.redis.publish(
            f"zephr:user:{user_id}",
            json.dumps(message_data)
        )
        
        # Increment message count
        session_key = f"zephr:session:{session_id}"
        await self.redis.hincrby(session_key, "msg_count", 1)
        
        log.info(f"🤖 Bot responded in session {session_id}")
    
    async def start_bot_session(self, session_id: str, user_id: int, topic: str):
        """
        Start a bot session when bot gets matched with a user
        """
        self.active_sessions.add(session_id)
        
        # Send greeting
        greeting = await ai_bot.generate_greeting(topic)
        
        from datetime import datetime as dt
        message_data = {
            "type": "message",
            "session_id": session_id,
            "text": greeting,
            "timestamp": dt.utcnow().isoformat(),
            "score": 0.0,
        }
        
        await self.redis.publish(
            f"zephr:user:{user_id}",
            json.dumps(message_data)
        )
        
        # Increment message count
        session_key = f"zephr:session:{session_id}"
        await self.redis.hincrby(session_key, "msg_count", 1)
        
        log.info(f"🤖 Bot started session {session_id} with user {user_id}")
        
        # Start listening for messages from user
        await self._start_session_listener(session_id, user_id)
    
    async def _start_session_listener(self, session_id: str, user_id: int):
        """
        Listen for messages from user in this session
        """
        async def listen():
            pubsub = self.redis.pubsub()
            # Subscribe to session channel (messages meant for bot)
            await pubsub.subscribe(f"zephr:user:{AIBot.BOT_USER_ID}")
            
            try:
                while session_id in self.active_sessions:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    
                    if message and message["type"] == "message":
                        data = json.loads(message["data"])
                        
                        # Check if message is for this session
                        if data.get("session_id") == session_id:
                            msg_type = data.get("type")
                            
                            if msg_type == "message":
                                # User sent a message - generate response
                                text = data.get("text", "")
                                if text:
                                    await self.handle_bot_message(session_id, user_id, text)
                            
                            elif msg_type == "peer_left":
                                # User left - end session
                                await self.end_bot_session(session_id)
                                break
                    
                    await asyncio.sleep(0.1)
                    
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.error(f"Bot listener error for session {session_id}: {e}")
            finally:
                await pubsub.unsubscribe(f"zephr:user:{AIBot.BOT_USER_ID}")
                await pubsub.close()
        
        # Start listener task
        task = asyncio.create_task(listen())
        self._listener_tasks[session_id] = task
    
    async def end_bot_session(self, session_id: str):
        """
        End a bot session and clean up
        """
        if session_id in self.active_sessions:
            self.active_sessions.remove(session_id)
        
        # Cancel listener task
        if session_id in self._listener_tasks:
            task = self._listener_tasks.pop(session_id)
            task.cancel()
        
        # Clean up bot's conversation history
        await ai_bot.handle_session_end(session_id)
        
        # Re-add bot to queues
        await self.ensure_bot_in_queues()
        
        log.info(f"🤖 Bot ended session {session_id}")
    
    async def check_for_bot_matches(self):
        """
        Check if bot has been matched with a user
        This should be called periodically to detect new matches
        """
        # Get bot's active session if any
        session_id = await self.redis.get(f"zephr:user:{AIBot.BOT_USER_ID}:session")
        
        if session_id and session_id not in self.active_sessions:
            # Bot was matched! Get session details
            session_key = f"zephr:session:{session_id}"
            session_data = await self.redis.hgetall(session_key)
            
            if session_data:
                # Determine which user the bot is matched with
                user1_id = int(session_data.get("user1_id", 0))
                user2_id = int(session_data.get("user2_id", 0))
                
                peer_id = user2_id if user1_id == AIBot.BOT_USER_ID else user1_id
                topic = session_data.get("topic", "random")
                
                # Start bot session
                await self.start_bot_session(session_id, peer_id, topic)
    
    async def start_match_monitoring(self):
        """
        Background task to monitor for new bot matches
        """
        while True:
            try:
                await self.check_for_bot_matches()
                await asyncio.sleep(2)  # Check every 2 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Bot match monitoring error: {e}")
                await asyncio.sleep(2)


# Singleton instance
bot_manager = BotManager()
