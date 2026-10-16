"""Lithe Audio speaker protocol client."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import ssl
import struct
import time
from dataclasses import dataclass, field
from typing import Callable, ClassVar, Optional
from urllib.parse import unquote, urlparse

import aiohttp

from .const import (
    BT_MCU_STATE, DEFAULT_PORT, MB_AUDIOCUE, MB_BLUETOOTH, MB_BROWSE, MB_CHIME,
    MB_DEVICE_INFO, MB_DEVICE_NAME, MB_DSP, MB_FACTORY_RESET, MB_FAVOURITES,
    MB_FIRMWARE, MB_INTERFACE_IP, MB_MUTE, MB_NETWORK_INFO, MB_NETWORK_STATUS,
    MB_NOW_PLAYING, MB_PLAY_STATE, MB_PLAYBACK_AUTH, MB_PLAYBACK_GRANT,
    MB_POSITION, MB_TUNNEL_START,
    MB_REGISTER, MB_RSSI, MB_SOURCE, MB_TIMEZONE, MB_TRANSPORT, MB_VOLUME,
    MAX_CHIME_SLOT, NETWORK_STATUS, PLAY_STATES, SOURCES,
    TRANSPORT_NEXT,
    TRANSPORT_PAUSE, TRANSPORT_PLAY, TRANSPORT_PREV, TRANSPORT_RESUME,
    TRANSPORT_STOP,
    DSP_BALANCE, DSP_EQ, DSP_EQ_BANDS, DSP_LOUDNESS, DSP_NIGHTMODE,
    DSP_OUTPUT, DSP_REMOTE_ID, DSP_STATUS_ALL, DSP_TUNNEL_PORT,
    IO1_PRESET, IO1_TREBLE_GAIN,
    IO1_TREBLE_HIGH_BAND, IO1_TREBLE_LOW_BAND, IO1_TREBLE_MID_BAND,
    LS10_FIXED_EQ_PRESET_BANDS,
    PRODUCT_IO1, PRODUCT_MICRO, PRODUCT_PRO2, PRODUCT_V3,
    SUB_DSP_CROSSOVER, SUB_DSP_GAIN, SUB_DSP_LOWPASS,
    SUB_DSP_PHASE, SUB_DSP_PROFILE, SUB_DSP_SET_FIELD,
    product_from_model,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class SpeakerState:
    """Current state of a Lithe Audio speaker."""
    name: str = ""
    firmware: str = ""
    model: str = ""
    detected_product: str = ""
    model_number: str = ""
    model_variant: str = ""
    mac: str = ""
    wifi_band: str = ""
    timezone: str = ""
    cast_version: str = ""
    net_mode: str = ""

    # Network — populated from MB#123, MB#124, MB#151, MB#208(READ_ssid)
    ip_address: str = ""        # IP from MB#123 (Wlan or Eth interface)
    network_interface: str = "" # "Wlan" / "Eth"
    network_status: str = ""    # "WLAN" / "Ethernet" / "P2P" / "WAC/SAC/LS-Connect"
    wifi_rssi_dbm: int = 0      # RSSI in dBm (negative number, e.g. -55)
    wifi_rssi_source: str = ""
    ssid: str = ""              # Connected SSID (from NV item)
    speaker_status: str = ""    # "Standby" / "Connected" / "Active" etc

    # Playback
    play_state: str = "stopped"
    source_id: int = 0
    volume: int = 50
    muted: bool = False
    mute_state_known: bool = False
    position_ms: int = 0
    position_updated_at: float = 0.0   # Unix time when MB#49 was last seen

    # Now playing
    title: str = ""
    artist: str = ""
    album: str = ""
    artwork_url: str = ""
    artwork_revision: int = 0
    duration_ms: int = 0
    is_live: bool = False  # True for radio/AirPlay streams (no SEEK)
    shuffle: bool = False
    repeat: str = "off"  # "off" | "all" | "one"

    # Most recent URL sent via play_url — useful as a fallback title
    # while waiting for the speaker to push fresh MB#42 metadata after
    # a source switch to Direct URL.
    last_played_url: str = ""
    display_title: str = ""
    display_artist: str = ""

    @property
    def position_seconds(self) -> float:
        """Return the exact MB#49 position converted from milliseconds."""
        return self.position_ms / 1000.0

    # Bluetooth
    bt_status: str = ""
    bt_enabled: bool | None = None

    # DSP state — populated from MB#112 push packets so HA reflects
    # changes made in the Lithe app (2-way sync). Sub-MB IDs verified
    # from app packet capture (dsp-sniffer, 2026-05-17):
    dsp_eq:        int | None = None  # 0x0A: 0=Normal 1=Acoustic 2=Jazz 3=Pop 4=HipHop
    dsp_bass:      int | None = None  # 0x09 field 0x06: signed -5..+5
    dsp_mid:       int | None = None  # 0x09 field 0x04: signed -5..+5
    dsp_treble:    int | None = None  # 0x09 field 0x02: signed -5..+5
    dsp_loudness:  int | None = None  # 0x0B: 0=OFF 1=ON
    dsp_loudness_gain: int | None = None  # MB112 subcommand 0x34: -10..+10 dB
    dsp_nightmode: int | None = None  # 0x0C: 0=OFF 1=ON
    dsp_balance:   int | None = None  # 0x0E: signed -6..+6
    dsp_output:    int | None = None  # 0x0F: 0=Mono 1=Stereo 2=Left 3=Right
    dsp_highpass:  int | None = None  # 0x32: 60/80/100/120/150 Hz
    dsp_tuning:    int | None = None  # 0x0D: 13L enclosure/open back
    sub_profile:   int | None = None  # 0x01: Subwoofer/Speaker/Custom
    sub_gain:      int | None = None  # 0x02: 0..100
    sub_crossover: int | None = None  # 0x03: four crossover indices
    sub_phase:     int | None = None  # 0x04: 0/180 degrees
    sub_lowpass:   int | None = None  # 0x05: disabled/enabled
    dsp_last_raw: str = ""
    dsp_last_decoded: str = ""
    # "restored" means HA loaded the last speaker-confirmed snapshot from
    # storage.  A valid MB#112 update changes this to "speaker" immediately.
    dsp_state_source: str = "unknown"
    dsp_feedback_revision: int = 0

    DSP_STATE_FIELDS: ClassVar[tuple[str, ...]] = (
        "dsp_eq", "dsp_bass", "dsp_mid", "dsp_treble",
        "dsp_loudness", "dsp_loudness_gain", "dsp_nightmode",
        "dsp_balance", "dsp_output", "dsp_highpass", "dsp_tuning",
        "sub_profile", "sub_gain", "sub_crossover", "sub_phase",
        "sub_lowpass",
    )

    def dsp_snapshot(self) -> dict[str, int]:
        """Return the known DSP values suitable for persistent storage."""
        return {
            name: value
            for name in self.DSP_STATE_FIELDS
            if isinstance((value := getattr(self, name)), int)
        }

    def restore_dsp_snapshot(self, values: object) -> bool:
        """Restore validated DSP values without treating them as live feedback."""
        if not isinstance(values, dict):
            return False
        restored = False
        for name in self.DSP_STATE_FIELDS:
            value = values.get(name)
            if isinstance(value, int):
                setattr(self, name, value)
                restored = True
        if restored:
            self.dsp_state_source = "restored"
        return restored

    # Player role (per API_NEW page 25): "Free" / "Master" / "Slave"
    # Slave devices cannot trigger chimes — they must be sent to the master.
    player_role: str = ""

    # Favourites
    favourites: list = field(default_factory=list)  # [{slot:int, name:str}]

    # Cast group — non-empty when user selected a Cast group from the
    # source list. Subsequent play_media calls route through this Cast
    # group's media_player entity (true multi-room sync via Google's
    # cloud infra). Cleared when user picks any other source.
    active_cast_group: str = ""        # display name, e.g. "Kitchen Group"
    active_cast_group_entity: str = "" # e.g. "media_player.kitchen_group"

    # Connection
    connected: bool = False

    @property
    def source_name(self) -> str:
        return SOURCES.get(self.source_id, f"Source {self.source_id}")


