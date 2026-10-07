"""Prayer-time scheduler for Lithe Audio.

Service: lithe_audio.set_prayer_schedule

data:
  city:    "London"
  country: "GB"
  method:  2                  # ISNA=2, MWL=3, Egyptian=5
  entries:
    - prayer: "fajr"
      speakers: ["192.168.1.38", "192.168.1.17"]
      url: "http://server/adhan.mp3"
      volume: 70
      days: "daily"           # daily | weekdays | weekends | friday
    - time: "07:00"           # fixed-time fallback (HH:MM)
      speakers: ["media_player.deck_v3"]
      url: "http://server/morning.mp3"
      volume: 50
      days: "weekdays"

The integration fetches prayer times once and re-fetches each day at 00:01.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    ALADHAN_URL, DATA_PRAYER, DATA_PRAYER_STATE, DOMAIN, PRAYER_NAMES,
)

_LOGGER = logging.getLogger(__name__)


async def async_fetch_prayer_times(
    hass: HomeAssistant, city: str, country: str, method: int = 2
) -> dict[str, str]:
    """Return dict {prayer_name: "HH:MM"} for today, in local time."""
    session = async_get_clientsession(hass)
    params = {"city": city, "country": country, "method": method}
    try:
        async with session.get(ALADHAN_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json()
    except Exception as e:
        _LOGGER.error("Aladhan fetch failed: %s", e)
        return {}
    if data.get("code") != 200:
        _LOGGER.error("Aladhan error: %s", data.get("status"))
        return {}
    timings = data.get("data", {}).get("timings", {})
    return {k.lower(): v[:5] for k, v in timings.items() if k.lower() in PRAYER_NAMES}


def _day_matches(days: str, dow: int) -> bool:
    """dow: 0=Monday … 6=Sunday."""
    if not days or days == "daily":
        return True
    if days == "weekdays":
        return dow < 5
    if days == "weekends":
        return dow >= 5
    if days == "friday":
        return dow == 4
    if days == "saturday":
        return dow == 5
    if days == "sunday":
        return dow == 6
    return True


class PrayerScheduler:
    """Registers HA time-change listeners for each prayer time daily."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        self.hass = hass
        self.config = config
        self._unsubs: list = []
        self._midnight_unsub = None

    async def async_setup(self) -> None:
        await self._schedule_today()
        # Re-schedule every day at 00:01
        self._midnight_unsub = async_track_time_change(
            self.hass, self._midnight_refresh, hour=0, minute=1, second=0,
        )

    async def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._unsubs.clear()
        if self._midnight_unsub:
            try:
                self._midnight_unsub()
            except Exception:
                pass
            self._midnight_unsub = None

    async def _midnight_refresh(self, _now: datetime) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._unsubs.clear()
        await self._schedule_today()

    async def _schedule_today(self) -> None:
        cfg     = self.config
        city    = cfg.get("city", "")
        country = cfg.get("country", "")
        method  = int(cfg.get("method", 2))
        entries = cfg.get("entries", []) or []

        times: dict[str, str] = {}
        if city and country:
            times = await async_fetch_prayer_times(self.hass, city, country, method)
            _LOGGER.info("Prayer times for %s/%s: %s", city, country, times)

        # Stash today's times so the Options Flow "View schedule" can display them
        # Scheduler lifecycle and display state must use separate slots.
        # DATA_PRAYER contains this PrayerScheduler object; older code then
        # tried to mutate it as a dict at midnight and stopped rescheduling.
        prayer_data = self.hass.data.setdefault(DOMAIN, {}).setdefault(
            DATA_PRAYER_STATE, {}
        )
        prayer_data["times"] = times
        prayer_data["last_fetch_city"] = city
        prayer_data["last_fetch_country"] = country

        # Use Home Assistant's configured timezone, not the host OS date.
        dow = dt_util.now().weekday()

        for entry in entries:
            prayer   = (entry.get("prayer") or "").lower()
            fixed    = entry.get("time", "")
            speakers = entry.get("speakers") or []
            url      = entry.get("url", "")
            vol      = int(entry.get("volume", 70))
            days     = entry.get("days", "daily")

            hhmm = times.get(prayer) if prayer else fixed
            if not hhmm or len(hhmm) < 4:
                _LOGGER.warning(
                    "Prayer %s was not scheduled because no time was resolved",
                    prayer or "fixed",
                )
                continue
            if not _day_matches(days, dow):
                continue
            if not str(url).strip():
                _LOGGER.error(
                    "Prayer %s was not scheduled because its audio URL is blank",
                    prayer or "fixed",
                )
                continue

            try:
                h, m = int(hhmm[:2]), int(hhmm[3:5])
            except ValueError:
                continue

            entry_copy = dict(entry)
            entry_copy["url"] = url
            entry_copy["volume"] = vol
            entry_copy["speakers"] = list(speakers)

            unsub = async_track_time_change(
                self.hass,
                lambda now, e=entry_copy: self.hass.async_create_task(self._fire(e)),
                hour=h, minute=m, second=0,
            )
            self._unsubs.append(unsub)
            _LOGGER.info(
                "Scheduled %s at %02d:%02d → %s",
                prayer or "fixed", h, m, speakers,
            )

    async def _fire(self, entry: dict[str, Any]) -> None:
        """Trigger the real Lithe tannoy service and await playback setup."""
        try:
            await self.hass.services.async_call(
                DOMAIN,
                "tannoy",
                {
                    "message": entry["url"],
                    "mode":     "start",
                    "volume":   int(entry["volume"]),
                    "speakers": entry["speakers"],
                },
                blocking=True,
            )
            _LOGGER.info(
                "Prayer playback started on %s: %s",
                entry["speakers"], entry["url"],
            )
        except Exception as e:
            _LOGGER.exception("Prayer playback failed: %s", e)


