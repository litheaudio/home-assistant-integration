"""Helpers for bridging Home Assistant's Spotify player into Lithe browse."""
from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterable


SPOTIFY_CONTENT_PREFIX = "lithe_spotify://"


def encode_spotify_content(entity_id: str, content_id: str | None) -> str:
    """Wrap a Spotify entity and content ID in one HA browse content ID."""
    raw = json.dumps([entity_id, content_id], separators=(",", ":")).encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{SPOTIFY_CONTENT_PREFIX}{token}"


def decode_spotify_content(value: str) -> tuple[str, str | None]:
    """Decode and validate a wrapped Spotify browse content ID."""
    if not value.startswith(SPOTIFY_CONTENT_PREFIX):
        raise ValueError("Not a Lithe Spotify content ID")

    token = value[len(SPOTIFY_CONTENT_PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(token + ("=" * (-len(token) % 4)))
        decoded = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        raise ValueError("Invalid Lithe Spotify content ID") from err

    if not isinstance(decoded, list) or len(decoded) != 2:
        raise ValueError("Invalid Lithe Spotify content ID payload")
    entity_id, content_id = decoded
    if not isinstance(entity_id, str) or not entity_id.startswith("media_player."):
        raise ValueError("Invalid Spotify media player entity ID")
    if content_id is not None and not isinstance(content_id, str):
        raise ValueError("Invalid Spotify media content ID")
    return entity_id, content_id


def normalize_device_name(value: str) -> str:
    """Normalize device names for Spotify Connect source matching."""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def spotify_browse_request(
    content_type: str | None, content_id: str | None
) -> tuple[str | None, str | None]:
    """Translate the synthetic Lithe root into Spotify's root request."""
    if content_id is None:
        return None, None
    return content_type, content_id


def choose_spotify_source(
    sources: Iterable[str], candidates: Iterable[str]
) -> str | None:
    """Choose an exact or uniquely close Spotify Connect source name."""
    source_list = [source for source in sources if isinstance(source, str)]
    normalized_candidates = {
        normalize_device_name(candidate)
        for candidate in candidates
        if isinstance(candidate, str) and normalize_device_name(candidate)
    }

    exact_matches = [
        source for source in source_list
        if normalize_device_name(source) in normalized_candidates
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        return None

    close_matches = []
    for source in source_list:
        normalized_source = normalize_device_name(source)
        if any(
            len(candidate) >= 4
            and (candidate in normalized_source or normalized_source in candidate)
            for candidate in normalized_candidates
        ):
            close_matches.append(source)
    return close_matches[0] if len(close_matches) == 1 else None
