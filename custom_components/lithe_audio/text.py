"""Editable names for Lithe Audio favourite slots."""
from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import LitheAudioCoordinator
from .local_favs import MAX_SLOTS, get_local_favs


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LitheAudioCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        LitheFavouriteNameText(coordinator, entry, slot)
        for slot in range(1, MAX_SLOTS + 1)
    )


class LitheFavouriteNameText(TextEntity):
    """HA-side display name for a local or native favourite slot."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:form-textbox"
    _attr_native_min = 1
    _attr_native_max = 80

    def __init__(
        self,
        coordinator: LitheAudioCoordinator,
        entry: ConfigEntry,
        slot: int,
    ) -> None:
        self._coordinator = coordinator
        self._client = coordinator.client
        self._entry = entry
        self._slot = slot
        self._attr_name = f"Favourites - Name {slot}"
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_fav_name_{slot}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        manager = get_local_favs(self.hass)
        if manager:
            self.async_on_remove(manager.async_listen(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        manager = get_local_favs(self.hass)
        if manager:
            local = manager.get(self._slot)
            if local and local.get("name"):
                return str(local["name"])
        for favourite in self._client.state.favourites or []:
            if int(favourite.get("slot", 0)) == self._slot:
                return str(favourite.get("name") or f"Favourite {self._slot}")
        return f"Favourite {self._slot}"

    async def async_set_value(self, value: str) -> None:
        manager = get_local_favs(self.hass)
        if manager is None:
            return
        await manager.async_rename(self._slot, value)
        self._coordinator.async_set_updated_data(self._client.state)
        self.async_write_ha_state()
