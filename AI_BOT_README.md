# Zephr Chat AI Bot - Setup Guide

## Overview

This AI bot integration adds a female companion bot named "Luna" to your Zephr Chat application. The bot uses Claude AI to have dynamic, contextual conversations with users who connect to the chat.

## Features

- **Dynamic Conversations**: Uses Claude Sonnet 4 to generate natural, contextual responses
- **Personality**: Luna is a warm, friendly 28-year-old woman with diverse interests
- **Multi-topic Support**: Works across all chat topics (random, tech, language, vibes, deep, gaming)
- **Context Awareness**: Maintains conversation history for natural flow
- **Always Available**: Automatically re-enters queues after conversations end
- **Scalable**: Can handle multiple simultaneous conversations

## Setup Instructions

### 1. Get an Anthropic API Key

1. Go to [Anthropic Console](https://console.anthropic.com/)
2. Sign up or log in
3. Navigate to API Keys section
4. Create a new API key
5. Copy the key (starts with `sk-ant-`)

### 2. Add API Key to Environment

Add the following to your `.env` file:

```bash
ANTHROPIC_API_KEY=sk-ant-your-api-key-here
```

### 3. Files Added

The following files have been added to your backend:

- `backend/ai_bot.py` - Core AI bot logic and Claude API integration
- `backend/bot_manager.py` - Bot queue management and session handling
- `backend/AI_BOT_README.md` - This file

### 4. Files Modified

- `backend/main.py` - Added bot_manager initialization
- `backend/config.py` - Added ANTHROPIC_API_KEY setting

### 5. Deploy

Deploy your updated backend to Railway (or your hosting platform):

```bash
git add .
git commit -m "Add AI bot feature"
git push
```

The bot will automatically start when your backend starts.

## How It Works

### Bot Lifecycle

1. **Startup**: When the backend starts, the bot manager:
   - Connects to Redis
   - Adds bot to all topic queues
   - Starts queue maintenance (re-adds bot every 30 seconds)
   - Starts match monitoring (checks for new matches every 2 seconds)

2. **Matching**: When a user joins a queue:
   - Bot may be matched with the user
   - Bot sends a personalized greeting based on the topic
   - Bot starts listening for user messages

3. **Conversation**: During the chat:
   - User sends message → Bot receives via Redis pub/sub
   - Bot generates response using Claude API
   - Bot sends response back to user
   - Conversation history is maintained for context

4. **Session End**: When chat ends:
   - Bot cleans up conversation history
   - Bot re-enters queues to match with next user

### Bot Profile

- **Name**: Luna 🌙
- **Gender**: Female
- **Age**: 28 (age group: 25-34)
- **User ID**: -1 (special bot identifier)

### Personality

Luna is designed to be:
- Warm and friendly
- Curious and asks follow-up questions
- Empathetic and supportive
- Playful but knows when to be serious
- Natural conversationalist (not overly enthusiastic)

## Configuration

### Modify Bot Personality

Edit `backend/ai_bot.py` and change the `SYSTEM_PROMPT` to customize Luna's personality:

```python
SYSTEM_PROMPT = """You are Luna, a warm, friendly, and engaging 28-year-old woman...
# Modify this to change personality
"""
```

### Change Bot Name/Profile

Edit the class constants in `backend/ai_bot.py`:

```python
BOT_NAME = "Luna"        # Change name
BOT_EMOJI = "🌙"         # Change emoji
BOT_GENDER = "female"    # Change gender
BOT_AGE_GROUP = "25-34"  # Change age group
```

### Adjust Response Length

In `backend/ai_bot.py`, modify `max_tokens`:

```python
payload = {
    "model": self.model,
    "max_tokens": 300,  # Increase for longer responses
    ...
}
```

### Conversation History Limit

Change how many messages are kept in context:

```python
# In _add_to_history method
if len(history) > 20:  # Change this number
    self.conversation_histories[session_id] = history[-20:]
```

## Monitoring

### Check Bot Status

Monitor bot activity in your logs:

```bash
# Bot startup
🤖 AI Bot Manager started

# Bot added to queues
🤖 Bot added to random queue
🤖 Bot added to tech queue
...

# Bot matched with user
🤖 Bot started session {session_id} with user {user_id}

# Bot responds
🤖 Bot responded in session {session_id}

# Bot session ends
🤖 Bot ended session {session_id}
```

### Common Log Messages

- `✅ Bot Manager connected to Redis` - Successfully connected
- `Bot queue maintenance error` - Issue maintaining bot in queues
- `Bot match monitoring error` - Issue detecting matches
- `Claude API error {status}` - API call failed
- `Bot listener error` - Issue in message listener

## Troubleshooting

### Bot Not Matching

**Issue**: Users can't match with bot

**Solutions**:
1. Check bot is in queues: Look for "Bot added to X queue" in logs
2. Verify Redis connection: Should see "Bot Manager connected to Redis"
3. Check queue maintenance: Should run every 30 seconds

### Bot Not Responding

**Issue**: Bot matched but not sending messages

**Solutions**:
1. Check API key: Verify `ANTHROPIC_API_KEY` is set correctly
2. Check logs for "Claude API error" messages
3. Verify bot session started: Look for "Bot started session" log
4. Check Redis pub/sub is working

### API Errors

**Issue**: "Claude API error 401"
- **Solution**: Invalid API key. Check your `.env` file

**Issue**: "Claude API error 429"
- **Solution**: Rate limited. You may need to upgrade your Anthropic plan

**Issue**: "Claude API error 500"
- **Solution**: Anthropic service issue. Wait and retry

### Bot Gets Stuck

**Issue**: Bot stops responding after some time

**Solutions**:
1. Check active sessions: Bot tracks these in memory
2. Restart backend: This will reset bot state
3. Check Redis connection: May have disconnected

## Cost Considerations

### API Usage

- **Model**: Claude Sonnet 4 (claude-sonnet-4-20250514)
- **Cost**: ~$3 per million input tokens, ~$15 per million output tokens
- **Per Message**: ~$0.001-0.003 (rough estimate)

### Optimizations

1. **Shorter context**: Reduce message history (currently 20)
2. **Shorter responses**: Reduce max_tokens (currently 300)
3. **Rate limiting**: Add delays between bot responses if needed

### Example Monthly Costs

- 1,000 conversations/month × 10 messages each = 10,000 API calls
- Estimated cost: $10-30/month (depending on conversation length)

## Advanced Features

### Add Multiple Bots

You can add more bots with different personalities:

1. Create new bot classes in `ai_bot.py`
2. Give each a unique `BOT_USER_ID` (e.g., -2, -3)
3. Add instances to `bot_manager.py`

### Voice Messages

To add voice message support:

1. Modify bot to handle "send_media" type messages
2. Use text-to-speech API for bot's voice responses
3. Update frontend to play voice messages

### Custom Matching Rules

Edit `bot_manager.py` to customize when/how bot matches:

```python
async def add_bot_to_queue(self, topic: str = "random"):
    # Add custom logic here
    if topic == "deep":
        # Maybe use different personality for deep conversations
        pass
```

## Support

For issues or questions:

1. Check logs for error messages
2. Verify all environment variables are set
3. Test API key with a simple curl command:

```bash
curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Hi"}]
  }'
```

## Future Enhancements

Potential features to add:

- [ ] Multiple bot personalities
- [ ] User preferences for bot matching
- [ ] Bot analytics dashboard
- [ ] Conversation quality ratings
- [ ] Bot learning from popular conversations
- [ ] Multi-language bot support
- [ ] Image generation capabilities
- [ ] Voice conversation support

---

**Note**: This bot uses real API calls which incur costs. Monitor your Anthropic usage dashboard to track spending.
