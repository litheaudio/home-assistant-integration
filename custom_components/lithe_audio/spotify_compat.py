"""Temporary compatibility for Spotify's 2026 playlist response changes."""
from __future__ import annotations

import json
import logging
from importlib import metadata
from typing import Any


_LOGGER = logging.getLogger(__name__)
_PATCHED = False


def normalize_playlist_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize old/new Spotify playlist shapes for spotifyaio 2.0.2."""
    normalized = dict(payload)
    collection = normalized.get("items")
    if not isinstance(collection, dict):
        collection = normalized.get("tracks")
    collection = dict(collection) if isinstance(collection, dict) else {}

    rows = collection.get("items")
    fixed_rows: list[Any] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and "track" not in row and "item" in row:
                row = {**row, "track": row["item"]}
            fixed_rows.append(row)

    # Metadata-only playlists contain href/total but no nested item list.
    collection["items"] = fixed_rows
    normalized["items"] = collection
    return normalized


def apply_spotifyaio_compat() -> bool:
    """Patch only affected spotifyaio versions; return whether active."""
    global _PATCHED
    if _PATCHED:
        return True

    try:
        installed_version = metadata.version("spotifyaio")
    except metadata.PackageNotFoundError:
        return False

    if installed_version != "2.0.2":
        _LOGGER.debug(
            "spotifyaio compatibility not needed for version %s",
            installed_version,
        )
        return False

    try:
        from spotifyaio.models import Playlist
    except ImportError:
        return False

    original_from_json = Playlist.from_json
    if getattr(original_from_json, "_lithe_spotify_compat", False):
        _PATCHED = True
        return True

    @classmethod
    def _from_json(cls, data: str | bytes, *args: Any, **kwargs: Any):
        try:
            payload = json.loads(data)
        except (TypeError, json.JSONDecodeError):
            return original_from_json(data, *args, **kwargs)
        if not isinstance(payload, dict):
            return original_from_json(data, *args, **kwargs)
        return cls.from_dict(normalize_playlist_payload(payload))

    _from_json.__func__._lithe_spotify_compat = True  # type: ignore[attr-defined]
    Playlist.from_json = _from_json  # type: ignore[method-assign]
    _PATCHED = True
    _LOGGER.warning(
        "Applied spotifyaio 2.0.2 playlist compatibility for Spotify's "
        "2026 items/item response schema"
    )
    return True
