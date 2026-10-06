"""Diagnostics support for Lithe Audio."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CERT_PATH, CONF_KEY_PATH, DATA_COORDINATOR, DOMAIN, PRODUCT_NAMES, caps,
)
from .coordinator import LitheAudioCoordinator

# Don't leak cert paths or device MAC in the downloadable diagnostic
TO_REDACT = {CONF_CERT_PATH, CONF_KEY_PATH, "mac", "mac_address"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coord: LitheAudioCoordinator | None = entry_data.get(DATA_COORDINATOR)

    product = entry.data.get("product", "")

    state_snapshot: dict[str, Any] = {}
    fav_count = 0
    if coord and coord.client:
        s = coord.client.state
        position_updated_at_utc = None
        position_age_ms = None
        if s.position_updated_at:
            position_updated_at_utc = datetime.fromtimestamp(
                s.position_updated_at, tz=timezone.utc
            ).isoformat()
            position_age_ms = max(
                0, round((time.time() - s.position_updated_at) * 1000)
            )
        state_snapshot = {
            "connected":         s.connected,
            "name":              s.name,
            "firmware":          s.firmware,
            "model":             s.model,
            "detected_product":  s.detected_product,
            "model_number":      s.model_number,
            "model_variant":     s.model_variant,
            "wifi_band":         s.wifi_band,
            "timezone":          s.timezone,
            "cast_version":      s.cast_version,
            "net_mode":          s.net_mode,
            # Network (MB#123, MB#124, MB#151, MB#208/NV)
            "host_ip":           entry.data.get("host", ""),
            "speaker_ip":        s.ip_address,
            "network_interface": s.network_interface,
            "network_status":    s.network_status,
            "speaker_status":    s.speaker_status,
            "wifi_rssi_dbm":     s.wifi_rssi_dbm,
            "wifi_rssi_source":  s.wifi_rssi_source,
            "ssid":              s.ssid,
            # Playback
            "play_state":        s.play_state,
            "source_id":         s.source_id,
            "source_name":       s.source_name,
            "volume":            s.volume,
            "muted":             s.muted,
            "position_ms":       s.position_ms,
            "position_seconds":  s.position_seconds,
            "position_updated_at_utc": position_updated_at_utc,
            "position_age_ms":   position_age_ms,
            "is_live":           s.is_live,
            "shuffle":           s.shuffle,
            "repeat":            s.repeat,
            "bt_status":         s.bt_status,
            "bt_enabled":        s.bt_enabled,
            "artwork_url":       s.artwork_url,
            "artwork_revision":  s.artwork_revision,
            "title":             s.title,
            "artist":            s.artist,
            "album":             s.album,
            "duration_ms":       s.duration_ms,
            # DSP / model-specific controls
            "dsp_highpass":      s.dsp_highpass,
            "dsp_tuning":        s.dsp_tuning,
        }
        fav_count = len(s.favourites)

    return {
        "entry": {
            "title":   entry.title,
            "version": entry.version,
            "data":    async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "product": {
            "id":           product,
            "display_name": PRODUCT_NAMES.get(product, product),
            "capabilities": caps(product),
        },
        "state":           async_redact_data(state_snapshot, TO_REDACT),
        "favourite_count": fav_count,
    }
