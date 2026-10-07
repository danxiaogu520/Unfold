from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS = {"api_key", "authorization", "password", "access_token", "refresh_token"}


def sanitized(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if str(key).lower() in SENSITIVE_KEYS else sanitized(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitized(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
        value = re.sub(r"\bsk-[a-zA-Z0-9_-]{20,}\b", "[redacted]", value)
        return value
    return value
