"""Data update coordinator for Lithe Audio."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL_S
from .lithe_client import LitheClient

_LOGGER = logging.getLogger(__name__)

DSP_STORAGE_VERSION = 1


class LitheAudioCoordinator(DataUpdateCoordinator):
    """Coordinator that manages connection and state for one Lithe Audio speaker."""

    def __init__(
        self, hass: HomeAssistant, client: LitheClient, entry_id: str
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_S),
        )
        self.client = client
        self._dsp_store: Store = Store(
            hass, DSP_STORAGE_VERSION, f"{DOMAIN}.dsp_state.{entry_id}"
        )
        self._saved_dsp_revision = client.state.dsp_feedback_revision

        # Register our state-change callback so entities update on push
        self.client.register_callback(self._on_speaker_push)

    def _on_speaker_push(self) -> None:
        """Called by client whenever the speaker sends a state update."""
        self.async_set_updated_data(self.client.state)
        revision = self.client.state.dsp_feedback_revision
        if revision != self._saved_dsp_revision:
            self._saved_dsp_revision = revision
            # Store.async_delay_save is HA's supported debounce mechanism.
            # It survives a burst of individual MB#112 records without
            # repeatedly cancelling in-progress file writes.
            self._dsp_store.async_delay_save(
                lambda: {"values": self.client.state.dsp_snapshot()},
                0.25,
            )

    async def async_restore_dsp_state(self) -> None:
        """Restore the last values observed in real MB#112 feedback."""
        try:
            data = await self._dsp_store.async_load()
        except Exception as err:
            _LOGGER.warning("Could not load stored DSP state: %s", err)
            return
        values = data.get("values") if isinstance(data, dict) else None
        if self.client.state.restore_dsp_snapshot(values):
            _LOGGER.info(
                "Restored %d remembered DSP values for %s; waiting for live "
                "MB#112 feedback",
                len(self.client.state.dsp_snapshot()),
                self.client.host,
            )

    async def _async_update_data(self):
        """Poll: request full state refresh if connected."""
        if not self.client.state.connected:
            try:
                await self.client.async_connect()
            except Exception as err:
                raise UpdateFailed(f"Cannot connect to {self.client.host}: {err}") from err

        try:
            await self.client.async_refresh()
        except Exception as err:
            raise UpdateFailed(f"Update failed: {err}") from err

        return self.client.state

    async def async_shutdown(self) -> None:
        if self.client.state.dsp_snapshot():
            try:
                await self._dsp_store.async_save({
                    "values": self.client.state.dsp_snapshot(),
                })
            except Exception as err:
                _LOGGER.warning("Could not flush DSP state: %s", err)
        await self.client.async_disconnect()
        await super().async_shutdown()
