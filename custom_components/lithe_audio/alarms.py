"""Lithe Audio — Alarm scheduler.

Provides per-speaker (or multi-speaker) alarms with:
  - One-off, daily, weekly (specific days), monthly schedules
  - Audio source: preset URL (Adhan/Quran), saved favourite, embedded chime,
    or custom URL
  - Volume with optional fade-in
  - Snooze/dismiss via services

Persisted to .storage/lithe_audio.alarms so alarms survive HA restart.

Design inspiration: hass-wake-alarm by scootaash (Sonos sunrise alarms).
We adopt the per-day toggle model and persistent storage; we drop the
light-ramp feature since this integration is audio-only.
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
        # ── Sunrise simulation (light ramp before audio fires) ────
        # Lights ramp from warm+dim to cool+bright over `sunrise_minutes`
        # ending AT the alarm time. Inspired by the Wake Alarm thread.
        "sunrise_enabled":      False,
        "sunrise_lights":       [],     # list of light entity_ids
        "sunrise_minutes":      20,     # ramp duration before alarm time
        "sunrise_start_kelvin": 2200,   # warm at start
        "sunrise_end_kelvin":   4500,   # cool at end
        "sunrise_start_brightness": 5,  # 1-255 (5 = barely lit)
        "sunrise_end_brightness":   220,# near full
        "sunrise_never_dim":    True,   # don't dim if light already brighter
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
        # Also cancel sunrise ramp if pending
        sunrise_key = f"{alarm_id}__sunrise"
        if sunrise_key in self._unsub:
            try:
                self._unsub[sunrise_key]()
            except Exception:
                pass
            self._unsub.pop(sunrise_key, None)
        # Cancel volume fade and any active sunrise ramp task.
        for task_key in (alarm_id, f"{alarm_id}__sunrise_task"):
            task = self._fade_tasks.pop(task_key, None)
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
                return None
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
        unsub = ev_helper.async_track_point_in_time(
            self.hass,
            lambda _now, aid=alarm_id: self.hass.async_create_task(
                self._fire(aid)
            ),
            fire_at,
        )
        self._unsub[alarm_id] = unsub
        _LOGGER.info(
            "Alarm %s '%s' scheduled for %s",
            alarm_id, alarm.get("name"), fire_at.isoformat(),
        )

        # If sunrise is enabled and starts in the future, schedule the
        # ramp start `sunrise_minutes` before the fire time. The audio
        # fires AT fire_at, but the lights start ramping earlier.
        if alarm.get("sunrise_enabled") and alarm.get("sunrise_lights"):
            ramp_minutes = int(alarm.get("sunrise_minutes", 20))
            ramp_start_at = fire_at - timedelta(minutes=ramp_minutes)
            if ramp_start_at > dt_util.now():
                ramp_unsub = ev_helper.async_track_point_in_time(
                    self.hass,
                    lambda _now, aid=alarm_id: self.hass.async_create_task(
                        self._fire_sunrise(aid)
                    ),
                    ramp_start_at,
                )
                # Stash under a sunrise-specific key so it doesn't collide
                # with the main fire unsub.
                self._unsub[f"{alarm_id}__sunrise"] = ramp_unsub
                _LOGGER.info(
                    "Alarm %s sunrise ramp starts at %s (%d min before %s)",
                    alarm_id, ramp_start_at.isoformat(), ramp_minutes,
                    fire_at.isoformat(),
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

    async def _fire_sunrise(self, alarm_id: str) -> None:
        """Start the sunrise light ramp for an alarm.

        Runs for `sunrise_minutes` and ends exactly when the audio fires.
        Lights ramp linearly from start (warm + dim) to end (cool + bright)
        in 3-second steps.
        """
        alarm = self._alarms.get(alarm_id)
        if not alarm or not alarm.get("enabled"):
            return
        lights = alarm.get("sunrise_lights") or []
        if not lights:
            return

        minutes = int(alarm.get("sunrise_minutes", 20))
        start_k = int(alarm.get("sunrise_start_kelvin", 2200))
        end_k   = int(alarm.get("sunrise_end_kelvin",   4500))
        start_b = int(alarm.get("sunrise_start_brightness", 5))
        end_b   = int(alarm.get("sunrise_end_brightness", 220))
        never_dim = bool(alarm.get("sunrise_never_dim", True))

        _LOGGER.info(
            "Alarm %s sunrise ramp: %d lights, %d min, %dK→%dK, %d→%d brightness",
            alarm_id, len(lights), minutes, start_k, end_k, start_b, end_b,
        )

        async def _ramp():
            try:
                # Track per-light "starting brightness" so we honour never_dim
                start_brightness_per_light: dict[str, int] = {}
                if never_dim:
                    for ent_id in lights:
                        st = self.hass.states.get(ent_id)
                        if st and st.attributes.get("brightness") is not None:
                            start_brightness_per_light[ent_id] = int(
                                st.attributes["brightness"]
                            )

                # 3-second step interval
                step_seconds = 3
                total_seconds = max(1, minutes * 60)
                steps = total_seconds // step_seconds
                for i in range(1, steps + 1):
                    if not self._alarms.get(alarm_id, {}).get("enabled"):
                        # Alarm was dismissed mid-ramp
                        return
                    frac = i / steps
                    cur_b = int(start_b + (end_b - start_b) * frac)
                    cur_k = int(start_k + (end_k - start_k) * frac)
                    for ent_id in lights:
                        existing_b = start_brightness_per_light.get(ent_id, 0)
                        target_b = max(cur_b, existing_b) if never_dim else cur_b
                        try:
                            await self.hass.services.async_call(
                                "light", "turn_on",
                                {
                                    "entity_id":   ent_id,
                                    "brightness":  target_b,
                                    "kelvin":      cur_k,
                                    "transition":  step_seconds,
                                },
                                blocking=False,
                            )
                        except Exception as e:
                            _LOGGER.debug(
                                "Sunrise turn_on for %s failed: %s", ent_id, e
                            )
                    await asyncio.sleep(step_seconds)
            except asyncio.CancelledError:
                _LOGGER.info("Alarm %s sunrise ramp cancelled", alarm_id)
                return

        task = self.hass.async_create_task(_ramp())
        self._fade_tasks[f"{alarm_id}__sunrise_task"] = task

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
        unsub = ev_helper.async_track_point_in_time(
            self.hass, lambda _now: self.hass.async_create_task(self._fire(alarm_id)),
            fire_at,
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
    mgr = LitheAlarmManager(hass)
    await mgr.async_load()
    hass.data.setdefault(DOMAIN, {})["alarms"] = mgr
    return mgr
