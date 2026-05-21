"""Button entities for Lithe Audio — chimes, reboot, factory defaults."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_PRODUCT, DATA_COORDINATOR, DOMAIN, PRODUCT_CHIMES,
)
from .coordinator import LitheAudioCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LitheAudioCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    product = entry.data[CONF_PRODUCT]

    entities: list[ButtonEntity] = []

    # Chime buttons — one per slot up to product's chime count
    chime_count = PRODUCT_CHIMES.get(product, 0)
    for slot in range(1, chime_count + 1):
        entities.append(LitheChimeButton(coordinator, entry, slot))

    # Save-to-favourite buttons (slots 1-9) — press to save currently playing
    for slot in range(1, 10):
        entities.append(LitheSaveFavouriteButton(coordinator, entry, slot))

    # Play-favourite buttons (slots 1-9) — press to play saved favourite.
    # Looks up HA-side favourites first (more reliable than native MB#70
    # which fails with GENERIC_FAV_SAVE_FAIL on Direct URL streams), then
    # falls back to the speaker's onboard favourite.
    for slot in range(1, 10):
        entities.append(LithePlayFavouriteButton(coordinator, entry, slot))

    # Heart button: saves current track to the NEXT free favourite slot.
    # Press once to add current track to favourites without picking a slot.
    entities.append(LitheHeartButton(coordinator, entry))

    # Diagnostics
    entities.append(LitheRebootButton(coordinator, entry))
    entities.append(LitheFactoryResetButton(coordinator, entry))
    # Release Source — unsticks the speaker when external source
    # (Spotify Connect, AirPlay, Cast, Favourites) blocks playback.
    # Sends SET MB#50 0 to release the audio path.
    entities.append(LitheReleaseSourceButton(coordinator, entry))

    async_add_entities(entities)


class _LitheBaseButton(CoordinatorEntity[LitheAudioCoordinator], ButtonEntity):
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


class LitheChimeButton(ButtonEntity):
    """Play a single chime slot — standalone (not coordinator-gated) for
    minimum latency between press and wire."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:bell-ring"

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry, slot: int):
        self._coordinator = coordinator
        self._client = coordinator.client
        self._entry = entry
        self._slot = slot
        # "Chimes — NN" prefix groups all chime entities together
        # alphabetically in the Controls section.
        self._attr_name = f"Chimes — {slot:02d}"
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_chime_{slot}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    @property
    def available(self) -> bool:
        # Always show as available — the chime fire path tolerates disconnection
        # and we don't want HA gating the press on coordinator state freshness.
        return True

    async def async_press(self) -> None:
        _LOGGER.info("Chime button %d pressed", self._slot)
        await self._client.async_play_chime(self._slot)


class LitheRebootButton(_LitheBaseButton):
    _attr_name = "Actions — Reboot"
    _attr_icon = "mdi:restart"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = None

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_reboot"

    async def async_press(self) -> None:
        await self._client.async_reboot()


class LitheFactoryResetButton(_LitheBaseButton):
    _attr_name = "Actions — Factory Reset"
    _attr_icon = "mdi:lock-reset"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_factory_reset"

    async def async_press(self) -> None:
        await self._client.async_factory_reset()


class LitheReleaseSourceButton(_LitheBaseButton):
    """Release the speaker's audio path (SET MB#50 0 = No Source).

    Useful when an external source (Spotify Connect, AirPlay, Cast,
    Favourites, etc.) owns the audio path and silently blocks Direct
    URL playback per LUCI spec §10.35. Press this to unstick the
    speaker, then retry your TTS / Radio / play_media action.
    """

    _attr_name = "Actions — Release Source"
    _attr_icon = "mdi:eject"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_release_source"

    async def async_press(self) -> None:
        from .const import MB_SOURCE
        _LOGGER.info(
            "Release Source pressed — sending SET MB#50 0 to unblock "
            "audio path (was source %d)",
            self._client.state.source_id,
        )
        await self._client._send(0x02, MB_SOURCE, "0")  # noqa: SLF001
        # Confirm source state after release
        import asyncio
        await asyncio.sleep(0.3)
        await self._client._send(0x01, MB_SOURCE, "")  # noqa: SLF001


