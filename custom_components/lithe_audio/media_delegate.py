"""Helpers for safely delegating browse items to another HA media player."""
from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterable


MUSIC_ASSISTANT_CONTENT_PREFIX = "lithe_music_assistant://"


def encode_music_assistant_content(
    entity_id: str, content_id: str | None
) -> str:
    """Wrap a Music Assistant entity and its native content ID."""
    raw = json.dumps([entity_id, content_id], separators=(",", ":")).encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{MUSIC_ASSISTANT_CONTENT_PREFIX}{token}"


def decode_music_assistant_content(value: str) -> tuple[str, str | None]:
    """Decode and validate a wrapped Music Assistant browse content ID."""
    if not value.startswith(MUSIC_ASSISTANT_CONTENT_PREFIX):
        raise ValueError("Not a Lithe Music Assistant content ID")

    token = value[len(MUSIC_ASSISTANT_CONTENT_PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(token + ("=" * (-len(token) % 4)))
        decoded = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        raise ValueError("Invalid Lithe Music Assistant content ID") from err

    if not isinstance(decoded, list) or len(decoded) != 2:
        raise ValueError("Invalid Lithe Music Assistant content ID payload")
    entity_id, content_id = decoded
    if not isinstance(entity_id, str) or not entity_id.startswith("media_player."):
        raise ValueError("Invalid Music Assistant media player entity ID")
    if content_id is not None and not isinstance(content_id, str):
        raise ValueError("Invalid Music Assistant media content ID")
    return entity_id, content_id


def normalize_player_name(value: str) -> str:
    """Normalize a player name for matching across integrations."""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def choose_music_assistant_entity(
    entities: Iterable[tuple[str, str]], candidates: Iterable[str]
) -> str | None:
    """Choose one exact or uniquely close Music Assistant player entity."""
    normalized_candidates = {
        normalize_player_name(candidate)
        for candidate in candidates
        if isinstance(candidate, str) and normalize_player_name(candidate)
    }
    available = [
        (
            entity_id,
            {
                normalize_player_name(name),
                normalize_player_name(entity_id.rsplit(".", 1)[-1]),
            },
        )
        for entity_id, name in entities
        if isinstance(entity_id, str) and isinstance(name, str)
    ]

    exact = [
        entity_id
        for entity_id, normalized_names in available
        if normalized_names & normalized_candidates
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    close = [
        entity_id
        for entity_id, normalized_names in available
        if any(
            len(candidate) >= 4
            and any(
                candidate in normalized_name or normalized_name in candidate
                for normalized_name in normalized_names
                if normalized_name
            )
            for candidate in normalized_candidates
        )
    ]
    if len(close) == 1:
        return close[0]

    # A single loaded MA player is unambiguous even when it was renamed
    # independently from the physical Lithe entity.
    return available[0][0] if len(available) == 1 else None
