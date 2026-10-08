"""Cast-group helper for Lithe Audio.

Provides the lithe_audio.play_group service:

    service: lithe_audio.play_group
    data:
      leader_ip:   192.168.1.38       # any member of the group (or the leader)
      uuid:        "b63105f8-…"        # Cast group UUID
      url:         "http://server/track.mp3"
      content_type: "audio/mp3"
      volume:      65                 # optional LUCI volume for all members
      member_ips:                     # optional: speakers to apply volume to
        - 192.168.1.38
        - 192.168.1.17

The Cast playback is sent to the group UUID via pychromecast (in a thread
executor). LUCI volume is then set on each member speaker individually,
because Cast groups have no LUCI endpoint of their own.
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, ServiceCall

from .const import DATA_COORDINATOR, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _cast_to_group(
    leader_ip: str, uuid: str, url: str, content_type: str
) -> bool:
    """Blocking helper that runs in the executor."""
    try:
        import pychromecast
    except ImportError:
        _LOGGER.error("pychromecast not installed — cannot cast to group")
        return False

    try:
        chromecasts, browser = pychromecast.get_listed_chromecasts(
            known_hosts=[leader_ip]
        )
    except Exception as e:
        _LOGGER.error("Cast discovery failed at %s: %s", leader_ip, e)
        return False

    try:
        target = None
        wanted = uuid.lower().replace("-", "")
        for c in chromecasts:
            cuuid = str(c.cast_info.uuid).lower().replace("-", "")
            if cuuid.startswith(wanted[:8]) or cuuid == wanted:
                target = c
                break
        if not target:
            _LOGGER.warning(
                "Cast group %s not found among devices at %s", uuid, leader_ip
            )
            return False
        target.wait(timeout=10)
        target.media_controller.play_media(url, content_type)
        target.media_controller.block_until_active()
        return True
    finally:
        try:
            pychromecast.stop_discovery(browser)
        except Exception:
            pass


async def async_register_cast_group_service(hass: HomeAssistant) -> None:
    """Register lithe_audio.play_group."""

    async def svc_play_group(call: ServiceCall) -> None:
        leader_ip    = call.data.get("leader_ip", "")
        uuid         = call.data.get("uuid", "")
        url          = call.data.get("url", "")
        content_type = call.data.get("content_type", "audio/mp3")
        member_ips   = call.data.get("member_ips", []) or []
        volume       = call.data.get("volume")

        if not (leader_ip and uuid and url):
            _LOGGER.error("play_group requires leader_ip, uuid and url")
            return

        ok = await hass.async_add_executor_job(
            _cast_to_group, leader_ip, uuid, url, content_type
        )
        if not ok:
            return

        # Sync LUCI volume on each member if requested
        if volume is not None:
            vol = max(0, min(100, int(volume)))
            bucket = hass.data.get(DOMAIN, {})
            for ip in member_ips:
                for _entry_id, entry_data in bucket.items():
                    if not isinstance(entry_data, dict):
                        continue
                    coord = entry_data.get(DATA_COORDINATOR)
                    if coord and coord.client.host == ip:
                        try:
                            await coord.client.async_set_volume(vol)
                        except Exception as e:
                            _LOGGER.warning(
                                "Group volume set failed on %s: %s", ip, e
                            )

    if not hass.services.has_service(DOMAIN, "play_group"):
        hass.services.async_register(DOMAIN, "play_group", svc_play_group)