async def async_register_prayer_service(hass: HomeAssistant) -> None:
    """Register the lithe_audio.set_prayer_schedule service."""
    from homeassistant.core import ServiceCall

    async def svc_set_prayer_schedule(call: ServiceCall) -> None:
        schedules = hass.data.setdefault(DOMAIN, {}).setdefault(DATA_PRAYER, {})
        # Migrate the old singleton shape without leaving callbacks active.
        if isinstance(schedules, PrayerScheduler):
            await schedules.async_shutdown()
            schedules = {}
            hass.data[DOMAIN][DATA_PRAYER] = schedules

        schedule_id = str(call.data.get("schedule_id") or "__service__")
        existing: PrayerScheduler | None = schedules.get(schedule_id)
        if existing:
            await existing.async_shutdown()

        scheduler = PrayerScheduler(hass, dict(call.data))
        await scheduler.async_setup()
        schedules[schedule_id] = scheduler

    if not hass.services.has_service(DOMAIN, "set_prayer_schedule"):
        hass.services.async_register(DOMAIN, "set_prayer_schedule", svc_set_prayer_schedule)


async def async_unload_prayer(hass: HomeAssistant) -> None:
    schedules = hass.data.get(DOMAIN, {}).get(DATA_PRAYER)
    if isinstance(schedules, PrayerScheduler):
        await schedules.async_shutdown()
    elif isinstance(schedules, dict):
        for scheduler in list(schedules.values()):
            if isinstance(scheduler, PrayerScheduler):
                await scheduler.async_shutdown()
    hass.data.get(DOMAIN, {}).pop(DATA_PRAYER, None)


async def async_remove_prayer_schedule(
    hass: HomeAssistant, schedule_id: str
) -> None:
    """Remove one config entry's prayer schedule without affecting others."""
    schedules = hass.data.get(DOMAIN, {}).get(DATA_PRAYER)
    if not isinstance(schedules, dict):
        return
    scheduler = schedules.pop(schedule_id, None)
    if isinstance(scheduler, PrayerScheduler):
        await scheduler.async_shutdown()
