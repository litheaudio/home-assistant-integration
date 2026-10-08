"""Data update coordinator for Lithe Audio."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL_S
from .lithe_client import LitheClient

_LOGGER = logging.getLogger(__name__)

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
        # Register our state-change callback so entities update on push
        self.client.register_callback(self._on_speaker_push)

    def _on_speaker_push(self) -> None:
        """Called by client whenever the speaker sends a state update."""
        self.async_set_updated_data(self.client.state)

    async def _async_update_data(self):
        """Poll: request full state refresh if connected."""
        if not self.client.state.connected:
            try:
                await self.client.async_connect()
            except Exception as err:
                raise UpdateFailed(f"Cannot connect to {self.client.host}: {err}") from err
        else:
            # async_connect() already performs the complete initial refresh.
            # Repeating it here doubled every GET and could add several NV-read
            # timeouts to each config entry during Home Assistant startup.
            try:
                await self.client.async_refresh()
            except Exception as err:
                raise UpdateFailed(f"Update failed: {err}") from err

        return self.client.state

    async def async_shutdown(self) -> None:
        await self.client.async_disconnect()
        await super().async_shutdown()
