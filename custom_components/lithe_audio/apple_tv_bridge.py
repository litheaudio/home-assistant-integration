"""Helpers for exposing Home Assistant Apple TV apps in Lithe browse."""
from __future__ import annotations

import base64
import json


APPLE_TV_CONTENT_PREFIX = "lithe_apple_tv://"


def encode_apple_tv_content(entity_id: str, source: str | None) -> str:
    """Wrap an Apple TV entity and optional app/source name."""
    raw = json.dumps([entity_id, source], separators=(",", ":")).encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{APPLE_TV_CONTENT_PREFIX}{token}"


def decode_apple_tv_content(value: str) -> tuple[str, str | None]:
    """Decode and validate an Apple TV browse content ID."""
    if not value.startswith(APPLE_TV_CONTENT_PREFIX):
        raise ValueError("Not a Lithe Apple TV content ID")

    token = value[len(APPLE_TV_CONTENT_PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(token + ("=" * (-len(token) % 4)))
        decoded = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        raise ValueError("Invalid Lithe Apple TV content ID") from err

    if not isinstance(decoded, list) or len(decoded) != 2:
        raise ValueError("Invalid Lithe Apple TV content ID payload")
    entity_id, source = decoded
    if not isinstance(entity_id, str) or not entity_id.startswith("media_player."):
        raise ValueError("Invalid Apple TV media player entity ID")
    if source is not None and not isinstance(source, str):
        raise ValueError("Invalid Apple TV source")
    return entity_id, source
