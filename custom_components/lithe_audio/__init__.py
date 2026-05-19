"""Lithe Audio integration for Home Assistant."""
from __future__ import annotations

import logging
import socket as _sock

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .cast_group import async_register_cast_group_service
from .const import (
    BT_DISC, BT_PAIR, CONF_CERT_PATH, CONF_HOST, CONF_KEY_PATH,
    CONF_PORT, CONF_PRODUCT, CONF_USE_TLS, DATA_COORDINATOR, DOMAIN,
    DSP_BALANCE, DSP_EQ, DSP_HIGHPASS, DSP_LOUDNESS, DSP_NIGHTMODE, DSP_OUTPUT,
    EQ_PRESETS, HP_OPTIONS, LS9_PRODUCTS, OUT_OPTIONS, PRODUCT_CHIMES,
)
from .coordinator import LitheAudioCoordinator
from .lithe_client import LitheClient, LitheClientLS9
from .alarms import (
    SOURCE_PRESET, SOURCE_FAVOURITE, SOURCE_CHIME, SOURCE_URL,
    REPEAT_ONE_OFF, REPEAT_DAILY, REPEAT_WEEKLY, REPEAT_MONTHLY,
    async_setup_alarm_manager, get_manager,
)
from .prayer import async_register_prayer_service, async_unload_prayer

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.MEDIA_PLAYER,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.SENSOR,
]