class LitheSaveFavouriteButton(ButtonEntity):
    """Save currently-playing track to a favourite slot.

    Press to save whatever is playing right now (Spotify Connect, Airable,
    etc.) into one of the speaker's favourite slots, accessible via
    play_favourite later.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:heart-plus"

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry, slot: int) -> None:
        self._coord = coordinator
        self._client = coordinator.client
        self._entry = entry
        self._slot = slot
        # "Favourites — Save N" groups under Favourites section in Controls
        self._attr_name = f"Favourites — Save {slot}"
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_save_fav_{slot}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    @property
    def available(self) -> bool:
        return self._client.state.connected

    async def async_press(self) -> None:
        _LOGGER.info("Save to favourite slot %d pressed", self._slot)
        await self._client.async_save_favourite(self._slot)


class LithePlayFavouriteButton(ButtonEntity):
    """Play a saved favourite from a specific slot.

    Tries HA-side favourites first (works around the speaker's frequent
    GENERIC_FAV_SAVE_FAIL on Direct URL slots), then falls back to the
    speaker's native MB#70 favourites if no HA-side entry exists.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:heart"

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry, slot: int) -> None:
        self._coord = coordinator
        self._client = coordinator.client
        self._entry = entry
        self._slot = slot
        # "Favourites — Play N" groups under Favourites section in Controls
        self._attr_name = f"Favourites — Play {slot}"
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_play_fav_{slot}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    @property
    def available(self) -> bool:
        return self._client.state.connected

    async def async_press(self) -> None:
        _LOGGER.info("Play favourite slot %d pressed", self._slot)

        # Try HA-side favourites first
        try:
            from .local_favs import get_local_favs
            local_favs = get_local_favs(self.hass)
            if local_favs:
                fav = local_favs.get(self._slot)
                if fav and fav.get("url"):
                    _LOGGER.info(
                        "Play Favourite %d (HA-side): %s",
                        self._slot, fav.get("name") or fav["url"],
                    )
                    await self._client.async_play_url(fav["url"])
                    return
        except Exception as e:
            _LOGGER.debug("HA-side favourite lookup failed: %s", e)

        # Fall back to the speaker's native favourite slot via MB#70
        _LOGGER.info("Play Favourite %d (native MB#70)", self._slot)
        try:
            await self._client.async_play_favourite(self._slot)
        except Exception as e:
            _LOGGER.warning("Native favourite play failed: %s", e)


class LitheHeartButton(ButtonEntity):
    """♥ Heart button — saves current track to the next free favourite slot.

    Press once to add the currently-playing track to favourites without
    needing to pick a slot. Auto-increments: first press writes slot 1,
    second writes slot 2, ... up to slot 9. After all 9 are filled, it
    wraps around to slot 1 (oldest gets overwritten).

    Slot state is tracked on the client; the next free slot is determined
    by scanning the speaker's reported favourites list for the lowest
    unused number 1-9.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:heart"

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry) -> None:
        self._coord = coordinator
        self._client = coordinator.client
        self._entry = entry
        self._attr_name = "Favourites — ♥ Save Current Track"
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_heart_save"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    @property
    def available(self) -> bool:
        return self._client.state.connected

    def _next_free_slot(self) -> int:
        """Find next free favourite slot (1-9). Wraps around after 9.

        Uses HA-side favourites (not the speaker's MB#70 list) so the
        slot picker works even when nothing has been saved natively yet.
        """
        from .local_favs import get_local_favs
        mgr = get_local_favs(self.hass)
        if mgr is None:
            return 1
        return mgr.next_free_slot()

    async def async_press(self) -> None:
        # Use HA-side favourites (works for any URL including Direct URL,
        # which the speaker's MB#70 FAV_SAVE rejects with GENERIC_FAV_SAVE_FAIL).
        from .local_favs import get_local_favs
        mgr = get_local_favs(self.hass)
        if mgr is None:
            _LOGGER.warning("Heart button: local favourites manager not initialised")
            return

        slot = self._next_free_slot()
        # Capture URL + name from current playback state
        s = self._client.state
        url = s.last_played_url or ""
        name = s.title or ""
        if not name and url:
            from urllib.parse import urlparse
            name = urlparse(url).path.rsplit("/", 1)[-1]
            if "." in name:
                name = name.rsplit(".", 1)[0]
        if not name:
            name = f"Favourite {slot}"

        if not url:
            _LOGGER.warning(
                "Heart pressed but no URL is currently playing on %s — "
                "cannot save. Try playing a track first.",
                self._client.host,
            )
            return

        _LOGGER.info(
            "Heart pressed — saving slot %d: %r → %s",
            slot, name, url,
        )
        await mgr.async_set(slot, name, url)
        # Also try the native MB#70 save for completeness (will fail for
        # Direct URL but works for Spotify/AirPlay sources).
        try:
            await self._client.async_save_favourite(slot)
        except Exception:
            pass
        self._client._heart_last_slot = slot  # type: ignore[attr-defined]
