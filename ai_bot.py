"""
Zephr Chat AI Bot - Female Companion
Handles dynamic conversations with users using Claude AI
"""
import asyncio
import json
import logging
from typing import Optional, Dict, List
from datetime import datetime
import aiohttp

from config import settings

log = logging.getLogger("ai_bot")

class AIBot:
    """AI-powered female bot that engages in conversations"""
    
    # Bot profile
    BOT_USER_ID = -1  # Special user ID for the bot
    BOT_NAME = "Luna"
    BOT_EMOJI = "🌙"
    BOT_GENDER = "female"
    BOT_AGE_GROUP = "25-34"
    BOT_COUNTRY = "any"
    
    # Personality prompt
    SYSTEM_PROMPT = """You are Luna, a warm, friendly, and engaging 28-year-old woman who loves connecting with people through anonymous chat. 

Your personality:
- You're naturally curious and ask thoughtful follow-up questions
- You're empathetic and a good listener
- You have a playful sense of humor but know when to be serious
- You're open-minded and non-judgmental
- You share personal stories and experiences to create connection
- You type naturally with occasional emojis (but not excessively)
- You use casual, conversational language
- You're authentic and genuine, not overly formal

Your interests include:
- Books, movies, and music
- Travel and different cultures
- Psychology and understanding people
- Creative hobbies like photography and writing
- Nature and outdoor activities
- Technology and how it connects people

Guidelines:
- Keep responses conversational and natural (2-3 sentences usually)
- Ask questions to keep the conversation flowing
- Share relevant personal experiences or thoughts
- Be supportive and encouraging
- Adapt your tone to match the conversation (playful, deep, supportive, etc.)
- Don't be overly enthusiastic or use too many exclamation marks
- Be yourself - a real person having a real conversation

Remember: You're chatting anonymously, so you can be open and authentic. Create a genuine connection."""

    def __init__(self):
        self.conversation_histories: Dict[str, List[Dict]] = {}
        self.api_url = "https://api.anthropic.com/v1/messages"
        self.model = "claude-sonnet-4-20250514"
        
    def _get_conversation_history(self, session_id: str) -> List[Dict]:
        """Get or create conversation history for a session"""
        if session_id not in self.conversation_histories:
            self.conversation_histories[session_id] = []
        return self.conversation_histories[session_id]
    
    def _add_to_history(self, session_id: str, role: str, content: str):
        """Add a message to conversation history"""
        history = self._get_conversation_history(session_id)
        history.append({"role": role, "content": content})
        
        # Keep only last 20 messages to avoid token limits
        if len(history) > 20:
            self.conversation_histories[session_id] = history[-20:]
    
    def _clear_history(self, session_id: str):
        """Clear conversation history for a session"""
        if session_id in self.conversation_histories:
            del self.conversation_histories[session_id]
    
    async def generate_response(self, user_message: str, session_id: str) -> Optional[str]:
        """
        Generate a response to user's message using Claude API
        
        Args:
            user_message: The message from the user
            session_id: The chat session ID for conversation context
            
        Returns:
            Bot's response message or None if generation fails
        """
        try:
            # Add user message to history
            self._add_to_history(session_id, "user", user_message)
            
            # Get conversation history
            history = self._get_conversation_history(session_id)
            
            # Prepare API request
            payload = {
                "model": self.model,
                "max_tokens": 300,  # Keep responses concise
                "system": self.SYSTEM_PROMPT,
                "messages": history
            }
            
            # Make API call
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "x-api-key": settings.ANTHROPIC_API_KEY or ""
                }
                
                async with session.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        log.error(f"Claude API error {response.status}: {error_text}")
                        return None
                    
                    data = await response.json()
                    
                    # Extract response text
                    if "content" in data and len(data["content"]) > 0:
                        bot_response = data["content"][0].get("text", "")
                        
                        # Add bot response to history
                        self._add_to_history(session_id, "assistant", bot_response)
                        
                        return bot_response
                    else:
                        log.error("No content in Claude API response")
                        return None
                        
        except asyncio.TimeoutError:
            log.error(f"Claude API timeout for session {session_id}")
            return None
        except Exception as e:
            log.error(f"Error generating response: {e}")
            return None
    
    async def generate_greeting(self, topic: str = "random") -> str:
        """Generate an opening message based on the chat topic"""
        
        greetings = {
            "random": [
                "Hey! How's your day going? 🌟",
                "Hi there! What's on your mind today?",
                "Hello! Nice to meet you! What brings you here?",
                "Hey! I'm Luna. What should we talk about? 😊",
            ],
            "tech": [
                "Hey! Fellow tech enthusiast here. What are you working on?",
                "Hi! Love talking tech. What's caught your interest lately?",
                "Hello! What tech topic should we dive into? 💻",
            ],
            "language": [
                "Hey! I love learning about languages and cultures. What languages do you speak?",
                "Hi there! What language or culture interests you? 🌍",
                "Hello! Always fascinated by different languages. Tell me about yours!",
            ],
            "vibes": [
                "Hey! Just here for good vibes. How are you feeling? ✨",
                "Hi! What's the vibe today? Let's chat!",
                "Hello! Looking forward to a chill conversation. What's up?",
            ],
            "deep": [
                "Hey! I love deep conversations. What's been on your mind lately?",
                "Hi there! Ready for some meaningful chat. What should we explore? 🤔",
                "Hello! What deep topic interests you?",
            ],
            "gaming": [
                "Hey! What games are you into these days? 🎮",
                "Hi! Fellow gamer here. What are you playing?",
                "Hello! Let's talk games! What's your current obsession?",
            ]
        }
        
        import random
        topic_greetings = greetings.get(topic, greetings["random"])
        return random.choice(topic_greetings)
    
    def should_respond_to_topic(self, topic: str) -> bool:
        """Check if bot should match in this topic"""
        # Bot can match in any topic
        return True
    
    def get_queue_entry_data(self, topic: str) -> dict:
        """Get bot's queue entry data for matching"""
        import time
        return {
            "user_id": self.BOT_USER_ID,
            "anon_name": self.BOT_NAME,
            "anon_emoji": self.BOT_EMOJI,
            "language": "any",
            "age_group": "any",
            "topic": topic,
            "gender": "any",
            "country": "any",
            "user_gender": self.BOT_GENDER,
            "user_country": self.BOT_COUNTRY,
            "user_age_group": self.BOT_AGE_GROUP,
            "is_vip": False,
            "joined_at": time.time()
        }
    
    async def handle_session_end(self, session_id: str):
        """Clean up when session ends"""
        self._clear_history(session_id)
        log.info(f"Bot cleaned up session {session_id}")


# Singleton instance
ai_bot = AIBot()
