"""Select entities for Lithe Audio DSP selectors."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_PRODUCT, DATA_COORDINATOR, DOMAIN,
    DSP_EQ, DSP_OUTPUT, EQ_PRESETS, OUT_OPTIONS, caps,
)
from .coordinator import LitheAudioCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LitheAudioCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    product = entry.data[CONF_PRODUCT]
    c = caps(product)

    entities: list[SelectEntity] = []
    if c["eq_select"]:
        entities.append(LitheEqSelect(coordinator, entry))
    if c["output_select"]:
        entities.append(LitheOutputSelect(coordinator, entry))

    # Cast Group selector — every speaker gets one. The dropdown lists
    # Cast groups discovered live from HA's Cast integration. Picking
    # one routes subsequent media playback through the Cast group's
    # media_player entity (Google's multi-room sync).
    entities.append(LitheCastGroupSelect(coordinator, entry))

    if entities:
        async_add_entities(entities)


class _LitheBaseSelect(CoordinatorEntity[LitheAudioCoordinator], SelectEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = coordinator.client
        self._current: str = self._attr_options[0] if hasattr(self, '_attr_options') else ""

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


class LitheEqSelect(_LitheBaseSelect):
    """EQ Preset selector."""

    _attr_name = "Audio — EQ Preset"
    _attr_options = EQ_PRESETS
    _attr_icon = "mdi:equalizer"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_eq"
        self._current = "Normal"
        self._optimistic_until: float = 0.0

    @property
    def current_option(self) -> str:
        import time
        if time.monotonic() >= self._optimistic_until:
            value = getattr(self._client.state, "dsp_eq", None)
            if isinstance(value, int) and 0 <= value < len(EQ_PRESETS):
                return EQ_PRESETS[value]
        return self._current

    async def async_select_option(self, option: str) -> None:
        import time
        idx = EQ_PRESETS.index(option) if option in EQ_PRESETS else 0
        self._current = option
        self._optimistic_until = time.monotonic() + 5.0
        await self._client.async_dsp_command(DSP_EQ, idx)
        self.async_write_ha_state()


class LitheOutputSelect(_LitheBaseSelect):
    """Speaker Output selector."""

    _attr_name = "Audio — Speaker Output"
    _attr_options = OUT_OPTIONS
    _attr_icon = "mdi:speaker"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_output"
        self._current = "Stereo"
        self._optimistic_until: float = 0.0

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
    _attr_name = "Audio — Cast Group"
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
