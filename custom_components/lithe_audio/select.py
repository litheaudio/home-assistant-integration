"""Select entities for Lithe Audio DSP selectors."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_PRODUCT,
    DATA_COORDINATOR,
    DOMAIN,
    DSP_EQ,
    DSP_HIGHPASS,
    DSP_OUTPUT,
    DSP_TUNING,
    EQ_PRESETS,
    HP_OPTIONS,
    IO1_EQ_PRESETS,
    OUT_OPTIONS,
    PRODUCT_IO1,
    TUNING_OPTIONS,
    audio_control_name,
    caps,
)
from .coordinator import LitheAudioCoordinator
from .local_favs import MAX_SLOTS, get_local_favs


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LitheAudioCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]
    product = entry.data[CONF_PRODUCT]
    c = caps(product)

    entities: list[SelectEntity] = []
    if c["eq_select"]:
        entities.append(LitheEqSelect(coordinator, entry))
    if c["output_select"]:
        entities.append(LitheOutputSelect(coordinator, entry))
    if c["highpass_select"]:
        entities.append(LitheHighPassSelect(coordinator, entry))
    if c["tuning_select"]:
        entities.append(LitheTuningSelect(coordinator, entry))

    # Only expose Cast routing when HA has a real Google Cast group to pick.
    # A selector containing only "None — local only" has no useful action.
    from .group import discover_cast_groups
    cast_groups = discover_cast_groups(hass)
    cast_group_unique_id = (
        f"{entry.data['host']}_{entry.entry_id}_cast_group"
    )
    if cast_groups:
        entities.append(LitheCastGroupSelect(coordinator, entry))
    else:
        # Remove the stale registry entry left by earlier releases so it does
        # not linger as an unavailable/confusing control on the device page.
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "select", DOMAIN, cast_group_unique_id
        )
        if entity_id:
            registry.async_remove(entity_id)
    entities.append(LitheFavouriteSaveSlotSelect(coordinator, entry))

    if entities:
        async_add_entities(entities)


class _LitheBaseSelect(CoordinatorEntity[LitheAudioCoordinator], SelectEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = coordinator.client
        options = getattr(self, "_attr_options", ())
        self._current: str = options[0] if options else ""

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    @property
    def available(self) -> bool:
        return self._client.state.connected

    @property
    def current_option(self) -> str:
        return self._current

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class LitheEqSelect(_LitheBaseSelect, RestoreEntity):
    """EQ Preset selector."""

    _attr_name = audio_control_name(1, "EQ Preset")
    _attr_options = EQ_PRESETS
    _attr_icon = "mdi:equalizer"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_options = (
            IO1_EQ_PRESETS
            if entry.data[CONF_PRODUCT] == PRODUCT_IO1
            else EQ_PRESETS
        )
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_eq"
        self._current = self._attr_options[0]
        self._optimistic_until: float = 0.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self._attr_options:
            self._current = last_state.state
            if self._client.state.dsp_eq is None:
                self._client.state.restore_dsp_snapshot({
                    "dsp_eq": self._attr_options.index(self._current),
                })

    @property
    def current_option(self) -> str:
        import time
        if time.monotonic() >= self._optimistic_until:
            value = getattr(self._client.state, "dsp_eq", None)
            if isinstance(value, int) and 0 <= value < len(self._attr_options):
                return self._attr_options[value]
        return self._current

    async def async_select_option(self, option: str) -> None:
        import time
        idx = self._attr_options.index(option) if option in self._attr_options else 0
        self._current = option
        self._optimistic_until = time.monotonic() + 5.0
        await self._client.async_dsp_command(DSP_EQ, idx)
        self.async_write_ha_state()


class LitheOutputSelect(_LitheBaseSelect, RestoreEntity):
    """Speaker Output selector."""

    _attr_name = audio_control_name(6, "Speaker Output")
    _attr_options = OUT_OPTIONS
    _attr_icon = "mdi:speaker"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_output"
        self._current = "Stereo"
        self._optimistic_until: float = 0.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in OUT_OPTIONS:
            self._current = last_state.state
            if self._client.state.dsp_output is None:
                self._client.state.restore_dsp_snapshot({
                    "dsp_output": OUT_OPTIONS.index(self._current),
                })

    @property
    def current_option(self) -> str:
        import time
        if time.monotonic() >= self._optimistic_until:
            value = getattr(self._client.state, "dsp_output", None)
            if isinstance(value, int) and 0 <= value < len(OUT_OPTIONS):
                return OUT_OPTIONS[value]
        return self._current

    async def async_select_option(self, option: str) -> None:
        import time
        idx = OUT_OPTIONS.index(option) if option in OUT_OPTIONS else 0
        self._current = option
        self._optimistic_until = time.monotonic() + 5.0
        await self._client.async_dsp_command(DSP_OUTPUT, idx)
        self.async_write_ha_state()


class LitheHighPassSelect(_LitheBaseSelect, RestoreEntity):
    """PRO 2 high-pass frequency selector (MCU HOSTMCUSETTINGS 0x32)."""

    _attr_name = audio_control_name(8, "High Pass Frequency")
    _attr_options = HP_OPTIONS
    _attr_icon = "mdi:filter"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_highpass"
        self._current = HP_OPTIONS[0]
        self._optimistic_until: float = 0.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state not in HP_OPTIONS:
            return

        # Keep the last speaker-confirmed HA value visible until a new MB#112
        # broadcast arrives. Never write restored UI state back to the speaker
        # during startup.
        self._current = last_state.state
        if self._client.state.dsp_highpass is None:
            self._client.state.restore_dsp_snapshot({
                "dsp_highpass": HP_OPTIONS.index(self._current),
            })

    @property
    def available(self) -> bool:
        # Frequency is meaningful only while open-back/high-pass protection
        # is enabled by the separate High Pass Filter switch.
        return (
            self._client.state.connected
            and self._client.state.dsp_tuning == 1
        )

    @property
    def current_option(self) -> str:
        import time
        if time.monotonic() >= self._optimistic_until:
            value = getattr(self._client.state, "dsp_highpass", None)
            if isinstance(value, int) and 0 <= value < len(HP_OPTIONS):
                return HP_OPTIONS[value]
        return self._current

    async def async_select_option(self, option: str) -> None:
        import time
        idx = HP_OPTIONS.index(option) if option in HP_OPTIONS else 0
        self._current = HP_OPTIONS[idx]
        self._optimistic_until = time.monotonic() + 5.0
        await self._client.async_dsp_command(DSP_HIGHPASS, idx)
        self._client.state.dsp_highpass = idx
        self.coordinator.async_set_updated_data(self._client.state)
        self.async_write_ha_state()


class LitheTuningSelect(_LitheBaseSelect):
    """PRO 2 enclosure/open-back protection selector (MCU 0x0D)."""

    _attr_name = "Audio — Speaker Tuning"
    _attr_options = TUNING_OPTIONS
    _attr_icon = "mdi:tune-variant"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_tuning"
        self._current = TUNING_OPTIONS[0]
        self._optimistic_until: float = 0.0

    @property
    def current_option(self) -> str:
        import time
        if time.monotonic() >= self._optimistic_until:
            value = getattr(self._client.state, "dsp_tuning", None)
            if isinstance(value, int) and 0 <= value < len(TUNING_OPTIONS):
                return TUNING_OPTIONS[value]
        return self._current

    async def async_select_option(self, option: str) -> None:
        import time
        idx = TUNING_OPTIONS.index(option) if option in TUNING_OPTIONS else 0
        self._current = TUNING_OPTIONS[idx]
        self._optimistic_until = time.monotonic() + 5.0
        await self._client.async_dsp_command(DSP_TUNING, idx)
        self.async_write_ha_state()


class LitheFavouriteSaveSlotSelect(_LitheBaseSelect):
    """Choose the explicit favourite slot used by Save Current Track."""

    _attr_name = "Favourites — Save Slot"
    _attr_icon = "mdi:playlist-edit"
    _attr_options: tuple[str, ...] = ()

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = (
            f"{entry.data['host']}_{entry.entry_id}_fav_save_slot"
        )
        if not hasattr(self._client, "_favourite_save_slot"):
            self._client._favourite_save_slot = 1
        self._favourite_manager = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        manager = get_local_favs(self.hass)
        if manager:
            self._favourite_manager = manager
            self.async_on_remove(manager.async_listen(self.async_write_ha_state))

    def _name_for_slot(self, slot: int) -> str:
        manager = self._favourite_manager
        local = manager.get(slot) if manager else None
        if local and local.get("name"):
            return str(local["name"])
        for favourite in self._client.state.favourites or []:
            if int(favourite.get("slot", 0) or 0) == slot:
                return str(favourite.get("name") or f"Favourite {slot}")
        return f"Favourite {slot}"

    @property
    def options(self) -> list[str]:
        return [
            f"{slot}: {self._name_for_slot(slot)}"
            for slot in range(1, MAX_SLOTS + 1)
        ]

    @property
    def current_option(self) -> str:
        slot = int(getattr(self._client, "_favourite_save_slot", 1))
        return f"{slot}: {self._name_for_slot(slot)}"

    async def async_select_option(self, option: str) -> None:
        try:
            slot = int(option.split(":", 1)[0])
        except (TypeError, ValueError):
            return
        self._client._favourite_save_slot = max(1, min(MAX_SLOTS, slot))
        self.coordinator.async_set_updated_data(self._client.state)
        self.async_write_ha_state()


class LitheCastGroupSelect(CoordinatorEntity[LitheAudioCoordinator], SelectEntity):
    """Cast Group selector — routes future playback through a Google Cast group.

    Lists Cast groups discovered from HA's Cast integration (configured in
    the Google Home app). Picking one means subsequent `play_media`,
    favourites, prayer audio, and TTS go through that Cast group's
    media_player entity — providing true multi-room sync via Google's
    cloud infrastructure.

    Picking "(None)" clears routing and restores direct local playback.
    """

    _attr_has_entity_name = True
    _attr_name = audio_control_name(10, "Cast Group")
    _attr_icon = "mdi:speaker-multiple"

    NONE_LABEL = "(None — local only)"

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = coordinator.client
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_cast_group"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    @property
    def available(self) -> bool:
        return self._client.state.connected

    def _discover(self) -> list[dict]:
        try:
            from .group import discover_cast_groups
            return discover_cast_groups(self.hass) or []
        except Exception:
            return []

    @property
    def options(self) -> list[str]:
        """Live list of Cast groups + a 'None' option to clear routing."""
        groups = self._discover()
        labels = [g["name"] for g in groups]
        return [self.NONE_LABEL] + sorted(labels)

    @property
    def current_option(self) -> str:
        """Current selection — the active Cast group name, or '(None)'."""
        current = getattr(self._client.state, "active_cast_group", "")
        return current if current else self.NONE_LABEL

    async def async_select_option(self, option: str) -> None:
        """Set or clear the Cast group routing for this speaker."""
        if option == self.NONE_LABEL:
            # Clear routing
            try:
                self._client.state.active_cast_group = ""
                self._client.state.active_cast_group_entity = ""
            except Exception:
                pass
            self.async_write_ha_state()
            return

        # Find the matching group's entity_id
        target_entity = None
        for cg in self._discover():
            if cg["name"] == option:
                target_entity = cg["entity_id"]
                break
        if not target_entity:
            return
        try:
            self._client.state.active_cast_group = option
            self._client.state.active_cast_group_entity = target_entity
        except Exception:
            pass
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
