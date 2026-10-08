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

import aiohttp

from .const import (
    DEFAULT_PORT, MB_AUDIOCUE, MB_BLUETOOTH, MB_BROWSE, MB_BT_STATUS, MB_CHIME,
    MB_DEVICE_INFO, MB_DEVICE_NAME, MB_DSP, MB_FACTORY_RESET, MB_FAVOURITES,
    MB_FIRMWARE, MB_INTERFACE_IP, MB_MUTE, MB_NETWORK_INFO, MB_NETWORK_STATUS,
    MB_NOW_PLAYING, MB_PLAY_STATE, MB_PLAYBACK_AUTH, MB_PLAYBACK_GRANT,
    MB_POSITION, MB_REBOOT_REQ,
    MB_REGISTER, MB_RSSI, MB_SOURCE, MB_TIMEZONE, MB_TRANSPORT, MB_VOLUME,
    MUTE_OFF, MUTE_ON, NETWORK_STATUS, PLAY_STATES, SOURCES, TRANSPORT_NEXT,
    TRANSPORT_PAUSE, TRANSPORT_PLAY, TRANSPORT_PREV, TRANSPORT_RESUME,
    TRANSPORT_STOP,
    DSP_REMOTE_ID, DSP_STATUS_ALL,
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
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._buf = b""
        self._read_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._artwork_refresh_task: Optional[asyncio.Task] = None
        self._metadata_refresh_task: Optional[asyncio.Task] = None
        self._volume_before_mute: int | None = None
        self._last_nonzero_volume: int = self.state.volume
        # Incremented only by valid MB#63 MUTE/UNMUTE feedback. Command code
        # uses this to distinguish a real state transition from local state.
        self._mute_feedback_revision: int = 0
        # Bluetooth ON/OFF is controlled by an HTTP endpoint. Serialize those
        # requests and retain the requested state briefly so delayed MB#210
        # packets cannot undo a freshly verified web setting.
        self._bluetooth_lock = asyncio.Lock()
        self._bluetooth_target: bool | None = None
        self._bluetooth_target_until: float = 0.0
        self._bluetooth_http_state_known: bool = False
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
        MB#49 Position, MB#210 BT Status) even though the
        spec marks them as push-only. We send them because they're the
        only way to get the speaker's current state on connect or after
        a stale period — the spec-only "push on change" never fires if
        nothing changed.

        We previously tried removing these per a strict spec read and it
        broke track info / source display / play state, so they stay.
        """
        # Do not send MB#112/0x15 here. Bench testing on CR443GP_4083 shows
        # that it acknowledges the packet with SUCCESS but does not return a
        # settings dump. DSP state is learned from genuine MB#112 broadcasts
        # and restored read-only by the coordinator across HA restarts.

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
                   MB_TIMEZONE,         # 573 TimeZone
                   MB_BT_STATUS):       # 210 BT Status (Tx_ but responds)
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

        # MB#70 Favourites — SET FAV_LIST is the documented query form
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
        await self._send(0x02, MB_FAVOURITES, "FAV_LIST")

    # ── Commands ───────────────────────────────────────────────────────────

    def _mute_remote_id(self) -> int:
        """Return the source-specific LUCI route for MB#40 mute commands."""
        # Spotify Connect and AirPlay accept the normal app route. Cast ignores
        # MB#40 on that route, but applies the same command through the MCU route.
        return 0x0000 if self.state.source_id == 24 else 0xAAAA

    async def async_set_volume(self, level: int) -> None:
        # RID 0x0000 reaches the MCU gain write. RID 0xAAAA may only update
        # the LS-side state without changing audible volume.
        level = max(0, min(100, level))
        if level > 0:
            self._last_nonzero_volume = level
        if level > 0 and self.state.muted:
            await self._send(
                0x02, MB_TRANSPORT, MUTE_OFF,
                remote_id=self._mute_remote_id(),
            )
            await asyncio.sleep(0.25)
        await self._send(
            0x02,
            MB_VOLUME,
            str(level),
            remote_id=0x0000,
        )
        self.state.volume = level
        self._notify()

    async def async_mute(self, mute: bool) -> None:
        """Mute / unmute speaker.

        The working Control4 driver sends MUTE/UNMUTE through MB#40. MB#63
        is feedback carrying the resulting mute state.
        """
        if mute:
            if self.state.volume > 0:
                self._volume_before_mute = self.state.volume
            await self._send(
                0x02, MB_TRANSPORT, MUTE_ON,
                remote_id=self._mute_remote_id(),
            )
            return

        # Spotify/AirPlay normally use RID 0xAAAA; Cast requires MCU route
        # 0x0000. Only MB#63 proves that the hardware state actually changed.
        feedback_revision = self._mute_feedback_revision
        primary_remote_id = self._mute_remote_id()
        await self._send(
            0x02, MB_TRANSPORT, MUTE_OFF,
            remote_id=primary_remote_id,
        )
        restore_volume = self._volume_before_mute or self._last_nonzero_volume
        await asyncio.sleep(0.35)

        confirmed_unmuted = (
            self._mute_feedback_revision != feedback_revision
            and not self.state.muted
        )
        # Only recover volume if device feedback actually reports zero after
        # UNMUTE. Normal Spotify playback retains its pre-mute MB#64 value.
        if confirmed_unmuted and self.state.volume <= 0 and restore_volume > 0:
            await self._send(
                0x02, MB_VOLUME, str(restore_volume), remote_id=0x0000,
            )
            self.state.volume = restore_volume
        self._last_nonzero_volume = restore_volume
        self._volume_before_mute = None
        self._notify()

    async def async_play(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_PLAY)

    async def async_pause(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_PAUSE)

    async def async_resume(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_RESUME)

    async def async_stop(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_STOP)

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
        self, delays: tuple[float, ...] = (0.15, 0.75, 1.5)
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
            except asyncio.CancelledError:
                pass
            except Exception as err:
                _LOGGER.debug("Metadata refresh failed: %s", err)

        self._metadata_refresh_task = asyncio.create_task(_refresh())

    async def async_play_url(self, url: str) -> None:
        """Push a direct stream URL to the speaker (MB#41 DIRECT).

        Match the working Control4 path exactly: MB#41 SET with
        PLAYITEM:DIRECT:<url>. MB#50 is source feedback, not a source-switch
        command, so no synthetic MB#50 SET preflight is sent.

        After sending the URL, schedule a metadata refresh so the HA
        media player shows the new track info promptly.
        """
        await self._send(0x02, MB_BROWSE, f"PLAYITEM:DIRECT:{url}")
        # Store the URL so the media player can display it as a friendly
        # title while metadata is being fetched
        self.state.last_played_url = url
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
        await self._send(0x02, MB_FAVOURITES, f"FAV_PLAY:{int(slot)}")

    async def async_save_favourite(self, slot: int) -> None:
        """Save the currently-playing entry to a favourite slot (MB#70).

        Per Lithe API_NEW page 23 (§5.4 Save & Resume Playback):
            SET MB#70 "FAV_SAVE:<slot>"

        The currently-active playback entry must be a valid saveable
        source (Spotify Connect, Airable, etc.). Embedded chimes and
        Direct URL cues cannot be saved.
        """
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
        n = max(1, min(14, int(chime_number)))
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
                # Confirm the requested state through the live LUCI path and
                # ask MB#210 to push its current Bluetooth status.
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

    async def _async_bluetooth_luci_fallback(
        self, command: str, enabled: bool
    ) -> None:
        """Apply Bluetooth ON/OFF through LUCI when the web UI is unreliable."""
        await self._send(0x02, MB_BLUETOOTH, command)
        await asyncio.sleep(0.15)
        await self._send(0x01, MB_BT_STATUS, "")
        self.state.bt_enabled = enabled
        self.state.bt_status = f"BLUETOOTH_{command}_LUCI"
        self._bluetooth_target = enabled
        self._bluetooth_target_until = time.monotonic() + 5.0
        self._notify()

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

    async def async_dsp_refresh(self) -> None:
        """Send the vendor 0x15 diagnostic trigger.

        CR443GP_4083 acknowledges this with SUCCESS but returns no settings
        to third-party clients, so normal refresh deliberately does not call
        it. Keep the exact packet available for protocol diagnostics only.
        """
        sub = bytes([0x00, 0x03, 0x00, DSP_STATUS_ALL, 0x01])
        header = struct.pack(
            "<HBHBHH", DSP_REMOTE_ID, 0x02, MB_DSP, 0, 0x0000, len(sub)
        )
        packet = header + sub + b"\x00"
        if not self._writer or self._writer.is_closing():
            _LOGGER.debug("DSP state refresh skipped because writer is unavailable")
            return
        _LOGGER.debug("TX DSP MB#112 full-state request via subcommand 0x15")
        await self._write_packet(packet)

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

        elif mbid == MB_MUTE:
            mute_state = payload.strip().upper()
            if mute_state in {"1", "MUTE"}:
                self.state.muted = True
                self.state.mute_state_known = True
                self._mute_feedback_revision += 1
                _LOGGER.info("Speaker confirmed mute state through MB#63: MUTE")
            elif mute_state in {"0", "UNMUTE"}:
                self.state.muted = False
                self.state.mute_state_known = True
                self._mute_feedback_revision += 1
                _LOGGER.info("Speaker confirmed mute state through MB#63: UNMUTE")

        elif mbid == MB_SOURCE:
            try:
                self.state.source_id = int(payload)
            except ValueError:
                pass

        elif mbid == MB_PLAY_STATE:
            self.state.play_state = PLAY_STATES.get(payload.strip(), "stopped")

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
            # Push format     (5 bytes): 00 03 <subMB_hi> <subMB_lo> <value>
            # SET response    (6 bytes): 00 04 <subMB_hi> <subMB_lo> 02 <value>
            #
            # Multiple sub-packets may be concatenated in one MB#112 frame.
            # We populate state.dsp_* fields so HA entities (selects,
            # switches, numbers) reflect changes made in the Lithe app
            # — 2-way sync.
            try:
                from .const import (
                    DSP_BALANCE, DSP_BASS_FIELD, DSP_EQ, DSP_EQ_BANDS,
                    DSP_HIGHPASS, DSP_LOUDNESS, DSP_LOUDNESS_GAIN,
                    DSP_MID_FIELD, DSP_NIGHTMODE,
                    DSP_OUTPUT, DSP_TREBLE_FIELD, DSP_TUNING,
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
                    # Push packet — 00 03 <subMB hi> <subMB lo> <value>
                    if raw[i] == 0x00 and raw[i+1] == 0x03 and i + 5 <= len(raw):
                        sub_mb = (raw[i+2] << 8) | raw[i+3]
                        if sub_mb > 0xFF:
                            sub_mb = raw[i+3]
                        else:
                            sub_mb = sub_mb if sub_mb else raw[i+3]
                        val_byte = raw[i+4]
                        parsed.append(f"push sub=0x{sub_mb:02x}({sub_mb}) val={val_byte}")
                        if sub_mb in _DSP_MAP:
                            attr, decode = _DSP_MAP[sub_mb]
                            updates.append((attr, decode(val_byte)))
                        i += 5
                    # SET response / GET response — 00 04 <subMB hi> <subMB lo> [status] <value>
                    elif raw[i] == 0x00 and raw[i+1] == 0x04 and i + 6 <= len(raw):
                        sub_mb = raw[i+3]  # low byte; high byte is 0
                        field = raw[i+4]
                        val_byte = raw[i+5]
                        parsed.append(
                            f"resp sub=0x{sub_mb:02x} field=0x{field:02x} val={val_byte}"
                        )
                        if (sub_mb, field) in _DSP_FIELD_MAP:
                            attr, decode = _DSP_FIELD_MAP[(sub_mb, field)]
                            updates.append((attr, decode(val_byte)))
                        elif sub_mb in _DSP_MAP:
                            attr, decode = _DSP_MAP[sub_mb]
                            updates.append((attr, decode(val_byte)))
                        i += 6
                    # GET response variant: 00 05 <hi> <lo> <value> (5 bytes)
                    elif raw[i] == 0x00 and raw[i+1] == 0x05 and i + 5 <= len(raw):
                        sub_mb = raw[i+3]
                        val_byte = raw[i+4]
                        parsed.append(f"get-resp sub=0x{sub_mb:02x}({sub_mb}) val={val_byte}")
                        if sub_mb in _DSP_MAP:
                            attr, decode = _DSP_MAP[sub_mb]
                            updates.append((attr, decode(val_byte)))
                        i += 5
                    else:
                        i += 1

                # Apply state updates
                for attr, val in updates:
                    setattr(self.state, attr, val)

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

        elif mbid == MB_BT_STATUS:
            self.state.bt_status = payload.strip()
            bt = self.state.bt_status.upper()
            reported: bool | None = None
            if "OFF" in bt or bt.endswith(":0"):
                reported = False
            elif (
                bt in {"ON", "1", "ENABLED", "BLUETOOTH_ON"}
                or bt.startswith("ENTPAIR")
                or "CONNECTED" in bt
                or bt.endswith(":1")
            ):
                reported = True

            # READY is a player/service status, not reliable radio-state
            # feedback. In particular it can arrive after Bluetooth_OFF.
            if reported is not None:
                target_pending = (
                    self._bluetooth_target is not None
                    and time.monotonic() < self._bluetooth_target_until
                )
                if target_pending and reported != self._bluetooth_target:
                    _LOGGER.debug(
                        "Ignoring stale MB#210 %r while Bluetooth target is %s",
                        payload,
                        "ON" if self._bluetooth_target else "OFF",
                    )
                else:
                    self.state.bt_enabled = reported

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

        old_track = (self.state.title, self.state.artist, self.state.album)

        self.state.title  = _first(
            "TrackName", "trackname", "track_name",
            "Title", "title", "track", "Track", "TrackTitle", "track_title",
            "name", "Name", "currentTitle", "current_title", "song", "Song",
            "currentSong", "current_song",
        )
        self.state.artist = _first(
            "Artist", "artist", "Performer", "performer",
            "currentArtist", "current_artist", "ArtistName", "artist_name",
        )
        self.state.album  = _first(
            "Album", "album", "AlbumName", "album_name",
            "currentAlbum", "current_album",
        )

        # Some older LinkPlay-derived firmwares swap track name into the
        # "Artist" field for Spotify Connect. Newer Lithe firmware (this one)
        # exposes the proper "TrackName" so we don't need the swap, but it
        # remains as a fallback for older firmware.
        if not self.state.title and self.state.artist:
            self.state.title = self.state.artist
            self.state.artist = ""
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
        art = _first(
            "CoverArtUrl", "CoverArtURL", "coverarturl",
            "AlbumArt", "Artwork", "ArtworkURI", "AlbumArtURI",
            "albumart", "artwork", "albumArt", "artworkUri", "AlbumArtUri",
            "CoverArt", "coverart", "cover", "Cover", "Image", "image",
            "logo", "Logo", "Icon", "icon",
        )
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

    async def async_transact(
        self,
        mbid: int,
        payload: str,
        cmd_type: int = 0x02,
        remote_id: int = 0xAAAA,
    ) -> list[tuple[int, str]]:
        """Connect, send one command, collect responses, disconnect."""
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
            writer.write(self._build_packet(cmd_type, mbid, payload, remote_id))
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
                        # ALWAYS dispatch — even MB#10 needs _handle_push so
                        # it can send the MB#11=1 grant. Previously this was
                        # suppressed which broke source switching on LS9.
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

    async def _send(
        self,
        cmd_type: int,
        mbid: int,
        payload: str,
        remote_id: int = 0xAAAA,
    ) -> None:
        await self.async_transact(mbid, payload, cmd_type, remote_id)

    async def async_connect(self) -> None:
        """LS9: no persistent connection — just mark connected and prime state."""
        self.state.connected = True
        await self.async_refresh()

    async def async_disconnect(self) -> None:
        self.state.connected = False
