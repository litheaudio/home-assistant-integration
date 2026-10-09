"""Config flow for Lithe Audio integration.

Two paths:
  - **Scan network**: LSSDP discovery, user picks from the list.
  - **Enter IP manually**: classic host + model entry.

Both paths auto-detect LS10 vs LS9 (LS10 = TLS 1.2, LS9 = plain TCP).
The TLS client certificate is **bundled with the integration** — users
never have to obtain or paste certs.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .const import (
    BUNDLED_CERT_KEY, BUNDLED_CERT_PEM,
    CONF_CERT_PATH, CONF_KEY_PATH, CONF_PRODUCT, CONF_USE_TLS,
    DATA_COORDINATOR, DEFAULT_PORT, DOMAIN, LS10_PRODUCTS, LS9_PRODUCTS,
    MAX_CHIME_SLOT,
    PRODUCT_NAMES,
    PRODUCT_PRO2, PRODUCT_V2, caps, product_from_model,
    all_preset_options,
)
from .discovery import DiscoveredDevice, async_discover

_LOGGER = logging.getLogger(__name__)


def _normalize_speaker_list(value, fallback_host: str) -> list[str]:
    """Normalize a speakers field value from the alarm/group UI.

    The multi-select SelectSelector returns a list, but if a user
    typed values manually we may get a comma-separated string. The
    YAML config also might pass a string. Always returns a list of
    stripped non-empty tokens.
    """
    if value is None or value == "":
        return [fallback_host] if fallback_host else []
    if isinstance(value, list):
        return [str(s).strip() for s in value if str(s).strip()]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    # Fallback: try to coerce
    try:
        return [str(value).strip()] if str(value).strip() else []
    except Exception:
        return []

PRODUCT_OPTIONS = {k: v for k, v in PRODUCT_NAMES.items()}


def _guess_product(dev: DiscoveredDevice) -> str:
    """Map an LSSDP-discovered model string to one of our product IDs."""
    if product := product_from_model(dev.model or dev.speaker_type):
        return product
    # Fall back on platform classification from LSSDP headers
    return PRODUCT_PRO2 if dev.platform == "LS10" else PRODUCT_V2


class LitheAudioConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Lithe Audio."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: list[DiscoveredDevice] = []
        self._discovery_host: str = ""
        self._discovery_name: str = ""

    # ── Step 1: choose Scan vs Manual ──────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """First step: scan network automatically, or enter an IP manually."""
        if user_input is not None:
            mode = user_input.get("mode", "scan")
            if mode == "manual":
                return await self.async_step_manual()
            return await self.async_step_scan()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("mode", default="scan"): vol.In({
                    "scan":   "Scan network for speakers",
                    "manual": "Enter speaker IP manually",
                }),
            }),
            description_placeholders={
                "products": ", ".join(PRODUCT_NAMES.values()),
            },
        )

    # ── Step 2a: scan path ──────────────────────────────────────────────

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Run LSSDP discovery and let the user pick a speaker."""
        if user_input is not None:
            choice = user_input.get("selected")
            if choice == "__manual__":
                return await self.async_step_manual()
            dev = next(
                (d for d in self._discovered if d.unique_id == choice), None
            )
            if dev is None:
                return await self.async_step_manual()
            return await self._async_finish_from_discovery(dev)

        # Perform the scan
        try:
            self._discovered = await async_discover(timeout=3.0)
        except Exception as exc:
            _LOGGER.warning("LSSDP discovery failed: %s", exc)
            self._discovered = []

        # Filter out already-configured speakers by unique_id
        existing = {
            e.unique_id for e in self._async_current_entries() if e.unique_id
        }
        new_devices = [d for d in self._discovered if d.unique_id not in existing]

        if not new_devices:
            # Nothing new found — fall through to manual entry
            return await self.async_step_manual()

        options = {d.unique_id: f"{d.name} — {d.host} ({d.platform})"
                   for d in new_devices}
        options["__manual__"] = "Enter speaker IP manually instead…"

        return self.async_show_form(
            step_id="scan",
            data_schema=vol.Schema({
                vol.Required("selected"): vol.In(options),
            }),
            description_placeholders={
                "count": str(len(new_devices)),
            },
        )

    async def _async_finish_from_discovery(
        self, dev: DiscoveredDevice
    ) -> FlowResult:
        """Create a config entry directly from a discovered device."""
        unique = dev.unique_id or f"{dev.host}_{dev.port}"
        await self.async_set_unique_id(unique)
        self._abort_if_unique_id_configured()

        product  = _guess_product(dev)
        use_tls  = product in LS10_PRODUCTS
        cert     = BUNDLED_CERT_PEM if use_tls else ""
        key      = BUNDLED_CERT_KEY if use_tls else ""

        # Quick connectivity test
        try:
            from .lithe_client import LitheClient, LitheClientLS9
            cls = LitheClientLS9 if product in LS9_PRODUCTS else LitheClient
            client = cls(dev.host, dev.port, use_tls, cert or None, key or None)
            await asyncio.wait_for(client.async_connect(), timeout=6.0)
            await client.async_disconnect()
        except Exception as e:
            _LOGGER.warning("Discovered %s but cannot connect: %s", dev.host, e)
            # Don't block setup — saving the entry lets the coordinator retry

        return self.async_create_entry(
            title=f"{dev.name} ({dev.host})",
            data={
                CONF_HOST:      dev.host,
                CONF_PORT:      dev.port,
                CONF_PRODUCT:   product,
                CONF_USE_TLS:   use_tls,
                CONF_CERT_PATH: cert,
                CONF_KEY_PATH:  key,
            },
        )

    # ── Step 2b: manual path ────────────────────────────────────────────

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Enter host / port / model manually. Certs are bundled — no prompt."""
        errors: dict[str, str] = {}

        if user_input is not None:
            product = user_input[CONF_PRODUCT]
            host    = user_input[CONF_HOST].strip()
            port    = int(user_input.get(CONF_PORT, DEFAULT_PORT))
            use_tls = product in LS10_PRODUCTS
            cert    = BUNDLED_CERT_PEM if use_tls else ""
            key     = BUNDLED_CERT_KEY if use_tls else ""

            await self.async_set_unique_id(f"{host}_{port}")
            self._abort_if_unique_id_configured()

            # Quick connectivity test
            try:
                from .lithe_client import LitheClient, LitheClientLS9
                cls = LitheClientLS9 if product in LS9_PRODUCTS else LitheClient
                client = cls(host, port, use_tls, cert or None, key or None)
                await asyncio.wait_for(client.async_connect(), timeout=6.0)
                await client.async_disconnect()
            except asyncio.TimeoutError:
                errors["base"] = "cannot_connect"
            except Exception as e:
                _LOGGER.debug("Manual flow connect error: %s", e)
                errors["base"] = "cannot_connect"

            if not errors:
                return self.async_create_entry(
                    title=f"{PRODUCT_NAMES[product]} ({host})",
                    data={
                        CONF_HOST:      host,
                        CONF_PORT:      port,
                        CONF_PRODUCT:   product,
                        CONF_USE_TLS:   use_tls,
                        CONF_CERT_PATH: cert,
                        CONF_KEY_PATH:  key,
                    },
                )

        schema = vol.Schema({
            vol.Required(CONF_PRODUCT, default=PRODUCT_PRO2): vol.In(PRODUCT_OPTIONS),
            vol.Required(CONF_HOST):  str,
            vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        })

        return self.async_show_form(
            step_id="manual",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "products": ", ".join(PRODUCT_NAMES.values()),
            },
        )

    # ── Zeroconf discovery (mDNS via _googlecast._tcp.local.) ──────────

    async def async_step_zeroconf(
        self, discovery_info: Any
    ) -> FlowResult:
        """Handle mDNS discovery via Cast."""
        host = discovery_info.host
        name = discovery_info.properties.get("fn", host)

        await self.async_set_unique_id(f"{host}_{DEFAULT_PORT}")
        self._abort_if_unique_id_configured()

        self.context["title_placeholders"] = {"name": name, "host": host}
        self._discovery_host = host
        self._discovery_name = name
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        host = self._discovery_host
        if user_input is not None:
            product = user_input[CONF_PRODUCT]
            use_tls = product in LS10_PRODUCTS
            cert    = BUNDLED_CERT_PEM if use_tls else ""
            key     = BUNDLED_CERT_KEY if use_tls else ""
            return self.async_create_entry(
                title=f"{PRODUCT_NAMES[product]} ({host})",
                data={
                    CONF_HOST:      host,
                    CONF_PORT:      DEFAULT_PORT,
                    CONF_PRODUCT:   product,
                    CONF_USE_TLS:   use_tls,
                    CONF_CERT_PATH: cert,
                    CONF_KEY_PATH:  key,
                },
            )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_PRODUCT, default=PRODUCT_PRO2): vol.In(PRODUCT_OPTIONS),
            }),
            description_placeholders={
                "host": host,
                "name": self._discovery_name,
            },
        )

    # ── Options flow entry point ───────────────────────────────────────
    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "LitheAudioOptionsFlow":
        """Return the options flow handler — provides Prayer Scheduler UI."""
        return LitheAudioOptionsFlow(config_entry)


# ── Constants used by the Options Flow (Prayer Scheduler UI) ────────────
PRAYER_NAMES_LIST = ["fajr", "sunrise", "dhuhr", "asr", "sunset", "maghrib", "isha"]
DAYS_OPTIONS = ["daily", "weekdays", "weekends", "friday", "saturday", "sunday"]
CALC_METHODS = {
    1:  "University of Islamic Sciences, Karachi",
    2:  "Islamic Society of North America (ISNA)",
    3:  "Muslim World League",
    4:  "Umm Al-Qura University, Makkah",
    5:  "Egyptian General Authority of Survey",
    8:  "Gulf Region",
    12: "Union des Organisations Islamiques de France",
    13: "Diyanet İşleri Başkanlığı, Turkey",
    15: "Moonsighting Committee Worldwide (Moonsighting.com)",
}

# Defaults if user has not yet configured anything
_DEFAULT_ADHAN_URL = "https://praytimes.org/audio/sunni/Adhan-Makkah.mp3"
_DEFAULT_VOLUME = 70


class LitheAudioOptionsFlow(config_entries.OptionsFlow):
    """Per-speaker options flow.

    Provides a built-in Prayer Scheduler UI similar to the standalone
    Adhan/Prayer scheduler webapp — configure city/country/method, then
    per-prayer URL/volume/days, all within the standard HA integration
    Configure dialog.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # NOTE: In HA Core 2024.12+ `self.config_entry` is a read-only
        # property on the OptionsFlow base class — we MUST NOT assign to it.
        # Store it under a private attribute instead.
        self._entry = config_entry
        self._draft: dict[str, Any] = dict(config_entry.options or {})
        # Per-prayer wizard state
        self._wizard_index: int = 0
        self._wizard_entries: dict[str, dict[str, Any]] = {}
        # Alarm edit state — None means "new alarm", dict means "editing"
        self._alarm_draft: dict[str, Any] | None = None
        # Group edit state — None means "new group", dict means "editing"
        self._group_draft: dict[str, Any] | None = None

    # ── Step 1: top-level menu ─────────────────────────────────────────
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        product_caps = caps(self._entry.data[CONF_PRODUCT])
        menu_options = {
            "groups":  "🔊  Multi-room Groups",
            "logging": "🐞  Debug logging",
        }
        if product_caps.get("scheduling", True):
            menu_options = {
                "prayer": "🕋  Prayer Schedule",
                "alarms": "⏰  Alarms",
                **menu_options,
            }
        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options,
        )

    # ── Groups — list existing groups + "Add new" ──────────────────────
    async def async_step_groups(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        from .group import get_group_manager
        mgr = get_group_manager(self.hass)
        if not mgr:
            return await self.async_step_init()

        groups = mgr.list_groups()

        if user_input is not None:
            choice = user_input.get("choice", "")
            if choice == "__add__":
                self._group_draft = None
                return await self.async_step_group_edit()
            if choice in {g["id"] for g in groups}:
                self._group_draft = dict(mgr.get_group(choice) or {})
                return await self.async_step_group_edit()
            return await self.async_step_init()

        choices: dict[str, str] = {}
        for g in groups:
            name = g.get("name", "Group")
            n = len(g.get("members", []) or [])
            choices[g["id"]] = f"🔊 {name} ({n} speakers)"
        choices["__add__"] = "➕  Add new group"

        # Detect Google Cast groups (configured in Google Home app) and
        # surface them in the description so users know they don't need
        # to build their own here.
        try:
            from .group import discover_cast_groups
            cast_groups = discover_cast_groups(self.hass)
        except Exception:
            cast_groups = []

        cast_summary = ""
        if cast_groups:
            cast_list = "\n".join(
                f"  • {cg['name']} → {cg['entity_id']}" for cg in cast_groups
            )
            cast_summary = (
                f"\n\nGoogle Cast groups detected ({len(cast_groups)}):\n{cast_list}"
                "\n\nThese are configured in the Google Home app and offer "
                "true multi-room sync. You can use them directly in "
                "automations / alarms / the Lithe card — no need to "
                "duplicate them here."
            )

        if groups:
            description = (
                f"You have {len(groups)} Lithe group(s). Pick one to edit/delete, "
                "or '➕ Add new group' to create one. Lithe groups are an "
                "application-level fan-out (each speaker streams its own copy) — "
                "for tight multi-room sync, use Google Cast groups instead."
                f"{cast_summary}"
            )
        else:
            description = (
                "Lithe groups are an application-level fan-out (each speaker "
                "streams its own copy). For tight multi-room sync, prefer "
                "Google Cast groups configured in the Google Home app."
                f"{cast_summary}"
                "\n\nChoose '➕ Add new group' to create a Lithe-side group."
            )

        schema = vol.Schema({
            vol.Required("choice", default="__add__"): vol.In(choices),
        })
        return self.async_show_form(
            step_id="groups",
            data_schema=schema,
            description_placeholders={"summary": description},
        )

    async def async_step_group_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        from .group import get_group_manager
        from .const import DATA_COORDINATOR
        mgr = get_group_manager(self.hass)
        if not mgr:
            return await self.async_step_init()

        editing = self._group_draft is not None
        existing = self._group_draft if editing else {
            "name": "New Group", "members": [], "default_volume": 50,
        }

        # Build list of all known Lithe speakers for the picker
        bucket = self.hass.data.get(DOMAIN, {})
        all_speakers: dict[str, str] = {}
        for _entry_id, entry_data in bucket.items():
            if not isinstance(entry_data, dict):
                continue
            coord = entry_data.get("coordinator") or entry_data.get(DATA_COORDINATOR)
            if coord:
                host = coord.client.host
                name = coord.client.state.name or host
                all_speakers[host] = f"{name} ({host})"

        # Handle delete
        if user_input is not None and user_input.get("_action") == "delete":
            if editing and existing.get("id"):
                await mgr.async_delete_group(existing["id"])
            return await self.async_step_groups()

        # Handle save
        if user_input is not None and user_input.get("_action") != "delete":
            members = user_input.get("members", []) or []
            patch = {
                "name":           (user_input.get("name") or "Group").strip(),
                "members":        list(members),
                "default_volume": int(user_input.get("default_volume", 50)),
            }
            if editing:
                await mgr.async_update_group(existing["id"], patch)
            else:
                await mgr.async_add_group(patch)
            return await self.async_step_groups()

        # Render form
        action_options = {"save": "💾  Save"}
        if editing:
            action_options["delete"] = "🗑  Delete this group"

        schema = vol.Schema({
            vol.Required("name", default=existing.get("name", "New Group")): str,
            vol.Required("members", default=existing.get("members", [])):
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": ip, "label": label}
                            for ip, label in all_speakers.items()
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ) if all_speakers else str,
            vol.Required("default_volume", default=existing.get("default_volume", 50)):
                vol.All(int, vol.Range(min=0, max=100)),
            vol.Required("_action", default="save"): vol.In(action_options),
        })
        return self.async_show_form(
            step_id="group_edit",
            data_schema=schema,
            description_placeholders={
                "title": "Edit Group" if editing else "New Group",
            },
        )

    # ── Alarms — list existing alarms + "Add new" ──────────────────────
    async def async_step_alarms(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Top-level Alarms menu: list existing alarms + add new."""
        from .alarms import get_manager
        mgr = get_manager(self.hass)
        if not mgr:
            return await self.async_step_init()

        alarms = mgr.list_alarms()

        # Selection from list
        if user_input is not None:
            choice = user_input.get("choice", "")
            if choice == "__add__":
                self._alarm_draft = None  # signals "new"
                return await self.async_step_alarm_edit()
            if choice and choice in {a["id"] for a in alarms}:
                self._alarm_draft = dict(mgr.get_alarm(choice) or {})
                return await self.async_step_alarm_edit()
            return await self.async_step_init()

        # Build choice dropdown: each alarm + add-new
        choices: dict[str, str] = {}
        for a in alarms:
            enabled_icon = "✅" if a.get("enabled") else "⚪"
            name = a.get("name", "Alarm")
            t = a.get("time", "--:--")
            repeat = a.get("repeat", "daily")
            if repeat == "weekly":
                days = ",".join(a.get("days", []) or [])
                repeat_str = f"weekly [{days}]"
            elif repeat == "one_off":
                repeat_str = f"once on {a.get('date') or 'next occurrence'}"
            elif repeat == "monthly":
                repeat_str = f"day {a.get('day_of_month','?')}"
            else:
                repeat_str = "daily"
            next_fire = mgr.next_fire_time(a["id"])
            if not a.get("enabled"):
                timer_status = "disabled"
            elif mgr.timer_registered(a["id"]):
                timer_status = (
                    f"next {next_fire.strftime('%Y-%m-%d %H:%M')}"
                    if next_fire else "timer registered"
                )
            else:
                timer_status = "timer not registered"
            choices[a["id"]] = (
                f"{enabled_icon} {name} — {t} ({repeat_str}; {timer_status})"
            )
        choices["__add__"] = "➕  Add new alarm"

        schema = vol.Schema({
            vol.Required("choice", default="__add__"): vol.In(choices),
        })
        if alarms:
            description = (
                f"You have {len(alarms)} alarm(s). Pick one to edit/delete, "
                "or '➕ Add new alarm' to create one."
            )
        else:
            description = (
                "No alarms yet. Choose '➕ Add new alarm' to create your first."
            )
        return self.async_show_form(
            step_id="alarms",
            data_schema=schema,
            description_placeholders={"summary": description},
        )

    # ── Alarm edit (also handles create) ───────────────────────────────
    async def async_step_alarm_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Create or edit an alarm.

        self._alarm_draft is either None (creating) or a dict (editing).
        """
        from .alarms import (
            get_manager, default_alarm, DAY_TOKENS,
            REPEAT_ONE_OFF, REPEAT_DAILY, REPEAT_WEEKLY, REPEAT_MONTHLY,
            SOURCE_PRESET, SOURCE_FAVOURITE, SOURCE_CHIME, SOURCE_URL,
        )
        mgr = get_manager(self.hass)
        if not mgr:
            return await self.async_step_init()

        editing = self._alarm_draft is not None
        existing = self._alarm_draft if editing else default_alarm()

        # Resolve the host of THIS speaker so it can be the default target
        my_host = self._entry.data.get("host", "")

        # Handle delete
        if user_input is not None and user_input.get("_action") == "delete":
            if editing and existing.get("id"):
                await mgr.async_delete_alarm(existing["id"])
                _LOGGER.info("Deleted alarm %s via UI", existing["id"])
            return await self.async_step_alarms()

        # Handle save
        if user_input is not None and user_input.get("_action") != "delete":
            # Build updated alarm dict
            patch = {
                "name":            (user_input.get("name") or existing["name"]).strip(),
                "time":            user_input.get("time", existing["time"]),
                "repeat":          user_input.get("repeat", existing["repeat"]),
                "days":            user_input.get("days", existing.get("days") or DAY_TOKENS),
                "day_of_month":    int(user_input.get("day_of_month",
                                                      existing.get("day_of_month", 1))),
                "date":            user_input.get("date", existing.get("date")),
                "speakers":        _normalize_speaker_list(
                                       user_input.get("speakers"), my_host
                                   ),
                "source":          user_input.get("source", existing["source"]),
                "preset_url":      (user_input.get("preset_url") or existing.get("preset_url","")).strip(),
                "favourite_slot":  int(user_input.get("favourite_slot",
                                                      existing.get("favourite_slot", 1))),
                "chime_slot":      int(user_input.get("chime_slot",
                                                      existing.get("chime_slot", 1))),
                "custom_url":      (user_input.get("custom_url") or existing.get("custom_url","")).strip(),
                "volume":          int(user_input.get("volume", existing.get("volume", 60))),
                "fade_in_seconds": int(user_input.get("fade_in_seconds",
                                                     existing.get("fade_in_seconds", 0))),
                "snooze_minutes":  int(user_input.get("snooze_minutes",
                                                     existing.get("snooze_minutes", 9))),
                "enabled":         bool(user_input.get("enabled", True)),
            }
            if patch["repeat"] == REPEAT_ONE_OFF and not patch["date"]:
                # A one-off alarm needs a concrete date. Resolve an omitted
                # date to the next occurrence of the selected wall-clock time
                # so an enabled alarm can never be saved without a timer.
                try:
                    hh, mm = map(int, str(patch["time"]).split(":")[:2])
                    now = dt_util.now()
                    fire = now.replace(
                        hour=hh, minute=mm, second=0, microsecond=0
                    )
                    if fire <= now:
                        from datetime import timedelta

                        fire += timedelta(days=1)
                    patch["date"] = fire.date().isoformat()
                except (TypeError, ValueError):
                    pass
            # If a preset selected via the dropdown, override preset_url
            preset_choice = (user_input.get("preset_choice") or "").strip()
            if preset_choice and patch["source"] == SOURCE_PRESET:
                patch["preset_url"] = preset_choice

            saved_alarm_id: str
            if editing:
                await mgr.async_update_alarm(existing["id"], patch)
                saved_alarm_id = existing["id"]
                _LOGGER.info("Updated alarm %s via UI", existing["id"])
            else:
                alarm = {**existing, **patch}
                alarm.pop("id", None)  # let manager assign new id
                saved_alarm_id = await mgr.async_add_alarm(alarm)
                _LOGGER.info("Created alarm %s via UI", saved_alarm_id)
            if user_input.get("_action") == "save_test":
                saved_alarm = mgr.get_alarm(saved_alarm_id)
                if saved_alarm:
                    await mgr._do_play(saved_alarm)  # noqa: SLF001
            return await self.async_step_alarms()

        # Render the form
        from .const import all_preset_options
        presets = all_preset_options()
        preset_choices = {"": "(use URL field below)"}
        for label, url in presets.items():
            preset_choices[url] = label

        # Match current preset_url to dropdown
        match_preset = ""
        cur_url = existing.get("preset_url", "")
        for url in presets.values():
            if url == cur_url:
                match_preset = url
                break

        # Build speaker picker options. Combines:
        #   - Lithe speakers (stored by IP, displayed by friendly name)
        #   - Google Cast groups (stored by entity_id, multi-room sync)
        bucket = self.hass.data.get(DOMAIN, {})
        speaker_options: list[dict[str, str]] = []
        # Lithe speakers
        for _entry_id, entry_data in bucket.items():
            if not isinstance(entry_data, dict):
                continue
            coord = entry_data.get("coordinator") or entry_data.get(DATA_COORDINATOR)
            if coord:
                if not caps(getattr(coord.client, "product", "")).get(
                    "scheduling", True
                ):
                    continue
                host = coord.client.host
                name = coord.client.state.name or host
                speaker_options.append({
                    "value": host,
                    "label": f"🔊 {name} ({host})",
                })
        # Google Cast groups (auto-discovered)
        try:
            from .group import discover_cast_groups
            for cg in discover_cast_groups(self.hass):
                speaker_options.append({
                    "value": cg["entity_id"],
                    "label": f"🎶 {cg['name']} (Cast group)",
                })
        except Exception as e:
            _LOGGER.debug("Cast group discovery failed: %s", e)

        # Current selection (existing.speakers may be IPs or entity_ids)
        speakers_default_list = existing.get("speakers") or [my_host]

        repeat_options = {
            REPEAT_ONE_OFF: "One-off (single date)",
            REPEAT_DAILY:   "Daily",
            REPEAT_WEEKLY:  "Weekly (specific days)",
            REPEAT_MONTHLY: "Monthly (day of month)",
        }
        source_options = {
            SOURCE_PRESET:    "Preset URL (Adhan / Quran)",
            SOURCE_FAVOURITE: "Saved Favourite (1-9)",
            SOURCE_CHIME:     "Embedded Chime",
            SOURCE_URL:       "Custom HTTP URL",
        }
        action_options = {
            "save":   "💾  Save",
            "save_test": "▶  Save and test now",
        }
        if editing:
            action_options["delete"] = "🗑  Delete this alarm"

        schema_dict: dict[Any, Any] = {
            vol.Required("name", default=existing.get("name", "New Alarm")): str,
            vol.Required("time", default=existing.get("time", "07:00")):
                selector.TimeSelector() if hasattr(selector, "TimeSelector") else str,
            vol.Required("enabled", default=bool(existing.get("enabled", True))): bool,
            vol.Required("repeat", default=existing.get("repeat", REPEAT_DAILY)):
                vol.In(repeat_options),
            vol.Optional("days", default=existing.get("days") or DAY_TOKENS):
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "mon", "label": "Monday"},
                            {"value": "tue", "label": "Tuesday"},
                            {"value": "wed", "label": "Wednesday"},
                            {"value": "thu", "label": "Thursday"},
                            {"value": "fri", "label": "Friday"},
                            {"value": "sat", "label": "Saturday"},
                            {"value": "sun", "label": "Sunday"},
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            vol.Optional("day_of_month", default=existing.get("day_of_month", 1)):
                vol.All(int, vol.Range(min=1, max=31)),
            vol.Optional("date", default=existing.get("date", "") or ""): str,
            vol.Required("speakers", default=speakers_default_list):
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=speaker_options,
                        multiple=True,
                        # DROPDOWN mode (not LIST) — required to allow
                        # custom_value entries, which lets users type in
                        # raw IPs or media_player entity IDs not yet
                        # auto-detected.
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ) if speaker_options else str,
            vol.Required("source", default=existing.get("source", SOURCE_PRESET)):
                vol.In(source_options),
            vol.Optional("preset_choice", default=match_preset):
                vol.In(preset_choices),
            vol.Optional("preset_url", default=existing.get("preset_url", "")): str,
            vol.Optional("favourite_slot", default=existing.get("favourite_slot", 1)):
                vol.All(int, vol.Range(min=1, max=9)),
            vol.Optional("chime_slot", default=existing.get("chime_slot", 1)):
                vol.All(int, vol.Range(min=1, max=MAX_CHIME_SLOT)),
            vol.Optional("custom_url", default=existing.get("custom_url", "")): str,
            vol.Required("volume", default=existing.get("volume", 60)):
                vol.All(int, vol.Range(min=0, max=100)),
            vol.Optional("fade_in_seconds", default=existing.get("fade_in_seconds", 0)):
                vol.All(int, vol.Range(min=0, max=600)),
            vol.Optional("snooze_minutes", default=existing.get("snooze_minutes", 9)):
                vol.All(int, vol.Range(min=1, max=60)),
            vol.Required("_action", default="save"): vol.In(action_options),
        }
        title_word = "Edit Alarm" if editing else "New Alarm"
        return self.async_show_form(
            step_id="alarm_edit",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "title": title_word,
                "name":  existing.get("name", "New Alarm"),
            },
        )

    # ── Prayer Schedule — UNIFIED single-screen UI ────────────────────
    #
    # Replaces the 4 old steps (general/entries/test/view/disable) with
    # one form that shows everything at once. Actions are dispatched via
    # a single "_action" dropdown at the bottom.
    async def async_step_prayer(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Single screen for Prayer Schedule: settings + per-prayer + actions."""
        opts = self._draft.get("prayer", {})
        presets = all_preset_options()
        preset_value_to_label = {"": "— Custom (type URL below) —"}
        for label, url in presets.items():
            preset_value_to_label[url] = label

        host = self._entry.data.get("host", "")

        # Handle save / action dispatch
        if user_input is not None:
            action = user_input.get("_action", "save")

            # This action is deliberately read-only: show the options that
            # were last saved and the timers currently registered from them.
            if action == "view":
                return await self.async_step_prayer_view()

            # Parse form values
            chosen_preset = (user_input.get("preset") or "").strip()
            url_field = (user_input.get("default_url") or "").strip()
            if chosen_preset:
                resolved_url = chosen_preset
            elif url_field:
                resolved_url = url_field
            else:
                resolved_url = _DEFAULT_ADHAN_URL

            enabled_prayers = user_input.get("enabled_prayers", []) or []

            # Preserve per-prayer overrides from existing config; new
            # prayers default to the schedule defaults.
            existing_entries = opts.get("entries", {}) or {}
            new_entries: dict[str, dict[str, Any]] = {}
            for p in PRAYER_NAMES_LIST:
                if p in enabled_prayers:
                    if p in existing_entries:
                        # Keep existing override config — only the toggle
                        # was relevant on this screen.
                        new_entries[p] = existing_entries[p]
                    else:
                        # Create new entry inheriting current defaults
                        new_entries[p] = {
                            "url":    resolved_url,
                            "volume": int(user_input.get("default_volume", _DEFAULT_VOLUME)),
                            "days":   "daily",
                        }

            self._draft["prayer"] = {
                **opts,
                "enabled":        bool(user_input.get("scheduler_enabled", True)),
                "city":           user_input.get("city", "London").strip(),
                "country":        user_input.get("country", "GB").strip(),
                "method":         int(user_input.get("method", 2)),
                "default_volume": int(user_input.get("default_volume", _DEFAULT_VOLUME)),
                "default_url":    resolved_url,
                "entries":        new_entries,
            }

            if action == "test":
                # Play the chosen Adhan now on this speaker. Run it
                # synchronously so it kicks off BEFORE we save options
                # (saving triggers an integration reload that would kill
                # a pending async_create_task).
                try:
                    await self.hass.services.async_call(
                        DOMAIN, "tannoy",
                        {
                            "message": resolved_url,
                            "mode":     "start",
                            "volume":   int(user_input.get("default_volume", _DEFAULT_VOLUME)),
                            "speakers": [host],
                        },
                        blocking=True,
                    )
                    _LOGGER.info("Prayer: tested %s on %s", resolved_url, host)
                except Exception as e:
                    _LOGGER.error("Test failed: %s", e)

                # Don't save+reload — just re-render the same form so the
                # user can hear the test and then pick another action.
                # We `user_input = None` to fall through to render path.
                user_input = None

            elif action == "advanced":
                # Drill into per-prayer overrides wizard
                self._wizard_index = 0
                self._wizard_entries = dict(new_entries)
                return await self._show_prayer_step(None)

            elif action == "save":
                # Save and exit
                return self.async_create_entry(title="", data=self._draft)
            else:
                # Unknown action — re-render the form
                user_input = None

        # ── Render the form ────────────────────────────────────────────

        current_url = opts.get("default_url", _DEFAULT_ADHAN_URL)
        matching_preset = ""
        for url in presets.values():
            if url == current_url:
                matching_preset = url
                break

        existing_entries = opts.get("entries", {}) or {}
        enabled_now = [p for p in PRAYER_NAMES_LIST if p in existing_entries]

        # Build today's prayer-time summary text shown above the form
        from .const import DATA_PRAYER_STATE
        prayer_state = self.hass.data.get(DOMAIN, {}).get(
            DATA_PRAYER_STATE, {}
        ) or {}
        entry_state = (
            prayer_state.get("schedules", {}).get(self._entry.entry_id, {})
            if isinstance(prayer_state, dict)
            else {}
        )
        times: dict[str, str] = (
            entry_state.get("times") or prayer_state.get("times", {}) or {}
        )
        registered_count = len(entry_state.get("registered", []))
        if times:
            parts = []
            for p in PRAYER_NAMES_LIST:
                t = times.get(p, "—")
                marker = "✅" if p in existing_entries else "⚪"
                parts.append(f"{marker} {p.capitalize()} {t}")
            schedule_summary = (
                "Today: " + " · ".join(parts)
                + f". Live timers: {registered_count}/{len(existing_entries)}."
            )
        else:
            schedule_summary = "Save once to fetch today's times for your city."

        schema = vol.Schema({
            vol.Required("scheduler_enabled",
                        default=bool(opts.get("enabled", True))): bool,
            vol.Required("city",    default=opts.get("city", "London")): str,
            vol.Required("country", default=opts.get("country", "GB")): str,
            vol.Required("method",  default=opts.get("method", 2)): vol.In(CALC_METHODS),
            vol.Required("default_volume",
                        default=opts.get("default_volume", _DEFAULT_VOLUME)):
                vol.All(int, vol.Range(min=0, max=100)),
            vol.Optional("preset", default=matching_preset):
                vol.In(preset_value_to_label),
            vol.Optional("default_url", default=current_url): str,
            vol.Optional("enabled_prayers", default=enabled_now):
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "fajr",    "label": "Fajr (Pre-dawn)"},
                            {"value": "sunrise", "label": "Sunrise (Shuruq) — optional"},
                            {"value": "dhuhr",   "label": "Dhuhr (Midday)"},
                            {"value": "asr",     "label": "Asr (Afternoon)"},
                            {"value": "sunset",  "label": "Sunset — optional"},
                            {"value": "maghrib", "label": "Maghrib"},
                            {"value": "isha",    "label": "Isha (Night)"},
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            vol.Required("_action", default="save"): vol.In({
                "save":     "💾  Save",
                "test":     "▶️  Save + Test Adhan now",
                "advanced": "⚙️  Save + Per-prayer overrides…",
                "view":     "📋  View saved schedule",
            }),
        })

        return self.async_show_form(
            step_id="prayer",
            data_schema=schema,
            description_placeholders={"summary": schedule_summary},
        )

    # ── Step 2: General (city/country/method/volume/enable) ────────────
    async def async_step_prayer_general(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        opts = self._draft.get("prayer", {})
        presets = all_preset_options()
        preset_choices = {"Custom URL (use field below)": ""} | presets

        if user_input is not None:
            chosen_preset = (user_input.get("preset") or "").strip()
            url_field = (user_input.get("default_url") or "").strip()
            # Preset wins over URL field
            if chosen_preset:
                url = chosen_preset
            elif url_field:
                url = url_field
            else:
                url = _DEFAULT_ADHAN_URL
            _LOGGER.info(
                "Prayer general saving: preset=%r url_field=%r → resolved default_url=%r",
                chosen_preset, url_field, url,
            )
            self._draft["prayer"] = {
                **opts,
                "enabled":  True,
                "city":     user_input["city"].strip(),
                "country":  user_input["country"].strip(),
                "method":   int(user_input["method"]),
                "default_volume": int(user_input["default_volume"]),
                "default_url":    url,
            }
            return await self.async_step_prayer_entries()

        # Reverse-lookup current default_url against presets to set initial value
        current_url = opts.get("default_url", _DEFAULT_ADHAN_URL)
        matching_label = ""
        for _label, url in presets.items():
            if url == current_url:
                matching_label = url
                break

        schema = vol.Schema({
            vol.Required("city",    default=opts.get("city", "London")): str,
            vol.Required("country", default=opts.get("country", "GB")): str,
            vol.Required("method",  default=opts.get("method", 2)): vol.In(CALC_METHODS),
            vol.Required("default_volume", default=opts.get("default_volume", _DEFAULT_VOLUME)):
                vol.All(int, vol.Range(min=0, max=100)),
            vol.Optional("preset", default=matching_label): vol.In({v: k for k, v in preset_choices.items()}),
            vol.Required("default_url", default=current_url): str,
        })
        return self.async_show_form(step_id="prayer_general", data_schema=schema)

    # ── Step 3: Per-prayer entries (wizard, one step per prayer) ───────
    async def async_step_prayer_entries(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Entry point — kick off the per-prayer wizard at the first prayer."""
        self._wizard_index = 0
        # Stash a working copy of entries to be mutated as we step through
        opts = self._draft.get("prayer", {})
        self._wizard_entries = dict(opts.get("entries", {}) or {})
        return await self._show_prayer_step(None)

    async def async_step_prayer_one(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Render one prayer's form; advance to next on submit."""
        return await self._show_prayer_step(user_input)

    async def _show_prayer_step(
        self, user_input: dict[str, Any] | None
    ) -> FlowResult:
        opts = self._draft.get("prayer", {})
        presets = all_preset_options()
        preset_value_to_label = {"": "Custom (use URL field)"}
        for label, url in presets.items():
            preset_value_to_label[url] = label

        default_url    = opts.get("default_url", _DEFAULT_ADHAN_URL)
        default_volume = opts.get("default_volume", _DEFAULT_VOLUME)

        # Save submitted values from the previous prayer (if any)
        if user_input is not None and self._wizard_index < len(PRAYER_NAMES_LIST):
            prayer = PRAYER_NAMES_LIST[self._wizard_index]
            enabled = user_input.get("enabled", False)
            if enabled:
                preset_url = (user_input.get("preset") or "").strip()
                url_field = (user_input.get("url") or "").strip()
                # Preset takes priority. If user picked a preset, USE IT and
                # ignore the URL field (HA UI can't dynamically clear the
                # field when the dropdown changes, so we enforce it here).
                if preset_url:
                    url = preset_url
                elif url_field:
                    url = url_field
                else:
                    url = default_url
                _LOGGER.info(
                    "Prayer wizard saving %s: preset=%r url_field=%r → resolved=%r",
                    prayer, preset_url, url_field, url,
                )
                self._wizard_entries[prayer] = {
                    "url":    url,
                    "volume": int(user_input.get("volume", default_volume)),
                    "days":   user_input.get("days", "daily"),
                }
            else:
                self._wizard_entries.pop(prayer, None)
            self._wizard_index += 1

        # Done? Save and return to menu
        if self._wizard_index >= len(PRAYER_NAMES_LIST):
            self._draft["prayer"] = {**opts, "entries": self._wizard_entries}
            return self.async_create_entry(title="", data=self._draft)

        # Render the next prayer
        prayer = PRAYER_NAMES_LIST[self._wizard_index]
        existing = self._wizard_entries.get(prayer, {})
        current_url = existing.get("url", default_url)
        matching_preset_value = ""
        for url in presets.values():
            if url == current_url:
                matching_preset_value = url
                break

        schema = vol.Schema({
            vol.Optional("enabled", default=bool(existing)): bool,
            vol.Optional("preset",  default=matching_preset_value):
                vol.In(preset_value_to_label),
            vol.Optional("url",     default=current_url): str,
            vol.Required("volume",  default=existing.get("volume", default_volume)):
                vol.All(int, vol.Range(min=0, max=100)),
            vol.Required("days",    default=existing.get("days", "daily")):
                vol.In(DAYS_OPTIONS),
        })

        # Pretty names for the step title placeholder
        pretty = {
            "fajr":    "Fajr (Pre-dawn)",
            "sunrise": "Sunrise (Shuruq)",
            "dhuhr":   "Dhuhr (Midday)",
            "asr":     "Asr (Afternoon)",
            "sunset":  "Sunset (Maghrib precursor)",
            "maghrib": "Maghrib (Sunset prayer)",
            "isha":    "Isha (Night)",
        }
        return self.async_show_form(
            step_id="prayer_one",
            data_schema=schema,
            description_placeholders={
                "prayer_name": pretty.get(prayer, prayer.capitalize()),
                "step_num":    str(self._wizard_index + 1),
                "step_total":  str(len(PRAYER_NAMES_LIST)),
            },
        )

    # ── Step 3b: Test play (immediately play a URL on THIS speaker) ─────
    async def async_step_prayer_test(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Play a chosen URL on this speaker right now via the tannoy flow.

        Lets the user verify their chosen Adhan / Quran URL actually plays
        before scheduling it. Uses the same notify.lithe_tannoy path that
        the scheduler uses at prayer time, so a working test means a
        working schedule.
        """
        opts = self._draft.get("prayer", {})
        presets = all_preset_options()
        # Reverse map: friendly label → URL. Add "Custom" sentinel.
        preset_label_to_url: dict[str, str] = {"Custom URL (use field below)": ""}
        for label, url in presets.items():
            preset_label_to_url[label] = url
        # voluptuous needs {value: label} for the dropdown to display labels
        preset_value_to_label = {v: k for k, v in preset_label_to_url.items()}

        errors: dict[str, str] = {}

        if user_input is not None:
            # Choose URL: preset wins if set, otherwise custom URL field
            preset_url = (user_input.get("preset") or "").strip()
            custom_url = (user_input.get("url") or "").strip()
            url = preset_url or custom_url or opts.get("default_url", _DEFAULT_ADHAN_URL)
            if not url:
                errors["base"] = "missing_url"
            else:
                volume = int(user_input.get("volume",
                                            opts.get("default_volume", _DEFAULT_VOLUME)))
                host = self._entry.data.get("host")
                # Await the same tannoy path used by the live schedule.
                try:
                    await self.hass.services.async_call(
                        DOMAIN, "tannoy",
                        {
                            "message": url,
                            "mode":     "start",
                            "volume":   volume,
                            "speakers": [host],
                        },
                        blocking=True,
                    )
                    _LOGGER.info(
                        "Prayer test: playing %s on %s at volume %d", url, host, volume,
                    )
                except Exception as e:
                    _LOGGER.error("Prayer test failed: %s", e)
                    errors["base"] = "test_failed"

                if not errors:
                    # Return to menu so the user can pick another action
                    return await self.async_step_init()

        # Default URL/preset based on what's saved
        default_url = opts.get("default_url", _DEFAULT_ADHAN_URL)
        matching_preset_value = ""
        for url in presets.values():
            if url == default_url:
                matching_preset_value = url
                break

        schema = vol.Schema({
            vol.Optional("preset", default=matching_preset_value):
                vol.In(preset_value_to_label),
            vol.Optional("url", default=default_url): str,
            vol.Required("volume", default=opts.get("default_volume", _DEFAULT_VOLUME)):
                vol.All(int, vol.Range(min=0, max=100)),
        })
        return self.async_show_form(
            step_id="prayer_test",
            data_schema=schema,
            errors=errors,
        )

    # ── Step 3c: View today's schedule (read-only summary) ──────────────
    async def async_step_prayer_view(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show today's resolved prayer times + scheduled actions."""
        if user_input is not None:
            action = user_input.get("_action", "edit")
            if action == "delete":
                self._draft.pop("prayer", None)
                return self.async_create_entry(title="", data=self._draft)
            return await self.async_step_prayer()

        # Pull current schedule state from the global prayer data store
        from .const import DATA_PRAYER_STATE
        prayer_state = self.hass.data.get(DOMAIN, {}).get(
            DATA_PRAYER_STATE, {}
        ) or {}
        entry_state = (
            prayer_state.get("schedules", {}).get(self._entry.entry_id, {})
            if isinstance(prayer_state, dict)
            else {}
        )
        times: dict[str, str] = (
            entry_state.get("times") or prayer_state.get("times", {}) or {}
        )
        host = self._entry.data.get("host")

        opts = self._draft.get("prayer", {})
        entries = opts.get("entries", {}) or {}
        city = opts.get("city", "—")
        country = opts.get("country", "—")
        method = opts.get("method", "—")
        method_label = CALC_METHODS.get(int(method), str(method)) if isinstance(method, int) else str(method)
        enabled = opts.get("enabled", False)

        live_registered = {
            item.get("prayer"): item
            for item in entry_state.get("registered", [])
            if isinstance(item, dict)
        }
        preset_labels = {
            url: label for label, url in all_preset_options().items()
        }

        # Build a human-readable summary
        lines = [
            f"**Speaker:** {host}",
            f"**Status:** {'✅ Enabled' if enabled else '⚪ Disabled'}",
            f"**Live scheduler:** {entry_state.get('status', 'not loaded')}",
            f"**Location:** {city}, {country}",
            f"**Calculation:** {method_label}",
            "",
            "**Saved and scheduled:**",
        ]
        if entries:
            for p, e in entries.items():
                t = times.get(p, "unresolved")
                days = e.get("days", "daily")
                vol_v = e.get("volume", opts.get("default_volume", "?"))
                url = e.get("url") or opts.get("default_url", "")
                audio = preset_labels.get(url, url or "no audio URL")
                timer = "timer registered" if p in live_registered else "timer not registered"
                lines.append(
                    f"  ✅ **{p.capitalize()}** at {t} — {days}, "
                    f"volume {vol_v}, {audio} ({timer})"
                )
        else:
            lines.append("  _No prayers have been saved._")

        lines.extend([
            "",
            f"**Registered timers:** {len(live_registered)} of {len(entries)}",
        ])

        description = "\n".join(lines)

        schema = vol.Schema({
            vol.Required("_action", default="edit"): vol.In({
                "edit": "Edit this schedule",
                "delete": "Delete this schedule",
            }),
        })
        return self.async_show_form(
            step_id="prayer_view",
            data_schema=schema,
            description_placeholders={"summary": description},
        )

    # ── Step 3d: Toggle debug logging ──────────────────────────────────
    async def async_step_logging(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Toggle debug logging for the lithe_audio integration.

        Saves the chosen level to logger.async_set_level() so the user
        doesn't have to edit configuration.yaml or restart HA.
        """
        if user_input is not None:
            level = user_input.get("level", "info")
            try:
                # HA service: logger.set_level — sets per-logger level live
                await self.hass.services.async_call(
                    "logger", "set_level",
                    {"custom_components.lithe_audio": level},
                    blocking=True,
                )
                _LOGGER.info("Lithe Audio log level set to %s", level)
            except Exception as e:
                _LOGGER.error("Failed to set log level: %s", e)
            return await self.async_step_init()

        schema = vol.Schema({
            vol.Required("level", default="info"): vol.In({
                "debug":    "Debug   — verbose (for chime/protocol issues)",
                "info":     "Info    — normal (recommended)",
                "warning":  "Warning — quiet",
                "error":    "Error   — silent unless something breaks",
            }),
        })
        return self.async_show_form(step_id="logging", data_schema=schema)

    # ── Step 4: Disable prayer scheduler ───────────────────────────────
    async def async_step_prayer_disable(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        opts = self._draft.get("prayer", {})
        opts["enabled"] = False
        self._draft["prayer"] = opts
        return self.async_create_entry(title="", data=self._draft)
