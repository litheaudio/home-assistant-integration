"""Tannoy / PA-override notify service for Lithe Audio.

Usage (via the modern lithe_audio.tannoy service):
    service: lithe_audio.tannoy
    data:
      message: "http://server/announcement.mp3"
      mode: start              # or "end"
      volume: 80               # announcement volume (start only)
      speakers:                # list of host IPs OR entity_ids
        - 192.168.1.38
        - media_player.deck_v3

The service saves volume + play_state for each target speaker on `start`,
pauses them, raises volume, then plays the URL on the first speaker. On
`end` it restores volume and resumes if the speaker was previously playing.

Also registered as the legacy `notify.lithe_tannoy` service for
backwards compatibility with older callers.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall

from .const import DATA_COORDINATOR, DATA_TANNOY_SAVED, DOMAIN
from .coordinator import LitheAudioCoordinator

_LOGGER = logging.getLogger(__name__)


def _resolve_coordinator(
    hass: HomeAssistant, target: str
) -> LitheAudioCoordinator | None:
    """Resolve a speaker reference (IP or entity_id) to its coordinator."""
    bucket = hass.data.get(DOMAIN, {})

    # Strip entity_id form (e.g. "media_player.foo" → look up its entry)
    if "." in target:
        try:
            from homeassistant.helpers import entity_registry as er
            ent_reg = er.async_get(hass)
        except Exception:
            ent_reg = None
        if ent_reg:
            ent = ent_reg.async_get(target)
            if ent and ent.config_entry_id and ent.config_entry_id in bucket:
                return bucket[ent.config_entry_id].get(DATA_COORDINATOR)
        return None

    # IP/hostname form — match against coord.client.host
    for entry_id, entry_data in bucket.items():
        if not isinstance(entry_data, dict):
            continue
        coord = entry_data.get(DATA_COORDINATOR) or entry_data.get("coordinator")
        if coord and coord.client.host == target:
            return coord
    return None


async def _tannoy_start(
    hass: HomeAssistant, speakers: list[str], url: str, volume: int,
) -> None:
    """Save vol+play_state, pause, raise volume, play URL on first speaker.

    Note on PAUSE vs STOP: we use PAUSE because STOP causes this
    firmware to push SPEAKER_INACTIVE and then refuse PLAYITEM:DIRECT
    entirely. PAUSE keeps the audio subsystem alive while reducing
    the chance of Spotify Connect interfering with our PLAYITEM.
    """
    saved = hass.data[DOMAIN].setdefault(DATA_TANNOY_SAVED, {})
    first_coord: LitheAudioCoordinator | None = None

    for target in speakers:
        coord = _resolve_coordinator(hass, target)
        if not coord:
            _LOGGER.warning("Tannoy: cannot resolve speaker %s", target)
            continue

        client = coord.client
        host = client.host

        # Save current state
        saved[host] = {
            "volume":     client.state.volume,
            "play_state": client.state.play_state,
            "muted":      client.state.muted,
            "source":     client.state.source_id,
        }

        # Pause + unmute + raise volume
        try:
            if client.state.muted:
                await client.async_mute(False)
            if client.state.play_state == "playing":
                await client.async_pause()
            await asyncio.sleep(0.15)
            await client.async_set_volume(volume)
        except Exception as e:
            _LOGGER.error("Tannoy start failed for %s: %s", host, e)

        if first_coord is None:
            first_coord = coord

    # Play the URL on the first speaker
    if first_coord and url:
        try:
            await first_coord.client.async_play_url(url)
            # Verify source actually became 17 (Direct URL)
            await asyncio.sleep(1.5)
            src = first_coord.client.state.source_id
            if src != 17:
                _LOGGER.warning(
                    "Tannoy: source did not switch to Direct URL "
                    "(still source=%d). Announcement may not be audible. "
                    "URL: %s",
                    src, url,
                )
            else:
                _LOGGER.info(
                    "Tannoy: source switched to Direct URL playing %s",
                    url,
                )
        except Exception as e:
            _LOGGER.error("Tannoy play_url failed: %s", e)
            raise
    elif not first_coord:
        # No speakers could be resolved — raise so the caller knows
        # to fall back to direct URL playback instead of silent failure.
        raise RuntimeError(
            f"Tannoy: none of the requested speakers {speakers!r} could "
            f"be resolved to a coordinator. Stale config entry or wrong IP?"
        )


async def _tannoy_end(hass: HomeAssistant, speakers: list[str]) -> None:
    """Restore volume and resume on each speaker."""
    saved = hass.data[DOMAIN].setdefault(DATA_TANNOY_SAVED, {})
    for target in speakers:
        coord = _resolve_coordinator(hass, target)
        if not coord:
            continue
        client = coord.client
        host = client.host
        sv = saved.pop(host, None)
        if not sv:
            continue
        try:
            await client.async_stop()
            await asyncio.sleep(0.1)
            await client.async_set_volume(int(sv.get("volume", 50)))
            if sv.get("muted"):
                await client.async_mute(True)
            if sv.get("play_state") == "playing":
                await asyncio.sleep(0.2)
                await client.async_resume()
        except Exception as e:
            _LOGGER.error("Tannoy end failed for %s: %s", host, e)


def register_tannoy_service(hass: HomeAssistant) -> None:
    """Register lithe_audio.tannoy as a service.

    Also tries to register notify.lithe_tannoy for backwards
    compatibility with callers that still use the old form.
    """

    async def svc_tannoy(call: ServiceCall) -> None:
        """Modern lithe_audio.tannoy service entry point.

        Accepts fields at the top level OR nested under 'data':
          message  — URL to play (start mode) or empty (end mode)
          mode     — 'start' | 'end' (default: start)
          volume   — 0-100 (default 80, start mode only)
          speakers — list of IPs or entity_ids
        """
        # Accept both flat form and notify-style nested form
        nested = call.data.get("data") or {}
        message  = call.data.get("message") or nested.get("message", "")
        mode     = (call.data.get("mode") or nested.get("mode") or "start").lower()
        volume   = int(call.data.get("volume", nested.get("volume", 80)))
        speakers = call.data.get("speakers") or nested.get("speakers") or []
        if isinstance(speakers, str):
            speakers = [s.strip() for s in speakers.split(",") if s.strip()]
        if not isinstance(speakers, list):
            speakers = [speakers]

        if mode == "start":
            await _tannoy_start(hass, speakers, message, volume)
        elif mode == "end":
            await _tannoy_end(hass, speakers)
        else:
            _LOGGER.warning("Unknown tannoy mode: %s", mode)

    hass.services.async_register(DOMAIN, "tannoy", svc_tannoy)

    # Backwards-compat: also expose as notify.lithe_tannoy. The legacy
    # platform helper (BaseNotificationService) requires YAML config,
    # which most users don't have. So we directly register the service
    # under the notify domain instead.
    async def svc_notify_tannoy(call: ServiceCall) -> None:
        """Legacy notify.lithe_tannoy form: { message, data: { mode, volume, speakers } }"""
        data = call.data.get("data") or {}
        message  = call.data.get("message", "")
        mode     = (data.get("mode") or "start").lower()
        volume   = int(data.get("volume", 80))
        speakers = data.get("speakers") or []
        if isinstance(speakers, str):
            speakers = [s.strip() for s in speakers.split(",") if s.strip()]
        if not isinstance(speakers, list):
            speakers = [speakers]
        if mode == "start":
            await _tannoy_start(hass, speakers, message, volume)
        elif mode == "end":
            await _tannoy_end(hass, speakers)

    try:
        hass.services.async_register("notify", "lithe_tannoy", svc_notify_tannoy)
    except Exception as e:
        _LOGGER.debug("Could not register notify.lithe_tannoy: %s", e)

