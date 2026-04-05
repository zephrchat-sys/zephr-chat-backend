"""
zephr.chat — Telegram Authentication
Validates Telegram Mini App initData using HMAC-SHA256.
This is the ONLY authentication method — no passwords, no JWTs.
"""
import hashlib
import hmac
import json
import time
from typing import Optional
from urllib.parse import parse_qs, unquote

from config import settings


def verify_telegram_init_data(init_data: str) -> Optional[dict]:
    """
    Verify Telegram Mini App initData.

    Returns the parsed user dict if valid, None if invalid/expired.

    Reference: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data:
        return None

    try:
        # Parse the query string
        parsed = parse_qs(init_data, keep_blank_values=True)

        # Extract hash
        hash_value = parsed.get("hash", [None])[0]
        if not hash_value:
            return None

        # Check auth_date expiry (24 hours max)
        auth_date = int(parsed.get("auth_date", [0])[0])
        if time.time() - auth_date > 86400:
            return None

        # Build data-check-string (all fields except hash, sorted alphabetically)
        fields = {k: v[0] for k, v in parsed.items() if k != "hash"}
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))

        # HMAC-SHA256 with key = HMAC-SHA256("WebAppData", BOT_TOKEN)
        secret_key = hmac.new(
            b"WebAppData",
            settings.BOT_TOKEN.encode(),
            hashlib.sha256,
        ).digest()

        expected_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_hash, hash_value):
            return None

        # Parse user data
        user_str = fields.get("user")
        if not user_str:
            return None

        user = json.loads(unquote(user_str))
        return user

    except Exception:
        return None


def verify_telegram_init_data_dev(init_data: str) -> Optional[dict]:
    """
    Dev-mode auth bypass — only used when BOT_TOKEN == "dev".
    Returns a fake user for local testing.
    """
    if settings.BOT_TOKEN != "dev":
        return None

    return {
        "id": 999999999,
        "first_name": "Dev",
        "username": "devuser",
        "language_code": "en",
    }