class LitheClient:
    """Asyncio client for the Lithe Audio speaker API (port 7777, LS10 = TLS)."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        use_tls: bool = True,
        cert_path: Optional[str] = None,
        key_path: Optional[str] = None,
        local_ip: str = "127.0.0.1",
    ) -> None:
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.cert_path = cert_path
        self.key_path = key_path
        self.local_ip = local_ip

        self.state = SpeakerState()
        self.product: str = ""
        self.supports_favourites: bool = True
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._buf = b""
        self._read_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._artwork_refresh_task: Optional[asyncio.Task] = None
        self._metadata_refresh_task: Optional[asyncio.Task] = None
        self._stale_direct_url_metadata: tuple[str, str, str, str] | None = None
        self._dsp_initial_refresh_task: Optional[asyncio.Task] = None
        self._dsp_readback_task: Optional[asyncio.Task] = None
        self._last_nonzero_volume: int = self.state.volume
        # Serialize Bluetooth changes and retain their target while the
        # speaker reports the resulting source through MB#50.
        self._bluetooth_lock = asyncio.Lock()
        self._bluetooth_target: bool | None = None
        self._bluetooth_target_until: float = 0.0
        self._bluetooth_http_state_known: bool = False
        self._pending_bluetooth_future: asyncio.Future[bool] | None = None
        # Match the working Control4 driver: serialize all LUCI writes and
        # leave 100 ms between packets.
        self._send_lock = asyncio.Lock()
        self._last_tx_time: float = 0.0
        self._callbacks: list[Callable] = []
        self._last_rx_time: float = 0.0
        self._last_chime_time: float = 0.0
        self._last_chime_mbid: int = 0
        # Track every RemoteID seen on the socket for diagnostic purposes
        # (multiple RemoteIDs can come from one speaker depending on source)
        self._seen_remote_ids: set[int] = set()
        # NV item being read via MB#208 READ_<item> — cleared on response
        self._pending_nv_read: str | None = None
        self._pending_nv_future: asyncio.Future[str] | None = None
        self._nv_read_lock = asyncio.Lock()
        self._nv_model_probe_complete: bool = False
        self._next_cast_wifi_probe: float = 0.0
        self._dsp_refresh_lock = asyncio.Lock()
        # Counter of consecutive resync events — triggers reconnect at 10
        self._resync_count: int = 0

    # ── Connection ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_tls_context(cert_path: str | None, key_path: str | None) -> ssl.SSLContext:
        """Build the TLS context. Runs in an executor — touches the filesystem."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        # IMPORTANT order: disable check_hostname BEFORE lowering verify_mode —
        # PROTOCOL_TLS_CLIENT enables check_hostname by default, and Python
        # raises ValueError if verify_mode is lowered while it's still on.
        ctx.check_hostname = False
        # CERT_NONE: the Lithe speakers use self-signed server certs against
        # the same CA as the client cert, and Python's chain validation
        # rejects self-signed certs even when the CA is in the trust store.
        # Mutual auth is still preserved because we present our client cert.
        ctx.verify_mode = ssl.CERT_NONE
        if cert_path and key_path:
            ctx.load_cert_chain(cert_path, key_path)
        return ctx

    async def async_connect(self) -> None:
        """Open connection and register with the speaker."""
        ctx = None
        if self.use_tls:
            # load_cert_chain is a blocking file read — do it in an executor
            loop = asyncio.get_running_loop()
            ctx = await loop.run_in_executor(
                None, self._build_tls_context, self.cert_path, self.key_path
            )

        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, ssl=ctx),
            timeout=8.0,
        )

        # Enable TCP keepalive with aggressive timing so we detect a
        # standby/zombie speaker fast (~10s) instead of the Linux default 2h.
        try:
            sock = self._writer.get_extra_info("socket")
            if sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                # These options aren't always supported, wrap each individually
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                except (OSError, AttributeError):
                    pass
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
                except (OSError, AttributeError):
                    pass
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                except (OSError, AttributeError):
                    pass
                # Disable Nagle so chime packets hit the wire immediately
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except (OSError, AttributeError):
                    pass
        except Exception:
            pass

        # LS10/TLS expects lowercase app_info JSON. LS9 retains the legacy
        # plain-IP registration payload.
        await self._send(0x02, MB_REGISTER, self._registration_payload())
        await asyncio.sleep(0.4)  # speaker needs ~400ms before accepting commands

        self.state.connected = True
        _LOGGER.info("Connected to Lithe Audio speaker at %s:%s", self.host, self.port)

        # Start background reader and the 30-second re-registration heartbeat.
        # The Control4 reference driver re-registers every 30 seconds and
        # re-queries the device name. Without this the speaker eventually
        # demotes our session, causing chime/audio commands to be processed
        # slowly or not at all after idle periods.
        self._read_task = asyncio.create_task(self._read_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # Request initial state
        await self.async_refresh()
        dsp_refreshed = await self.async_dsp_refresh()
        # The HOST-MCU dump is slower than ordinary LUCI reads and some
        # installations have several speakers. One bounded read is required
        # before entity creation so HA starts with the live speaker settings.
        # If the tunnel is still coming online, retry it in the background.
        if not dsp_refreshed:
            self._dsp_initial_refresh_task = asyncio.create_task(
                self._delayed_initial_dsp_refresh()
            )

    async def _delayed_initial_dsp_refresh(self) -> None:
        """Fetch live DSP state after Core and the entity platforms can start."""
        try:
            # The MCU tunnel often becomes available after the main LUCI
            # session. Retry capability misses so restored HA state is always
            # replaced by the speaker's live settings when the tunnel appears.
            for delay in (2.0, 2.0, 4.0):
                await asyncio.sleep(delay)
                if not self.state.connected:
                    return
                if await self.async_dsp_refresh():
                    return
        except asyncio.CancelledError:
            pass
        except Exception as err:
            _LOGGER.warning("Delayed DSP startup refresh failed: %s", err)

    async def _heartbeat_loop(self) -> None:
        """Re-register every 30s — mirrors Control4 driver behaviour.

        The speaker treats long socket silence as session demotion. To stay
        in a fully-responsive state we re-send the registration plus a
        device-name GET every 30 seconds, exactly like the proven Control4
        Lua driver does.
        """
        try:
            while self.state.connected and self._writer:
                await asyncio.sleep(30.0)
                if not self.state.connected or not self._writer or self._writer.is_closing():
                    break
                try:
                    await self._send(0x02, MB_REGISTER, self._registration_payload())
                    await self._send(0x01, MB_DEVICE_NAME, "")
                    _LOGGER.debug("Heartbeat: re-registered with speaker")
                except Exception as e:
                    _LOGGER.debug("Heartbeat write failed: %s", e)
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            _LOGGER.debug("Heartbeat loop ended: %s", e)

    async def async_disconnect(self) -> None:
        """Disconnect from speaker."""
        self.state.connected = False
        self._seen_remote_ids.clear()
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
        if getattr(self, "_heartbeat_task", None) and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._artwork_refresh_task and not self._artwork_refresh_task.done():
            self._artwork_refresh_task.cancel()
        if self._metadata_refresh_task and not self._metadata_refresh_task.done():
            self._metadata_refresh_task.cancel()
        if (
            self._dsp_initial_refresh_task
            and not self._dsp_initial_refresh_task.done()
        ):
            self._dsp_initial_refresh_task.cancel()
        if self._dsp_readback_task and not self._dsp_readback_task.done():
            self._dsp_readback_task.cancel()
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

    # ── State refresh ──────────────────────────────────────────────────────

    async def async_refresh(self) -> None:
        """Request all state from speaker.

        NOTE: empirically this firmware responds to GETs on several Tx_-only
        mailboxes (MB#42 Now Playing, MB#50 Source, MB#51 Play State,
        MB#49 Position) even though the
        spec marks them as push-only. We send them because they're the
        only way to get the speaker's current state on connect or after
        a stale period — the spec-only "push on change" never fires if
        nothing changed.

        We previously tried removing these per a strict spec read and it
        broke track info / source display / play state, so they stay.
        """
        # Standard refresh — speaker responds to all of these
        for mb in (MB_DEVICE_NAME,      # 90  Device Name
                   MB_FIRMWARE,         # 5   Firmware Version
                   MB_INTERFACE_IP,     # 123 Interface IP
                   MB_NETWORK_STATUS,   # 124 Network Status
                   MB_VOLUME,           # 64  Volume
                   MB_SOURCE,           # 50  Current Source (Tx_ but responds)
                   MB_PLAY_STATE,       # 51  Play State (Tx_ but responds)
                   MB_NOW_PLAYING,      # 42  Now Playing JSON (Tx_ but responds)
                   MB_POSITION,         # 49  Position (Tx_ but responds)
                   MB_TIMEZONE):        # 573 TimeZone
            await self._send(0x01, mb, "")
            await asyncio.sleep(0.05)

        # The TechNote specifies RID 0xAAAA for MB#151, while current Lithe
        # app traffic commonly uses RID 0x0000. Some LS10 firmware only
        # answers one route, so issue the same documented GET through both.
        await self._send(0x01, MB_RSSI, "", remote_id=0xAAAA)
        await asyncio.sleep(0.05)
        if self.use_tls:
            await self._send(0x01, MB_RSSI, "", remote_id=0x0000)
            await asyncio.sleep(0.05)

        # MB#91 NETWORK INFO requires SET MACADDR payload (per spec §9.35)
        await self._send(0x02, MB_NETWORK_INFO, "MACADDR")
        await asyncio.sleep(0.05)

        # MB#70 is not implemented by the WiFi Micro Subwoofer.
        if self.supports_favourites:
            await self._send(0x02, MB_FAVOURITES, "FAV_LIST")
            await asyncio.sleep(0.05)

        # MB#208 NV reads are serialized because responses contain only the
        # value, not the requested key. Model selects the product capability
        # profile; SSID remains diagnostic network state.
        if not self._nv_model_probe_complete:
            for item in ("Model", "Model_num", "ModelVariant"):
                await self.async_read_nv(item)
            self._nv_model_probe_complete = True
        await self.async_read_nv("ssid")

        # LUCI v15.0.7 explicitly marks MB#151 unsupported on LS10/11.
        # Cast diagnostics provide RSSI and, on newer builds, the connected
        # frequency/channel used to derive the actual Wi-Fi band.
        if self.use_tls:
            await self._refresh_cast_wifi_state()

        # The HTTP page is the confirmed source of Bluetooth service state.
        await self._refresh_bluetooth_http_state()

    async def async_read_nv(self, item: str) -> str | None:
        """Read an NV item via MB#208 SET READ_<item>.

        Per LUCI spec §10.23, the speaker responds on the same MB#208 with
        the NV item's value as the payload.
        """
        async with self._nv_read_lock:
            loop = asyncio.get_running_loop()
            future: asyncio.Future[str] = loop.create_future()
            self._pending_nv_read = item
            self._pending_nv_future = future
            try:
                await self._send(0x02, MB_DEVICE_INFO, f"READ_{item}")
                return await asyncio.wait_for(future, timeout=1.5)
            except asyncio.TimeoutError:
                _LOGGER.debug("NV read %r timed out on %s", item, self.host)
                return None
            finally:
                if self._pending_nv_future is future:
                    self._pending_nv_read = None
                    self._pending_nv_future = None

    async def _fetch_now_playing_burst(self) -> None:
        """Quickly fetch metadata when playback starts.

        Triggered when MB#49 position pushes start arriving but we don't
        yet have track info / source / play state. Fires off a tight set
        of GETs to populate the now-playing card without waiting for the
        next 30s coordinator cycle.
        """
        try:
            await self._send(0x01, MB_SOURCE, "")        # 50  Current Source
            await asyncio.sleep(0.05)
            await self._send(0x01, MB_PLAY_STATE, "")    # 51  Play State
            await asyncio.sleep(0.05)
            await self._send(0x01, MB_NOW_PLAYING, "")   # 42  Now Playing JSON
        except Exception as e:
            _LOGGER.debug("Now-playing burst fetch failed: %s", e)

    async def async_get_play_view(self) -> None:
        """Request the current Play View via MB#41 GETUI:PLAY.

        Per spec §9.15, the response arrives in MB#42 with the play view
        JSON (track info, artwork, transport state).
        """
        await self._send(0x02, MB_BROWSE, "GETUI:PLAY")

    async def async_get_home_view(self) -> None:
        """Request the Browse Home View via MB#41 GETUI:HOME.

        Per spec §9.15, the response arrives in MB#42 with the home view
        JSON listing available browseable sources (USB, Airable, DMR, etc).
        """
        await self._send(0x02, MB_BROWSE, "GETUI:HOME")

    async def async_request_favourites(self) -> None:
        if not self.supports_favourites:
            return
        await self._send(0x02, MB_FAVOURITES, "FAV_LIST")

    # ── Commands ───────────────────────────────────────────────────────────

    async def async_set_volume(self, level: int) -> None:
        # RID 0x0000 reaches the MCU gain write. RID 0xAAAA may only update
        # the LS-side state without changing audible volume.
        level = max(0, min(100, level))
        if level > 0:
            self._last_nonzero_volume = level
        await self._send(
            0x02,
            MB_VOLUME,
            str(level),
            remote_id=0x0000,
        )
        self.state.volume = level
        self.state.muted = level == 0
        self.state.mute_state_known = True
        self._notify()

    async def async_mute(self, mute: bool) -> None:
        """Mute with volume zero and restore the previous level on unmute."""
        if mute:
            if self.state.volume > 0:
                self._last_nonzero_volume = self.state.volume
            await self.async_set_volume(0)
            return
        await self.async_set_volume(max(1, self._last_nonzero_volume or 50))

    async def async_play(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_PLAY)

    async def async_pause(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_PAUSE)

    async def async_resume(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_RESUME)

    async def async_stop(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_STOP)
        self.state.display_title = ""
        self.state.display_artist = ""
        self._notify()

    async def async_next_track(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_NEXT)
        self._schedule_metadata_refresh()

    async def async_prev_track(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_PREV)
        self._schedule_metadata_refresh()

    async def async_seek(self, position_ms: int) -> None:
        await self._send(0x02, MB_TRANSPORT, f"SEEK:{int(position_ms)}")

    async def async_set_shuffle(self, on: bool) -> None:
        """Toggle shuffle on/off via MB#40."""
        await self._send(0x02, MB_TRANSPORT, "SHUFFLE:ON" if on else "SHUFFLE:OFF")
        self.state.shuffle = bool(on)
        self._notify()
        self._schedule_metadata_refresh(delays=(0.25, 1.0))

    async def async_set_repeat(self, mode: object) -> None:
        """Set repeat mode via MB#40.

        mode: 'off' | 'all' | 'one'
        Per LUCI Tech Note: REPEAT:OFF, REPEAT:ALL, REPEAT:ONE.
        """
        raw_mode = getattr(mode, "value", mode) or "off"
        # Accept both modern StrEnum values ("all") and older enum string
        # representations ("RepeatMode.ALL").
        m = str(raw_mode).rsplit(".", 1)[-1].lower()
        cmd = {"off": "REPEAT:OFF", "all": "REPEAT:ALL", "one": "REPEAT:ONE"}.get(m, "REPEAT:OFF")
        await self._send(0x02, MB_TRANSPORT, cmd)
        self.state.repeat = m if m in ("off", "all", "one") else "off"
        self._notify()
        self._schedule_metadata_refresh(delays=(0.25, 1.0))

    def _schedule_metadata_refresh(
        self,
        delays: tuple[float, ...] = (0.15, 0.75, 1.5),
        *,
        include_play_view: bool = False,
    ) -> None:
        """Fetch changing track metadata before the normal MB#42 cadence."""
        if self._metadata_refresh_task and not self._metadata_refresh_task.done():
            self._metadata_refresh_task.cancel()

        async def _refresh() -> None:
            try:
                elapsed = 0.0
                for target in delays:
                    await asyncio.sleep(max(0.0, target - elapsed))
                    elapsed = target
                    await self._send(0x01, MB_NOW_PLAYING, "")
                    if include_play_view:
                        # LS9 radio services commonly refresh their metadata
                        # only through the Play View request. Its response is
                        # delivered through the same MB#42 parser.
                        await self._send(0x02, MB_BROWSE, "GETUI:PLAY")
            except asyncio.CancelledError:
                pass
            except Exception as err:
                _LOGGER.debug("Metadata refresh failed: %s", err)

        self._metadata_refresh_task = asyncio.create_task(_refresh())

    def _clear_now_playing_for_source(self, source_id: int) -> None:
        """Discard metadata owned by the previous playback source."""
        self._cancel_artwork_refresh()
        self.state.title = ""
        self.state.artist = ""
        self.state.album = ""
        self.state.artwork_url = ""
        self.state.artwork_revision += 1
        self.state.duration_ms = 0
        self.state.position_ms = 0
        self.state.position_updated_at = 0.0
        self.state.is_live = False
        self.state.shuffle = False
        self.state.repeat = "off"
        if source_id != 17:
            self.state.last_played_url = ""
            self.state.display_title = ""
            self.state.display_artist = ""
            self._stale_direct_url_metadata = None

    def set_playback_display(self, title: str, artist: str = "") -> None:
        """Set integration-owned player text for scheduled/local playback."""
        self.state.display_title = str(title or "").strip()
        self.state.display_artist = str(artist or "").strip()
        self._notify()

    @staticmethod
    def _url_display_title(url: str) -> str:
        """Create a readable fallback title for an arbitrary URL."""
        try:
            parsed = urlparse(url)
            name = unquote(parsed.path.rsplit("/", 1)[-1]).strip()
            if name:
                if "." in name:
                    name = name.rsplit(".", 1)[0]
                return name.replace("-", " ").replace("_", " ").strip()
            if parsed.hostname:
                return parsed.hostname
        except (TypeError, ValueError):
            pass
        return "Direct URL"

    async def async_play_url(
        self, url: str, *, title: str | None = None, artist: str | None = None
    ) -> None:
        """Push a direct stream URL to the speaker (MB#41 DIRECT).

        Match the working Control4 path exactly: MB#41 SET with
        PLAYITEM:DIRECT:<url>. MB#50 is source feedback, not a source-switch
        command, so no synthetic MB#50 SET preflight is sent.

        After sending the URL, schedule a metadata refresh so the HA
        media player shows the new track info promptly.
        """
        previous_metadata = (
            self.state.title,
            self.state.artist,
            self.state.album,
            self.state.artwork_url,
        )
        self._stale_direct_url_metadata = (
            previous_metadata if any(previous_metadata) else None
        )
        self._clear_now_playing_for_source(17)
        # Store the URL so the media player can display it as a friendly
        # title while metadata is being fetched
        self.state.last_played_url = url
        self.state.display_title = str(
            title or self._url_display_title(url)
        ).strip()
        self.state.display_artist = str(artist or "Direct URL").strip()
        self._notify()
        await self._send(0x02, MB_BROWSE, f"PLAYITEM:DIRECT:{url}")
        # Trigger several metadata refreshes — first immediate (helps if
        # the speaker is still on the old source), then after a delay
        # (gives MB#10/MB#11 auth flow time to complete and source to
        # switch to 17 Direct URL).
        async def _refresh():
            try:
                await asyncio.sleep(0.8)
                await self._send(0x01, MB_NOW_PLAYING, "")  # MB#42 GET
                await self._send(0x01, MB_SOURCE, "")        # MB#50 GET
                await self._send(0x01, MB_PLAY_STATE, "")    # MB#51 GET
                await asyncio.sleep(2.0)
                await self._send(0x01, MB_NOW_PLAYING, "")  # second refresh
            except Exception as e:
                _LOGGER.debug("play_url metadata refresh failed: %s", e)
        asyncio.create_task(_refresh())

    async def async_play_favourite(self, slot: int) -> None:
        """Play a saved favourite by slot (MB#70)."""
        if not self.supports_favourites:
            return
        await self._send(0x02, MB_FAVOURITES, f"FAV_PLAY:{int(slot)}")

    async def async_save_favourite(self, slot: int) -> None:
        """Save the currently-playing entry to a favourite slot (MB#70).

        Per Lithe API_NEW page 23 (§5.4 Save & Resume Playback):
            SET MB#70 "FAV_SAVE:<slot>"

        The currently-active playback entry must be a valid saveable
        source (Spotify Connect, Airable, etc.). Embedded chimes and
        Direct URL cues cannot be saved.
        """
        if not self.supports_favourites:
            return
        slot = max(1, min(40, int(slot)))
        await self._send(0x02, MB_FAVOURITES, f"FAV_SAVE:{slot}")
        # Refresh favourite list so UI reflects the new entry
        await asyncio.sleep(0.2)
        await self._send(0x02, MB_FAVOURITES, "FAV_LIST")

    async def async_set_name(self, name: str) -> None:
        await self._send(0x02, MB_DEVICE_NAME, name)

    async def async_play_chime(self, chime_number: int) -> None:
        """Trigger an embedded audio cue via MB#80.

        Per LUCI v14.1 spec §6.45 (Tx_MB#80 Play Audio Index):

            Command:  0xAAAA SET 80 NA <"play N">     for single-digit slots
            Response: 0xAAAA SET 80 success/failure
                      data field = SUCCESS | NI | FILE_NOT_FOUND
              - SUCCESS         — valid play accepted, audio cue triggered
              - NI              — index out of range
              - FILE_NOT_FOUND  — index valid but no audio file installed

        That's the complete host protocol. The working Control4 driver does
        not send MB#82 AUDIOPATH_OPEN; MB#82 is treated as lifecycle feedback.

        Source-blocking caveat (per LUCI spec §10.35 MB#494 Cast Setup):
          "If the device's audio path is assigned to external sources,
           the user might not hear the audio test tone played by the
           LS9... this notification serves the purpose of changing the
           audio path back to LS9."

        External sources (Spotify, AirPlay, Cast) can silently block
        chime output. The documented remedy is to switch the source
        away first (e.g. SET MB#50 to a local source). We don't
        auto-do this because it'd interrupt user-driven playback.
        """
        # PRO 2 exposes 14 installed assets. Its parser accepts indexed
        # commands for 1-9 and legacy asset names for two-digit slots.
        n = max(1, min(MAX_CHIME_SLOT, int(chime_number)))
        now = asyncio.get_event_loop().time()
        sock_state = "no_writer" if self._writer is None else (
            "closing" if self._writer.is_closing() else "open"
        )

        _LOGGER.info(
            "CHIME-DIAG slot=%d sock=%s connected=%s play_state=%s source=%d",
            n, sock_state, self.state.connected,
            self.state.play_state, self.state.source_id,
        )

        # Refuse if socket isn't actually open — gives clear log feedback
        # instead of silent no-op.
        if sock_state != "open" or not self.state.connected:
            _LOGGER.warning(
                "CHIME-SKIP slot=%d — socket not ready (%s, connected=%s). "
                "Try again in 1-2 seconds.",
                n, sock_state, self.state.connected,
            )
            return

        try:
            # PRO 2 firmware has two MB#80 cue parsers in circulation. Slots
            # 1-9 use the current indexed command; all two-digit slots use
            # the legacy embedded asset name that made slot 10 reliable.
            payload = f"song{n}.wav" if n >= 10 else f"play {n}"
            await self._send(0x02, MB_CHIME, payload)
            _LOGGER.info("CHIME-DIAG slot=%d MB#80 payload=%r", n, payload)
            self._last_chime_mbid = MB_CHIME
        except Exception as e:
            _LOGGER.warning("Chime send failed: %s", e)

        self._last_chime_time = now

    async def async_bluetooth(self, command: str) -> None:
        """BT command: ON / OFF / ENTPAIR / DISCONNECT."""
        if command in ("ON", "OFF"):
            enabled = command == "ON"
            if self.use_tls:
                # Current Lithe apps control LS10 Bluetooth through the raw
                # HOST-MCU tunnel, not LUCI MB#209.
                await self._async_bluetooth_mcu_tunnel(enabled)
                return
            url = f"http://{self.host}/goform/SetBluetoothmode"
            value = "Bluetooth_ON" if enabled else "Bluetooth_OFF"
            async with self._bluetooth_lock:
                self._bluetooth_target = enabled
                self._bluetooth_target_until = time.monotonic() + 8.0
                last_observed: bool | None = None

                # The service occasionally acknowledges before the setting is
                # applied. Poll its web flag and repeat the POST once when the
                # old value remains visible.
                for attempt in range(2):
                    try:
                        timeout = aiohttp.ClientTimeout(total=5)
                        async with aiohttp.ClientSession(timeout=timeout) as session:
                            async with session.post(
                                url,
                                data={"Bluetooth": value},
                                headers={
                                    "Cache-Control": "no-cache",
                                    "Connection": "close",
                                },
                            ) as response:
                                body = await response.text()
                                if response.status >= 400:
                                    raise RuntimeError(
                                        "Bluetooth HTTP control failed "
                                        f"({response.status}): {body[:120]}"
                                    )
                    except (
                        aiohttp.ClientError,
                        asyncio.TimeoutError,
                        OSError,
                        RuntimeError,
                    ) as err:
                        _LOGGER.warning(
                            "Bluetooth %s HTTP control unavailable on %s: %s; "
                            "using LUCI MB#209 fallback",
                            command,
                            self.host,
                            err,
                        )
                        await self._async_bluetooth_luci_fallback(command, enabled)
                        return

                    # Show the requested state immediately, but verify it
                    # before declaring the operation complete.
                    self.state.bt_enabled = enabled
                    self.state.bt_status = value
                    self._notify()

                    for delay in (0.25, 0.5, 0.9):
                        await asyncio.sleep(delay)
                        observed = await self._read_bluetooth_http_state()
                        if observed is None:
                            continue
                        last_observed = observed
                        if observed == enabled:
                            self._bluetooth_http_state_known = True
                            self.state.bt_enabled = observed
                            self.state.bt_status = value
                            self._bluetooth_target_until = time.monotonic() + 2.0
                            self._notify()
                            return

                    if attempt == 0 and last_observed != enabled:
                        _LOGGER.warning(
                            "Bluetooth %s not visible on %s yet; retrying HTTP control",
                            command,
                            self.host,
                        )

                # A stale web flag or missing getbtvalue is not proof that the
                # POST failed. Embedded web pages are cached on some builds.
                # Confirm the requested state through the live LUCI path.
                _LOGGER.warning(
                    "Bluetooth %s HTTP state on %s was %s; applying LUCI "
                    "MB#209 fallback",
                    command,
                    self.host,
                    "unavailable" if last_observed is None else (
                        "ON" if last_observed else "OFF"
                    ),
                )
                await self._async_bluetooth_luci_fallback(command, enabled)
                return

        # Pairing and disconnect remain LUCI MB#209 operations; the HTTP
        # endpoint only defines service ON/OFF.
        await self._send(0x02, MB_BLUETOOTH, command)

    async def _async_bluetooth_mcu_tunnel(self, enabled: bool) -> None:
        """Set LS10 Bluetooth using the app payload over the LUCI MCU carrier."""
        value = 1 if enabled else 0
        setter = bytes([
            0x00, 0x04, (BT_MCU_STATE + 0x02 + value) & 0xFF,
            BT_MCU_STATE, 0x02, value,
        ])
        getter = bytes([
            0x00, 0x03, (BT_MCU_STATE + 0x01) & 0xFF,
            BT_MCU_STATE, 0x01,
        ])
        self._bluetooth_target = enabled
        self._bluetooth_target_until = time.monotonic() + 5.0
        self.state.bt_enabled = enabled
        self.state.bt_status = "Bluetooth_ON" if enabled else "Bluetooth_OFF"
        self._notify()
        try:
            # This speaker rejects attempts to create another raw MB#111
            # listener while its MCU tunnel is occupied. MB#112 is the working
            # LUCI carrier used by HA's audible EQ writes, so carry the exact
            # app packet through it without opening or locking port 4444.
            for payload in (setter, getter):
                header = struct.pack(
                    "<HBHBHH",
                    DSP_REMOTE_ID,
                    0x02,
                    MB_DSP,
                    0,
                    0x0000,
                    len(payload),
                )
                await self._write_packet(header + payload + b"\x00")
                await asyncio.sleep(0.1)
            _LOGGER.info(
                "Bluetooth MCU command %s sent through MB#112 on %s",
                "ON" if enabled else "OFF",
                self.host,
            )
        except Exception as err:
            _LOGGER.warning(
                "Bluetooth MCU command %s failed on %s: %s",
                "ON" if enabled else "OFF",
                self.host,
                err,
            )

    async def _async_bluetooth_luci_fallback(
        self, command: str, enabled: bool
    ) -> None:
        """Apply Bluetooth through LUCI and verify its MB#50 source change."""
        # The LUCI specification defines MB#209 ON/OFF as source actions and
        # MB#50 as their status notification. MB#210 is DMR restart, not
        # Bluetooth status, so polling it can leave the UI stuck or reversed.
        remote_id = 0xAAAA
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending_bluetooth_future = future
        self._bluetooth_target = enabled
        self._bluetooth_target_until = time.monotonic() + 5.0
        self.state.bt_enabled = enabled
        self.state.bt_status = "Bluetooth_ON" if enabled else "Bluetooth_OFF"
        self._notify()
        await self._send(0x02, MB_BLUETOOTH, command, remote_id=remote_id)
        await asyncio.sleep(0.15)
        await self._send(0x01, MB_SOURCE, "", remote_id=remote_id)
        try:
            observed = await asyncio.wait_for(future, timeout=2.0)
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Bluetooth %s on %s was not confirmed by MB#50",
                command,
                self.host,
            )
        else:
            if observed != enabled:
                _LOGGER.warning(
                    "Bluetooth %s on %s was rejected; MB#50 reports %s",
                    command,
                    self.host,
                    "ON" if observed else "OFF",
                )
            else:
                _LOGGER.info(
                    "Bluetooth %s confirmed on %s by MB#50",
                    command,
                    self.host,
                )
        finally:
            if self._pending_bluetooth_future is future:
                self._pending_bluetooth_future = None
            # Keep the requested state briefly if firmware delays MB#50. The
            # coordinator's next source refresh will replace it with reality.
            self._bluetooth_target_until = time.monotonic() + 3.0

    @staticmethod
    def _parse_bluetooth_http_state(body: str) -> bool | None:
        """Extract the Bluetooth service flag from the device web page."""
        match = re.search(
            r"getbtvalue\s*=\s*['\"]?([01])['\"]?",
            body,
            re.IGNORECASE,
        )
        return match.group(1) == "1" if match else None

    async def _read_bluetooth_http_state(self) -> bool | None:
        """Read the Bluetooth service flag without mutating speaker state."""
        try:
            timeout = aiohttp.ClientTimeout(total=2)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"http://{self.host}/?bt_state={time.monotonic_ns()}",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "close",
                    },
                ) as response:
                    if response.status != 200:
                        return None
                    body = await response.text()
            return self._parse_bluetooth_http_state(body)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            _LOGGER.debug("Bluetooth HTTP state unavailable for %s", self.host)
            return None

    async def _refresh_bluetooth_http_state(self) -> None:
        """Refresh Bluetooth state from the authoritative device web flag."""
        enabled = await self._read_bluetooth_http_state()
        if enabled is None:
            return
        changed = self.state.bt_enabled != enabled
        self._bluetooth_http_state_known = True
        self.state.bt_enabled = enabled
        self.state.bt_status = "Bluetooth_ON" if enabled else "Bluetooth_OFF"
        if changed:
            self._notify()

    @staticmethod
    def _wifi_band_from_value(value, *, channel: bool = False) -> str | None:
        """Normalize a Cast frequency, channel, or band label."""
        text = str(value).strip().lower().replace(" ", "")
        if not text:
            return None
        if "2.4" in text or text in {"2g", "2ghz", "24g", "24ghz"}:
            return "2.4 GHz"
        if text in {"5g", "5ghz"}:
            return "5 GHz"
        if text in {"6g", "6ghz"}:
            return "6 GHz"
        try:
            number = float(re.sub(r"[^0-9.]", "", text))
        except ValueError:
            return None
        if channel:
            if 1 <= number <= 14:
                return "2.4 GHz"
            if 32 <= number <= 177:
                return "5 GHz"
            return None
        if 2.3 <= number <= 2.6 or 2300 <= number <= 2600:
            return "2.4 GHz"
        if 4.9 <= number <= 5.9 or 4900 <= number <= 5900:
            return "5 GHz"
        if 5.925 <= number <= 7.125 or 5925 <= number <= 7125:
            return "6 GHz"
        return None

    def _apply_cast_wifi_info(self, data: dict) -> bool:
        """Apply Wi-Fi telemetry from a Cast eureka_info response."""
        flattened: list[tuple[str, object]] = []

        def _walk(value) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    flattened.append((str(key).lower(), child))
                    _walk(child)
            elif isinstance(value, list):
                for child in value:
                    _walk(child)

        _walk(data)
        changed = False

        for key, value in flattened:
            if key not in {"signal_level", "signallevel", "rssi", "wifi_rssi"}:
                continue
            try:
                signal = int(float(value))
            except (TypeError, ValueError):
                continue
            if -127 <= signal < 0:
                if self.state.wifi_rssi_dbm != signal:
                    changed = True
                self.state.wifi_rssi_dbm = signal
                self.state.wifi_rssi_source = "Cast eureka_info"
                break

        band = None
        for key, value in flattened:
            if key in {"wifi_band", "wifiband", "band"}:
                band = self._wifi_band_from_value(value)
            elif key in {"wifi_frequency", "frequency", "freq", "frequency_mhz"}:
                band = self._wifi_band_from_value(value)
            elif key in {"wifi_channel", "channel"}:
                band = self._wifi_band_from_value(value, channel=True)
            if band:
                break
        if band:
            if self.state.wifi_band != band:
                changed = True
            self.state.wifi_band = band

        if not self.state.ssid:
            for key, value in flattened:
                if key == "ssid" and isinstance(value, str) and value.strip():
                    self.state.ssid = value.strip()
                    changed = True
                    break
        return changed

    async def _refresh_cast_wifi_state(self) -> None:
        """Use read-only Cast diagnostics when LS10 omits MB#151."""
        now = time.monotonic()
        if now < self._next_cast_wifi_probe:
            return
        try:
            timeout = aiohttp.ClientTimeout(total=2)
            url = f"http://{self.host}:8008/setup/eureka_info?options=detail"
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        self._next_cast_wifi_probe = now + 300.0
                        return
                    data = await response.json(content_type=None)
            self._next_cast_wifi_probe = now + 25.0
            if isinstance(data, dict) and self._apply_cast_wifi_info(data):
                self._notify()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
            self._next_cast_wifi_probe = now + 300.0
            _LOGGER.debug("Cast Wi-Fi diagnostics unavailable for %s", self.host)

    async def async_reboot(self) -> None:
        """Request speaker reboot.

        Per LUCI spec §9.42–9.43, MB#114 and MB#115 form a request/grant
        pair where LSx asks the HOST to reboot it (during OTA). There is
        NO documented host-initiated "reboot now" command in the LUCI
        protocol.

        Sending MB#114 from us is a protocol violation that the speaker
        ignores. The only reliable way to reboot is via the Lithe app
        or HTTP Cast endpoint, neither of which is exposed via LUCI.
        """
        _LOGGER.warning(
            "Reboot via LUCI is not supported by the speaker firmware. "
            "Use the Lithe Audio app or power-cycle the speaker manually."
        )

    async def async_factory_reset(self) -> None:
        """Factory reset via MB#150."""
        await self._send(0x02, MB_FACTORY_RESET, "")

    async def async_dsp_command(self, sub_mb: int, value: int, field: int = 0x02) -> None:
        """Send a DSP command via MB#112 tunnel (LS10 only).

        Sub-packet shape: 00 04 [sub_mb hi] [sub_mb lo] [field] [value].
        RemoteID 0x0000 is mandatory: 0xAAAA returns SUCCESS but bypasses
        the MCU/DSP, producing no audible change.
        """
        # Captured firmware uses sign-magnitude nibbles for DSP controls:
        # F1=-1, F2=-2, ... F6=-6 (not two's complement FF/FE/.../FA).
        byte_val = value & 0xFF if value >= 0 else 0xF0 | min(0x0F, abs(value))
        sub = bytes([
            0x00, 0x04,
            (sub_mb >> 8) & 0xFF, sub_mb & 0xFF,
            field & 0xFF,
            byte_val,
        ])
        # DataLen counts payload bytes only — terminator is separate
        data_len = len(sub)
        header = struct.pack(
            "<HBHBHH", DSP_REMOTE_ID, 0x02, MB_DSP, 0, 0x0000, data_len
        )
        pkt = header + sub + b"\x00"  # terminator per vendor §10.2

        if not self._writer:
            _LOGGER.warning(
                "DSP TX sub=0x%02x field=0x%02x val=%d DROPPED — no writer",
                sub_mb, field, value,
            )
            return
        if self._writer.is_closing():
            _LOGGER.warning(
                "DSP TX sub=0x%02x field=0x%02x val=%d DROPPED — writer closing",
                sub_mb, field, value,
            )
            return

        hex_preview = " ".join(f"{b:02X}" for b in pkt)
        _LOGGER.info(
            "TX DSP MB#112 sub=0x%02x field=0x%02x val=%d (%d bytes): %s",
            sub_mb, field, value, len(pkt), hex_preview,
        )
        try:
            await self._write_packet(pkt)
        except Exception as e:
            _LOGGER.warning(
                "DSP TX sub=0x%02x field=0x%02x val=%d write FAILED: %s",
                sub_mb, field, value, e,
            )
        else:
            # This firmware replies SUCCESS to the sending client but sends
            # the binary MB#112 state record only to the other registered
            # clients. Record a successfully transmitted local command so it
            # can be restored after HA restarts. Any later speaker/app push
            # remains authoritative and overwrites it.
            self._record_local_dsp_command(sub_mb, value, field)
            if sub_mb == DSP_EQ and self.state.connected:
                self._schedule_dsp_readback()

    @staticmethod
    def _build_io1_tunnel_packet(
        command: int, payload: bytes, field: int = 0x02
    ) -> bytes:
        """Build the variable-length packet used by iO1 MCU controls."""
        body = bytes([command & 0xFF, field & 0xFF]) + payload
        # Length includes checksum + command/field/payload, but excludes the
        # two-byte length prefix itself (TunnelCommandUtils.getPacket).
        return bytes([0x00, len(body) + 1, sum(body) & 0xFF]) + body

    async def _async_io1_command(self, command: int, payload: bytes) -> bytes:
        """Send an iO1 command on the same raw tunnel used by the app."""
        packet = self._build_io1_tunnel_packet(command, payload)
        response = b""
        tunnel_writer: asyncio.StreamWriter | None = None
        try:
            async with self._dsp_refresh_lock:
                tunnel_reader, tunnel_writer = await self._async_open_dsp_tunnel()
                tunnel_writer.write(packet)
                await tunnel_writer.drain()
                try:
                    response = await asyncio.wait_for(
                        tunnel_reader.read(4096), timeout=0.75
                    )
                except asyncio.TimeoutError:
                    pass
        except Exception as err:
            # Preserve the existing MB112 route for firmware that does not
            # expose the app's raw TCP listener.
            _LOGGER.debug(
                "iO1 DSP tunnel unavailable; using MB112 fallback: %s", err
            )
            header = struct.pack(
                "<HBHBHH", DSP_REMOTE_ID, 0x02, MB_DSP, 0, 0x0000,
                len(packet),
            )
            if self._writer and not self._writer.is_closing():
                await self._write_packet(header + packet + b"\x00")
        finally:
            if tunnel_writer is not None:
                tunnel_writer.close()
                try:
                    await tunnel_writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

        if response:
            self._handle_push(MB_DSP, response, DSP_REMOTE_ID)
        return response

    async def async_io1_eq_command(self, band: int, value: int) -> None:
        """Set one iO1 2/4/6 kHz band using [band, gain + 6]."""
        band = max(IO1_TREBLE_LOW_BAND, min(IO1_TREBLE_HIGH_BAND, int(band)))
        value = max(-6, min(6, int(value)))
        revision_before = self.state.dsp_feedback_revision
        await self._async_io1_command(
            IO1_TREBLE_GAIN, bytes([band, value + 6])
        )
        if self.state.dsp_feedback_revision == revision_before:
            attr = {
                IO1_TREBLE_LOW_BAND: "dsp_treble",
                IO1_TREBLE_MID_BAND: "dsp_mid",
                IO1_TREBLE_HIGH_BAND: "dsp_bass",
            }[band]
            setattr(self.state, attr, value)
            self.state.dsp_state_source = "local"
            self.state.dsp_feedback_revision += 1
            self._notify()
        if self.state.connected:
            self._schedule_dsp_readback()

    async def async_io1_preset_command(self, preset: int) -> None:
        """Set the dedicated iO1 Outdoor/Indoor/Pendent preset."""
        preset = max(0, min(2, int(preset)))
        revision_before = self.state.dsp_feedback_revision
        await self._async_io1_command(IO1_PRESET, bytes([preset]))
        if self.state.dsp_feedback_revision == revision_before:
            self.state.dsp_eq = preset
            self.state.dsp_state_source = "local"
            self.state.dsp_feedback_revision += 1
            self._notify()
        if self.state.connected:
            self._schedule_dsp_readback()

    def _schedule_dsp_readback(self) -> None:
        """Debounce a live readback after a preset changes derived EQ bands."""
        if self._dsp_readback_task and not self._dsp_readback_task.done():
            self._dsp_readback_task.cancel()
        self._dsp_readback_task = asyncio.create_task(
            self._delayed_dsp_readback()
        )

    async def _delayed_dsp_readback(self) -> None:
        """Read MCU-derived values after the preset has finished applying."""
        try:
            for delay in (0.6, 1.5):
                await asyncio.sleep(delay)
                if not self.state.connected:
                    return
                if await self.async_dsp_refresh():
                    return
        except asyncio.CancelledError:
            pass
        except Exception as err:
            _LOGGER.debug("DSP preset readback failed: %s", err)

    def _record_local_dsp_command(
        self, sub_mb: int, value: int, field: int
    ) -> None:
        """Mirror a successfully written DSP command into persistent state."""
        from .const import (
            DSP_BALANCE, DSP_BASS_FIELD, DSP_EQ, DSP_EQ_BANDS,
            DSP_HIGHPASS, DSP_LOUDNESS, DSP_LOUDNESS_GAIN,
            DSP_MID_FIELD, DSP_NIGHTMODE, DSP_OUTPUT,
            DSP_TREBLE_FIELD, DSP_TUNING,
            PRODUCT_MICRO, SUB_DSP_CROSSOVER, SUB_DSP_GAIN,
            SUB_DSP_LOWPASS, SUB_DSP_PHASE, SUB_DSP_PROFILE,
        )

        attr: str | None = None
        state_value = value
        if self.product == PRODUCT_MICRO:
            attr = {
                SUB_DSP_PROFILE: "sub_profile",
                SUB_DSP_GAIN: "sub_gain",
                SUB_DSP_CROSSOVER: "sub_crossover",
                SUB_DSP_PHASE: "sub_phase",
                SUB_DSP_LOWPASS: "sub_lowpass",
            }.get(sub_mb)
        elif sub_mb == DSP_EQ_BANDS:
            attr = {
                DSP_BASS_FIELD: "dsp_bass",
                DSP_MID_FIELD: "dsp_mid",
                DSP_TREBLE_FIELD: "dsp_treble",
            }.get(field)
        else:
            attr = {
                DSP_EQ: "dsp_eq",
                DSP_LOUDNESS: "dsp_loudness",
                DSP_LOUDNESS_GAIN: "dsp_loudness_gain",
                DSP_NIGHTMODE: "dsp_nightmode",
                DSP_BALANCE: "dsp_balance",
                DSP_OUTPUT: "dsp_output",
                DSP_HIGHPASS: "dsp_highpass",
                DSP_TUNING: "dsp_tuning",
            }.get(sub_mb)
            if sub_mb == DSP_LOUDNESS_GAIN:
                state_value = value - 10

        if attr is None:
            return
        setattr(self.state, attr, state_value)
        if sub_mb == DSP_EQ:
            self._apply_ls10_fixed_eq_curve(value, reset_normal=True)
        self.state.dsp_state_source = "local"
        self.state.dsp_feedback_revision += 1
        self._notify()

    def _apply_ls10_fixed_eq_curve(
        self, preset: int, *, reset_normal: bool = False
    ) -> bool:
        """Mirror the fixed LS10 preset curve displayed by the Lithe app."""
        if self.product not in {PRODUCT_PRO2, PRODUCT_V3}:
            return False
        bands = LS10_FIXED_EQ_PRESET_BANDS.get(preset)
        if bands is None:
            if preset != 0 or not reset_normal:
                return False
            bands = (0, 0, 0)
        treble, mid, bass = bands
        self.state.dsp_treble = treble
        self.state.dsp_mid = mid
        self.state.dsp_bass = bass
        return True

    async def async_dsp_refresh(self) -> bool:
        """Read the current HOST-MCU settings through the MB#111 tunnel.

        MB#112 on the TLS LUCI socket is the LS-to-MCU transport envelope. Its
        SUCCESS acknowledgement is not the MCU reply. The Lithe app first
        creates an MB#111 TCP tunnel, sends the raw 0x15 request through that
        socket, and reads the MCU's 00 04 state records there.
        """
        if not self._writer or self._writer.is_closing():
            _LOGGER.debug("DSP live refresh skipped because writer is unavailable")
            return False

        def _get_packet(
            sub_mb: int, field: int = 0x01, payload: bytes = b""
        ) -> bytes:
            body = bytes([sub_mb & 0xFF, field & 0xFF]) + payload
            return bytes([0x00, len(body) + 1, sum(body) & 0xFF]) + body

        requests = [_get_packet(DSP_STATUS_ALL)]
        if self.product == PRODUCT_IO1:
            # The app selects each 2/4/6 kHz band through TREBLE_GAIN_GET and
            # receives [band index, unsigned gain+6].  The ordinary 0x09
            # Treble/Mid/Bass getters are PRO/V3 commands and are only echoed
            # by iO1 firmware.
            requests.extend((
                _get_packet(
                    IO1_TREBLE_GAIN, payload=bytes([IO1_TREBLE_LOW_BAND])
                ),
                _get_packet(
                    IO1_TREBLE_GAIN, payload=bytes([IO1_TREBLE_MID_BAND])
                ),
                _get_packet(
                    IO1_TREBLE_GAIN, payload=bytes([IO1_TREBLE_HIGH_BAND])
                ),
                _get_packet(IO1_PRESET),
                _get_packet(DSP_BALANCE),
                _get_packet(DSP_OUTPUT),
            ))
        async with self._dsp_refresh_lock:
            tunnel_writer: asyncio.StreamWriter | None = None
            try:
                tunnel_reader, tunnel_writer = await self._async_open_dsp_tunnel()

                _LOGGER.debug(
                    "TX DSP tunnel live-state requests %s",
                    ", ".join(
                        " ".join(f"{byte:02X}" for byte in request)
                        for request in requests
                    ),
                )

                chunks: list[bytes] = []
                if self.product == PRODUCT_IO1:
                    # iO1 drops later requests when several are written before
                    # the previous response is consumed. This mirrors the app:
                    # select one band, read it, then select the next band.
                    for request in requests:
                        tunnel_writer.write(request)
                        await tunnel_writer.drain()
                        try:
                            chunk = await asyncio.wait_for(
                                tunnel_reader.read(4096), timeout=0.75
                            )
                        except asyncio.TimeoutError:
                            continue
                        if chunk:
                            chunks.append(chunk)
                else:
                    for request in requests:
                        tunnel_writer.write(request)
                    await tunnel_writer.drain()
                    deadline = asyncio.get_running_loop().time() + 3.0
                    while asyncio.get_running_loop().time() < deadline:
                        timeout = 1.0 if not chunks else 0.45
                        try:
                            chunk = await asyncio.wait_for(
                                tunnel_reader.read(4096), timeout=timeout
                            )
                        except asyncio.TimeoutError:
                            break
                        if not chunk:
                            break
                        chunks.append(chunk)

                if chunks:
                    raw = b"".join(chunks)
                    _LOGGER.info(
                        "RX DSP live tunnel report: %d bytes", len(raw)
                    )
                    revision_before = self.state.dsp_feedback_revision
                    self._handle_push(MB_DSP, raw, DSP_REMOTE_ID)
                    updated = self.state.dsp_feedback_revision > revision_before
                    loudness_gain_present = (
                        b"\x00\x04\x00\x34" in raw
                        or b"\x00\x05\x00\x34" in raw
                    )
                    if self.product == PRODUCT_PRO2 and not loudness_gain_present:
                        _LOGGER.warning(
                            "DSP startup report from %s was partial: loudness "
                            "gain 0x34 was missing; retrying",
                            self.host,
                        )
                        return False
                    if self.product in {PRODUCT_PRO2, PRODUCT_V3, PRODUCT_IO1}:
                        # Some LS10 builds answer unrelated controls but omit
                        # every EQ field.  That partial response must not be
                        # reported as a successful EQ synchronisation.
                        response_subcommands: set[int] = set()
                        io1_bands: set[int] = set()
                        index = 0
                        while index + 5 <= len(raw):
                            packet_length = int.from_bytes(
                                raw[index:index + 2], "big"
                            ) + 2
                            if (
                                raw[index] == 0x00
                                and packet_length >= 5
                                and index + packet_length <= len(raw)
                            ):
                                command = raw[index + 3]
                                response_subcommands.add(command)
                                if (
                                    command == IO1_TREBLE_GAIN
                                    and packet_length >= 7
                                ):
                                    io1_bands.add(raw[index + 5])
                                index += packet_length
                            else:
                                index += 1
                        if self.product == PRODUCT_IO1:
                            eq_state_present = (
                                IO1_PRESET in response_subcommands
                                and io1_bands == {
                                    IO1_TREBLE_LOW_BAND,
                                    IO1_TREBLE_MID_BAND,
                                    IO1_TREBLE_HIGH_BAND,
                                }
                            )
                        else:
                            eq_state_present = {
                                DSP_EQ_BANDS, DSP_EQ
                            }.issubset(response_subcommands)
                        if not eq_state_present:
                            _LOGGER.debug(
                                "LS10 DSP report from %s omitted EQ preset/bands; "
                                "keeping the last confirmed EQ state and retrying",
                                self.host,
                            )
                            return False
                    if not updated:
                        _LOGGER.warning(
                            "DSP startup report from %s contained no decoded "
                            "state; retrying",
                            self.host,
                        )
                        return False
                    return True
                else:
                    # Several firmware builds accept the tunnel request but do
                    # not implement the full-state report. Live MB#112 pushes
                    # still work, so this is a capability miss, not a fault.
                    _LOGGER.debug("DSP live tunnel returned no state records")
            except Exception as err:
                # DSP polling must not take the whole media player offline.
                _LOGGER.debug("DSP live tunnel refresh unavailable: %s", err)
            finally:
                if tunnel_writer is not None:
                    tunnel_writer.close()
                    try:
                        await tunnel_writer.wait_closed()
                    except (ConnectionError, OSError):
                        pass
        return False

    async def _async_open_dsp_tunnel(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Connect to the HOST-MCU tunnel, creating its listener if needed."""
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(self.host, DSP_TUNNEL_PORT),
                timeout=0.75,
            )
        except (OSError, asyncio.TimeoutError):
            await self._send(
                0x02,
                MB_TUNNEL_START,
                str(DSP_TUNNEL_PORT),
                remote_id=DSP_REMOTE_ID,
            )
            await asyncio.sleep(0.2)
            return await asyncio.wait_for(
                asyncio.open_connection(self.host, DSP_TUNNEL_PORT),
                timeout=2.0,
            )

    # ── Callbacks ──────────────────────────────────────────────────────────

    def register_callback(self, cb: Callable) -> None:
        if cb not in self._callbacks:
            self._callbacks.append(cb)

    def remove_callback(self, cb: Callable) -> None:
        if cb in self._callbacks:
            self._callbacks.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._callbacks):
            try:
                cb()
            except Exception:
                _LOGGER.debug("Callback error", exc_info=True)

    # ── Read loop and packet parsing ───────────────────────────────────────

    async def _read_loop(self) -> None:
        """Background task: read and parse incoming packets."""
        try:
            while self.state.connected and self._reader:
                try:
                    chunk = await asyncio.wait_for(self._reader.read(4096), timeout=300.0)
                    if not chunk:
                        break
                    self._buf += chunk
                    self._process_buffer()
                except asyncio.TimeoutError:
                    pass
        except Exception as e:
            _LOGGER.debug("Read loop ended: %s", e)
        finally:
            self.state.connected = False
            self._notify()

    def _process_buffer(self) -> None:
        """Parse all complete packets from buffer.

        Strategy:
          1. Read DataLen from offset 8-9 (BE — empirically the speaker
             uses BE on TX).
          2. Calculate packet end at 10 + data_len.
          3. SANITY CHECK: the byte immediately after this packet should
             be either a NUL terminator (0x00) followed by next packet's
             RID, OR the start of the next packet's RID directly (0xAA).
             If neither matches, the parser is misaligned — RESYNC by
             scanning forward for the next plausible packet start.
          4. After a successful sanity check, consume the packet AND any
             trailing NUL terminator before continuing.

        This protects against any single mis-parsed packet propagating
        forever — common cause of "chime command sent but no response"
        because all subsequent responses get glued onto a phantom packet.
        """
        while len(self._buf) >= 10:
            try:
                # Empirical: this firmware sends DataLen as BIG-endian
                # on the wire, despite the vendor Python example showing
                # struct.pack("<H...", ...). v1.1.83 switched to <H and
                # broke parsing entirely (every packet became
                # "Implausible DataLen=N" → resync → disconnect cycle).
                # Reverted to >H which matches actual byte order on wire.
                data_len = struct.unpack_from(">H", self._buf, 8)[0]
            except struct.error:
                break

            # Hard cap: legitimate LUCI payloads are well under 16KB.
            # Anything bigger means misalignment — resync now.
            if data_len > 16384:
                _LOGGER.warning(
                    "Implausible DataLen=%d at buf head — resyncing parser",
                    data_len,
                )
                self._resync_buffer()
                continue

            total = 10 + data_len
            if len(self._buf) < total:
                break  # need more bytes

            # Sanity-check: byte after this packet should be either:
            #   - the LUCI 0x00 packet terminator (per vendor §10.2), OR
            #   - the start of the next packet's RemoteID (0xAA = high byte
            #     of 0xAAAA, or 0x00 = high byte of 0x0000).
            need_sanity = total < len(self._buf)
            if need_sanity:
                next_byte = self._buf[total]
                if next_byte not in (0x00, 0xAA):
                    _LOGGER.warning(
                        "Packet boundary mismatch (next byte 0x%02x after "
                        "DataLen=%d) — resyncing parser",
                        next_byte, data_len,
                    )
                    self._resync_buffer()
                    continue

            # Header fields — empirical wire format is big-endian
            try:
                remote_id = struct.unpack_from(">H", self._buf, 0)[0]
            except struct.error:
                remote_id = 0
            mbid = struct.unpack_from(">H", self._buf, 3)[0]

            # Sanity: MBID must be in the valid LUCI range. Per spec the
            # highest documented MB is around 600 (timezone is 573, cast
            # setup is 494). Anything beyond ~700 is parser garbage.
            if mbid > 700:
                _LOGGER.warning(
                    "Implausible MBID=%d (>700) — resyncing parser",
                    mbid,
                )
                self._resync_buffer()
                continue

            status = self._buf[5]
            payload_bytes = self._buf[10:10 + data_len]
            if mbid == MB_DSP:
                # MB#112 is binary. Preserve signed bytes such as F1 (-1);
                # UTF-8 replacement would destroy them before DSP parsing.
                payload: str | bytes = payload_bytes
            else:
                try:
                    payload = payload_bytes.decode("utf-8", "replace").rstrip("\x00")
                except Exception:
                    payload = ""

            # Consume this packet — do NOT skip trailing NUL.
            # The speaker's packet stream is back-to-back; any byte after
            # `total` is part of the next packet's RID. Skipping bytes here
            # offsets the parser forever.
            # (v1.1.83 tried adding conditional terminator skip but combined
            # with the failed endianness flip it caused complete parser
            # failure. Reverted to original behaviour which is known good.)
            self._buf = self._buf[total:]
            # Reset resync counter — we successfully parsed a packet
            self._resync_count = 0

            self._last_rx_time = asyncio.get_event_loop().time()

            # Track RemoteIDs we see for diagnostics
            if remote_id not in self._seen_remote_ids:
                self._seen_remote_ids.add(remote_id)
                _LOGGER.info(
                    "RID-DIAG New RemoteID 0x%04x first seen on MB#%d: %s",
                    remote_id, mbid, payload[:80],
                )

            _LOGGER.debug(
                "RX MB#%d (rid=0x%04x, status=%d, %d bytes): %s",
                mbid, remote_id, status, data_len, payload[:200],
            )

            if status not in (0, 1):
                _LOGGER.warning(
                    "RX MB#%d returned status=%d (%s). RID=0x%04x payload=%r",
                    mbid, status,
                    {2: "Generic error", 3: "Device not ready",
                     4: "CRC error"}.get(status, f"unknown ({status})"),
                    remote_id, payload[:80],
                )

            self._handle_push(mbid, payload, remote_id)

    def _resync_buffer(self) -> None:
        """Scan forward in self._buf for the next plausible packet header.

        Repeated misalignments indicate persistent corruption — likely
        from the speaker sending data we can't interpret. We track how
        often this happens; if it exceeds threshold, the parser
        disconnects and forces a reconnect.
        """
        self._resync_count += 1
        if self._resync_count > 10:
            _LOGGER.warning(
                "%d resyncs in a row — disconnecting to force clean reconnect",
                self._resync_count,
            )
            self._resync_count = 0
            self._buf = b""
            # Trigger reconnect by closing the writer
            if self._writer and not self._writer.is_closing():
                try:
                    self._writer.close()
                except Exception:
                    pass
            return

        # Plausible header starts with RemoteID = 0xAAAA or 0x0000.
        # We look for either pattern and discard bytes before it.
        i = 1
        while i < len(self._buf) - 1:
            b0 = self._buf[i]
            b1 = self._buf[i+1]
            if (b0 == 0xAA and b1 == 0xAA) or (b0 == 0x00 and b1 == 0x00):
                discarded = i
                self._buf = self._buf[i:]
                _LOGGER.debug(
                    "Resynced — discarded %d bytes to next packet header",
                    discarded,
                )
                return
            i += 1
        # No plausible start found — discard everything and start fresh
        _LOGGER.warning(
            "Resync failed — discarded %d bytes (no plausible packet header)",
            len(self._buf),
        )
        self._buf = b""

    def _handle_push(
        self, mbid: int, payload: str | bytes, remote_id: int = 0
    ) -> None:
        """Handle an incoming message from the speaker.

        Note: every RX is already logged in _process_buffer with its
        RemoteID. We don't repeat the preview here.
        """
        changed = True

        if mbid == MB_PLAYBACK_AUTH:
            # ── Playback Authorisation flow (LUCI spec §9.7) ──
            # Speaker sends MB#10 with the new source ID when a source
            # change is requested (e.g. our PLAYITEM:DIRECT triggers
            # source 17). It then waits 5-7 seconds for our MB#11 grant.
            # If we don't respond (or respond with 0) the new source is
            # DENIED and playback stops silently.
            #
            # We grant unconditionally — denying is reserved for HOST MCUs
            # that need to coordinate gain tables or audio routing before
            # the new source takes over. For HA integration the right
            # default is "allow" so all source switches proceed.
            new_source = payload.strip()
            if self._last_chime_time:
                ms = (asyncio.get_event_loop().time() - self._last_chime_time) * 1000.0
                _LOGGER.info(
                    "MB#10 (PlaybackAuth) new_source=%r +%.1fms — granting via MB#11=1",
                    new_source, ms,
                )
            else:
                _LOGGER.info(
                    "MB#10 (PlaybackAuth) new_source=%r — granting via MB#11=1",
                    new_source,
                )
            # Schedule the grant; respond fast (target 50-500ms per spec)
            asyncio.create_task(self._send(0x02, MB_PLAYBACK_GRANT, "1"))
            return

        elif mbid == MB_DEVICE_NAME:
            new_name = payload.strip()
            # The PRO 2 reports two different names on MB#90:
            #   - Individual identity: "WiFi PRO 23503b8" (or similar)
            #   - Group/zone name: "Kitchen Sub" (when paired/grouped)
            # Both arrive on the same RemoteID, indistinguishable at the
            # protocol level. Prefer the individual name (matches the
            # speaker's hardcoded SSID-derived identity) and ignore group
            # name overwrites to keep HA's device name stable.
            if not self.state.name:
                self.state.name = new_name
            elif new_name and (
                new_name.startswith(("WiFi ", "iO1", "LS10", "LS9", "Lithe"))
                or new_name == self.state.name
            ):
                self.state.name = new_name
            else:
                _LOGGER.debug(
                    "Ignoring MB#90 group-name push %r (keeping %r)",
                    new_name, self.state.name,
                )

        elif mbid == MB_FIRMWARE:
            new_fw = payload.strip()
            # Same dual-response issue as MB#90: PRO 2 reports its own
            # firmware "CR443GP_3713" and also a paired peer's firmware
            # number "15244". Prefer the longer/CR-prefixed string.
            if not self.state.firmware:
                self.state.firmware = new_fw
            elif new_fw and (
                new_fw.startswith(("CR", "LS", "WP"))
                or new_fw == self.state.firmware
                or len(new_fw) > len(self.state.firmware)
            ):
                self.state.firmware = new_fw
            else:
                _LOGGER.debug(
                    "Ignoring MB#5 peer-firmware push %r (keeping %r)",
                    new_fw, self.state.firmware,
                )

        elif mbid == MB_VOLUME:
            try:
                volume = int(payload)
            except ValueError:
                pass
            else:
                self.state.volume = volume
                if volume > 0:
                    self._last_nonzero_volume = volume
                self.state.muted = volume == 0
                self.state.mute_state_known = True

        elif mbid == MB_MUTE:
            mute_state = payload.strip().upper()
            if mute_state in {"1", "MUTE"}:
                self.state.muted = True
                self.state.mute_state_known = True
                _LOGGER.info("Speaker confirmed mute state through MB#63: MUTE")
            elif mute_state in {"0", "UNMUTE"}:
                self.state.muted = False
                self.state.mute_state_known = True
                _LOGGER.info("Speaker confirmed mute state through MB#63: UNMUTE")

        elif mbid == MB_SOURCE:
            try:
                source_id = int(payload)
            except ValueError:
                pass
            else:
                bluetooth_enabled = source_id == 19
                if self.use_tls:
                    # On LS10, Bluetooth service state is independent of the
                    # active playback source. Source 19 proves it is enabled,
                    # but Spotify/URL/AirPlay must not be treated as disabled.
                    if bluetooth_enabled:
                        self.state.bt_enabled = True
                        self.state.bt_status = "Bluetooth_ON"
                else:
                    pending_future = self._pending_bluetooth_future
                    target = self._bluetooth_target
                    target_pending = (
                        pending_future is not None
                        and not pending_future.done()
                        and target is not None
                    )
                    if not target_pending or bluetooth_enabled == target:
                        self.state.bt_enabled = bluetooth_enabled
                        self.state.bt_status = (
                            "Bluetooth_ON" if bluetooth_enabled else "Bluetooth_OFF"
                        )
                    if target_pending and bluetooth_enabled == target:
                        pending_future.set_result(bluetooth_enabled)
                if source_id != self.state.source_id:
                    old_source = self.state.source_id
                    self.state.source_id = source_id
                    self._clear_now_playing_for_source(source_id)
                    _LOGGER.info(
                        "Playback source changed %s -> %s; refreshing metadata",
                        old_source,
                        source_id,
                    )
                    self._schedule_metadata_refresh(
                        delays=(0.1, 0.6, 1.5, 3.0),
                        include_play_view=True,
                    )

        elif mbid == MB_PLAY_STATE:
            self.state.play_state = PLAY_STATES.get(payload.strip(), "stopped")
            if self.state.play_state == "playing":
                if not self.state.position_updated_at:
                    self.state.position_updated_at = time.time()
                asyncio.create_task(self._send(0x01, MB_POSITION, ""))
            elif self.state.play_state == "stopped":
                self.state.position_ms = 0
                self.state.position_updated_at = 0.0

        elif mbid == MB_POSITION:
            try:
                new_pos = int(payload)
            except ValueError:
                pass
            else:
                import time as _time
                old_pos = self.state.position_ms
                # If position is moving but we don't know what's playing, fire
                # a fast metadata refresh. Spotify Connect / AirPlay starts
                # pushing MB#49 immediately but MB#42/50/51 don't always push
                # on their own — we have to GET them. Without this nudge the
                # user waits up to 30s (full coordinator cycle) for track
                # info and source to appear.
                if (new_pos > 0 and self.state.position_ms == 0
                        and (not self.state.title or self.state.source_id == 0)):
                    _LOGGER.debug(
                        "Position became active with no metadata — fetching now-playing"
                    )
                    asyncio.create_task(self._fetch_now_playing_burst())
                # A rollback to the beginning normally marks a new track.
                # Fetch MB#42 immediately instead of waiting for its delayed
                # periodic push, which otherwise leaves old artwork visible.
                elif (
                    new_pos < 10000
                    and old_pos > 15000
                    and new_pos + 5000 < old_pos
                ):
                    self._schedule_metadata_refresh()
                self.state.position_ms = new_pos
                self.state.position_updated_at = _time.time()

        elif mbid == MB_NOW_PLAYING:
            self._parse_now_playing(payload)

        elif mbid == MB_NETWORK_INFO:
            # MB#91 has the format: <Interface>:<MAC>
            # E.g. "Eth0:CC:90:93:35:03:BA" or "Wlan0:CC:90:93:10:2E:8C"
            # The speaker sends both — we prefer Wlan since LS10 speakers are wireless.
            p = payload.strip()
            if p and ":" in p:
                iface, _, mac = p.partition(":")
                iface_lower = iface.strip().lower()
                mac = mac.strip()
                # Wifi MAC takes precedence over Ethernet
                if iface_lower.startswith("wlan") or iface_lower == "wifi":
                    self.state.mac = mac
                elif iface_lower.startswith("eth"):
                    # Only set MAC if we haven't seen a wifi MAC yet
                    if not self.state.mac or self.state.mac.startswith("Eth"):
                        self.state.mac = mac

        elif mbid == MB_FAVOURITES:
            self._parse_favourites(payload)

        elif mbid == MB_CHIME:
            # Per LUCI v14.1 spec §6.45 Tx_MB#80:
            # Response data field is SUCCESS | NI | FILE_NOT_FOUND
            #   SUCCESS         — valid play accepted
            #   NI              — index out of range (No Index, 1..10 only)
            #   FILE_NOT_FOUND  — index valid but no audio file present
            r = payload.strip()
            ru = r.upper()
            if self._last_chime_mbid == MB_CHIME and self._last_chime_time:
                ack_ms = (asyncio.get_event_loop().time() - self._last_chime_time) * 1000.0
                _LOGGER.info("CHIME-DIAG MB#80 ack in %.1fms: %r", ack_ms, r)
            if ru == "FILE_NOT_FOUND":
                _LOGGER.warning(
                    "Chime slot is empty on speaker firmware (FILE_NOT_FOUND). "
                    "Per LUCI v14.1 spec MB#80 supports indexes 1..10; this "
                    "specific slot has no audio file installed."
                )
            elif ru == "NI":
                _LOGGER.warning(
                    "Chime index out of range (NI). Per LUCI v14.1 spec "
                    "MB#80 supports indexes 1..10 only."
                )
            elif ru == "SUCCESS":
                _LOGGER.debug("Chime accepted (MB#80 SUCCESS)")
            elif r:
                _LOGGER.debug("Chime MB#80 response (unrecognised): %s", r)

        elif mbid == MB_AUDIOCUE:
            # Audiocue lifecycle (Lithe firmware extension, MB#82).
            #
            # Per Lithe developer guidance (2026-05-14):
            #   "We have added a LUCI Message box to notify Audio chime cue
            #    start to MCU. On getting this notification, MCU must open
            #    the audio path for playback."
            #
            #   "LUCI MB#80 is used to play the audio cues on the device.
            #    LS10 send response back in MB#82(newly added). Audio cue
            #    start, playback success/failure notifications are notified
            #    through MB#82."
            #
            # The host sends MB#80. MB#82 reports the internal audio-cue
            # lifecycle; the firmware/MCU owns physical audio-path control.
            r = payload.strip()
            ru = r.upper()
            if self._last_chime_time:
                ms = (asyncio.get_event_loop().time() - self._last_chime_time) * 1000.0
                _LOGGER.info("CHIME-DIAG MB#82 +%.1fms: %r", ms, r)
            else:
                _LOGGER.info("CHIME-DIAG MB#82 (unsolicited): %r", r)

            if ru in ("AUDIOCUE_START", "AUDIO_CUE_START", "START"):
                # The working C4 driver treats this as firmware/MCU lifecycle
                # feedback and sends no external AUDIOPATH_OPEN response.
                _LOGGER.info("MB#82 AUDIOCUE_START received")
            elif ru in ("NI", "FILE_NOT_FOUND", "FAILURE", "FAIL"):
                _LOGGER.warning(
                    "Audiocue playback failed: '%s'. Slot may be empty "
                    "or speaker rejected the cue.",
                    r,
                )
            elif ru == "SUCCESS":
                _LOGGER.info("Audiocue completed successfully")

        elif mbid == MB_DSP:
            # Payload is binary DSP sub-packet(s) — see vendor packet
            # capture for the on-wire format.
            #
            # The first two bytes contain the big-endian packet length minus
            # those two bytes. Most controls are 5/6 bytes total; iO1 treble
            # gain responses are 7 bytes because their payload is [band,gain].
            #
            # Multiple sub-packets may be concatenated in one MB#112 frame.
            # We populate state.dsp_* fields so HA entities (selects,
            # switches, numbers) reflect changes made in the Lithe app
            # — 2-way sync.
            try:
                from .const import (
                    BT_MCU_STATE, DSP_BALANCE, DSP_BASS_FIELD, DSP_EQ, DSP_EQ_BANDS,
                    DSP_HIGHPASS, DSP_LOUDNESS, DSP_LOUDNESS_GAIN,
                    DSP_MID_FIELD, DSP_NIGHTMODE,
                    DSP_OUTPUT, DSP_TREBLE_FIELD, DSP_TUNING,
                    PRODUCT_MICRO, SUB_DSP_CROSSOVER, SUB_DSP_GAIN,
                    SUB_DSP_LOWPASS, SUB_DSP_PHASE, SUB_DSP_PROFILE,
                )

                def _signed_8(b: int) -> int:
                    if b & 0xF0 == 0xF0:
                        return -(b & 0x0F)
                    return b
                def _unsigned(b: int) -> int:
                    return b
                def _boolean(b: int) -> int:
                    # Accept binary and ASCII state bytes from firmware builds.
                    return 1 if b in (1, 0x31) else 0
                def _loudness_gain_feedback(b: int) -> int:
                    return b - 10
                def _bluetooth(b: int) -> bool:
                    return b in (1, 0x31)

                _DSP_MAP: dict[int, tuple[str, callable]] = {
                    DSP_EQ:        ("dsp_eq",        _unsigned),
                    DSP_LOUDNESS:  ("dsp_loudness",  _boolean),
                    DSP_LOUDNESS_GAIN: (
                        "dsp_loudness_gain", _loudness_gain_feedback
                    ),
                    DSP_NIGHTMODE: ("dsp_nightmode", _boolean),
                    DSP_BALANCE:   ("dsp_balance",   _signed_8),
                    DSP_OUTPUT:    ("dsp_output",    _unsigned),
                    DSP_HIGHPASS:  ("dsp_highpass",  _unsigned),
                    DSP_TUNING:    ("dsp_tuning",    _boolean),
                    BT_MCU_STATE:  ("bt_enabled",    _bluetooth),
                }
                if self.product == PRODUCT_MICRO:
                    _DSP_MAP = {
                        SUB_DSP_PROFILE: ("sub_profile", _unsigned),
                        SUB_DSP_GAIN: ("sub_gain", _unsigned),
                        SUB_DSP_CROSSOVER: ("sub_crossover", _unsigned),
                        SUB_DSP_PHASE: ("sub_phase", _boolean),
                        SUB_DSP_LOWPASS: ("sub_lowpass", _boolean),
                    }
                _DSP_FIELD_MAP: dict[tuple[int, int], tuple[str, callable]] = {
                    # All_informationGET returns getter field IDs 01/03/05.
                    # App changes use setter IDs 02/04/06, so decode both.
                    (DSP_EQ_BANDS, 0x05):             ("dsp_bass", _signed_8),
                    (DSP_EQ_BANDS, DSP_BASS_FIELD):   ("dsp_bass", _signed_8),
                    (DSP_EQ_BANDS, 0x03):             ("dsp_mid", _signed_8),
                    (DSP_EQ_BANDS, DSP_MID_FIELD):    ("dsp_mid", _signed_8),
                    (DSP_EQ_BANDS, 0x01):             ("dsp_treble", _signed_8),
                    (DSP_EQ_BANDS, DSP_TREBLE_FIELD): ("dsp_treble", _signed_8),
                }

                raw = payload.encode("latin-1") if isinstance(payload, str) else payload
                self.state.dsp_last_raw = " ".join(f"{byte:02X}" for byte in raw)
                parsed = []
                updates: list[tuple[str, int]] = []
                i = 0
                while i + 5 <= len(raw):
                    packet_length = int.from_bytes(raw[i:i + 2], "big") + 2
                    if (
                        raw[i] != 0x00
                        or packet_length < 5
                        or i + packet_length > len(raw)
                    ):
                        i += 1
                        continue

                    packet = raw[i:i + packet_length]
                    sub_mb = packet[3]
                    field = packet[4]
                    values = packet[5:]
                    i += packet_length

                    # A five-byte GET request may be echoed by the MCU. It
                    # carries no setting value and must never make an entity
                    # available.
                    if not values:
                        parsed.append(
                            f"get-request sub=0x{sub_mb:02x} field={field}"
                        )
                        continue

                    parsed.append(
                        f"resp sub=0x{sub_mb:02x} field=0x{field:02x} "
                        f"payload={' '.join(f'{value:02X}' for value in values)}"
                    )

                    if self.product == PRODUCT_IO1 and sub_mb == IO1_TREBLE_GAIN:
                        if len(values) >= 2:
                            band, wire_gain = values[0], values[1]
                            attr = {
                                IO1_TREBLE_LOW_BAND: "dsp_treble",
                                IO1_TREBLE_MID_BAND: "dsp_mid",
                                IO1_TREBLE_HIGH_BAND: "dsp_bass",
                            }.get(band)
                            if attr is not None and 0 <= wire_gain <= 12:
                                updates.append((attr, wire_gain - 6))
                        continue
                    if self.product == PRODUCT_IO1 and sub_mb == IO1_PRESET:
                        updates.append(("dsp_eq", values[-1]))
                        continue

                    val_byte = values[-1]
                    if (sub_mb, field) in _DSP_FIELD_MAP:
                        attr, decode = _DSP_FIELD_MAP[(sub_mb, field)]
                        updates.append((attr, decode(val_byte)))
                    elif sub_mb in _DSP_MAP:
                        attr, decode = _DSP_MAP[sub_mb]
                        updates.append((attr, decode(val_byte)))

                # Apply state updates
                for attr, val in updates:
                    setattr(self.state, attr, val)
                    if attr == "bt_enabled":
                        self.state.bt_status = (
                            "Bluetooth_ON" if val else "Bluetooth_OFF"
                        )

                # PRO 2/V3 reports retain the Normal/manual band registers
                # while a fixed preset is selected.  The Lithe app renders
                # the preset curve instead; do the same after parsing the
                # complete packet so stale/raw manual values cannot win.
                if self.state.dsp_eq in LS10_FIXED_EQ_PRESET_BANDS:
                    self._apply_ls10_fixed_eq_curve(self.state.dsp_eq)

                if updates:
                    self.state.dsp_state_source = "speaker"
                    self.state.dsp_feedback_revision += 1

                self.state.dsp_last_decoded = "; ".join(parsed)

                if parsed:
                    _LOGGER.info(
                        "RX DSP MB#112 raw=%s decoded=%s%s",
                        self.state.dsp_last_raw,
                        "; ".join(parsed),
                        f" → updated {len(updates)} fields" if updates else "",
                    )
            except Exception as e:
                _LOGGER.debug("DSP MB#112 parse error: %s", e)

        elif mbid == MB_TIMEZONE:
            self.state.timezone = payload.strip()

        elif mbid == MB_INTERFACE_IP:
            # MB#123 — "Wlan:192.168.1.101" or "Eth:192.168.1.100"
            p = payload.strip()
            if ":" in p:
                iface, _, ip = p.partition(":")
                iface = iface.strip()
                ip = ip.strip()
                # Prefer Wlan over Eth when both come through
                if iface.lower().startswith("wlan") or not self.state.ip_address:
                    self.state.ip_address = ip
                    self.state.network_interface = iface

        elif mbid == MB_NETWORK_STATUS:
            # MB#124 — "<active>#WLAN,status#ETH,status#P2P,status#CONF,status"
            # active: 1=WLAN, 2=ETH, 3=P2P, 4=WAC/SAC/LS-Connect
            p = payload.strip()
            if p:
                active = p.split("#", 1)[0].strip()
                self.state.network_status = NETWORK_STATUS.get(active, "Unknown")
                # Set speaker_status based on whether any interface is active
                if active in NETWORK_STATUS:
                    self.state.speaker_status = "Connected"
                else:
                    self.state.speaker_status = "Standby"

        elif mbid == MB_RSSI:
            # MB#151 is normally "-55" or "-55,-60", but some builds wrap
            # it as "RSSI:-55". Extract all plausible dBm values.
            p = payload.strip()
            vals = [int(v) for v in re.findall(r"-?\d+", p)]
            vals = [v for v in vals if -127 <= v < 0]
            if vals:
                self.state.wifi_rssi_dbm = max(vals)
                self.state.wifi_rssi_source = f"MB#151 RID 0x{remote_id:04X}"

        elif mbid == MB_DEVICE_INFO:
            # MB#208 is dual-purpose: device info JSON OR NV-read response.
            # If the payload starts with a recognisable NV-read marker we treat
            # it specially. Otherwise fall through to the existing device-info
            # parser.
            p = payload.strip()
            key = ""
            value = ""
            if ":" in p and not p.startswith("{"):
                key, _, value = p.partition(":")

            # Keyed broadcasts such as Model:WiFi v3 are device information,
            # not the bare response to some other pending READ (for example
            # READ_ssid). Only consume one when its key matches the request.
            if key:
                self._parse_device_info(payload)
                if (
                    self._pending_nv_read
                    and self._normalise_nv_name(key)
                    == self._normalise_nv_name(self._pending_nv_read)
                ):
                    self._complete_nv_read(value.strip())
            elif p and self._pending_nv_read:
                self._apply_nv_read_value(self._pending_nv_read, p)
                self._complete_nv_read(p)
            elif p:
                self._parse_device_info(payload)

        else:
            changed = False

        if changed:
            self._notify()

    def _parse_now_playing(self, payload: str) -> None:
        """Parse MB#42 now-playing JSON.

        Different firmwares use different key names. We try a broad set of
        candidates for each field so this works across versions.
        """
        try:
            data = json.loads(payload)
        except Exception:
            _LOGGER.debug("Could not parse now-playing JSON")
            return

        # The Lithe firmware wraps real metadata inside "Window CONTENTS".
        # The OUTER "Title" is just the view name (e.g. "PlayView") — don't
        # read from there or we'll pick up the view name as the track title.
        w = None
        for wrapper in ("Window CONTENTS", "WindowContents", "window_contents", "data", "Data"):
            if isinstance(data.get(wrapper), dict):
                w = data[wrapper]
                break
        if w is None:
            _LOGGER.debug("MB#42 has no Window CONTENTS wrapper, skipping")
            return

        def _first(*keys: str) -> str:
            for k in keys:
                v = w.get(k)
                if v:
                    return str(v)
            return ""

        new_title = _first(
            "TrackName", "trackname", "track_name",
            "Title", "title", "track", "Track", "TrackTitle", "track_title",
            "name", "Name", "currentTitle", "current_title", "song", "Song",
            "currentSong", "current_song",
        )
        new_artist = _first(
            "Artist", "artist", "Performer", "performer",
            "currentArtist", "current_artist", "ArtistName", "artist_name",
        )
        new_album = _first(
            "Album", "album", "AlbumName", "album_name",
            "currentAlbum", "current_album",
        )

        # Some older LinkPlay-derived firmwares swap track name into the
        # "Artist" field for Spotify Connect. Newer Lithe firmware (this one)
        # exposes the proper "TrackName" so we don't need the swap, but it
        # remains as a fallback for older firmware.
        if not new_title and new_artist:
            new_title = new_artist
            new_artist = ""

        incoming_track = (new_title, new_artist, new_album)
        art = _first(
            "CoverArtUrl", "CoverArtURL", "coverarturl",
            "AlbumArt", "Artwork", "ArtworkURI", "AlbumArtURI",
            "albumart", "artwork", "albumArt", "artworkUri", "AlbumArtUri",
            "CoverArt", "coverart", "cover", "Cover", "Image", "image",
            "logo", "Logo", "Icon", "icon",
        )
        if (
            self._stale_direct_url_metadata is not None
            and self._matches_stale_direct_url_metadata(incoming_track, art)
        ):
            _LOGGER.debug(
                "Ignoring stale previous-source metadata after Direct URL start"
            )
            return
        if self._stale_direct_url_metadata is not None and any(incoming_track):
            self._stale_direct_url_metadata = None

        old_track = (self.state.title, self.state.artist, self.state.album)
        self.state.title, self.state.artist, self.state.album = incoming_track
        track_changed = old_track != (
            self.state.title, self.state.artist, self.state.album
        )

        # ── Shuffle / Repeat state (MB#42 Window CONTENTS) ────────────────
        # Lithe firmware exposes:
        #   "Shuffle": 0 (off) or 1 (on)
        #   "Repeat":  0 (off), 1 (all), 2 (one)  — observed values
        if "Shuffle" in w:
            try:
                self.state.shuffle = bool(int(w.get("Shuffle", 0)))
            except (ValueError, TypeError):
                self.state.shuffle = False
        if "Repeat" in w:
            try:
                r = int(w.get("Repeat", 0))
                self.state.repeat = {0: "off", 1: "all", 2: "one"}.get(r, "off")
            except (ValueError, TypeError):
                self.state.repeat = "off"

        # Artwork — real key on Lithe firmware (CR443GP_3713) is "CoverArtUrl"
        # Some firmwares return a relative path — only accept if it looks like a URL
        if art and (art.startswith("http://") or art.startswith("https://")):
            self._cancel_artwork_refresh()
            self.state.artwork_url = art
        elif art:
            # The working Control4 driver special-cases coverart.jpg because
            # firmware rewrites that file for each track and may take several
            # seconds to finish. Give every track a new URL and refresh it at
            # 3s and 6s so HA cannot keep serving the first cached image.
            base_url = (
                f"http://{self.host}{art}"
                if art.startswith("/")
                else f"http://{self.host}/{art}"
            )
            if art.lower().split("?", 1)[0].endswith("coverart.jpg"):
                if track_changed or not self.state.artwork_url:
                    self.state.artwork_revision += 1
                self.state.artwork_url = (
                    f"{base_url}?v={self.state.artwork_revision}"
                )
                if track_changed:
                    self._schedule_artwork_refresh(base_url)
            else:
                self._cancel_artwork_refresh()
                self.state.artwork_url = base_url
        else:
            self._cancel_artwork_refresh()
            self.state.artwork_url = ""

        # Duration — try numerous keys, accept ms or seconds
        duration_raw = None
        for k in ("TotalTime", "totaltime", "Duration", "duration", "track_duration", "Length", "length"):
            if k in w and w[k] not in (None, "", 0):
                duration_raw = w[k]
                break
        try:
            d = int(duration_raw) if duration_raw is not None else 0
            # If under 10000, the speaker probably reports seconds; convert to ms
            if 0 < d < 10000:
                d *= 1000
            self.state.duration_ms = d
        except (TypeError, ValueError):
            self.state.duration_ms = 0

        # Live streams report no duration
        self.state.is_live = (self.state.duration_ms == 0)

    def _matches_stale_direct_url_metadata(
        self, incoming_track: tuple[str, str, str], artwork: str
    ) -> bool:
        """Recognize partial late MB#42 replies from the previous source."""
        stale = self._stale_direct_url_metadata
        if stale is None:
            return False

        incoming = (*incoming_track, artwork)
        comparable = [
            new == old
            for new, old in zip(incoming, stale)
            if new and old
        ]
        if not comparable or not all(comparable):
            return False

        # Require a strong identity field. Firmwares often omit album or
        # artist on a delayed reply, which made tuple equality too strict and
        # allowed stale Spotify artwork back into Direct URL playback.
        return bool(
            (incoming_track[0] and incoming_track[0] == stale[0])
            or (artwork and artwork == stale[3])
        )

    def _cancel_artwork_refresh(self) -> None:
        """Cancel delayed refreshes for a previous local cover image."""
        if self._artwork_refresh_task and not self._artwork_refresh_task.done():
            self._artwork_refresh_task.cancel()
        self._artwork_refresh_task = None

    def _schedule_artwork_refresh(self, base_url: str) -> None:
        """Refresh firmware-generated coverart.jpg after 3 and 6 seconds."""
        self._cancel_artwork_refresh()

        async def _refresh() -> None:
            try:
                for _ in range(2):
                    await asyncio.sleep(3)
                    self.state.artwork_revision += 1
                    self.state.artwork_url = (
                        f"{base_url}?v={self.state.artwork_revision}"
                    )
                    self._notify()
            except asyncio.CancelledError:
                pass

        self._artwork_refresh_task = asyncio.create_task(_refresh())

    def _parse_device_info(self, payload: str) -> None:
        """Parse MB#208 device info.

        Three shapes observed across firmwares:
          1) JSON dict
          2) Single "key: value" line
          3) Multi-line "key: value\nkey: value\n…" block
        """
        # Comprehensive key map — keys are normalised (lower, no spaces, no underscores)
        attr_map = {
            # name / friendly
            "devicename":     "name",
            "networkname":    "name",
            "groupname":      "name",
            "name":           "name",
            # model
            "model":          "model",
            "modelname":      "model",
            "modelid":        "model",
            "deviceid":       "model",
            "hardware":       "model",
            "modelnum":       "model_number",
            "modelnumber":    "model_number",
            "modelvariant":   "model_variant",
            # firmware
            "fwversion":      "firmware",
            "firmware":       "firmware",
            "firmwareversion":"firmware",
            "swversion":      "firmware",
            "version":        "firmware",
            "release":        "firmware",
            # mac
            "mac":            "mac",
            "macaddress":     "mac",
            "macaddr":        "mac",
            "ethernet":       "mac",
            "ethermac":       "mac",
            "wifimac":        "mac",
            "wlanmac":        "mac",
            "bssid":          "mac",
            # wifi band / mode
            "wifiband":       "wifi_band",
            "band":           "wifi_band",
            "wifimode":       "wifi_band",
            "wlanmode":       "wifi_band",
            # net mode
            "netmode":        "net_mode",
            "networkmode":    "net_mode",
            # cast
            "castversion":    "cast_version",
            "castfwversion":  "cast_version",
        }

        def _norm(k: str) -> str:
            return k.strip().lower().replace(" ", "").replace("_", "").replace("-", "")

        def _apply(key: str, val: str) -> None:
            attr = attr_map.get(_norm(key))
            if attr and val:
                cleaned = str(val).strip()
                if attr == "wifi_band":
                    cleaned = self._wifi_band_from_value(cleaned) or ""
                    if not cleaned:
                        return
                setattr(self.state, attr, cleaned)
                if attr in {"model", "model_number", "model_variant"}:
                    detected = product_from_model(
                        self.state.model,
                        self.state.model_number,
                        self.state.model_variant,
                    )
                    if detected:
                        self.state.detected_product = detected

        # JSON first
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                for k, v in data.items():
                    _apply(k, v)
                return
        except Exception:
            pass

        # Fallback: line-by-line key:value parsing
        for line in payload.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                _apply(key, val)

    @staticmethod
    def _normalise_nv_name(value: str) -> str:
        """Normalize an NV key for response/request correlation."""
        return value.strip().lower().replace("_", "").replace("-", "")

    def _apply_nv_read_value(self, item: str, value: str) -> None:
        """Apply a bare MB#208 READ response to typed speaker state."""
        key = self._normalise_nv_name(item)
        cleaned = value.strip()
        if not cleaned or cleaned == "2":
            return
        if key == "ssid":
            self.state.ssid = cleaned
        elif key == "model":
            self.state.model = cleaned
            if detected := product_from_model(cleaned):
                self.state.detected_product = detected
        elif key in {"modelnum", "modelnumber"}:
            self.state.model_number = cleaned
        elif key == "modelvariant":
            self.state.model_variant = cleaned

        detected = product_from_model(
            self.state.model,
            self.state.model_number,
            self.state.model_variant,
        )
        if detected:
            self.state.detected_product = detected

    def _complete_nv_read(self, value: str) -> None:
        """Resolve the active serialized MB#208 request."""
        item = self._pending_nv_read
        future = self._pending_nv_future
        # Clear synchronously so a second unsolicited MB#208 packet cannot be
        # consumed as another response before the waiting coroutine resumes.
        self._pending_nv_read = None
        self._pending_nv_future = None
        if item:
            self._apply_nv_read_value(item, value)
            _LOGGER.debug("NV read %r = %r", item, value)
        if future and not future.done():
            future.set_result(value)

    def _parse_favourites(self, payload: str) -> None:
        """Parse MB#70 favourites payload.

        Accepts either JSON list/dict, or "FAV_LIST:1=Name1|2=Name2|..." text format.
        """
        try:
            data = json.loads(payload)
            favs = []
            if isinstance(data, list):
                for i, item in enumerate(data, 1):
                    if isinstance(item, dict):
                        favs.append({
                            "slot": int(item.get("slot", i)),
                            "name": str(item.get("name", f"Favourite {i}")),
                        })
                    else:
                        favs.append({"slot": i, "name": str(item)})
            elif isinstance(data, dict):
                for k, v in data.items():
                    try:
                        favs.append({"slot": int(k), "name": str(v)})
                    except ValueError:
                        continue
            self.state.favourites = sorted(favs, key=lambda x: x["slot"])
            return
        except Exception:
            pass
        # Fallback text format
        if payload.startswith("FAV_LIST"):
            body = payload.split(":", 1)[1] if ":" in payload else ""
            favs = []
            for chunk in body.split("|"):
                if "=" in chunk:
                    s, name = chunk.split("=", 1)
                    try:
                        favs.append({"slot": int(s), "name": name.strip()})
                    except ValueError:
                        continue
            if favs:
                self.state.favourites = sorted(favs, key=lambda x: x["slot"])

    # ── Packet builder ─────────────────────────────────────────────────────

    @staticmethod
    def _build_packet(
        cmd_type: int, mbid: int, payload: str, remote_id: int = 0xAAAA
    ) -> bytes:
        """Build a TX packet matching the LUCI spec.

        Per Lithe's official Python example (vendor docs §10.2):

          header = struct.pack("<H B H B H H",
              REMOTE_ID,    # 0xAAAA
              cmd_type,     # 0x01 GET or 0x02 SET
              mbid,
              0x00,         # CmdStatus
              0x0000,       # CRC
              len(payload)) # DataLen u16 little-endian
          sock.sendall(header + payload + b"\\x00")  # terminator

        Notes:
          - DataLen is LITTLE-endian (not BE as I mistakenly assumed)
          - DataLen excludes the trailing terminator
          - Terminator 0x00 IS appended after the packet
        """
        data = payload.encode("utf-8")
        data_len = len(data)
        header = struct.pack(
            "<HBHBHH", remote_id, cmd_type, mbid, 0, 0x0000, data_len
        )
        return header + data + b"\x00"

    def _registration_payload(self) -> str:
        """Return a valid, uniquely identified LUCI push registration."""
        if not self.use_tls:
            return self.local_ip
        return json.dumps(
            {
                "app_info": {
                    # Firmware indexes push clients by app ID. Sharing the
                    # Control4 driver's ID makes MB#112 delivery intermittent.
                    "id": "home-assistant",
                    "ip": self.local_ip,
                    "version": "1.0.0",
                }
            },
            separators=(",", ":"),
        ) + "\n"

    async def _write_packet(self, packet: bytes) -> None:
        """Serialize writes and preserve the C4 driver's 100 ms spacing."""
        if not self._writer or self._writer.is_closing():
            return
        async with self._send_lock:
            loop = asyncio.get_running_loop()
            delay = 0.1 - (loop.time() - self._last_tx_time)
            if delay > 0:
                await asyncio.sleep(delay)
            self._writer.write(packet)
            await self._writer.drain()
            self._last_tx_time = loop.time()

    async def _send(
        self,
        cmd_type: int,
        mbid: int,
        payload: str,
        remote_id: int = 0xAAAA,
    ) -> None:
        if self._writer and not self._writer.is_closing():
            if _LOGGER.isEnabledFor(logging.DEBUG):
                op = "GET" if cmd_type == 0x01 else "SET"
                preview = payload[:80] + ("…" if len(payload) > 80 else "")
                _LOGGER.debug("TX %s MB#%d (%d bytes): %s", op, mbid, len(payload), preview)
            await self._write_packet(
                self._build_packet(cmd_type, mbid, payload, remote_id)
            )


class LitheClientLS9(LitheClient):
    """
    LS9 transactional client — connect, send, disconnect per command.
    LS9 firmware only allows one TCP connection at a time.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._ls9_transaction_lock = asyncio.Lock()
        self._source_auth_task: asyncio.Task | None = None
        self._source_auth_error: Exception | None = None
        self._initial_ordinary_refresh_task: asyncio.Task | None = None

    @staticmethod
    def _build_tunnel_packet(
        command: int,
        field: int,
        value: int | None = None,
    ) -> bytes:
        """Build the checksummed HOST-MCU packet used by the Lithe app.

        Lithe Audio LA1.7.5.22 calculates byte 2 as the unsigned sum of the
        command, field and payload bytes. LS9 replies use zero in that byte,
        so receive parsing deliberately continues to treat it as opaque.
        """
        body = [command & 0xFF, field & 0xFF]
        if value is not None:
            body.append(value & 0xFF)
        return bytes([0x00, len(body) + 1, sum(body) & 0xFF, *body])

    @staticmethod
    def _build_binary_packet(
        cmd_type: int,
        mbid: int,
        payload: bytes,
        remote_id: int = 0xAAAA,
    ) -> bytes:
        """Build an LS9 LUCI packet carrying an opaque tunnel payload."""
        header = struct.pack(
            "<HBHBHH", remote_id, cmd_type, mbid, 0, 0x0000, len(payload)
        )
        return header + payload + b"\x00"

    async def async_transact(
        self,
        mbid: int,
        payload: str | bytes,
        cmd_type: int = 0x02,
        remote_id: int = 0xAAAA,
    ) -> list[tuple[int, str]]:
        """Connect, send one command, collect responses, disconnect."""
        async with self._ls9_transaction_lock:
            return await self._async_transact_unlocked(
                mbid, payload, cmd_type, remote_id
            )

    async def _async_transact_unlocked(
        self,
        mbid: int,
        payload: str | bytes,
        cmd_type: int = 0x02,
        remote_id: int = 0xAAAA,
    ) -> list[tuple[int, str]]:
        """Run one LS9 transaction while the connection lock is held."""
        responses: list[tuple[int, str]] = []
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=4.0
            )
            try:
                sock = writer.get_extra_info("socket")
                if sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except Exception:
                pass

            # Register with plain IP string (LS9: no JSON)
            writer.write(self._build_packet(0x02, MB_REGISTER, self.local_ip))
            await writer.drain()
            await asyncio.sleep(0.15)

            # Send command
            packet = (
                self._build_binary_packet(cmd_type, mbid, payload, remote_id)
                if isinstance(payload, bytes)
                else self._build_packet(cmd_type, mbid, payload, remote_id)
            )
            writer.write(packet)
            await writer.drain()

            # Read responses for up to 1.5s
            buf = b""
            deadline = asyncio.get_event_loop().time() + 1.5
            while asyncio.get_event_loop().time() < deadline:
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=0.25)
                    if not chunk:
                        break
                    buf += chunk
                    while len(buf) >= 10:
                        try:
                            # BE on wire (empirical) — see comment in
                            # main client _consume_packets.
                            data_len = struct.unpack_from(">H", buf, 8)[0]
                        except struct.error:
                            break
                        total = 10 + data_len
                        if len(buf) < total:
                            break
                        r_mbid = struct.unpack_from(">H", buf, 3)[0]
                        r_payload_bytes = buf[10:10 + data_len]
                        r_payload: str | bytes
                        if r_mbid == MB_DSP:
                            r_payload = r_payload_bytes
                        else:
                            r_payload = r_payload_bytes.decode(
                                "utf-8", "replace"
                            ).rstrip("\x00")
                        buf = buf[total:]
                        if r_mbid == MB_PLAYBACK_AUTH:
                            # LS9 permits one TCP connection. Grant on this
                            # exact socket; scheduling self._send() would open
                            # a second connection and the firmware discards it.
                            writer.write(
                                self._build_packet(
                                    0x02, MB_PLAYBACK_GRANT, "1", remote_id
                                )
                            )
                            await writer.drain()
                            _LOGGER.info(
                                "LS9 MB#10 source %r granted on active "
                                "socket with MB#11=1",
                                str(r_payload).strip(),
                            )
                        else:
                            self._handle_push(r_mbid, r_payload)
                        # Caller wants non-grant responses (so it can see
                        # the actual reply, not the auth handshake)
                        if r_mbid != MB_PLAYBACK_AUTH:
                            responses.append((r_mbid, r_payload))
                except asyncio.TimeoutError:
                    if responses:
                        break
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        except Exception as e:
            _LOGGER.debug("LS9 transact %s MB#%d: %s", self.host, mbid, e)
        return responses

    async def async_open_source_authorization_window(
        self, duration: float = 8.0
    ) -> None:
        """Listen briefly for an externally initiated Spotify source change.

        Spotify Connect is initiated through Spotify's API, not through a LUCI
        command. LS9 therefore needs a live registered HOST socket while the
        transfer happens so MB#10 can be answered immediately with MB#11=1.
        """
        if self._source_auth_task and not self._source_auth_task.done():
            self._source_auth_task.cancel()
            try:
                await self._source_auth_task
            except (asyncio.CancelledError, ConnectionError, OSError):
                pass

        ready = asyncio.Event()
        self._source_auth_error = None
        self._source_auth_task = asyncio.create_task(
            self._source_authorization_listener(duration, ready)
        )
        try:
            await asyncio.wait_for(ready.wait(), timeout=3.0)
        except asyncio.TimeoutError as err:
            self._source_auth_task.cancel()
            raise ConnectionError(
                f"Could not open LS9 Spotify authorization window on "
                f"{self.host}"
            ) from err
        if self._source_auth_error is not None:
            raise ConnectionError(
                f"Could not open LS9 Spotify authorization window on "
                f"{self.host}: {self._source_auth_error}"
            ) from self._source_auth_error

    async def _source_authorization_listener(
        self, duration: float, ready: asyncio.Event
    ) -> None:
        """Hold one LS9 socket and grant MB#10 on that same connection."""
        writer: asyncio.StreamWriter | None = None
        try:
            async with self._ls9_transaction_lock:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), timeout=4.0
                )
                writer.write(
                    self._build_packet(0x02, MB_REGISTER, self.local_ip)
                )
                await writer.drain()
                await asyncio.sleep(0.15)
                ready.set()

                buf = b""
                deadline = asyncio.get_running_loop().time() + duration
                while asyncio.get_running_loop().time() < deadline:
                    timeout = min(
                        0.5,
                        max(0.05, deadline - asyncio.get_running_loop().time()),
                    )
                    try:
                        chunk = await asyncio.wait_for(
                            reader.read(4096), timeout=timeout
                        )
                    except asyncio.TimeoutError:
                        continue
                    if not chunk:
                        break
                    buf += chunk
                    while len(buf) >= 10:
                        data_len = struct.unpack_from(">H", buf, 8)[0]
                        total = 10 + data_len
                        if len(buf) < total:
                            break
                        r_mbid = struct.unpack_from(">H", buf, 3)[0]
                        raw = buf[10:10 + data_len]
                        buf = buf[total:]
                        r_payload = raw.decode(
                            "utf-8", "replace"
                        ).rstrip("\x00")
                        if r_mbid == MB_PLAYBACK_AUTH:
                            writer.write(
                                self._build_packet(
                                    0x02, MB_PLAYBACK_GRANT, "1", 0xAAAA
                                )
                            )
                            await writer.drain()
                            _LOGGER.info(
                                "LS9 Spotify source %r authorized with "
                                "MB#11=1 on the listening socket",
                                r_payload.strip(),
                            )
                        else:
                            self._handle_push(r_mbid, r_payload)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._source_auth_error = err
            if not ready.is_set():
                ready.set()
            _LOGGER.warning(
                "LS9 Spotify authorization listener failed on %s: %s",
                self.host,
                err,
            )
        finally:
            if not ready.is_set():
                ready.set()
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

    async def async_dsp_command(
        self,
        sub_mb: int,
        value: int,
        field: int = 0x02,
    ) -> None:
        """Send an MCU/DSP setting through the documented LS9 TCP tunnel."""
        byte_val = (
            value & 0xFF
            if value >= 0
            else 0xF0 | min(0x0F, abs(value))
        )
        payload = self._build_tunnel_packet(sub_mb, field, byte_val)
        _LOGGER.info(
            "TX LS9 DSP tunnel sub=0x%02x field=0x%02x val=%d: %s",
            sub_mb,
            field,
            value,
            " ".join(f"{byte:02X}" for byte in payload),
        )
        tunnel_writer: asyncio.StreamWriter | None = None
        revision_before = self.state.dsp_feedback_revision
        try:
            async with self._dsp_refresh_lock:
                tunnel_reader, tunnel_writer = await self._async_open_ls9_dsp_tunnel()
                tunnel_writer.write(payload)
                await tunnel_writer.drain()

                # LS9 returns the authoritative MCU value on this raw socket,
                # often as a SET echo followed by a fragmented state report.
                # Previously the reader was discarded and the tunnel closed
                # immediately, leaving HA on restored/optimistic state.
                chunks: list[bytes] = []
                deadline = asyncio.get_running_loop().time() + 1.5
                while asyncio.get_running_loop().time() < deadline:
                    try:
                        chunk = await asyncio.wait_for(
                            tunnel_reader.read(4096), timeout=0.35
                        )
                    except asyncio.TimeoutError:
                        continue
                    if not chunk:
                        break
                    chunks.append(chunk)
                    response = bytes([
                        0x00, 0x04, 0x00, sub_mb & 0xFF,
                        field & 0xFF, byte_val,
                    ])
                    if response in b"".join(chunks):
                        # The MCU echoed this exact setter and therefore
                        # confirmed it. Do not hold a HA slider/switch service
                        # call open while unrelated full-state fragments trail.
                        break
                if chunks:
                    self._handle_push(
                        MB_DSP, b"".join(chunks), remote_id=0xAAAA
                    )
        except Exception as err:
            # A small number of early LS9 builds expose MB112 directly but do
            # not create the MB111 listener. Keep that documented route as a
            # fallback, using the legacy 0xAAAA RemoteID from the LUCI spec.
            _LOGGER.debug(
                "LS9 DSP tunnel unavailable; using MB112 fallback: %s", err
            )
            await self.async_transact(
                MB_DSP,
                payload,
                cmd_type=0x02,
                remote_id=0xAAAA,
            )
        finally:
            if tunnel_writer is not None:
                tunnel_writer.close()
                try:
                    await tunnel_writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
        if self.state.dsp_feedback_revision == revision_before:
            self._record_local_dsp_command(sub_mb, value, field)
        if self.state.connected and self.product != PRODUCT_MICRO:
            # LS9 tunnel writes are audible but are not broadcast back to the
            # sending LUCI client. Confirm the resulting MCU state separately
            # so HA does not remain on an optimistic value.
            self._schedule_dsp_readback()

    async def _async_open_ls9_dsp_tunnel(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open the MB111 listener used by LS9 network-to-MCU commands."""
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(self.host, DSP_TUNNEL_PORT),
                timeout=0.75,
            )
        except (OSError, asyncio.TimeoutError):
            await self.async_transact(
                MB_TUNNEL_START,
                str(DSP_TUNNEL_PORT),
                cmd_type=0x02,
                remote_id=0xAAAA,
            )
            await asyncio.sleep(0.2)
            return await asyncio.wait_for(
                asyncio.open_connection(self.host, DSP_TUNNEL_PORT),
                timeout=2.0,
            )

    async def async_dsp_refresh(self) -> bool:
        """Read current LS9 EQ state directly from the MCU tunnel."""
        if self.product == PRODUCT_MICRO:
            revision = self.state.dsp_feedback_revision
            await self.async_subwoofer_refresh()
            return self.state.dsp_feedback_revision > revision

        requests = [
            self._build_tunnel_packet(DSP_STATUS_ALL, 0x01),
            # Older LS9 firmware can return a partial or empty response to
            # All_informationGET. The Lithe app follows it with individual
            # getter requests, including each EQ-band getter field.
            self._build_tunnel_packet(DSP_EQ_BANDS, 0x01),
            self._build_tunnel_packet(DSP_EQ_BANDS, 0x03),
            self._build_tunnel_packet(DSP_EQ_BANDS, 0x05),
            self._build_tunnel_packet(DSP_EQ, 0x01),
            self._build_tunnel_packet(DSP_LOUDNESS, 0x01),
            self._build_tunnel_packet(DSP_NIGHTMODE, 0x01),
            self._build_tunnel_packet(DSP_BALANCE, 0x01),
            self._build_tunnel_packet(DSP_OUTPUT, 0x01),
        ]
        tunnel_writer: asyncio.StreamWriter | None = None
        try:
            async with self._dsp_refresh_lock:
                tunnel_reader, tunnel_writer = (
                    await self._async_open_ls9_dsp_tunnel()
                )
                for request in requests:
                    tunnel_writer.write(request)
                await tunnel_writer.drain()

                chunks: list[bytes] = []
                deadline = asyncio.get_running_loop().time() + 2.5
                while asyncio.get_running_loop().time() < deadline:
                    try:
                        chunk = await asyncio.wait_for(
                            tunnel_reader.read(4096),
                            timeout=0.75 if not chunks else 0.25,
                        )
                    except asyncio.TimeoutError:
                        # Captured V2 firmware pauses between fragments of the
                        # all-settings response. Keep the tunnel open until the
                        # overall deadline so output/preset records arriving in
                        # a later fragment are not discarded.
                        continue
                    if not chunk:
                        break
                    chunks.append(chunk)

                if not chunks:
                    return False
                revision = self.state.dsp_feedback_revision
                self._handle_push(
                    MB_DSP, b"".join(chunks), remote_id=0xAAAA
                )
                return self.state.dsp_feedback_revision > revision
        except Exception as err:
            _LOGGER.debug("LS9 DSP live readback unavailable: %s", err)
            return False
        finally:
            if tunnel_writer is not None:
                tunnel_writer.close()
                try:
                    await tunnel_writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

    async def async_subwoofer_refresh(self) -> None:
        """Request every live Micro Subwoofer setting from its MCU."""
        if self.product != PRODUCT_MICRO:
            return
        request = self._build_tunnel_packet(0x00, 0x00)
        tunnel_writer: asyncio.StreamWriter | None = None
        try:
            async with self._dsp_refresh_lock:
                tunnel_reader, tunnel_writer = (
                    await self._async_open_ls9_dsp_tunnel()
                )
                tunnel_writer.write(request)
                await tunnel_writer.drain()

                chunks: list[bytes] = []
                deadline = asyncio.get_running_loop().time() + 1.5
                while asyncio.get_running_loop().time() < deadline:
                    try:
                        chunk = await asyncio.wait_for(
                            tunnel_reader.read(4096),
                            timeout=0.75 if not chunks else 0.25,
                        )
                    except asyncio.TimeoutError:
                        break
                    if not chunk:
                        break
                    chunks.append(chunk)
                if chunks:
                    self._handle_push(
                        MB_DSP, b"".join(chunks), remote_id=0xAAAA
                    )
        except Exception as err:
            _LOGGER.debug(
                "Micro DSP tunnel refresh unavailable; using MB112 fallback: %s",
                err,
            )
            await self.async_transact(
                MB_DSP,
                request,
                cmd_type=0x02,
                remote_id=0xAAAA,
            )
        finally:
            if tunnel_writer is not None:
                tunnel_writer.close()
                try:
                    await tunnel_writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

    async def async_save_subwoofer_custom(
        self,
        gain: int,
        crossover: int,
        phase: int,
        lowpass: int,
    ) -> None:
        """Apply the complete Custom profile to the Micro Subwoofer."""
        for sub_mb, value in (
            (SUB_DSP_PROFILE, 2),
            (SUB_DSP_GAIN, max(0, min(100, int(gain)))),
            (SUB_DSP_CROSSOVER, max(0, min(3, int(crossover)))),
            (SUB_DSP_PHASE, 1 if phase else 0),
            (SUB_DSP_LOWPASS, 1 if lowpass else 0),
        ):
            await self.async_dsp_command(sub_mb, value, SUB_DSP_SET_FIELD)

    async def async_refresh(self) -> None:
        """Refresh ordinary LUCI state plus Micro Subwoofer MCU settings."""
        await super().async_refresh()
        if self.product == PRODUCT_MICRO:
            await self.async_subwoofer_refresh()
        else:
            # LS9 does not maintain the persistent LUCI session that delivers
            # LS10 MB#112 pushes. Poll the MCU tunnel each coordinator cycle
            # so changes from the Lithe app and power-cycle defaults replace
            # restored or optimistic Home Assistant state.
            await self.async_dsp_refresh()

    async def _send(
        self,
        cmd_type: int,
        mbid: int,
        payload: str,
        remote_id: int = 0xAAAA,
    ) -> None:
        await self.async_transact(mbid, payload, cmd_type, remote_id)

    async def async_connect(self) -> None:
        """Prime live MCU state, then fetch slower LUCI metadata in background.

        A complete LS9 metadata sweep opens a separate transactional socket for
        every mailbox and can take well over Home Assistant's setup warning
        threshold.  DSP state is the startup-critical data: read it first so
        RestoreEntity cannot replace speaker values with stale HA values, then
        let the ordinary name/source/firmware sweep finish after platforms are
        available.
        """
        self.state.connected = True
        await self.async_dsp_refresh()
        self._notify()

        if (
            self._initial_ordinary_refresh_task is None
            or self._initial_ordinary_refresh_task.done()
        ):
            self._initial_ordinary_refresh_task = asyncio.create_task(
                self._async_initial_ordinary_refresh()
            )

    async def _async_initial_ordinary_refresh(self) -> None:
        """Populate non-DSP LS9 state without holding config-entry setup."""
        try:
            await super().async_refresh()
            self._notify()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.warning(
                "Deferred LS9 startup refresh failed on %s: %s",
                self.host,
                err,
            )

    async def async_disconnect(self) -> None:
        if (
            self._initial_ordinary_refresh_task
            and not self._initial_ordinary_refresh_task.done()
        ):
            self._initial_ordinary_refresh_task.cancel()
            try:
                await self._initial_ordinary_refresh_task
            except asyncio.CancelledError:
                pass
        if self._source_auth_task and not self._source_auth_task.done():
            self._source_auth_task.cancel()
            try:
                await self._source_auth_task
            except (asyncio.CancelledError, ConnectionError, OSError):
                pass
        if self._dsp_readback_task and not self._dsp_readback_task.done():
            self._dsp_readback_task.cancel()
            try:
                await self._dsp_readback_task
            except asyncio.CancelledError:
                pass
        self.state.connected = False
