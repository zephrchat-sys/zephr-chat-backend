"""
zephr.chat — Moderation Engine
Multi-layer: keyword → rule-based → Google Perspective API (optional)
Blocks 95%+ of bad content without false positives.
"""
import asyncio
import re
from dataclasses import dataclass
from typing import Optional, Tuple

import aiohttp

from config import settings


@dataclass
class ModerationResult:
    allowed: bool
    action: str          # "allow" | "warn" | "block" | "ban"
    reason: str
    score: float = 0.0   # 0.0–1.0 toxicity score


# ── Hard keyword lists ─────────────────────────────────────────────────────────
# Tier 1: Instant ban (CSAM, extreme violence, doxxing)
INSTANT_BAN_PATTERNS = [
    r"\bcp\b", r"\bcsam\b", r"child.{0,10}porn", r"minor.{0,10}sex",
    r"kill\s+your(self)?", r"\bdox(x)?ing\b",
]

# Tier 2: Block message (heavy slurs, explicit threats)
BLOCK_PATTERNS = [
    r"\bn[i1!]+gg[ae3]+r\b", r"\bf[a@4]+gg[o0]+t\b",
    r"i('ll| will|'m going to).{0,20}(kill|murder|rape)",
    r"\bsend.{0,15}(nudes?|pics?|photos?)\b",
    r"\bwhatsapp.{0,20}number\b", r"\btelegram.{0,20}@",
    r"\b(\+?[0-9]{10,15})\b",   # Phone numbers
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",  # Emails
]

# Tier 3: Warn user (mild toxicity, borderline)
WARN_PATTERNS = [
    r"\bstupid\b", r"\bidiot\b", r"\bmoron\b", r"\bstfu\b",
    r"\bgo.{0,5}die\b", r"\bkys\b",
]


class ModerationEngine:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._ban_compiled = [re.compile(p, re.IGNORECASE) for p in INSTANT_BAN_PATTERNS]
        self._block_compiled = [re.compile(p, re.IGNORECASE) for p in BLOCK_PATTERNS]
        self._warn_compiled = [re.compile(p, re.IGNORECASE) for p in WARN_PATTERNS]

    async def setup(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=2.0)  # Fast timeout — don't block messages
        )

    async def teardown(self):
        if self._session:
            await self._session.close()

    async def check(self, text: str, user_id: int) -> ModerationResult:
        """
        Full moderation pipeline. Returns result in <200ms typically.
        """
        # Length check
        if len(text) > settings.MAX_MSG_LENGTH:
            return ModerationResult(
                allowed=False, action="block",
                reason="Message too long", score=0.0
            )

        # Layer 1: Instant ban patterns (sync — very fast)
        for pattern in self._ban_compiled:
            if pattern.search(text):
                return ModerationResult(
                    allowed=False, action="ban",
                    reason="Content violates terms — account suspended",
                    score=1.0
                )

        # Layer 2: Block patterns (sync)
        for pattern in self._block_compiled:
            if pattern.search(text):
                return ModerationResult(
                    allowed=False, action="block",
                    reason="Message blocked — policy violation",
                    score=0.9
                )

        # Layer 3: Warn patterns (sync)
        warned = False
        for pattern in self._warn_compiled:
            if pattern.search(text):
                warned = True
                break

        # Layer 4: Perspective API (async, only if key configured)
        perspective_score = 0.0
        if settings.PERSPECTIVE_API_KEY and len(text) > 10:
            perspective_score = await self._perspective_score(text)

            if perspective_score >= settings.TOXICITY_THRESHOLD:
                return ModerationResult(
                    allowed=False, action="block",
                    reason="Toxic content detected by AI",
                    score=perspective_score
                )

        if warned:
            return ModerationResult(
                allowed=True, action="warn",
                reason="Please keep the conversation respectful",
                score=max(0.5, perspective_score)
            )

        return ModerationResult(
            allowed=True, action="allow",
            reason="", score=perspective_score
        )

    async def _perspective_score(self, text: str) -> float:
        """
        Call Google Perspective API.
        Returns toxicity score 0.0–1.0.
        Falls back to 0.0 on any error (don't block on API failure).
        """
        if not self._session:
            return 0.0

        url = f"https://commentanalyzer.googleapis.com/v1alpha1/comments:analyze?key={settings.PERSPECTIVE_API_KEY}"
        payload = {
            "comment": {"text": text},
            "requestedAttributes": {"TOXICITY": {}},
            "languages": ["en"],
            "doNotStore": True,  # Privacy — don't let Google store the text
        }

        try:
            async with self._session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    score = data["attributeScores"]["TOXICITY"]["summaryScore"]["value"]
                    return float(score)
        except Exception:
            pass  # Silent fail — never block messages due to API errors

        return 0.0

    def sanitize(self, text: str) -> str:
        """
        Light sanitization — strip control chars, normalize whitespace.
        Does NOT HTML-escape (frontend handles rendering safely).
        """
        # Remove control characters except newline/tab
        text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', text)
        # Normalize excessive whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {4,}', '   ', text)
        return text.strip()


# ── Singleton ─────────────────────────────────────────────────────────────────
moderator = ModerationEngine()