def _detect_local_ip() -> str:
    try:
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _infer_product(entry_data: dict) -> str:
    """Guess a product ID from legacy/incomplete config-entry data.

    Used when migrating entries created by older versions that didn't
    persist `product`. Defaults err on the conservative side.
    """
    from .const import (
        PRODUCT_MICRO, PRODUCT_PRO, PRODUCT_PRO2, PRODUCT_V2, PRODUCT_V3,
        PRODUCT_IO1,
    )
    # Sometimes older entries stored a 'platform' string instead
    plat = (entry_data.get("platform") or "").upper()
    title_hint = (entry_data.get("title") or entry_data.get("name") or "").upper()
    cert_present = bool(entry_data.get(CONF_CERT_PATH)) or entry_data.get(CONF_USE_TLS)

    # Title-based hint first
    if "PRO2" in title_hint or "PRO 2" in title_hint:  return PRODUCT_PRO2
    if "V3" in title_hint:                              return PRODUCT_V3
    if "IO1" in title_hint:                             return PRODUCT_IO1
    if "MICRO" in title_hint:                           return PRODUCT_MICRO
    if "V2" in title_hint:                              return PRODUCT_V2
    if "PRO" in title_hint:                             return PRODUCT_PRO

    # Platform-based fallback
    if plat == "LS10" or cert_present:                  return PRODUCT_PRO2
    return PRODUCT_V2


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Lithe Audio from a config entry."""
    # Version banner — helps diagnose partial-install issues. The user
    # can grep the log for this line to confirm the running version.
    def _read_install_info() -> tuple[str, list[str]]:
        """Read manifest version + check critical files for staleness.

        Runs in executor to avoid blocking the event loop with sync I/O.
        Returns (version, list_of_stale_files).
        """
        import json as _json
        from pathlib import Path as _Path
        _here = _Path(__file__).parent
        _version = "unknown"
        _mpath = _here / "manifest.json"
        if _mpath.exists():
            try:
                with open(_mpath) as _f:
                    _version = _json.load(_f).get("version", "unknown")
            except Exception:
                pass
        # Critical-file freshness check via known markers in current code
        _expected_markers = {
            "lithe_client.py": "Sniffer-confirmed mappings (2026-05-18)",
            "card_resource.py": "Probe filesystem in executor",
            "switch.py": "2-way sync (factually verified 2026-05-18)",
            "number.py": "2-way sync (factually verified 2026-05-18)",
            "media_player.py": "_EXCLUDED_SOURCES",
        }
        _stale: list[str] = []
        for _fname, _marker in _expected_markers.items():
            _fpath = _here / _fname
            try:
                _text = _fpath.read_text(encoding="utf-8", errors="ignore")
                if _marker not in _text:
                    _stale.append(_fname)
            except Exception:
                pass
        return _version, _stale

    try:
        _version, _stale = await hass.async_add_executor_job(_read_install_info)
        _LOGGER.warning(
            "=== Lithe Audio integration starting — manifest version %s ===",
            _version,
        )
        for _fname in _stale:
            _LOGGER.error(
                "PARTIAL INSTALL DETECTED: %s is STALE (missing marker). "
                "Delete /config/custom_components/lithe_audio/ entirely "
                "and reinstall v%s. Bug reports from a partial install "
                "won't be reproducible.",
                _fname, _version,
            )
    except Exception:
        pass

    # Migration: entries created by older versions may not have `product`.
    if CONF_PRODUCT not in entry.data:
        inferred = _infer_product({**entry.data, "title": entry.title})
        _LOGGER.warning(
            "Lithe Audio entry %s has no '%s' key — migrating with inferred value '%s'. "
            "Delete and re-add the entry if this is wrong.",
            entry.title, CONF_PRODUCT, inferred,
        )
        new_data = {**entry.data, CONF_PRODUCT: inferred}
        hass.config_entries.async_update_entry(entry, data=new_data)

    # Cleanup: earlier versions registered Cast group proxy entities
    # under the lithe_audio domain. v1.3.5 removes that approach
    # entirely (Cast groups are selected via the dedicated select
    # entity, not the join picker). Remove any leftover proxy registry
    # entries so they don't show as ghosts.
    try:
        from homeassistant.helpers import device_registry as dr, entity_registry as er
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)
        # Remove stale proxy ENTITIES (unique_id starts with lithe_cast_proxy_)
        stale_entity_ids = [
            e.entity_id for e in ent_reg.entities.values()
            if e.platform == DOMAIN
            and e.unique_id
            and e.unique_id.startswith("lithe_cast_proxy_")
        ]
        for eid in stale_entity_ids:
            ent_reg.async_remove(eid)
            _LOGGER.info("Removed stale Cast proxy entity: %s", eid)
        # Remove stale standalone proxy DEVICES (v1.1.98 leftovers)
        for dev in list(dev_reg.devices.values()):
            for domain, ident in dev.identifiers:
                if domain == DOMAIN and isinstance(ident, str) and ident.startswith("cast_proxy_"):
                    dev_reg.async_remove_device(dev.id)
                    _LOGGER.info("Removed stale Cast proxy device: %s", dev.name or dev.id)
                    break
    except Exception as e:
        _LOGGER.debug("Cast proxy cleanup skipped: %s", e)

    product = entry.data[CONF_PRODUCT]
    host    = entry.data[CONF_HOST]
    port    = entry.data.get(CONF_PORT, 7777)
    use_tls = entry.data.get(CONF_USE_TLS, product not in LS9_PRODUCTS)
    cert    = entry.data.get(CONF_CERT_PATH) or None
    key     = entry.data.get(CONF_KEY_PATH) or None

    # Fall back to bundled certs if a TLS product has no cert path
    if use_tls and not (cert and key):
        from .const import BUNDLED_CERT_KEY, BUNDLED_CERT_PEM
        cert = cert or BUNDLED_CERT_PEM
        key  = key  or BUNDLED_CERT_KEY

    local_ip = _detect_local_ip()

    ClientCls = LitheClientLS9 if product in LS9_PRODUCTS else LitheClient
    client = ClientCls(host, port, use_tls, cert, key, local_ip)

    coordinator = LitheAudioCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass)
    await async_register_prayer_service(hass)
    await async_register_cast_group_service(hass)

    # Alarm manager — single instance shared across config entries.
    if "alarms" not in hass.data.get(DOMAIN, {}):
        await async_setup_alarm_manager(hass)
        _register_alarm_services(hass)

    # Group manager — single instance shared across config entries.
    if "groups_mgr" not in hass.data.get(DOMAIN, {}):
        from .group import async_setup_group_manager
        await async_setup_group_manager(hass)
        _register_group_services(hass)

    # Announce / broadcast / doorbell services (high-level wrappers)
    if not hass.data.get(DOMAIN, {}).get("_announce_registered"):
        from .announce import register_announce_services
        register_announce_services(hass)
        hass.data[DOMAIN]["_announce_registered"] = True

    # Local (HA-side) favourites — works around firmware limitation
    # where Direct URL streams cannot be saved as native favourites.
    if "local_favs" not in hass.data.get(DOMAIN, {}):
        from .local_favs import async_setup_local_favourites, register_local_fav_services
        await async_setup_local_favourites(hass)
        register_local_fav_services(hass)

    # Tannoy / PA override service — register lithe_audio.tannoy AND
    # notify.lithe_tannoy (legacy callers).
    if not hass.data.get(DOMAIN, {}).get("_tannoy_registered"):
        from .notify import register_tannoy_service
        register_tannoy_service(hass)
        hass.data[DOMAIN]["_tannoy_registered"] = True

    # Snapshot / restore / sleep-timer services (Sonos + Bluesound patterns)
    if not hass.data.get(DOMAIN, {}).get("_snapshot_registered"):
        from .snapshot import register_snapshot_services
        register_snapshot_services(hass)
        hass.data[DOMAIN]["_snapshot_registered"] = True

    # Lovelace card auto-registration (idempotent, safe to call repeatedly)
    if not hass.data.get(DOMAIN, {}).get("_card_registered"):
        try:
            from .card_resource import async_register_card
            await async_register_card(hass)
            hass.data[DOMAIN]["_card_registered"] = True
        except Exception as e:
            _LOGGER.debug("Card registration failed (non-fatal): %s", e)

    # Apply Prayer Scheduler options if user has configured them via UI
    await _apply_prayer_options(hass, entry)

    # Reload the integration when options change so the scheduler picks up edits
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration so Prayer Scheduler picks up new schedule."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _apply_prayer_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """If user has configured Prayer in Options Flow, start the scheduler."""
    opts = entry.options or {}
    prayer_cfg = opts.get("prayer") or {}
    if not prayer_cfg.get("enabled"):
        return

    host = entry.data.get("host")
    entries_cfg = prayer_cfg.get("entries", {}) or {}
    if not entries_cfg:
        return

    # Convert UI per-prayer entries into the format set_prayer_schedule expects.
    # Each entry targets THIS speaker only — the user configures per-speaker via
    # the Options Flow on each integration card.
    entries_list = []
    for prayer_name, e in entries_cfg.items():
        entries_list.append({
            "prayer":   prayer_name,
            "speakers": [host],
            "url":      e.get("url"),
            "volume":   int(e.get("volume", 70)),
            "days":     e.get("days", "daily"),
        })
    if not entries_list:
        return

    try:
        await hass.services.async_call(
            DOMAIN, "set_prayer_schedule",
            {
                "city":    prayer_cfg.get("city", "London"),
                "country": prayer_cfg.get("country", "GB"),
                "method":  int(prayer_cfg.get("method", 2)),
                "entries": entries_list,
            },
            blocking=False,
        )
        _LOGGER.info(
            "Applied Prayer Schedule for %s — %d entries",
            host, len(entries_list),
        )
    except Exception as e:
        _LOGGER.error("Failed to apply Prayer Schedule: %s", e)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        bucket = hass.data.get(DOMAIN, {})
        entry_data = bucket.pop(entry.entry_id, None)
        if entry_data:
            coordinator: LitheAudioCoordinator = entry_data[DATA_COORDINATOR]
            await coordinator.async_shutdown()

        # If this was the last config entry, tear down shared services too
        has_other_entries = any(
            isinstance(v, dict) and DATA_COORDINATOR in v
            for v in bucket.values()
        )
        if not has_other_entries:
            await async_unload_prayer(hass)
            for svc in (
                "play_chime", "play_url", "play_favourite", "save_favourite",
                "play_quran_juz", "play_adhan",
                "set_volume_preset", "select_source_type",
                "alarm_create", "alarm_update", "alarm_delete",
                "alarm_toggle", "alarm_snooze", "alarm_dismiss",
                "group_create", "group_update", "group_delete",
                "announce", "broadcast", "doorbell",
                "fav_save", "fav_play", "fav_list", "fav_delete",
                "snapshot", "restore",
                "set_sleep_timer", "clear_sleep_timer",
                "tannoy",
                "set_dsp_eq", "set_dsp_output", "set_dsp_nightmode",
                "set_dsp_highpass", "set_dsp_balance", "set_dsp_loudness",
                "bluetooth_pair", "bluetooth_disconnect",
                "reboot", "set_name", "play_group", "set_prayer_schedule",
            ):
                if hass.services.has_service(DOMAIN, svc):
                    hass.services.async_remove(DOMAIN, svc)
    return unload_ok


# ── Service dispatch ────────────────────────────────────────────────────────

def _resolve_coordinators(hass: HomeAssistant, call: ServiceCall) -> list[LitheAudioCoordinator]:
    """Resolve a service call's targets to coordinator instances.

    Looks at entity_id (single or list) on the call data and matches each one
    to the integration's entry that owns it.
    """
    bucket = hass.data.get(DOMAIN, {})
    if not bucket:
        return []

    raw = call.data.get("entity_id")
    if isinstance(raw, str):
        targets = [raw]
    elif isinstance(raw, list):
        targets = list(raw)
    else:
        targets = []

    coordinators: list[LitheAudioCoordinator] = []
    seen: set[str] = set()

    if targets:
        ent_reg = er.async_get(hass)
        for eid in targets:
            ent = ent_reg.async_get(eid)
            if not ent or not ent.config_entry_id:
                continue
            entry_data = bucket.get(ent.config_entry_id)
            if entry_data and entry_data[DATA_COORDINATOR] not in coordinators:
                coordinators.append(entry_data[DATA_COORDINATOR])
                seen.add(ent.config_entry_id)
        if coordinators:
            return coordinators

    # Fallback: every configured speaker
    for entry_id, entry_data in bucket.items():
        if not isinstance(entry_data, dict) or DATA_COORDINATOR not in entry_data:
            continue
        if entry_id in seen:
            continue
        coordinators.append(entry_data[DATA_COORDINATOR])
    return coordinators


def _register_group_services(hass: HomeAssistant) -> None:
    """Register group-management services.

    Services:
      lithe_audio.group_create
      lithe_audio.group_update
      lithe_audio.group_delete
    """
    from .group import get_group_manager

    async def svc_group_create(call):
        mgr = get_group_manager(hass)
        if not mgr:
            return
        members = call.data.get("members") or []
        if isinstance(members, str):
            members = [m.strip() for m in members.split(",") if m.strip()]
        group = {
            "name":     (call.data.get("name") or "New Group").strip(),
            "members":  members,
            "default_volume": int(call.data.get("default_volume", 50)),
        }
        gid = await mgr.async_add_group(group)
        _LOGGER.info("Created group %s — restart HA to see the new media_player entity", gid)

    async def svc_group_update(call):
        mgr = get_group_manager(hass)
        if not mgr:
            return
        gid = call.data.get("id")
        if not gid:
            return
        patch = {k: v for k, v in call.data.items() if k != "id"}
        if "members" in patch and isinstance(patch["members"], str):
            patch["members"] = [m.strip() for m in patch["members"].split(",") if m.strip()]
        await mgr.async_update_group(gid, patch)

    async def svc_group_delete(call):
        mgr = get_group_manager(hass)
        if not mgr:
            return
        gid = call.data.get("id")
        if gid:
            await mgr.async_delete_group(gid)

    hass.services.async_register(DOMAIN, "group_create", svc_group_create)
    hass.services.async_register(DOMAIN, "group_update", svc_group_update)
    hass.services.async_register(DOMAIN, "group_delete", svc_group_delete)


def _register_alarm_services(hass: HomeAssistant) -> None:
    """Register alarm-related services on the lithe_audio domain.

    Services:
      lithe_audio.alarm_create   — create a new alarm
      lithe_audio.alarm_update   — modify an existing alarm
      lithe_audio.alarm_delete   — remove an alarm
      lithe_audio.alarm_toggle   — enable/disable
      lithe_audio.alarm_snooze   — snooze a firing alarm
      lithe_audio.alarm_dismiss  — stop a firing alarm
    """

    async def svc_create(call):
        mgr = get_manager(hass)
        if not mgr:
            return
        from .alarms import default_alarm
        alarm = default_alarm()
        # Apply user-provided fields
        for key in (
            "name", "time", "repeat", "days", "day_of_month", "date",
            "speakers", "source", "preset_url", "favourite_slot",
            "chime_slot", "custom_url", "volume", "fade_in_seconds",
            "snooze_minutes", "enabled",
        ):
            if key in call.data:
                alarm[key] = call.data[key]
        # Type fixes
        if "volume" in call.data:
            alarm["volume"] = int(call.data["volume"])
        if "fade_in_seconds" in call.data:
            alarm["fade_in_seconds"] = int(call.data["fade_in_seconds"])
        if "snooze_minutes" in call.data:
            alarm["snooze_minutes"] = int(call.data["snooze_minutes"])
        if "favourite_slot" in call.data:
            alarm["favourite_slot"] = int(call.data["favourite_slot"])
        if "chime_slot" in call.data:
            alarm["chime_slot"] = int(call.data["chime_slot"])
        if "day_of_month" in call.data:
            alarm["day_of_month"] = int(call.data["day_of_month"])
        alarm_id = await mgr.async_add_alarm(alarm)
        _LOGGER.info("Created alarm %s via service", alarm_id)

    async def svc_update(call):
        mgr = get_manager(hass)
        if not mgr:
            return
        alarm_id = call.data.get("id")
        if not alarm_id:
            _LOGGER.error("alarm_update requires 'id'")
            return
        patch = {k: v for k, v in call.data.items() if k != "id"}
        await mgr.async_update_alarm(alarm_id, patch)

    async def svc_delete(call):
        mgr = get_manager(hass)
        if not mgr:
            return
        alarm_id = call.data.get("id")
        if not alarm_id:
            return
        await mgr.async_delete_alarm(alarm_id)

    async def svc_toggle(call):
        mgr = get_manager(hass)
        if not mgr:
            return
        alarm_id = call.data.get("id")
        enabled = bool(call.data.get("enabled", True))
        if alarm_id:
            await mgr.async_toggle_alarm(alarm_id, enabled)

    async def svc_snooze(call):
        mgr = get_manager(hass)
        if not mgr:
            return
        alarm_id = call.data.get("id")
        minutes = call.data.get("minutes")
        if alarm_id:
            await mgr.async_snooze(alarm_id, int(minutes) if minutes else None)

    async def svc_dismiss(call):
        mgr = get_manager(hass)
        if not mgr:
            return
        alarm_id = call.data.get("id")
        if alarm_id:
            await mgr.async_dismiss(alarm_id)

    hass.services.async_register(DOMAIN, "alarm_create",   svc_create)
    hass.services.async_register(DOMAIN, "alarm_update",   svc_update)
    hass.services.async_register(DOMAIN, "alarm_delete",   svc_delete)
    hass.services.async_register(DOMAIN, "alarm_toggle",   svc_toggle)
    hass.services.async_register(DOMAIN, "alarm_snooze",   svc_snooze)
    hass.services.async_register(DOMAIN, "alarm_dismiss",  svc_dismiss)


def _register_services(hass: HomeAssistant) -> None:
    """Register all Lithe Audio service actions."""

    if hass.services.has_service(DOMAIN, "play_chime"):
        return  # Already registered for another entry

    async def _for_each(call: ServiceCall, fn) -> None:
        coords = _resolve_coordinators(hass, call)
        if not coords:
            raise HomeAssistantError("No Lithe Audio speaker matched the service target")
        for coord in coords:
            try:
                await fn(coord.client)
            except Exception as e:
                _LOGGER.error("%s failed on %s: %s", call.service, coord.client.host, e)

    # ── Playback / chime ────────────────────────────────────────────────
    async def svc_play_chime(call: ServiceCall) -> None:
        n = max(1, min(int(call.data.get("chime_number", 1)), 15))
        method = str(call.data.get("method", "Indexed")).strip().lower()
        if method.startswith("direct"):
            # Method 2: MB#41 PLAYITEM:DIRECT:/system/usr/songN.mp3
            path = f"/system/usr/song{n}.mp3"
            await _for_each(call, lambda c: c.async_play_url(path))
        else:
            # Method 1: MB#80 "play N"  (default)
            await _for_each(call, lambda c: c.async_play_chime(n))

    async def svc_play_url(call: ServiceCall) -> None:
        url = str(call.data.get("url", "")).strip()
        if not url:
            return
        await _for_each(call, lambda c: c.async_play_url(url))

    async def svc_play_favourite(call: ServiceCall) -> None:
        """Play a saved favourite by slot number.

        Prefers HA-side local favourites (works for any URL). Falls back
        to native MB#70 PLAYFAVITEM if the slot has no local entry.
        """
        slot = int(call.data.get("slot", 1))
        # Try local favs first (faster, works for Direct URL streams)
        from .local_favs import get_local_favs
        mgr = get_local_favs(hass)
        if mgr is not None:
            fav = mgr.get(slot)
            if fav and fav.get("url"):
                _LOGGER.info("play_favourite: local slot %d → %s",
                             slot, fav["url"])
                await _for_each(call, lambda c: c.async_play_url(fav["url"]))
                return
        # Fall back to native firmware favourite
        _LOGGER.info("play_favourite: trying native MB#70 slot %d", slot)
        await _for_each(call, lambda c: c.async_play_favourite(slot))

    async def svc_save_favourite(call: ServiceCall) -> None:
        """Save current playback to a slot.

        Tries HA-side local favourites first (saves the current URL).
        Also tries the native MB#70 FAV_SAVE — that one only works for
        streaming sources (Spotify/AirPlay/Cast), Direct URL streams
        get GENERIC_FAV_SAVE_FAIL from firmware, which is normal.
        """
        slot = int(call.data.get("slot", 1))
        from .local_favs import get_local_favs
        mgr = get_local_favs(hass)

        async def save_one(client) -> None:
            url = client.state.last_played_url
            name = client.state.title or ""
            if not name and url:
                from urllib.parse import urlparse
                name = urlparse(url).path.rsplit("/", 1)[-1]
                if "." in name:
                    name = name.rsplit(".", 1)[0]
            if not name:
                name = f"Favourite {slot}"
            if mgr is not None and url:
                await mgr.async_set(slot, name, url)
            # Also try native firmware save (silently fails for Direct URL)
            try:
                await client.async_save_favourite(slot)
            except Exception:
                pass

        await _for_each(call, save_one)

    async def svc_set_volume_preset(call: ServiceCall) -> None:
        """Quick-pick volume 0/20/40/60/80/100."""
        level = int(call.data.get("level", 40))
        level = max(0, min(100, level))
        # Each Lithe speaker exposes async_set_volume(0-100)
        await _for_each(call, lambda c: c.async_set_volume(level))
        _LOGGER.info("set_volume_preset → %d%%", level)

    async def svc_select_source_type(call: ServiceCall) -> None:
        """Switch active source by friendly name."""
        # Friendly name → numeric source ID (per LUCI MB#50)
        SOURCE_NAME_TO_ID = {
            "no_source":   0,
            "airplay":     1,
            "dlna":        2,    # DMR
            "spotify":     4,
            "usb":         5,
            "aux":         13,   # AUX In
            "spdif":       14,   # SPDIF In (Optical)
            "direct_url":  17,
            "bluetooth":   19,
            "cast":        24,   # Google Cast
        }
        source = str(call.data.get("source", "")).strip().lower()
        src_id = SOURCE_NAME_TO_ID.get(source)
        if src_id is None:
            _LOGGER.warning(
                "select_source_type: unknown source '%s' (valid: %s)",
                source, sorted(SOURCE_NAME_TO_ID.keys()),
            )
            return
        # Send MB#50 SET <id> per LUCI API
        async def do_switch(c):
            await c._send(0x02, 50, str(src_id))  # noqa: SLF001
            _LOGGER.info(
                "select_source_type: requested switch to '%s' (source=%d)",
                source, src_id,
            )
        await _for_each(call, do_switch)

    async def svc_play_quran_juz(call: ServiceCall) -> None:
        """Play a specific Juz of the Quran via the tannoy flow."""
        from .const import QURAN_JUZ
        juz = int(call.data.get("juz", 1))
        juz = max(1, min(30, juz))
        url = QURAN_JUZ.get(juz)
        if not url:
            _LOGGER.error("Quran Juz %d not in preset table", juz)
            return
        volume = int(call.data.get("volume", 70))
        coords = _resolve_coordinators(hass, call)
        speakers = [c.client.host for c in coords]
        if not speakers:
            _LOGGER.warning("play_quran_juz: no target speakers resolved")
            return
        await hass.services.async_call(
            "notify", "lithe_tannoy",
            {
                "message": url,
                "data": {"mode": "start", "volume": volume, "speakers": speakers},
            },
            blocking=False,
        )

    async def svc_play_adhan(call: ServiceCall) -> None:
        """Play one of the preset Adhan recordings via the tannoy flow."""
        from .const import ADHAN_PRESETS
        preset = call.data.get("preset", "Adhan — Makkah")
        url = ADHAN_PRESETS.get(preset)
        if not url:
            _LOGGER.error("Adhan preset %r not in preset table", preset)
            return
        volume = int(call.data.get("volume", 70))
        coords = _resolve_coordinators(hass, call)
        speakers = [c.client.host for c in coords]
        if not speakers:
            _LOGGER.warning("play_adhan: no target speakers resolved")
            return
        await hass.services.async_call(
            "notify", "lithe_tannoy",
            {
                "message": url,
                "data": {"mode": "start", "volume": volume, "speakers": speakers},
            },
            blocking=False,
        )

    async def svc_set_name(call: ServiceCall) -> None:
        name = str(call.data.get("name", "")).strip()
        if not name:
            return
        await _for_each(call, lambda c: c.async_set_name(name))

    # ── DSP ─────────────────────────────────────────────────────────────
    async def svc_set_eq(call: ServiceCall) -> None:
        preset = call.data.get("preset", "Normal")
        idx = EQ_PRESETS.index(preset) if preset in EQ_PRESETS else 0
        await _for_each(call, lambda c: c.async_dsp_command(DSP_EQ, idx))

    async def svc_set_output(call: ServiceCall) -> None:
        mode = call.data.get("mode", "Stereo")
        idx = OUT_OPTIONS.index(mode) if mode in OUT_OPTIONS else 0
        await _for_each(call, lambda c: c.async_dsp_command(DSP_OUTPUT, idx))

    async def svc_set_nightmode(call: ServiceCall) -> None:
        val = 1 if call.data.get("enabled") else 0
        await _for_each(call, lambda c: c.async_dsp_command(DSP_NIGHTMODE, val))

    async def svc_set_highpass(call: ServiceCall) -> None:
        freq = call.data.get("frequency", "OFF")
        idx = HP_OPTIONS.index(freq) if freq in HP_OPTIONS else 0
        await _for_each(call, lambda c: c.async_dsp_command(DSP_HIGHPASS, idx))

    async def svc_set_balance(call: ServiceCall) -> None:
        val = max(-6, min(6, int(call.data.get("balance", 0))))
        await _for_each(call, lambda c: c.async_dsp_command(DSP_BALANCE, val))

    async def svc_set_loudness(call: ServiceCall) -> None:
        val = int(call.data.get("value", 0))
        await _for_each(call, lambda c: c.async_dsp_command(DSP_LOUDNESS, val))

    # ── Bluetooth / system ──────────────────────────────────────────────
    async def svc_bt_pair(call: ServiceCall) -> None:
        await _for_each(call, lambda c: c.async_bluetooth(BT_PAIR))

    async def svc_bt_disc(call: ServiceCall) -> None:
        await _for_each(call, lambda c: c.async_bluetooth(BT_DISC))

    async def svc_reboot(call: ServiceCall) -> None:
        await _for_each(call, lambda c: c.async_reboot())

    hass.services.async_register(DOMAIN, "play_chime",       svc_play_chime)
    hass.services.async_register(DOMAIN, "play_url",         svc_play_url)
    hass.services.async_register(DOMAIN, "play_favourite",   svc_play_favourite)
    hass.services.async_register(DOMAIN, "save_favourite",   svc_save_favourite)
    hass.services.async_register(DOMAIN, "play_quran_juz",   svc_play_quran_juz)
    hass.services.async_register(DOMAIN, "play_adhan",       svc_play_adhan)
    hass.services.async_register(DOMAIN, "set_volume_preset",   svc_set_volume_preset)
    hass.services.async_register(DOMAIN, "select_source_type",  svc_select_source_type)
    hass.services.async_register(DOMAIN, "set_name",         svc_set_name)
    hass.services.async_register(DOMAIN, "set_dsp_eq",       svc_set_eq)
    hass.services.async_register(DOMAIN, "set_dsp_output",   svc_set_output)
    hass.services.async_register(DOMAIN, "set_dsp_nightmode", svc_set_nightmode)
    hass.services.async_register(DOMAIN, "set_dsp_highpass", svc_set_highpass)
    hass.services.async_register(DOMAIN, "set_dsp_balance",  svc_set_balance)
    hass.services.async_register(DOMAIN, "set_dsp_loudness", svc_set_loudness)
    hass.services.async_register(DOMAIN, "bluetooth_pair",   svc_bt_pair)
    hass.services.async_register(DOMAIN, "bluetooth_disconnect", svc_bt_disc)
    hass.services.async_register(DOMAIN, "reboot",           svc_reboot)
