"""Lithe Audio media player entity."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_PRODUCT, DATA_COORDINATOR, DOMAIN, PRODUCT_NAMES,
    PRODUCT_SOURCES, SOURCES,
)
from .coordinator import LitheAudioCoordinator

_LOGGER = logging.getLogger(__name__)

# Media content types we accept for play_media
_PLAYABLE_TYPES = {
    MediaType.MUSIC, MediaType.URL,
    "audio/mp3", "audio/mpeg", "audio/wav", "audio/x-wav",
    "audio/aac", "audio/flac", "audio/ogg", "audio/x-mpegurl",
}

# Internal content_id prefix for favourites
_FAV_PREFIX = "lithe_fav://"
# Internal content_id prefix for chimes
_CHIME_PREFIX = "lithe_chime://"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LitheAudioCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    entities: list = [LitheAudioMediaPlayer(coordinator, entry)]

    # Add group media_player entities (created once across all entries).
    # We attach to the FIRST entry that loads so groups appear in HA;
    # subsequent entries skip the group creation.
    if not hass.data.get(DOMAIN, {}).get("_groups_added"):
        try:
            from .group import (
                get_group_manager,
                LitheGroupMediaPlayer,
            )
            mgr = get_group_manager(hass)
            if mgr:
                groups = mgr.list_groups()
                for g in groups:
                    entities.append(LitheGroupMediaPlayer(hass, g))
                hass.data[DOMAIN]["_groups_added"] = True
                hass.data[DOMAIN]["_group_async_add_entities"] = async_add_entities
                _LOGGER.info("Created %d Lithe group media_player entities", len(groups))

                # Register listener so newly-created groups appear without restart.
                def _on_groups_changed():
                    _LOGGER.info("Lithe groups changed — restart required to fully apply add/remove")
                mgr.register_listener(_on_groups_changed)

            # Note: Cast group proxies are NOT created. Earlier attempts
            # to put Cast groups in HA's stock Group/Join picker proved
            # confusing — the join picker is multi-select toggle UI
            # which doesn't match Cast group routing semantics ("send my
            # audio TO this Cast group" is a single-target operation,
            # not a join). Cast groups are selected via the dedicated
            # select.lithe_audio_*_cast_group entity instead. The Group
            # icon then correctly shows only Lithe speakers — useful for
            # pairing PRO 2 with the Sub woofer.
        except Exception as e:
            _LOGGER.error("Failed to set up group entities: %s", e)

    async_add_entities(entities)


class LitheAudioMediaPlayer(CoordinatorEntity[LitheAudioCoordinator], MediaPlayerEntity):
    """Lithe Audio speaker media player entity."""

    _attr_has_entity_name = True
    _attr_name = None  # Use device name

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._product = entry.data[CONF_PRODUCT]
        self._client = coordinator.client

        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_player"

        # Build source list from product capability matrix.
        #
        # We filter to only sources that can be ACTIVATED via MB#50 SET.
        # Streaming-app sources (Spotify Connect, AirPlay, Cast, etc.)
        # are passive — they become active only when an external client
        # device starts streaming to them. Showing them in select_source
        # produces a non-working dropdown (looks like a bug), so we omit
        # them. They still appear in `source_name` attribute when active.
        # (This matches the Sonos integration pattern — Sonos only shows
        # locally-switchable sources in its source picker.)
        # Local inputs that show in the source dropdown.
        # AUX In, SPDIF In, Bluetooth have been MOVED to Browse Media
        # per user UX request — they appear as top-level browse entries
        # with icons there. Favourites and Direct URL stay in source.
        _ACTIVATABLE_SOURCES = {
            0,   # No Source (releases current)
            5,   # USB
            17,  # Direct URL
            23,  # Favourites
        }
        src_ids = PRODUCT_SOURCES.get(self._product, list(SOURCES.keys()))
        self._source_list = [
            SOURCES[s] for s in src_ids
            if s in SOURCES
            and s in _ACTIVATABLE_SOURCES
            and SOURCES[s] != "No Source"
        ]
        # Reverse-lookup name → id (still includes AUX/SPDIF/BT so
        # Browse Media can switch to them and source_name still
        # displays them when they activate themselves)
        self._source_id_by_name = {SOURCES[s]: s for s in src_ids if s in SOURCES}

        # Grace timer for available property — set in available() the
        # first time we see state.connected=True
        self._last_seen_connected: float = 0.0

    @property
    def device_info(self) -> DeviceInfo:
        state = self._client.state
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.data["host"])},
            name=state.name or PRODUCT_NAMES.get(self._product, "Lithe Audio"),
            manufacturer="Lithe Audio",
            model=state.model or PRODUCT_NAMES.get(self._product),
            sw_version=state.firmware or None,
            # Bare http:// to the speaker IP — most reliable URL across
            # firmware versions (the Cast eureka_info endpoint isn't always
            # exposed). Some users may need to use the Lithe app instead.
            configuration_url=f"http://{self._client.host}",
        )

    # ── Feature flags ──────────────────────────────────────────────────────

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        flags = (
            MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.NEXT_TRACK
            | MediaPlayerEntityFeature.PREVIOUS_TRACK
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.PLAY_MEDIA
            | MediaPlayerEntityFeature.BROWSE_MEDIA
            | MediaPlayerEntityFeature.SHUFFLE_SET
            | MediaPlayerEntityFeature.REPEAT_SET
            | MediaPlayerEntityFeature.SELECT_SOUND_MODE
            # GROUPING enables HA's stock "Group" icon (4th round button)
            # in the more-info dialog. When the user opens it, HA shows
            # a checklist of other media_players to add to the group.
            # We translate that selection into Cast group routing: the
            # picked target becomes our active_cast_group and subsequent
            # play_media calls forward through it.
            | MediaPlayerEntityFeature.GROUPING
        )
        # MEDIA_ANNOUNCE was added in HA 2022.12 — guard the import
        ann = getattr(MediaPlayerEntityFeature, "MEDIA_ANNOUNCE", None)
        if ann is not None:
            flags |= ann
        # SEEK only when source is not a live stream
        if not self._client.state.is_live:
            flags |= MediaPlayerEntityFeature.SEEK
        return flags

    # ── State properties ───────────────────────────────────────────────────

    @property
    def state(self) -> MediaPlayerState:
        if not self._client.state.connected:
            return MediaPlayerState.OFF
        return {
            "playing":    MediaPlayerState.PLAYING,
            "paused":     MediaPlayerState.PAUSED,
            "stopped":    MediaPlayerState.IDLE,
            "connecting": MediaPlayerState.BUFFERING,
            "buffering":  MediaPlayerState.BUFFERING,
        }.get(self._client.state.play_state, MediaPlayerState.IDLE)

    @property
    def available(self) -> bool:
        """Speaker availability with brief disconnect grace.

        We treat the speaker as available if either:
          - the LUCI socket is currently connected, OR
          - we've been disconnected for less than 90 seconds (grace
            period to ride out brief network blips and the coordinator's
            reconnect attempts without flapping the UI to Unavailable).

        Once 90 seconds pass without successful reconnection we surface
        the Unavailable state so the user knows there's a real problem.
        """
        if self._client.state.connected:
            # Note the moment we became connected for the grace timer
            self._last_seen_connected = self.hass.loop.time() if self.hass else 0.0
            return True
        # Disconnected — check grace window
        try:
            now = self.hass.loop.time()
            last_seen = getattr(self, "_last_seen_connected", 0.0)
            if last_seen and (now - last_seen) < 90.0:
                return True
        except Exception:
            pass
        return False

    @property
    def volume_level(self) -> float:
        return self._client.state.volume / 100.0

    @property
    def is_volume_muted(self) -> bool:
        return self._client.state.muted

    @property
    def source(self) -> str | None:
        # Show the speaker's actual current local source. Cast group
        # routing (when active) is reflected in the dedicated Cast Group
        # select entity (see select.py), not here, so the dropdown
        # selection always matches a list entry.
        return self._client.state.source_name

    @property
    def source_list(self) -> list[str]:
        """Selectable destinations in the source dropdown.

        Sections:
          1. Local inputs (USB/AUX/SPDIF/Bluetooth/Direct URL/legacy
             Favourites entry that triggers the speaker's native source).
          2. Saved favourites — each populated slot 1-9 shows as
             "♥ Favourite N: <name>" and plays directly when picked.

        Cast groups are now reached via the 4th icon (Group button) in
        the player card, not through this dropdown — HA's stock player
        renders that icon because we enable the GROUPING feature flag.
        """
        base = list(self._source_list)

        # Favourites — each saved one inline so user can pick directly
        try:
            from .local_favs import get_local_favs
            local_favs = get_local_favs(self.hass)
            if local_favs:
                for fav in local_favs.list_all():
                    if fav.get("url"):  # only show populated slots
                        label = f"♥ Favourite {fav['slot']}: {fav.get('name') or fav['url']}"
                        base.append(label)
        except Exception:
            pass

        return base

    def _discover_cast_groups(self) -> list[dict[str, Any]]:
        """Return Cast group entities currently registered in HA.

        Each entry: {"entity_id": "media_player.kitchen_group",
                     "name": "Kitchen Group"}
        """
        try:
            from .group import discover_cast_groups
            return discover_cast_groups(self.hass) or []
        except Exception:
            return []

    @property
    def media_title(self) -> str | None:
        # Prefer real title from MB#42, fall back to filename of the URL
        # we last asked the speaker to play (helps when speaker hasn't
        # pushed metadata yet for Direct URL streams).
        if self._client.state.title:
            return self._client.state.title
        url = self._client.state.last_played_url
        if url:
            # Strip query string, take last path segment
            try:
                from urllib.parse import urlparse
                path = urlparse(url).path
                fname = path.rsplit("/", 1)[-1] or url
                # Strip extension for prettiness
                if "." in fname:
                    fname = fname.rsplit(".", 1)[0]
                return fname or None
            except Exception:
                return url
        return None

    @property
    def media_artist(self) -> str | None:
        return self._client.state.artist or None

    @property
    def media_album_name(self) -> str | None:
        return self._client.state.album or None

    @property
    def media_image_url(self) -> str | None:
        # Prefer the current track's artwork. Fall back to the Lithe logo
        # so the player card always has a visual identity even when idle.
        # We serve the icon ourselves via the static path registered in
        # card_resource.py — the local brand API endpoint requires an
        # auth token which is awkward to inject from here. The brand/
        # folder still handles the integration-level icon in the Devices
        # & Services screen automatically (HA 2026.3+ local brands).
        return self._client.state.artwork_url or "/lithe_audio_assets/icon.png"

    @property
    def media_duration(self) -> int | None:
        d = self._client.state.duration_ms
        return d // 1000 if d else None

    @property
    def media_position(self) -> int | None:
        p = self._client.state.position_ms
        return p // 1000 if p else None

    @property
    def media_position_updated_at(self):
        """When position was last fetched. Lets HA extrapolate live position."""
        ts = self._client.state.position_updated_at
        if not ts:
            return None
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    @property
    def media_content_type(self) -> MediaType:
        return MediaType.MUSIC

    @property
    def shuffle(self) -> bool:
        return self._client.state.shuffle

    @property
    def repeat(self) -> RepeatMode:
        m = (self._client.state.repeat or "off").lower()
        return {
            "off": RepeatMode.OFF,
            "all": RepeatMode.ALL,
            "one": RepeatMode.ONE,
        }.get(m, RepeatMode.OFF)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._client.state

        # Build a merged favourites list combining HA-side and speaker-side.
        # HA-side wins for any slot conflicts since it covers more sources.
        merged_favs: list[dict[str, Any]] = []
        try:
            from .local_favs import get_local_favs
            mgr = get_local_favs(self.hass)
            if mgr:
                # list_all returns 9 entries (filled + empty placeholders)
                for f in mgr.list_all():
                    if f.get("url"):  # only show populated slots
                        merged_favs.append({
                            "slot":   f["slot"],
                            "name":   f["name"],
                            "url":    f.get("url", ""),
                            "source": "ha",   # marker so card knows
                        })
        except Exception:
            pass
        # Add any speaker-side favs not already represented
        ha_slots = {f["slot"] for f in merged_favs}
        for f in (s.favourites or []):
            slot = f.get("slot")
            if slot and slot not in ha_slots:
                merged_favs.append({
                    "slot":   slot,
                    "name":   f.get("name", f"Favourite {slot}"),
                    "url":    "",
                    "source": "firmware",
                })
        merged_favs.sort(key=lambda x: x.get("slot", 99))

        return {
            # Hardware / firmware
            "product":         PRODUCT_NAMES.get(self._product, self._product),
            "firmware":        s.firmware,
            "mac_address":     s.mac,
            "wifi_band":       s.wifi_band,
            "timezone":        s.timezone,
            "cast_version":    s.cast_version,
            "net_mode":        s.net_mode,
            # Playback state for automation triggers (e.g. when artist changes)
            "source_id":       s.source_id,
            "source_name":     s.source_name,
            "position_ms":     s.position_ms,
            "is_live":         s.is_live,
            "title":           s.title,
            "artist":          s.artist,
            "album":           s.album,
            "duration_ms":     s.duration_ms,
            "last_played_url": s.last_played_url,
            "volume_percent":  s.volume,           # 0-100 not 0.0-1.0
            "shuffle":         s.shuffle,
            "repeat":          s.repeat,
            # Favourites — list of {slot, name} for picker UIs
            "favourites":      merged_favs,
            # Bluetooth
            "bt_status":       s.bt_status,
        }

    # ── Commands ───────────────────────────────────────────────────────────

    async def async_set_volume_level(self, volume: float) -> None:
        await self._client.async_set_volume(int(volume * 100))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._client.async_mute(mute)

    async def async_media_play(self) -> None:
        # Use RESUME if previously paused, else PLAY
        if self._client.state.play_state == "paused":
            await self._client.async_resume()
        else:
            await self._client.async_play()

    async def async_media_pause(self) -> None:
        await self._client.async_pause()

    async def async_media_stop(self) -> None:
        await self._client.async_stop()

    async def async_media_next_track(self) -> None:
        await self._client.async_next_track()

    async def async_media_previous_track(self) -> None:
        await self._client.async_prev_track()

    async def async_media_seek(self, position: float) -> None:
        await self._client.async_seek(int(position * 1000))

    async def async_set_shuffle(self, shuffle: bool) -> None:
        """Toggle shuffle mode."""
        await self._client.async_set_shuffle(shuffle)

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        """Set repeat mode (off / all / one)."""
        await self._client.async_set_repeat(str(repeat))

    # ── Multi-room via Cast groups (4th icon: GROUPING) ───────────────
    #
    # Lithe doesn't do native LUCI multi-room sync. Google Cast groups
    # (created in the Google Home app) do — and they appear as their
    # own media_player.* entities via HA's Cast integration.
    #
    # We expose the GROUPING feature flag so HA's stock player card
    # renders the 4th round button (Group icon). When the user taps it,
    # HA opens a checklist of other media_player entities. They pick the
    # Cast group they want to send audio to. We translate their choice
    # into Cast group routing: subsequent play_media calls forward
    # through that Cast group's media_player entity (Google's sync infra).
    #
    # group_members reports what's currently joined so HA reflects the
    # active routing state in the card UI.

    @property
    def group_members(self) -> list[str]:
        """Entity_ids currently joined to this speaker.

        When a Cast group is active, return the PROXY entity_id (the
        one HA's picker displays under our integration) so the picker
        shows the correct row as ticked when re-opened. Resolves via
        entity_registry from the underlying Cast entity_id.
        """
        cg_entity = getattr(self._client.state, "active_cast_group_entity", "")
        if not cg_entity:
            return []
        # Find the proxy whose unique_id wraps this Cast entity
        try:
            from homeassistant.helpers import entity_registry as er
            ent_reg = er.async_get(self.hass)
            proxy_uid = f"lithe_cast_proxy_{cg_entity}"
            for ent in ent_reg.entities.values():
                if ent.platform == "lithe_audio" and ent.unique_id == proxy_uid:
                    return [self.entity_id, ent.entity_id]
        except Exception:
            pass
        # Fallback: return the underlying entity if proxy lookup fails
        return [self.entity_id, cg_entity]

    async def async_join_players(self, group_members: list[str]) -> None:
        """Route this speaker's audio through a Cast group.

        HA's Group dialog passes ALL ticked entities here. Lithe can
        only route through ONE Cast group at a time, so if multiple
        are selected we take the LAST one (most recently toggled).

        Accepts:
          - Direct Cast group entities (from HA's Cast integration), or
          - Our LitheCastGroupProxy entities (which represent Cast groups
            but belong to our integration so they appear in HA's picker).

        Non-Cast entities (other Lithe speakers) are ignored — Lithe
        firmware can't do native multi-room sync over LUCI.
        """
        # All Cast groups discovered in HA (regardless of how they got there)
        cast_groups = self._discover_cast_groups()
        cast_entity_to_name = {cg["entity_id"]: cg["name"] for cg in cast_groups}

        try:
            from homeassistant.helpers import entity_registry as er
            ent_reg = er.async_get(self.hass)
        except Exception:
            ent_reg = None

        # Walk in REVERSE so the most-recently-added Cast group wins
        # when multiple are selected.
        chosen_entity = None
        chosen_name = None
        for member in reversed(group_members):
            if member == self.entity_id:
                continue

            # Direct match — user picked a Cast group entity directly
            if member in cast_entity_to_name:
                chosen_entity = member
                chosen_name = cast_entity_to_name[member]
                break

            # Proxy match — resolve to underlying Cast entity
            if ent_reg:
                ent = ent_reg.async_get(member)
                if ent and ent.unique_id and ent.unique_id.startswith("lithe_cast_proxy_"):
                    underlying = ent.unique_id[len("lithe_cast_proxy_"):]
                    if underlying in cast_entity_to_name:
                        chosen_entity = underlying
                        chosen_name = cast_entity_to_name[underlying]
                        break

        if not chosen_entity:
            _LOGGER.warning(
                "join_players: no Cast group in %s. Lithe speakers can "
                "only multi-room via Google Cast groups. Create one in "
                "the Google Home app first.",
                group_members,
            )
            return

        try:
            self._client.state.active_cast_group = chosen_name
            self._client.state.active_cast_group_entity = chosen_entity
        except Exception:
            pass
        _LOGGER.info(
            "join_players: routing through Cast group %r (entity=%s) "
            "[picked from %d candidates]",
            chosen_name, chosen_entity, len(group_members),
        )
        self.async_write_ha_state()

    async def async_unjoin_player(self) -> None:
        """Stop routing through Cast group — return to local playback."""
        try:
            self._client.state.active_cast_group = ""
            self._client.state.active_cast_group_entity = ""
        except Exception:
            pass
        _LOGGER.info("unjoin_player: cleared Cast group routing")
        self.async_write_ha_state()

    # ── Sound mode (EQ preset, Denon-style) ───────────────────────────

    @property
    def sound_mode(self) -> str | None:
        """Current EQ preset, exposed as a Denon-style sound_mode."""
        from .const import EQ_PRESETS
        idx = getattr(self._client.state, "dsp_eq", None)
        if idx is not None and 0 <= idx < len(EQ_PRESETS):
            return EQ_PRESETS[idx]
        return None

    @property
    def sound_mode_list(self) -> list[str] | None:
        from .const import EQ_PRESETS
        return list(EQ_PRESETS)

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        """Set EQ preset by friendly name (Denon-style sound_mode)."""
        from .const import EQ_PRESETS, DSP_EQ
        if sound_mode not in EQ_PRESETS:
            _LOGGER.warning(
                "select_sound_mode: unknown mode %r (available: %s)",
                sound_mode, EQ_PRESETS,
            )
            return
        idx = EQ_PRESETS.index(sound_mode)
        _LOGGER.info("select_sound_mode: %s (idx=%d)", sound_mode, idx)
        await self._client.async_dsp_command(DSP_EQ, idx)

    async def async_select_source(self, source: str) -> None:
        """Switch source by friendly name. Handles:
          - Local inputs (USB/AUX/SPDIF/Bluetooth/Favourites) → MB#50
          - "♥ Favourite N: <name>" → plays saved favourite N
        Cast groups are reached via the Group icon now, not this dropdown.
        """
        import asyncio

        # ── Favourite picker ──────────────────────────────────────────
        if source.startswith("♥ Favourite "):
            try:
                slot_part = source[len("♥ Favourite "):].split(":", 1)[0].strip()
                slot = int(slot_part)
            except (ValueError, IndexError):
                _LOGGER.warning("select_source: bad favourite label %r", source)
                return
            try:
                from .local_favs import get_local_favs
                local_favs = get_local_favs(self.hass)
                if local_favs:
                    fav = local_favs.get(slot)
                    if fav and fav.get("url"):
                        _LOGGER.info(
                            "select_source: playing favourite %d (%s)",
                            slot, fav.get("name") or fav["url"],
                        )
                        await self._client.async_play_url(fav["url"])
                        return
            except Exception as e:
                _LOGGER.debug("HA-side favourite lookup failed: %s", e)
            try:
                await self._client.async_play_favourite(slot)
            except Exception as e:
                _LOGGER.warning("Native favourite play failed: %s", e)
            return

        # ── Local source switch ──────────────────────────────────────
        # Clear any active Cast routing first.
        prev_cast = getattr(self._client.state, "active_cast_group", "")
        if prev_cast:
            try:
                self._client.state.active_cast_group = ""
                self._client.state.active_cast_group_entity = ""
            except Exception:
                pass

        src_id = self._source_id_by_name.get(source)
        if src_id is None:
            _LOGGER.warning(
                "select_source: unknown source %r (available: %s)",
                source, self.source_list,
            )
            return

        _LOGGER.info("select_source: switching to %s (id=%d)", source, src_id)
        await self._client._send(0x02, 50, str(src_id))  # noqa: SLF001

        async def _refresh():
            await asyncio.sleep(0.8)
            await self._client._send(0x01, 50, "")
            await self._client._send(0x01, 51, "")
        self.hass.async_create_task(_refresh())

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a URL or favourite on the speaker.

        Supports HA's standard play_media announce flow (per the
        media_player docs at https://www.home-assistant.io/integrations/
        media_player/#action-play-media). When ``announce=True`` is
        passed, the media is treated as a temporary announcement that
        interrupts current playback — this routes through the tannoy
        notify path which:

          1. Saves current volume + play state
          2. Pauses current source
          3. Sets announcement volume (from extra.volume or default 70)
          4. Plays the announcement URL via MB#41 PLAYITEM:DIRECT
          5. (Future) restores volume + resumes original source

        This is the standard way to do chimes / TTS / prayer / doorbell
        sounds in Home Assistant and works with the built-in voice
        assistant announcement UI, dashboard buttons, automations, etc.
        """
        # Favourite by content_id (no announce flow — favourites resume
        # the speaker's own playback engine)
        if media_id.startswith(_FAV_PREFIX):
            slot = int(media_id[len(_FAV_PREFIX):])
            await self._client.async_play_favourite(slot)
            return

        # Local input switch from Browse Media (AUX/SPDIF/Bluetooth).
        # These were moved out of the source dropdown into Browse Media
        # — picking one here switches the speaker's MB#50 source.
        if media_id.startswith("lithe_source:"):
            source_name = media_id[len("lithe_source:"):]
            await self.async_select_source(source_name)
            return

        # HA-side local favourite — saved URL plays via play_url
        if media_id.startswith("lithe_local_fav:"):
            slot = int(media_id[len("lithe_local_fav:"):])
            try:
                from .local_favs import get_local_favs
                mgr = get_local_favs(self.hass)
                if mgr:
                    fav = mgr.get(slot)
                    if fav and fav.get("url"):
                        await self._client.async_play_url(fav["url"])
                        return
                _LOGGER.warning("local fav slot %d is empty", slot)
            except Exception as e:
                _LOGGER.error("Failed to play local fav %d: %s", slot, e)
            return

        # Direct URL preset (Adhan/Quran/radio from our Browse Media tree)
        if media_id.startswith("lithe_url:"):
            media_id = media_id[len("lithe_url:"):]
            # Fall through to URL playback below

        # Resolve media_source:// URIs (Radio Browser, My Media, TTS, etc.)
        # into a real HTTP URL the speaker can stream.
        if media_id.startswith("media-source://") or media_source.is_media_source_id(media_id):
            try:
                resolved = await media_source.async_resolve_media(
                    self.hass, media_id, self.entity_id,
                )
                # Convert relative URLs (e.g. TTS) to absolute so the
                # speaker can reach them across the network.
                media_id = async_process_play_media_url(self.hass, resolved.url)
                if not media_type or media_type == "":
                    media_type = MediaType.MUSIC
                _LOGGER.debug("Resolved media_source → %s", media_id)
            except Exception as e:
                _LOGGER.error("Failed to resolve media_source %s: %s",
                              media_id, e)
                return

        # Detect announce intent (HA media_player standard)
        announce = bool(kwargs.get("announce") or False)
        extra: dict = kwargs.get("extra") or {}

        if announce and media_id.startswith(("http://", "https://")):
            # Route through tannoy notify service which handles
            # save/pause/volume/play. Use extra.volume if provided.
            volume = int(extra.get("volume", 70))
            try:
                await self.hass.services.async_call(
                    "notify", "lithe_tannoy",
                    {
                        "message": media_id,
                        "data": {
                            "mode":     "start",
                            "volume":   volume,
                            "speakers": [self._client.host],
                        },
                    },
                    blocking=False,
                )
                _LOGGER.info(
                    "play_media announce: routed to lithe_tannoy "
                    "(url=%s volume=%d)", media_id, volume,
                )
            except Exception as e:
                _LOGGER.error("Announce via tannoy failed: %s", e)
            return

        # Regular play (no announce). If user selected a Cast group as
        # the source, forward this URL to the Cast group entity instead
        # of playing locally. The Cast group will synchronously stream
        # to all its members (Google handles the timing infrastructure).
        if media_type in _PLAYABLE_TYPES or media_id.startswith(("http://", "https://")):
            cast_group_entity = getattr(self._client.state, "active_cast_group_entity", "")
            if cast_group_entity:
                _LOGGER.info(
                    "play_media: routing through Cast group %s (url=%s)",
                    cast_group_entity, media_id,
                )
                try:
                    await self.hass.services.async_call(
                        "media_player", "play_media",
                        {
                            "entity_id":          cast_group_entity,
                            "media_content_type": media_type or "music",
                            "media_content_id":   media_id,
                        },
                        blocking=False,
                    )
                except Exception as e:
                    _LOGGER.error(
                        "Cast group routing failed: %s — falling back to local",
                        e,
                    )
                    await self._client.async_play_url(media_id)
                return

            # No Cast group active — play locally only
            await self._client.async_play_url(media_id)
            return

        _LOGGER.warning("Unsupported play_media: type=%s id=%s", media_type, media_id)

    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Browse Lithe favourites + HA media sources + Direct URL presets.

        Top-level tree:
          ├── Favourites          (speaker-side saved slots)
          ├── 🔗 Direct URL       (Adhan, Quran, custom HTTP URLs)
          ├── 📻 Radio Browser    (from HA media_source)
          ├── 📁 My Media         (from HA media_source)
          ├── 🔊 Text-to-speech   (from HA media_source)
          └── …other HA sources
        """
        # Direct URL folder — expand it to see Adhan/Quran/custom presets
        if media_content_id == "lithe_direct_url":
            return self._build_direct_url_folder()

        # A direct-url item: play immediately (handled by play_media)
        if media_content_id and media_content_id.startswith("lithe_url:"):
            url = media_content_id[len("lithe_url:"):]
            # Browse-into of a leaf node — HA will call play_media instead;
            # this branch shouldn't normally hit, but return self for safety.
            return BrowseMedia(
                title=url, media_class=MediaClass.URL,
                media_content_id=media_content_id,
                media_content_type=MediaType.MUSIC,
                can_play=True, can_expand=False,
            )

        # Drill-down into a media_source:// URI
        if media_content_id and media_content_id.startswith("media-source://"):
            return await media_source.async_browse_media(
                self.hass, media_content_id,
                content_filter=lambda item: item.media_content_type.startswith("audio/")
                                            or item.media_content_type in _PLAYABLE_TYPES,
            )

        # Build root: favourites + media sources
        children: list[BrowseMedia] = []

        # 1a) HA-side favourites (saved by Heart button or fav_save service)
        try:
            from .local_favs import get_local_favs
            local_favs_mgr = get_local_favs(self.hass)
            if local_favs_mgr:
                for fav in local_favs_mgr.list_all():
                    if not fav.get("url"):
                        continue  # skip empty slots
                    children.append(BrowseMedia(
                        title=f"❤ {fav['name']}",
                        media_class=MediaClass.MUSIC,
                        media_content_id=f"lithe_local_fav:{fav['slot']}",
                        media_content_type=MediaType.MUSIC,
                        can_play=True,
                        can_expand=False,
                    ))
        except Exception as e:
            _LOGGER.debug("Failed to list local favourites: %s", e)

        # 1b) Firmware favourites (Spotify/AirPlay saved on speaker)
        for fav in self._client.state.favourites:
            children.append(BrowseMedia(
                title=fav.get("name", f"Favourite {fav.get('slot')}"),
                media_class=MediaClass.MUSIC,
                media_content_id=f"{_FAV_PREFIX}{fav.get('slot')}",
                media_content_type=MediaType.MUSIC,
                can_play=True,
                can_expand=False,
            ))

        # 2) Direct URL folder (Adhan, Quran, internet radio presets)
        children.append(BrowseMedia(
            title="🔗 Direct URL — Adhan, Quran & Radio",
            media_class=MediaClass.DIRECTORY,
            media_content_id="lithe_direct_url",
            media_content_type="library",
            can_play=False,
            can_expand=True,
            thumbnail=None,
        ))

        # 3) Local inputs as browse entries (moved from source dropdown
        # per user request). Each plays immediately when picked.
        for label, source_name, icon in (
            ("🔌 AUX In",       "AUX In",    "mdi:audio-input-rca"),
            ("💿 SPDIF In",     "SPDIF In",  "mdi:toslink"),
            ("🎧 Bluetooth",    "Bluetooth", "mdi:bluetooth"),
        ):
            if source_name in self._source_id_by_name:
                children.append(BrowseMedia(
                    title=label,
                    media_class=MediaClass.URL,
                    media_content_id=f"lithe_source:{source_name}",
                    media_content_type=MediaType.MUSIC,
                    can_play=True,
                    can_expand=False,
                ))

        # 4) HA media sources (Radio Browser, My Media, TTS, etc.)
        # Filter out non-audio sources (Image, Image upload, AI generated
        # images, Camera) per user request — these aren't useful on an
        # audio-only speaker.
        _EXCLUDED_SOURCES = {
            "image", "image_upload", "ai_task", "ai_generated_images",
            "ai_image", "camera",
        }
        try:
            ms_root = await media_source.async_browse_media(
                self.hass, None,
                content_filter=lambda item: item.media_content_type.startswith("audio/")
                                            or item.media_content_type in _PLAYABLE_TYPES,
            )
            if ms_root and ms_root.children:
                for c in ms_root.children:
                    # Skip non-audio sources by their media_content_id domain
                    skip = False
                    if c.media_content_id and c.media_content_id.startswith("media-source://"):
                        # URI shape: media-source://<domain>/...
                        try:
                            domain = c.media_content_id[len("media-source://"):].split("/", 1)[0]
                            if domain.lower() in _EXCLUDED_SOURCES:
                                skip = True
                        except Exception:
                            pass
                    # Also skip by title — some sources use display names
                    title_lower = (c.title or "").lower()
                    if any(bad in title_lower for bad in (
                        "image", "camera", "ai generated", "ai task", "upload",
                    )):
                        skip = True
                    if skip:
                        continue
                    children.append(c)
        except Exception as e:
            _LOGGER.warning("Failed to browse HA media sources: %s", e)

        if not children:
            children.append(BrowseMedia(
                title="No favourites or media sources yet",
                media_class=MediaClass.URL,
                media_content_id="lithe_empty",
                media_content_type="library",
                can_play=False,
                can_expand=False,
            ))

        return BrowseMedia(
            title="Lithe Audio",
            media_class=MediaClass.DIRECTORY,
            media_content_id="lithe_root",
            media_content_type="library",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.MUSIC,
        )

    def _build_direct_url_folder(self) -> BrowseMedia:
        """Build the 'Direct URL' sub-folder with Adhan + Quran presets.

        Each preset is a playable BrowseMedia item with the URL embedded
        in the content_id (prefix 'lithe_url:'). When the user taps one,
        HA calls async_play_media with that content_id; we strip the
        prefix and route to async_play_url → MB#41 PLAYITEM:DIRECT.

        Users can also call play_media with any HTTP URL directly (no
        browse needed) — this folder is just a convenience preset list.
        """
        from .const import ADHAN_PRESETS, QURAN_JUZ

        children: list[BrowseMedia] = []

        # Adhan presets first (most commonly played)
        for label, url in ADHAN_PRESETS.items():
            children.append(BrowseMedia(
                title=f"🕌 {label}",
                media_class=MediaClass.MUSIC,
                media_content_id=f"lithe_url:{url}",
                media_content_type=MediaType.MUSIC,
                can_play=True,
                can_expand=False,
            ))

        # Then all 30 Juz of the Quran
        for juz_num, url in QURAN_JUZ.items():
            children.append(BrowseMedia(
                title=f"📖 Juz {juz_num} — Quran",
                media_class=MediaClass.MUSIC,
                media_content_id=f"lithe_url:{url}",
                media_content_type=MediaType.MUSIC,
                can_play=True,
                can_expand=False,
            ))

        # A few common internet radio URLs as bonus
        radio_extras = {
            "🎵 BBC Radio 1":  "http://stream.live.vc.bbcmedia.co.uk/bbc_radio_one",
            "🎵 BBC Radio 2":  "http://stream.live.vc.bbcmedia.co.uk/bbc_radio_two",
            "🎵 BBC Radio 4":  "http://stream.live.vc.bbcmedia.co.uk/bbc_radio_fourfm",
            "🎵 Classic FM":   "http://media-ice.musicradio.com/ClassicFMMP3",
        }
        for label, url in radio_extras.items():
            children.append(BrowseMedia(
                title=label,
                media_class=MediaClass.MUSIC,
                media_content_id=f"lithe_url:{url}",
                media_content_type=MediaType.MUSIC,
                can_play=True,
                can_expand=False,
            ))

        return BrowseMedia(
            title="Direct URL Presets",
            media_class=MediaClass.DIRECTORY,
            media_content_id="lithe_direct_url",
            media_content_type="library",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.MUSIC,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
