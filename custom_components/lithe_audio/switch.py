"""Switch entities for Lithe Audio toggles."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BT_OFF, BT_ON, CONF_PRODUCT, DATA_COORDINATOR, DOMAIN,
    DSP_LOUDNESS, DSP_NIGHTMODE, caps,
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

    entities: list[SwitchEntity] = []
    if c["nightmode_switch"]:
        entities.append(LitheNightModeSwitch(coordinator, entry))
    if c["loudness_switch"]:
        entities.append(LitheLoudnessSwitch(coordinator, entry))
    if c["bluetooth_switch"]:
        entities.append(LitheBluetoothSwitch(coordinator, entry))
    # Do not expose AUX/SPDIF switches through MB#50. The working C4 driver
    # treats MB#50 as feedback and does not activate inputs with it.

    if entities:
        async_add_entities(entities)


class _LitheBaseSwitch(CoordinatorEntity[LitheAudioCoordinator], SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = coordinator.client
        self._state = False

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    @property
    def available(self) -> bool:
        return self._client.state.connected

    @property
    def is_on(self) -> bool:
        return self._state

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class LitheNightModeSwitch(_LitheBaseSwitch):
    """Night Mode switch.

    2-way sync (factually verified 2026-05-18):
      - HA → speaker: TX sub-MB 0x18, byte-identical to Lithe app
        (sniffer-confirmed). Speaker accepts but does NOT push
        confirmation back to our LUCI session.
      - App → speaker → HA: when the Lithe app changes Night Mode,
        the speaker broadcasts the change as legacy sub-MB 0x0C to
        all connected clients. Our parser maps 0x0C → dsp_nightmode.

    UI strategy: prefer speaker state (so app changes are reflected),
    but use local _state during a 5-second optimistic window after a
    user toggle (covers wire latency without flipping back).
    """

    _attr_name = "Audio — Night Mode"
    _attr_icon = "mdi:weather-night"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_nightmode"
        self._optimistic_until: float = 0.0

    @property
    def is_on(self) -> bool:
        import time
        # Inside optimistic window — trust the value the user just set.
        if time.monotonic() < self._optimistic_until:
            return self._state
        # After window — prefer speaker state if known (gets updated by
        # legacy 0x0C broadcasts when the app changes Night Mode).
        val = getattr(self._client.state, "dsp_nightmode", None)
        if val is not None:
            return val == 1
        return self._state

    async def async_turn_on(self, **kwargs) -> None:
        import time
        self._state = True
        self._optimistic_until = time.monotonic() + 5.0
        await self._client.async_dsp_command(DSP_NIGHTMODE, 1)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        import time
        self._state = False
        self._optimistic_until = time.monotonic() + 5.0
        await self._client.async_dsp_command(DSP_NIGHTMODE, 0)
        self.async_write_ha_state()


class LitheLoudnessSwitch(_LitheBaseSwitch):
    """Loudness ON/OFF switch (V3, iO1, V2, PRO)."""

    _attr_name = "Audio — Loudness"
    _attr_icon = "mdi:volume-plus"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_loudness_sw"

    @property
    def is_on(self) -> bool:
        val = self._client.state.dsp_loudness
        if val is not None:
            return val != 0
        return self._state

    async def async_turn_on(self, **kwargs) -> None:
        self._state = True
        await self._client.async_dsp_command(DSP_LOUDNESS, 1)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._state = False
        await self._client.async_dsp_command(DSP_LOUDNESS, 0)
        self.async_write_ha_state()


class LitheBluetoothSwitch(_LitheBaseSwitch):
    """Bluetooth enable/disable switch (all products).

    Sends LUCI MB#209 with payload "ON" or "OFF" per the spec
    (LibreSync LUCI Tech Note §10.21):

      "ON"  — Change source as bluetooth and turn ON bluetooth.
      "OFF" — Come out of bluetooth source and turn OFF bluetooth.

    State is read from the speaker rather than a local flag:
      - source_id == 19 (Bluetooth source) → switch ON
      - bt_status starts with "BT:READY" or contains "CONNECT" → ON
      - otherwise → OFF

    We keep a brief 3-second optimistic state after the user toggles so
    the UI is responsive even before the speaker pushes MB#210.
    """

    _attr_name = "Inputs — Bluetooth"
    _attr_icon = "mdi:bluetooth"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_bluetooth"
        # Optimistic-state expiry timestamp (event-loop time)
        self._optimistic_until: float = 0.0
        self._optimistic_state: bool = False

    @property
    def is_on(self) -> bool:
        """Derive state from speaker rather than a local flag.

        Bluetooth is ON when:
          - we're in the optimistic window after a user toggle, OR
          - the speaker's current source is Bluetooth (id 19), OR
          - bt_status reports a connection / ready state
        """
        import time
        if time.monotonic() < self._optimistic_until:
            return self._optimistic_state

        st = self._client.state
        # Active BT source means BT is on
        if st.source_id == 19:
            return True
        # bt_status reflects radio state
        bt = (st.bt_status or "").upper()
        if bt.startswith("BT:READY") or "CONNECT" in bt or bt == "ON":
            return True
        return False

    async def async_turn_on(self, **kwargs) -> None:
        import time
        # Set optimistic state for 3 seconds while we wait for the
        # speaker's MB#210 status push to confirm.
        self._optimistic_state = True
        self._optimistic_until = time.monotonic() + 3.0
        self.async_write_ha_state()

        await self._client.async_bluetooth(BT_ON)

        # Kick a refresh — request MB#210 status so we get the
        # confirmation push promptly rather than waiting for poll.
        try:
            from .const import MB_BT_STATUS
            await self._client._send(0x01, MB_BT_STATUS, "")  # noqa: SLF001
        except Exception:
            pass

    async def async_turn_off(self, **kwargs) -> None:
        import time
        self._optimistic_state = False
        self._optimistic_until = time.monotonic() + 3.0
        self.async_write_ha_state()

        await self._client.async_bluetooth(BT_OFF)

        try:
            from .const import MB_BT_STATUS
            await self._client._send(0x01, MB_BT_STATUS, "")  # noqa: SLF001
        except Exception:
            pass


class _LithePassthroughSwitch(_LitheBaseSwitch):
    """Base class for AUX In / SPDIF In passthrough switches.

    These switches let the user toggle whether the speaker is routing
    audio through its line input (AUX/SPDIF). Unlike Bluetooth, these
    aren't radios — they're physical inputs always carrying signal —
    so "on" means "actively use this input as the audio source" and
    "off" means "release this input and stop output from it".

    Wire protocol:
      - ON:  SET MB#50 <source_id>   (13 = AUX In, 14 = SPDIF In)
      - OFF: SET MB#50 0             (No Source — releases audio path)
    """

    _SOURCE_ID: int = 0  # subclasses override

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._optimistic_until: float = 0.0
        self._optimistic_state: bool = False

    @property
    def is_on(self) -> bool:
        import time
        if time.monotonic() < self._optimistic_until:
            return self._optimistic_state
        return self._client.state.source_id == self._SOURCE_ID

    async def async_turn_on(self, **kwargs) -> None:
        import time
        from .const import MB_SOURCE
        self._optimistic_state = True
        self._optimistic_until = time.monotonic() + 3.0
        self.async_write_ha_state()
        await self._client._send(0x02, MB_SOURCE, str(self._SOURCE_ID))  # noqa: SLF001
        try:
            await self._client._send(0x01, MB_SOURCE, "")  # noqa: SLF001
        except Exception:
            pass

    async def async_turn_off(self, **kwargs) -> None:
        import time
        from .const import MB_SOURCE
        self._optimistic_state = False
        self._optimistic_until = time.monotonic() + 3.0
        self.async_write_ha_state()
        await self._client._send(0x02, MB_SOURCE, "0")  # noqa: SLF001
        try:
            await self._client._send(0x01, MB_SOURCE, "")  # noqa: SLF001
        except Exception:
            pass


class LitheAuxInSwitch(_LithePassthroughSwitch):
    """AUX In passthrough toggle."""

    _attr_name = "Inputs — AUX In"
    _attr_icon = "mdi:audio-input-rca"
    _SOURCE_ID = 13

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_aux_in"


class LitheSpdifInSwitch(_LithePassthroughSwitch):
    """SPDIF In passthrough toggle."""

    _attr_name = "Inputs — SPDIF In"
    _attr_icon = "mdi:toslink"
    _SOURCE_ID = 14

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_spdif_in"
