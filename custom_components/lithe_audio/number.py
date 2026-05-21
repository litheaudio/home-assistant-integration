"""Number entities for Lithe Audio DSP controls."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_PRODUCT, DATA_COORDINATOR, DOMAIN, DSP_BALANCE, DSP_LOUDNESS, caps,
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

    entities: list[NumberEntity] = []
    if c["balance_number"]:
        entities.append(LitheBalanceNumber(coordinator, entry))
    if c["loudness_number"]:
        entities.append(LitheLoudnessNumber(coordinator, entry))

    if entities:
        async_add_entities(entities)


class _LitheBaseNumber(CoordinatorEntity[LitheAudioCoordinator], NumberEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = coordinator.client

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    @property
    def available(self) -> bool:
        return self._client.state.connected

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class LitheLoudnessNumber(_LitheBaseNumber):
    """Loudness slider for WiFi PRO 2: -10 to +10 dB."""

    _attr_name = "Audio — Loudness"
    _attr_native_min_value = -10
    _attr_native_max_value = 10
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "dB"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:equalizer"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_loudness"
        self._value = 0
        self._optimistic_until: float = 0.0

    @property
    def native_value(self) -> float:
        """Loudness slider value.

        2-way sync (factually verified 2026-05-18):
          - HA → speaker: TX sub-MB 0x16, signed -10..+10
          - App → HA: speaker broadcasts as sub-MB 0x34, wire byte 0..20
            which the parser decodes to -10..+10 and stores in
            state.dsp_loudness.

        UI strategy: prefer speaker state, but use local _value during
        a 5-second optimistic window after a HA toggle to avoid
        flipback if the speaker hasn't echoed yet.
        """
        import time
        if time.monotonic() < self._optimistic_until:
            return float(self._value)
        val = getattr(self._client.state, "dsp_loudness", None)
        if val is not None:
            return float(val)
        return float(self._value)

    async def async_set_native_value(self, value: float) -> None:
        import time
        self._value = int(value)
        self._optimistic_until = time.monotonic() + 5.0
        await self._client.async_dsp_command(DSP_LOUDNESS, self._value)
        self.async_write_ha_state()


class LitheBalanceNumber(_LitheBaseNumber):
    """Balance slider: -6 (full left) to +6 (full right)."""

    _attr_name = "Audio — Balance"
    _attr_native_min_value = -6
    _attr_native_max_value = 6
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:pan-horizontal"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_balance"
        self._value = 0

    @property
    def native_value(self) -> float:
        # Use local _value — speaker does not push MB#112 confirmation
        # for sub-MB 0x1E, so reading state.dsp_balance would be stale.
        return float(self._value)

    async def async_set_native_value(self, value: float) -> None:
        self._value = int(value)
        await self._client.async_dsp_command(DSP_BALANCE, self._value)
        self.async_write_ha_state()
