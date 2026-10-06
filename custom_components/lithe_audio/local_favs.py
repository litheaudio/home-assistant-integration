"""HA-side favourites for Lithe Audio.

The speaker's firmware MB#70 FAV_SAVE only accepts streaming-app entries
(Spotify, AirPlay, Cast, Airable). Direct URL streams and ad-hoc HTTP
audio cannot be saved as native favourites — the speaker returns
GENERIC_FAV_SAVE_FAIL.

This module gives users a separate, more flexible favourites list that
works for ANY playable URL:

  Storage:  .storage/lithe_audio.favourites_local
  Format:   {"slots": [{"slot": 1, "name": "BBC Radio 1", "url": "..."}, ...]}

  Save:     lithe_audio.fav_save     (slot, name, url)
  Recall:   lithe_audio.fav_play     (slot)  → calls async_play_url
  List:     lithe_audio.fav_list     (returns the stored list)
  Delete:   lithe_audio.fav_delete   (slot)

Heart button uses fav_save with the current playback URL.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.storage import Store

from .const import DATA_COORDINATOR, DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.favourites_local"

MAX_SLOTS = 9


class LitheLocalFavourites:
    """Singleton: HA-side favourites manager."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._slots: dict[int, dict[str, Any]] = {}
        self._listeners: set[Callable[[], None]] = set()

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data and isinstance(data, dict):
            slots_data = data.get("slots", [])
            if isinstance(slots_data, list):
                for item in slots_data:
                    try:
                        s = int(item["slot"])
                        self._slots[s] = {
                            "slot": s,
                            "name": str(item.get("name", f"Favourite {s}")),
                            "url":  str(item.get("url", "")),
                        }
                    except (KeyError, ValueError, TypeError):
                        continue
        _LOGGER.info("Loaded %d HA-side favourites", len(self._slots))

    async def async_save(self) -> None:
        await self._store.async_save({
            "slots": list(self._slots.values()),
        })

    def async_listen(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Notify entities when a favourite name or URL changes."""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    def list_all(self) -> list[dict[str, Any]]:
        """Return slots 1-9 in order; empty slots are placeholders."""
        result = []
        for s in range(1, MAX_SLOTS + 1):
            if s in self._slots:
                result.append(self._slots[s])
            else:
                result.append({"slot": s, "name": "(empty)", "url": ""})
        return result

    def get(self, slot: int) -> dict[str, Any] | None:
        return self._slots.get(int(slot))

    async def async_set(self, slot: int, name: str, url: str) -> None:
        slot = max(1, min(MAX_SLOTS, int(slot)))
        self._slots[slot] = {"slot": slot, "name": name.strip(), "url": url.strip()}
        await self.async_save()
        self._notify()
        _LOGGER.info("Saved local favourite slot %d: %r → %s", slot, name, url)

    async def async_rename(self, slot: int, name: str) -> None:
        """Set an HA display name while preserving any saved URL.

        A name-only entry also acts as an alias for a native MB#70 favourite
        in the same slot; playback still falls through to the speaker.
        """
        slot = max(1, min(MAX_SLOTS, int(slot)))
        existing = self._slots.get(slot, {})
        self._slots[slot] = {
            "slot": slot,
            "name": name.strip() or f"Favourite {slot}",
            "url": str(existing.get("url", "")),
        }
        await self.async_save()
        self._notify()
        _LOGGER.info("Renamed favourite slot %d to %r", slot, name)

    async def async_delete(self, slot: int) -> None:
        self._slots.pop(int(slot), None)
        await self.async_save()
        self._notify()

    def next_free_slot(self, occupied_slots: set[int] | None = None) -> int:
        """Return the lowest free slot (1-9). Wraps to 1 if all full."""
        occupied_slots = occupied_slots or set()
        for s in range(1, MAX_SLOTS + 1):
            # A name-only entry is an alias waiting for content, not a used
            # favourite. This lets users name slot 1 "Doorbell" first and
            # then save the next track into that same slot.
            if (
                s not in occupied_slots
                and (s not in self._slots or not self._slots[s].get("url"))
            ):
                return s
        return 1  # all full — overwrite slot 1


async def async_capture_current_favourite(
    manager: LitheLocalFavourites,
    client: Any,
    slot: int | None = None,
) -> int:
    """Save current playback while preserving the configured slot alias.

    URL-based playback is stored locally. Spotify/AirPlay sessions often do
    not expose a reusable URL, so the native MB#70 save is still attempted.
    """
    native_slots = {
        int(item.get("slot", 0) or 0)
        for item in (getattr(client.state, "favourites", None) or [])
    }
    slot = slot or manager.next_free_slot(native_slots)
    slot = max(1, min(MAX_SLOTS, int(slot)))
    existing = manager.get(slot) or {}
    alias = str(existing.get("name") or "").strip()
    state = client.state
    url = str(state.last_played_url or "").strip()
    name = alias or str(state.title or "").strip()
    if not name and url:
        from urllib.parse import urlparse
        name = urlparse(url).path.rsplit("/", 1)[-1]
        if "." in name:
            name = name.rsplit(".", 1)[0]
    name = name or f"Favourite {slot}"

    if url:
        await manager.async_set(slot, name, url)
    elif alias:
        # Keep the chosen label visible while the firmware owns the content.
        await manager.async_rename(slot, alias)

    try:
        await client.async_save_favourite(slot)
    except Exception as err:
        if not url:
            raise
        _LOGGER.debug("Native favourite save failed for slot %d: %s", slot, err)

    _LOGGER.info("Captured current playback as favourite %d (%s)", slot, name)
    return slot


def get_local_favs(hass: HomeAssistant) -> LitheLocalFavourites | None:
    return hass.data.get(DOMAIN, {}).get("local_favs")


async def async_setup_local_favourites(hass: HomeAssistant) -> LitheLocalFavourites:
    mgr = LitheLocalFavourites(hass)
    await mgr.async_load()
    hass.data.setdefault(DOMAIN, {})["local_favs"] = mgr
    return mgr


def _get_target_coordinator(hass: HomeAssistant, entity_id: str | None):
    """Find the coordinator matching an entity_id. Falls back to first
    available coordinator if no entity_id is given."""
    bucket = hass.data.get(DOMAIN, {})
    for entry_id, entry_data in bucket.items():
        if not isinstance(entry_data, dict):
            continue
        coord = entry_data.get(DATA_COORDINATOR) or entry_data.get("coordinator")
        if coord is None:
            continue
        if not entity_id:
            return coord
        # Match entity_id loosely against the coordinator's speaker host
        host = coord.client.host
        if entity_id.endswith(host.replace(".", "_")) or host in entity_id:
            return coord
    # Last resort: first coordinator
    for entry_id, entry_data in bucket.items():
        if isinstance(entry_data, dict):
            coord = entry_data.get(DATA_COORDINATOR) or entry_data.get("coordinator")
            if coord:
                return coord
    return None


def register_local_fav_services(hass: HomeAssistant) -> None:
    """Register the 4 HA-side favourite services."""

    async def svc_save(call: ServiceCall) -> None:
        """Save a URL+name to a slot. If url is not given, capture the
        currently-playing URL from the target speaker's state."""
        mgr = get_local_favs(hass)
        if not mgr:
            return
        slot = int(call.data.get("slot", 0) or 0)
        name = (call.data.get("name") or "").strip()
        url  = (call.data.get("url") or "").strip()

        # Auto-pick next free slot if not specified
        if slot < 1:
            slot = mgr.next_free_slot()

        existing = mgr.get(slot) or {}
        if not name:
            # A user-assigned slot label is authoritative. Capturing new
            # content must not silently replace "Oskar" with a track title.
            name = str(existing.get("name") or "").strip()

        # If no URL given, capture the current playback URL from speaker
        entity_id = None
        target = call.data.get("entity_id") or call.data.get("target")
        if isinstance(target, dict):
            entity_id = (target.get("entity_id") or [""])[0] if target.get("entity_id") else None
        elif isinstance(target, list):
            entity_id = target[0] if target else None
        elif isinstance(target, str):
            entity_id = target

        if not url:
            coord = _get_target_coordinator(hass, entity_id)
            if coord:
                # Capture currently-playing URL from state
                url = coord.client.state.last_played_url or ""
                if not name:
                    # Use track title or URL filename as name
                    name = coord.client.state.title
                    if not name and url:
                        from urllib.parse import urlparse
                        name = urlparse(url).path.rsplit("/", 1)[-1]
                        if "." in name:
                            name = name.rsplit(".", 1)[0]

        if not url:
            _LOGGER.warning(
                "fav_save: no URL given and no currently-playing URL to capture. "
                "Pass 'url' explicitly or play something first."
            )
            return
        if not name:
            name = f"Favourite {slot}"

        await mgr.async_set(slot, name, url)

    async def svc_play(call: ServiceCall) -> None:
        """Play a saved local favourite by slot number."""
        mgr = get_local_favs(hass)
        if not mgr:
            return
        slot = int(call.data.get("slot", 1))
        fav = mgr.get(slot)
        if not fav or not fav.get("url"):
            _LOGGER.warning("fav_play: slot %d is empty", slot)
            return

        entity_id = None
        target = call.data.get("entity_id") or call.data.get("target")
        if isinstance(target, dict):
            entity_id = (target.get("entity_id") or [""])[0] if target.get("entity_id") else None
        elif isinstance(target, list):
            entity_id = target[0] if target else None
        elif isinstance(target, str):
            entity_id = target

        coord = _get_target_coordinator(hass, entity_id)
        if not coord:
            _LOGGER.warning("fav_play: no target coordinator found")
            return
        _LOGGER.info("fav_play: slot %d → %s on %s",
                     slot, fav["url"], coord.client.host)
        await coord.client.async_play_url(fav["url"])

    async def svc_list(call: ServiceCall) -> dict[str, Any]:
        """Return the current list as a service response."""
        mgr = get_local_favs(hass)
        if not mgr:
            return {"favourites": []}
        return {"favourites": mgr.list_all()}

    async def svc_delete(call: ServiceCall) -> None:
        mgr = get_local_favs(hass)
        if not mgr:
            return
        slot = int(call.data.get("slot", 0))
        if slot > 0:
            await mgr.async_delete(slot)

    hass.services.async_register(DOMAIN, "fav_save",   svc_save)
    hass.services.async_register(DOMAIN, "fav_play",   svc_play)
    hass.services.async_register(DOMAIN, "fav_delete", svc_delete)

    # fav_list returns service response — needs SupportsResponse
    from homeassistant.core import SupportsResponse
    hass.services.async_register(
        DOMAIN, "fav_list", svc_list,
        supports_response=SupportsResponse.ONLY,
    )
