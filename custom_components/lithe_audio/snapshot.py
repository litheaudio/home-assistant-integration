"""Snapshot / restore services for Lithe Audio.

Sonos-pattern: capture the current playback state of one or more
speakers, do something (announcement, switch source, etc.), then
restore the original state.

  lithe_audio.snapshot
    Saves current vol/source/play_state/URL per speaker. Multiple
    snapshots can coexist (keyed by a 'name'); calling with the same
    name overwrites.

  lithe_audio.restore
    Reverses the snapshot: restores vol/source/play_state/URL.

The snapshot dict lives in hass.data[DOMAIN]["snapshots"] — in-memory
only (not persisted). Use case is short-lived ad-hoc save/restore.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall

from .const import DATA_COORDINATOR, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _coords_for_call(hass: HomeAssistant, call: ServiceCall) -> list:
    """Resolve a target list of speaker IPs or entity_ids to coordinator
    objects."""
    speakers = call.data.get("speakers") or call.data.get("entity_id") or []
    if isinstance(speakers, str):
        speakers = [s.strip() for s in speakers.split(",") if s.strip()]
    if not isinstance(speakers, list):
        speakers = [speakers]

    bucket = hass.data.get(DOMAIN, {})
    out = []

    # Default: all connected speakers if none specified
    if not speakers:
        for _eid, entry_data in bucket.items():
            if isinstance(entry_data, dict):
                coord = entry_data.get(DATA_COORDINATOR) or entry_data.get("coordinator")
                if coord and coord.client.state.connected:
                    out.append(coord)
        return out

    # Resolve each target
    for target in speakers:
        coord = None
        if isinstance(target, str) and "." in target and not target.replace(".", "").isdigit():
            # entity_id form (media_player.foo)
            try:
                from homeassistant.helpers import entity_registry as er
                ent_reg = er.async_get(hass)
                ent = ent_reg.async_get(target)
                if ent and ent.config_entry_id and ent.config_entry_id in bucket:
                    coord = bucket[ent.config_entry_id].get(DATA_COORDINATOR)
            except Exception:
                pass
        else:
            # IP form
            for _eid, entry_data in bucket.items():
                if not isinstance(entry_data, dict):
                    continue
                c = entry_data.get(DATA_COORDINATOR) or entry_data.get("coordinator")
                if c and c.client.host == target:
                    coord = c
                    break
        if coord:
            out.append(coord)
    return out


def register_snapshot_services(hass: HomeAssistant) -> None:
    """Register lithe_audio.snapshot and lithe_audio.restore."""

    hass.data.setdefault(DOMAIN, {}).setdefault("snapshots", {})

    async def svc_snapshot(call: ServiceCall) -> None:
        """Save state of selected speakers under an optional snapshot name."""
        name = (call.data.get("name") or "default").strip() or "default"
        coords = _coords_for_call(hass, call)
        snapshot: dict[str, dict[str, Any]] = {}
        for coord in coords:
            client = coord.client
            host = client.host
            snapshot[host] = {
                "volume":         client.state.volume,
                "muted":          client.state.muted,
                "play_state":     client.state.play_state,
                "source_id":      client.state.source_id,
                "last_played_url": client.state.last_played_url,
            }
        hass.data[DOMAIN]["snapshots"][name] = snapshot
        _LOGGER.info(
            "Snapshot %r captured for %d speaker(s): %s",
            name, len(snapshot), list(snapshot.keys()),
        )

    async def svc_restore(call: ServiceCall) -> None:
        """Restore previously snapshotted state."""
        name = (call.data.get("name") or "default").strip() or "default"
        snapshots = hass.data.get(DOMAIN, {}).get("snapshots", {})
        snapshot = snapshots.get(name)
        if not snapshot:
            _LOGGER.warning("Restore: no snapshot named %r", name)
            return

        bucket = hass.data.get(DOMAIN, {})
        for host, saved in snapshot.items():
            # Find the coordinator for this host
            coord = None
            for _eid, entry_data in bucket.items():
                if not isinstance(entry_data, dict):
                    continue
                c = entry_data.get(DATA_COORDINATOR) or entry_data.get("coordinator")
                if c and c.client.host == host:
                    coord = c
                    break
            if not coord:
                continue
            client = coord.client
            try:
                # Stop any current announcement
                await client.async_stop()
                # Restore volume + mute
                await client.async_set_volume(int(saved.get("volume", 50)))
                if saved.get("muted"):
                    await client.async_mute(True)
                # Resume playback if it was playing
                if saved.get("play_state") == "playing":
                    url = saved.get("last_played_url")
                    if url:
                        await client.async_play_url(url)
                    else:
                        await client.async_resume()
                _LOGGER.info("Restored snapshot %r on %s", name, host)
            except Exception as e:
                _LOGGER.error("Restore failed on %s: %s", host, e)

        # Optionally remove the snapshot after restore so it's a one-shot
        if call.data.get("delete", False):
            snapshots.pop(name, None)

    hass.services.async_register(DOMAIN, "snapshot", svc_snapshot)
    hass.services.async_register(DOMAIN, "restore",  svc_restore)

    # ── Sleep timer (Bluesound-style) ──────────────────────────────────
    # Stop playback (or fade-stop) after N minutes. Multiple timers can
    # be active (one per speaker), keyed by host IP.
    import asyncio
    from homeassistant.helpers import event as ev_helper
    from datetime import timedelta
    from homeassistant.util import dt as dt_util

    sleep_timers: dict[str, Any] = hass.data[DOMAIN].setdefault("sleep_timers", {})

    async def svc_set_sleep_timer(call: ServiceCall) -> None:
        """Stop playback on selected speakers after `minutes`."""
        minutes = int(call.data.get("minutes", 30))
        fade_seconds = int(call.data.get("fade_seconds", 0))
        if minutes <= 0:
            return
        coords = _coords_for_call(hass, call)
        if not coords:
            return

        fire_at = dt_util.now() + timedelta(minutes=minutes)

        async def stop_speakers(_now):
            for coord in coords:
                client = coord.client
                try:
                    if fade_seconds > 0:
                        # Linear fade to 0 then stop
                        start_vol = client.state.volume
                        steps = max(1, fade_seconds // 2)
                        for i in range(1, steps + 1):
                            level = int(start_vol * (1 - i / steps))
                            await client.async_set_volume(max(0, level))
                            await asyncio.sleep(2)
                    await client.async_stop()
                    _LOGGER.info("Sleep timer expired on %s", client.host)
                except Exception as e:
                    _LOGGER.error("Sleep timer stop failed on %s: %s",
                                  client.host, e)
            # Clear timer record
            for c in coords:
                sleep_timers.pop(c.client.host, None)

        # Cancel any existing timer for these speakers, then schedule new
        for coord in coords:
            existing = sleep_timers.pop(coord.client.host, None)
            if existing:
                try:
                    existing()
                except Exception:
                    pass
            unsub = ev_helper.async_track_point_in_time(
                hass, stop_speakers, fire_at,
            )
            sleep_timers[coord.client.host] = unsub
        _LOGGER.info(
            "Sleep timer set: %d minutes on %d speaker(s), fire at %s",
            minutes, len(coords), fire_at.isoformat(),
        )

    async def svc_clear_sleep_timer(call: ServiceCall) -> None:
        """Cancel an active sleep timer."""
        coords = _coords_for_call(hass, call)
        cleared = 0
        for coord in coords:
            unsub = sleep_timers.pop(coord.client.host, None)
            if unsub:
                try:
                    unsub()
                    cleared += 1
                except Exception:
                    pass
        _LOGGER.info("Cleared %d sleep timer(s)", cleared)

    hass.services.async_register(DOMAIN, "set_sleep_timer",   svc_set_sleep_timer)
    hass.services.async_register(DOMAIN, "clear_sleep_timer", svc_clear_sleep_timer)
