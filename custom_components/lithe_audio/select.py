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
    DSP_EQ, DSP_HIGHPASS, DSP_OUTPUT, DSP_TUNING,
    EQ_PRESETS, HP_OPTIONS, OUT_OPTIONS, caps,
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
    if c["highpass_select"]:
        entities.append(LitheHighPassSelect(coordinator, entry))
    if c["tuning_select"]:
        entities.append(LitheTuningSelect(coordinator, entry))

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

    @property
    def current_option(self) -> str:
        # Use local _current — TX is byte-verified identical to the
        # Lithe app's wire packets (sniffer 2026-05-18). The local
        # value is authoritative since we just sent it.
        return self._current

    async def async_select_option(self, option: str) -> None:
        idx = EQ_PRESETS.index(option) if option in EQ_PRESETS else 0
        self._current = option
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

    @property
    def current_option(self) -> str:
        # Use local _current — TX is byte-verified identical to the app.
        return self._current

    async def async_select_option(self, option: str) -> None:
        idx = OUT_OPTIONS.index(option) if option in OUT_OPTIONS else 0
        self._current = option
        await self._client.async_dsp_command(DSP_OUTPUT, idx)
        self.async_write_ha_state()


class LitheHighPassSelect(_LitheBaseSelect):
    """High Pass Filter selector — PRO 2 only."""

    _attr_name = "Audio — High Pass Filter"
    _attr_options = HP_OPTIONS
    _attr_icon = "mdi:filter"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_highpass"
        self._current = "OFF"

    @property
    def current_option(self) -> str:
        # Use local _current — speaker does not push MB#112 confirmation
        # for sub-MB 0x1A, so reading state.dsp_highpass would be stale.
        return self._current

    async def async_select_option(self, option: str) -> None:
        # Sniffed values: 0=OFF, 1=60Hz, 2=80Hz, 3=100Hz, 4=120Hz
        # HP_OPTIONS order matches exactly so we use index directly.
        idx = HP_OPTIONS.index(option) if option in HP_OPTIONS else 0
        self._current = option
        await self._client.async_dsp_command(DSP_HIGHPASS, idx)
        self.async_write_ha_state()


class LitheTuningSelect(_LitheBaseSelect):
    """Speaker Tuning selector — PRO 2 only."""

    _attr_name = "Audio — Speaker Tuning"
    _attr_options = ["Enclosure 13L", "Open Back"]
    _attr_icon = "mdi:tune"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_tuning"
        self._current = "Enclosure 13L"

    @property
    def current_option(self) -> str:
        # Use local _current — speaker does not push MB#112 confirmation
        # for sub-MB 0x1D, so reading state.dsp_tuning would be stale.
        return self._current

    async def async_select_option(self, option: str) -> None:
        idx = 0 if option == "Enclosure 13L" else 1
        self._current = option
        await self._client.async_dsp_command(DSP_TUNING, idx)
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
