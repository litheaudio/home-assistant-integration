"""Lithe Audio — Alarm scheduler.

Provides per-speaker (or multi-speaker) alarms with:
  - One-off, daily, weekly (specific days), monthly schedules
  - Audio source: preset URL (Adhan/Quran), saved favourite, embedded chime,
    or custom URL
  - Volume with optional fade-in
  - Snooze/dismiss via services

Persisted to .storage/lithe_audio.alarms so alarms survive HA restart.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import event as ev_helper
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.alarms"

# Day-of-week tokens
DAY_TOKENS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Source types for the alarm audio
SOURCE_PRESET    = "preset"      # URL from ADHAN_PRESETS / QURAN_JUZ
SOURCE_FAVOURITE = "favourite"   # speaker's saved favourite (1-9)
SOURCE_CHIME     = "chime"       # embedded chime slot (product-dependent)
SOURCE_URL       = "url"         # arbitrary HTTP(S) URL

# Repeat modes
REPEAT_ONE_OFF = "one_off"
REPEAT_DAILY   = "daily"
REPEAT_WEEKLY  = "weekly"
REPEAT_MONTHLY = "monthly"

LEGACY_LIGHT_FIELDS = {
    "sunrise_enabled",
    "sunrise_lights",
    "sunrise_minutes",
    "sunrise_start_kelvin",
    "sunrise_end_kelvin",
    "sunrise_start_brightness",
    "sunrise_end_brightness",
    "sunrise_never_dim",
}


def new_alarm_id() -> str:
    return f"alarm_{uuid.uuid4().hex[:8]}"


def default_alarm() -> dict[str, Any]:
    return {
        "id":              new_alarm_id(),
        "name":            "New Alarm",
        "enabled":         True,
        "time":            "07:00",
        "repeat":          REPEAT_DAILY,
        "days":            list(DAY_TOKENS),       # all days for daily/weekly
        "day_of_month":    1,                       # for monthly
        "date":            None,                    # ISO date for one_off
        "speakers":        [],                      # host IPs
        "source":          SOURCE_PRESET,
        "preset_url":      "https://praytimes.org/audio/sunni/Adhan-Makkah.mp3",
        "favourite_slot":  1,
        "chime_slot":      1,
        "custom_url":      "",
        "volume":          60,
        "fade_in_seconds": 0,
        "snooze_minutes":  9,
    }


class LitheAlarmManager:
    """Manages persistent alarms across the integration.

    Single instance per HA install (lives under hass.data[DOMAIN]).
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._alarms: dict[str, dict[str, Any]] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._unsub: dict[str, callback] = {}
        self._snoozes: dict[str, callback] = {}
        self._fade_tasks: dict[str, asyncio.Task] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def async_load(self) -> None:
        """Load persisted alarms and schedule timers for enabled ones."""
        data = await self._store.async_load()
        if data and isinstance(data, dict):
            self._alarms = data.get("alarms", {}) or {}
        migrated = False
        for alarm in self._alarms.values():
            for field in LEGACY_LIGHT_FIELDS:
                if field in alarm:
                    alarm.pop(field, None)
                    migrated = True
        if migrated:
            await self.async_save()
        _LOGGER.info("Loaded %d alarms from storage", len(self._alarms))
        # Schedule each enabled alarm
        for alarm in self._alarms.values():
            if alarm.get("enabled"):
                self._schedule(alarm)

    async def async_save(self) -> None:
        await self._store.async_save({"alarms": self._alarms})

    async def async_shutdown(self) -> None:
        for unsub in self._unsub.values():
            try:
                unsub()
            except Exception:
                pass
        for h in self._timers.values():
            h.cancel()
        for unsub in self._snoozes.values():
            try:
                unsub()
            except Exception:
                pass
        for t in self._fade_tasks.values():
            t.cancel()
        self._timers.clear()
        self._unsub.clear()
        self._snoozes.clear()
        self._fade_tasks.clear()

    # ── CRUD ──────────────────────────────────────────────────────────

    def list_alarms(self) -> list[dict[str, Any]]:
        return list(self._alarms.values())

    def get_alarm(self, alarm_id: str) -> dict[str, Any] | None:
        return self._alarms.get(alarm_id)

    def next_fire_time(self, alarm_id: str) -> datetime | None:
        """Return the next calculated fire time for one saved alarm."""
        alarm = self._alarms.get(alarm_id)
        if not alarm or not alarm.get("enabled"):
            return None
        return self._next_fire_time(alarm)

    def timer_registered(self, alarm_id: str) -> bool:
        """Return whether Home Assistant currently holds this alarm timer."""
        return alarm_id in self._unsub

    async def async_add_alarm(self, alarm: dict[str, Any]) -> str:
        if "id" not in alarm:
            alarm["id"] = new_alarm_id()
        self._alarms[alarm["id"]] = alarm
        if alarm.get("enabled", True):
            self._schedule(alarm)
        await self.async_save()
        _LOGGER.info("Added alarm %s '%s' at %s", alarm["id"], alarm.get("name"), alarm.get("time"))
        return alarm["id"]

    async def async_update_alarm(self, alarm_id: str, patch: dict[str, Any]) -> None:
        if alarm_id not in self._alarms:
            return
        # Cancel existing timer
        self._cancel_timer(alarm_id)
        # Merge patch
        self._alarms[alarm_id] = {**self._alarms[alarm_id], **patch}
        # Reschedule if enabled
        if self._alarms[alarm_id].get("enabled"):
            self._schedule(self._alarms[alarm_id])
        await self.async_save()

    async def async_delete_alarm(self, alarm_id: str) -> None:
        self._cancel_timer(alarm_id)
        self._alarms.pop(alarm_id, None)
        await self.async_save()

    async def async_toggle_alarm(self, alarm_id: str, enabled: bool) -> None:
        await self.async_update_alarm(alarm_id, {"enabled": enabled})

    # ── Scheduling ────────────────────────────────────────────────────

    def _cancel_timer(self, alarm_id: str) -> None:
        if alarm_id in self._timers:
            self._timers[alarm_id].cancel()
            self._timers.pop(alarm_id, None)
        if alarm_id in self._unsub:
            try:
                self._unsub[alarm_id]()
            except Exception:
                pass
            self._unsub.pop(alarm_id, None)
        task = self._fade_tasks.pop(alarm_id, None)
        if task:
            task.cancel()

    def _next_fire_time(self, alarm: dict[str, Any]) -> datetime | None:
        """Compute the next datetime this alarm should fire."""
        try:
            hh, mm = map(int, alarm["time"].split(":")[:2])
        except Exception:
            _LOGGER.warning("Alarm %s has invalid time %r", alarm.get("id"), alarm.get("time"))
            return None

        now = dt_util.now()
        repeat = alarm.get("repeat", REPEAT_DAILY)

        if repeat == REPEAT_ONE_OFF:
            date_str = alarm.get("date")
            if not date_str:
                # Older UI versions allowed an enabled one-off alarm to be
                # saved without a date. Treat that as the next occurrence of
                # its wall-clock time instead of silently registering no
                # callback at all.
                fire = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                return fire if fire > now else fire + timedelta(days=1)
            try:
                d = datetime.fromisoformat(date_str).date()
            except Exception:
                return None
            fire = datetime.combine(d, time(hh, mm)).replace(tzinfo=now.tzinfo)
            if fire <= now:
                return None  # past — don't re-fire
            return fire

        if repeat == REPEAT_DAILY:
            today = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            return today if today > now else today + timedelta(days=1)

        if repeat == REPEAT_WEEKLY:
            days = alarm.get("days", []) or []
            # Map tokens to weekday ints (Mon=0..Sun=6)
            allowed = {DAY_TOKENS.index(d) for d in days if d in DAY_TOKENS}
            if not allowed:
                return None
            candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            for delta in range(0, 8):
                check = candidate + timedelta(days=delta)
                if check.weekday() in allowed and check > now:
                    return check
            return None

        if repeat == REPEAT_MONTHLY:
            dom = int(alarm.get("day_of_month", 1))
            # Try this month, then next month
            for month_offset in range(0, 13):
                year = now.year
                month = now.month + month_offset
                while month > 12:
                    month -= 12
                    year += 1
                try:
                    fire = datetime(year, month, dom, hh, mm,
                                    tzinfo=now.tzinfo)
                except ValueError:
                    continue  # day doesn't exist this month
                if fire > now:
                    return fire
            return None

        return None

    def _schedule(self, alarm: dict[str, Any]) -> None:
        alarm_id = alarm["id"]
        self._cancel_timer(alarm_id)
        fire_at = self._next_fire_time(alarm)
        if not fire_at:
            _LOGGER.info("Alarm %s '%s' has no future fire time", alarm_id, alarm.get("name"))
            return
        # Use HA's async_track_point_in_time so we honour daylight saving
        async def _alarm_due(_now: datetime) -> None:
            await self._fire(alarm_id)

        unsub = ev_helper.async_track_point_in_time(
            self.hass, _alarm_due, fire_at,
        )
        self._unsub[alarm_id] = unsub
        _LOGGER.info(
            "Alarm %s '%s' scheduled for %s",
            alarm_id, alarm.get("name"), fire_at.isoformat(),
        )

    # ── Firing ────────────────────────────────────────────────────────

    async def _fire(self, alarm_id: str) -> None:
        # This callback has fired; remove its stale unsubscribe handle before
        # calculating and registering the next occurrence.
        self._unsub.pop(alarm_id, None)
        alarm = self._alarms.get(alarm_id)
        if not alarm or not alarm.get("enabled"):
            return
        _LOGGER.info("Alarm %s '%s' firing", alarm_id, alarm.get("name"))

        try:
            await self._do_play(alarm)
        except Exception as e:
            _LOGGER.exception("Alarm %s playback failed: %s", alarm_id, e)

        # Disable one-off after firing
        if alarm.get("repeat") == REPEAT_ONE_OFF:
            await self.async_update_alarm(alarm_id, {"enabled": False})
        else:
            # Reschedule for next occurrence
            self._schedule(alarm)

    async def _do_play(self, alarm: dict[str, Any]) -> None:
        """Trigger the audio for this alarm."""
        source = alarm.get("source", SOURCE_PRESET)
        volume = int(alarm.get("volume", 60))
        fade_seconds = int(alarm.get("fade_in_seconds", 0))
        speakers = alarm.get("speakers", []) or []

        if not speakers:
            _LOGGER.warning("Alarm %s has no target speakers", alarm.get("id"))
            return

        # Decide what to play
        url: str | None = None
        chime_slot: int | None = None
        favourite_slot: int | None = None

        if source == SOURCE_PRESET:
            url = (alarm.get("preset_url") or "").strip()
        elif source == SOURCE_URL:
            url = (alarm.get("custom_url") or "").strip()
        elif source == SOURCE_CHIME:
            chime_slot = int(alarm.get("chime_slot", 1))
        elif source == SOURCE_FAVOURITE:
            favourite_slot = int(alarm.get("favourite_slot", 1))

        # Resolve targets. `speakers` may contain:
        #   - Lithe IP addresses → use LUCI protocol directly
        #   - media_player.* entity IDs → use HA's media_player service
        #     (this handles Google Cast Groups, where group_entity is the
        #     synchronized multi-room target)
        bucket = self.hass.data.get(DOMAIN, {})
        coords = []
        media_player_entities: list[str] = []
        for target in speakers:
            if target.startswith("media_player."):
                media_player_entities.append(target)
                continue
            for _entry_id, entry_data in bucket.items():
                if not isinstance(entry_data, dict):
                    continue
                coord = entry_data.get("coordinator")
                if coord and coord.client.host == target:
                    coords.append(coord)
                    break

        if not coords and not media_player_entities:
            _LOGGER.warning(
                "Alarm %s — no matching speakers connected (wanted: %s)",
                alarm.get("id"), speakers,
            )
            return

        if source in (SOURCE_PRESET, SOURCE_URL) and not url:
            _LOGGER.error(
                "Alarm %s has URL source %r but no audio URL",
                alarm.get("id"), source,
            )
            return

        # Wake the hardware audio path before setting volume. Do this even
        # when cached mute state says unmuted: after a speaker power cycle the
        # MCU can remain muted before the first MB#63 state broadcast arrives.
        start_vol = 0 if fade_seconds > 0 else volume
        for c in coords:
            try:
                await c.client.async_mute(False)
                if (
                    source in (SOURCE_PRESET, SOURCE_URL, SOURCE_FAVOURITE)
                    and c.client.state.play_state == "playing"
                ):
                    await c.client.async_pause()
                    await asyncio.sleep(0.15)
                await c.client.async_set_volume(start_vol)
            except Exception as e:
                _LOGGER.warning(
                    "Alarm audio preparation failed on %s: %s",
                    c.client.host, e,
                )
        for ent_id in media_player_entities:
            try:
                await self.hass.services.async_call(
                    "media_player", "volume_set",
                    {"entity_id": ent_id, "volume_level": start_vol / 100.0},
                    blocking=False,
                )
            except Exception as e:
                _LOGGER.warning("Volume set failed on %s: %s", ent_id, e)

        # Trigger playback on Lithe coordinators (direct LUCI)
        for c in coords:
            try:
                if url:
                    await c.client.async_play_url(url)
                elif chime_slot is not None:
                    await c.client.async_play_chime(chime_slot)
                elif favourite_slot is not None:
                    await c.client.async_play_favourite(favourite_slot)
            except Exception as e:
                _LOGGER.error("Play failed on %s: %s", c.client.host, e)

        # Trigger playback on media_player entities (incl. Cast groups)
        # via HA's media_player.play_media. Note: chime / favourite-slot
        # sources only work via LUCI, so they're skipped for Cast targets.
        for ent_id in media_player_entities:
            if not url:
                _LOGGER.warning(
                    "Alarm %s targets cast/media_player entity %s but source "
                    "is %r — only URL sources work via Cast. Skipping.",
                    alarm.get("id"), ent_id, source,
                )
                continue
            try:
                await self.hass.services.async_call(
                    "media_player", "play_media",
                    {
                        "entity_id":            ent_id,
                        "media_content_type":   "music",
                        "media_content_id":     url,
                    },
                    blocking=False,
                )
                _LOGGER.info("Alarm %s → play_media on %s: %s",
                             alarm.get("id"), ent_id, url)
            except Exception as e:
                _LOGGER.error("Play failed on %s: %s", ent_id, e)

        # Fade in if requested (Lithe LUCI targets only; Cast handles its own volume)
        if fade_seconds > 0 and volume > 0 and coords:
            task = self.hass.async_create_task(
                self._fade_volume(coords, 0, volume, fade_seconds, alarm.get("id"))
            )
            self._fade_tasks[alarm.get("id", "")] = task

    async def _fade_volume(
        self, coords, start: int, end: int, seconds: int, alarm_id: str | None,
    ) -> None:
        """Linear fade from start to end over `seconds` seconds."""
        try:
            steps = max(1, seconds // 2)   # update every 2s
            step_dt = seconds / steps
            for i in range(1, steps + 1):
                level = int(start + (end - start) * i / steps)
                for c in coords:
                    try:
                        await c.client.async_set_volume(level)
                    except Exception:
                        pass
                await asyncio.sleep(step_dt)
        except asyncio.CancelledError:
            return
        finally:
            if alarm_id:
                self._fade_tasks.pop(alarm_id, None)

    # ── Snooze / dismiss ──────────────────────────────────────────────

    async def async_snooze(self, alarm_id: str, minutes: int | None = None) -> None:
        alarm = self._alarms.get(alarm_id)
        if not alarm:
            return
        # Stop current playback by pausing target speakers
        await self._stop_playback(alarm)
        m = int(minutes if minutes is not None else alarm.get("snooze_minutes", 9))
        fire_at = dt_util.now() + timedelta(minutes=m)
        # Cancel any prior snooze
        if alarm_id in self._snoozes:
            self._snoozes.pop(alarm_id)()
        async def _snooze_due(_now: datetime) -> None:
            await self._fire(alarm_id)

        unsub = ev_helper.async_track_point_in_time(
            self.hass, _snooze_due, fire_at,
        )
        self._snoozes[alarm_id] = unsub
        _LOGGER.info("Alarm %s snoozed for %d min (fire at %s)", alarm_id, m, fire_at.isoformat())

    async def async_dismiss(self, alarm_id: str) -> None:
        alarm = self._alarms.get(alarm_id)
        if not alarm:
            return
        await self._stop_playback(alarm)
        # Cancel fade
        t = self._fade_tasks.pop(alarm_id, None)
        if t:
            t.cancel()
        # Cancel pending snooze
        s = self._snoozes.pop(alarm_id, None)
        if s:
            try:
                s()
            except Exception:
                pass
        _LOGGER.info("Alarm %s dismissed", alarm_id)

    async def _stop_playback(self, alarm: dict[str, Any]) -> None:
        bucket = self.hass.data.get(DOMAIN, {})
        for _entry_id, entry_data in bucket.items():
            if not isinstance(entry_data, dict):
                continue
            coord = entry_data.get("coordinator")
            if not coord:
                continue
            if coord.client.host in (alarm.get("speakers") or []):
                try:
                    await coord.client.async_pause()
                except Exception:
                    pass

    # ── Sensor support ────────────────────────────────────────────────

    def next_alarm(self) -> tuple[str, datetime] | None:
        """Return (name, time) of next firing alarm across all enabled."""
        soonest: tuple[str, datetime] | None = None
        for a in self._alarms.values():
            if not a.get("enabled"):
                continue
            t = self._next_fire_time(a)
            if not t:
                continue
            if soonest is None or t < soonest[1]:
                soonest = (a.get("name", "Alarm"), t)
        return soonest


# ── Module-level helpers used by other code ─────────────────────────

def get_manager(hass: HomeAssistant) -> LitheAlarmManager | None:
    return hass.data.get(DOMAIN, {}).get("alarms")


async def async_setup_alarm_manager(hass: HomeAssistant) -> LitheAlarmManager:
    """Create the singleton alarm manager and load persisted alarms."""
    bucket = hass.data.setdefault(DOMAIN, {})
    existing = bucket.get("alarms")
    if isinstance(existing, LitheAlarmManager):
        return existing

    setup_task = bucket.get("_alarm_setup_task")
    if isinstance(setup_task, asyncio.Task):
        return await asyncio.shield(setup_task)

    async def _load_manager() -> LitheAlarmManager:
        mgr = LitheAlarmManager(hass)
        await mgr.async_load()
        bucket["alarms"] = mgr
        return mgr

    # Publish a shared setup task before yielding so concurrent config-entry
    # setup cannot create competing managers or orphan timer callbacks.
    setup_task = asyncio.create_task(_load_manager())
    bucket["_alarm_setup_task"] = setup_task
    try:
        return await asyncio.shield(setup_task)
    finally:
        if bucket.get("_alarm_setup_task") is setup_task:
            bucket.pop("_alarm_setup_task", None)
