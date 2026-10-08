"""Number entities for Lithe Audio DSP controls."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_PRODUCT, DATA_COORDINATOR, DOMAIN,
    DSP_BALANCE, DSP_BASS_FIELD, DSP_EQ_BANDS, DSP_MID_FIELD,
    DSP_LOUDNESS_GAIN, DSP_TREBLE_FIELD, caps, loudness_gain_to_wire,
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
    if c["eq_select"]:
        entities.extend((
            LitheEqBandNumber(coordinator, entry, "Bass", "dsp_bass", DSP_BASS_FIELD),
            LitheEqBandNumber(coordinator, entry, "Mid", "dsp_mid", DSP_MID_FIELD),
            LitheEqBandNumber(coordinator, entry, "Treble", "dsp_treble", DSP_TREBLE_FIELD),
        ))
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


class LitheEqBandNumber(_LitheBaseNumber, RestoreEntity):
    """One of the three signed EQ bands carried by DSP sub-MB 0x09."""

    _attr_native_min_value = -5
    _attr_native_max_value = 5
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "dB"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:equalizer"

    def __init__(self, coordinator, entry, name: str, state_attr: str, field: int):
        super().__init__(coordinator, entry)
        self._attr_name = f"Audio — {name}"
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_{name.lower()}"
        self._state_attr = state_attr
        self._field = field
        self._value = 0
        self._optimistic_until: float = 0.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                self._value = max(-5, min(5, int(float(last_state.state))))
            except (TypeError, ValueError):
                pass

    @property
    def native_value(self) -> float:
        import time
        if time.monotonic() < self._optimistic_until:
            return float(self._value)
        val = getattr(self._client.state, self._state_attr, None)
        if val is not None:
            return float(val)
        return float(self._value)

    async def async_set_native_value(self, value: float) -> None:
        import time
        self._value = int(value)
        self._optimistic_until = time.monotonic() + 5.0
        await self._client.async_dsp_command(DSP_EQ_BANDS, self._value, self._field)
        self.async_write_ha_state()


class LitheBalanceNumber(_LitheBaseNumber, RestoreEntity):
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
        self._optimistic_until: float = 0.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                self._value = max(-6, min(6, int(float(last_state.state))))
            except (TypeError, ValueError):
                pass

    @property
    def native_value(self) -> float:
        import time
        if time.monotonic() >= self._optimistic_until:
            value = getattr(self._client.state, "dsp_balance", None)
            if value is not None:
                return float(value)
        return float(self._value)

    async def async_set_native_value(self, value: float) -> None:
        import time
        self._value = int(value)
        self._optimistic_until = time.monotonic() + 5.0
        await self._client.async_dsp_command(DSP_BALANCE, self._value)
        self.async_write_ha_state()


class LitheLoudnessNumber(_LitheBaseNumber, RestoreEntity):
    """PRO 2 loudness gain, enabled by the separate loudness switch."""

    _attr_name = "Audio — Loudness Gain"
    _attr_native_min_value = -10
    _attr_native_max_value = 10
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "dB"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:equalizer"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        # Preserve the original unique ID so an existing disabled loudness
        # slider is revived rather than duplicated in the entity registry.
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_loudness"
        self._value = 0
        self._optimistic_until: float = 0.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None:
            return
        try:
            self._value = max(-10, min(10, int(float(last_state.state))))
        except (TypeError, ValueError):
            return

        # UI fallback only. A genuine MB#112 broadcast remains authoritative
        # and must not be overwritten by restored HA state.

    @property
    def available(self) -> bool:
        enabled = self._client.state.dsp_loudness
        # The firmware does not provide a safe initial DSP settings dump.
        # Keep the gain usable while state is unknown; once a 0/1 push or a
        # local switch action is seen, strictly follow the loudness switch.
        return super().available and (enabled is None or enabled != 0)

    @property
    def native_value(self) -> float:
        import time
        if time.monotonic() < self._optimistic_until:
            return float(self._value)
        value = getattr(self._client.state, "dsp_loudness_gain", None)
        if value is not None:
            return float(value)
        return float(self._value)

    async def async_set_native_value(self, value: float) -> None:
        import time
        self._value = max(-10, min(10, int(value)))
        self._optimistic_until = time.monotonic() + 5.0
        # The MCU SetLoudness routine accepts an unsigned 0..20 table index.
        # Home Assistant presents the friendlier -10..+10 dB scale.
        wire_value = loudness_gain_to_wire(self._value)
        await self._client.async_dsp_command(DSP_LOUDNESS_GAIN, wire_value)
        self._client.state.dsp_loudness_gain = self._value
        self.coordinator.async_set_updated_data(self._client.state)
        self.async_write_ha_state()
