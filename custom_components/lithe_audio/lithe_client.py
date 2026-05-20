"""Lithe Audio speaker protocol client."""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import ssl
import struct
from dataclasses import dataclass, field
from typing import Callable, Optional

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
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class SpeakerState:
    """Current state of a Lithe Audio speaker."""
    name: str = ""
    firmware: str = ""
    model: str = ""
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
    ssid: str = ""              # Connected SSID (from NV item)
    speaker_status: str = ""    # "Standby" / "Connected" / "Active" etc

    # Playback
    play_state: str = "stopped"
    source_id: int = 0
    volume: int = 50
    muted: bool = False
    position_ms: int = 0
    position_updated_at: float = 0.0   # asyncio loop time when MB#49 last seen

    # Now playing
    title: str = ""
    artist: str = ""
    album: str = ""
    artwork_url: str = ""
    duration_ms: int = 0
    is_live: bool = False  # True for radio/AirPlay streams (no SEEK)
    shuffle: bool = False
    repeat: str = "off"  # "off" | "all" | "one"

    # Most recent URL sent via play_url — useful as a fallback title
    # while waiting for the speaker to push fresh MB#42 metadata after
    # a source switch to Direct URL.
    last_played_url: str = ""

    # Bluetooth
    bt_status: str = ""

    # DSP state — populated from MB#112 push packets so HA reflects
    # changes made in the Lithe app (2-way sync). Sub-MB IDs verified
    # from app packet capture (dsp-sniffer, 2026-05-17):
    dsp_eq:        int | None = None  # 0x0A: 0=Normal 1=Acoustic 2=Jazz 3=Pop 4=HipHop
    dsp_treble:    int | None = None  # 0x09: signed
    dsp_loudness:  int | None = None  # 0x16: signed -10..+10
    dsp_nightmode: int | None = None  # 0x18: 0=OFF 1=ON
    dsp_highpass:  int | None = None  # 0x1A: 0=OFF 1=60Hz 2=80Hz 3=100Hz 4=120Hz
    dsp_tuning:    int | None = None  # 0x1D: 0=Enclosure 13L, 1=Open Back
    dsp_balance:   int | None = None  # 0x1E: signed -6..+6
    dsp_output:    int | None = None  # 0x0F: 0=Mono 1=Stereo 2=Left 3=Right

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
        self._callbacks: list[Callable] = []
        self._last_rx_time: float = 0.0
        self._last_chime_time: float = 0.0
        self._last_chime_mbid: int = 0
        # Track every RemoteID seen on the socket for diagnostic purposes
        # (multiple RemoteIDs can come from one speaker depending on source)
        self._seen_remote_ids: set[int] = set()
        # NV item being read via MB#208 READ_<item> — cleared on response
        self._pending_nv_read: str | None = None
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

        # Register. Match the Control4 reference driver: send the host's
        # network address as a plain string for ALL platforms (LS9 + LS10).
        # We previously used a JSON {"APP_info": {...}} blob for LS10
        # speakers, but Control4 — which works reliably — uses plain IP
        # everywhere. The JSON format may put the speaker in a different
        # session mode that delays chime processing.
        reg = self.local_ip

        self._writer.write(self._build_packet(0x02, MB_REGISTER, reg))
        await self._writer.drain()
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
                    # Re-register with plain IP (matches Control4 driver)
                    self._writer.write(self._build_packet(0x02, MB_REGISTER, self.local_ip))
                    # Refresh device name (Control4 does this too)
                    self._writer.write(self._build_packet(0x01, MB_DEVICE_NAME, ""))
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
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

    # ── State refresh ──────────────────────────────────────────────────────

    async def async_refresh(self) -> None:
        """Request all state from speaker.

        NOTE: empirically this firmware responds to GETs on Tx_-only
        mailboxes (MB#42 Now Playing, MB#50 Source, MB#51 Play State,
        MB#49 Position, MB#63 Mute, MB#210 BT Status) even though the
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
                   MB_RSSI,             # 151 RSSI
                   MB_VOLUME,           # 64  Volume
                   MB_MUTE,             # 63  Mute (Tx_ but responds)
                   MB_SOURCE,           # 50  Current Source (Tx_ but responds)
                   MB_PLAY_STATE,       # 51  Play State (Tx_ but responds)
                   MB_NOW_PLAYING,      # 42  Now Playing JSON (Tx_ but responds)
                   MB_POSITION,         # 49  Position (Tx_ but responds)
                   MB_TIMEZONE,         # 573 TimeZone
                   MB_BT_STATUS):       # 210 BT Status (Tx_ but responds)
            await self._send(0x01, mb, "")
            await asyncio.sleep(0.05)

        # MB#91 NETWORK INFO requires SET MACADDR payload (per spec §9.35)
        await self._send(0x02, MB_NETWORK_INFO, "MACADDR")
        await asyncio.sleep(0.05)

        # MB#70 Favourites — SET FAV_LIST is the documented query form
        await self._send(0x02, MB_FAVOURITES, "FAV_LIST")
        await asyncio.sleep(0.05)

        # NV read for SSID via MB#208 — Lithe's published NV-read protocol
        await self.async_read_nv("ssid")

    async def async_read_nv(self, item: str) -> None:
        """Read an NV item via MB#208 SET READ_<item>.

        Per LUCI spec §10.23, the speaker responds on the same MB#208 with
        the NV item's value as the payload.
        """
        self._pending_nv_read = item
        await self._send(0x02, MB_DEVICE_INFO, f"READ_{item}")

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

    async def async_set_volume(self, level: int) -> None:
        await self._send(0x02, MB_VOLUME, str(max(0, min(100, level))))

    async def async_mute(self, mute: bool) -> None:
        """Mute / unmute speaker.

        Empirically this firmware accepts SET on MB#63 directly. The spec
        says MB#63 is Tx_ only and mute should go through MB#40 SET MUTE/
        UNMUTE — but MB#63 SET works and was the proven path in earlier
        working versions. Keep what works.
        """
        await self._send(0x02, MB_MUTE, MUTE_ON if mute else MUTE_OFF)

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

    async def async_prev_track(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_PREV)

    async def async_seek(self, position_ms: int) -> None:
        await self._send(0x02, MB_TRANSPORT, f"SEEK:{int(position_ms)}")

    async def async_set_shuffle(self, on: bool) -> None:
        """Toggle shuffle on/off via MB#40."""
        await self._send(0x02, MB_TRANSPORT, "SHUFFLE:ON" if on else "SHUFFLE:OFF")

    async def async_set_repeat(self, mode: str) -> None:
        """Set repeat mode via MB#40.

        mode: 'off' | 'all' | 'one'
        Per LUCI Tech Note: REPEAT:OFF, REPEAT:ALL, REPEAT:ONE.
        """
        m = (mode or "off").lower()
        cmd = {"off": "REPEAT:OFF", "all": "REPEAT:ALL", "one": "REPEAT:ONE"}.get(m, "REPEAT:OFF")
        await self._send(0x02, MB_TRANSPORT, cmd)

    async def async_play_url(self, url: str) -> None:
        """Push a direct stream URL to the speaker (MB#41 DIRECT).

        Source-release preflight: per LUCI spec §10.35, external sources
        (Spotify Connect, AirPlay, Cast, Tidal, Deezer, Favourites etc.)
        silently block Direct URL playback. If the speaker is currently
        on one of these, we send SET MB#50 0 first to release the audio
        path. Without this, PLAYITEM:DIRECT is silently ignored and the
        user hears nothing.

        After sending the URL, schedule a metadata refresh so the HA
        media player shows the new track info promptly.
        """
        # Sources that own the audio path and silently block PLAYITEM:DIRECT
        # per observation + LUCI spec §10.35.
        # 0  = No Source (already free, no preflight needed)
        # 17 = Direct URL (we're already there, no preflight needed)
        # All other non-local sources need release first.
        _BLOCKING_SOURCES = {
            1,   # AirPlay
            2,   # DMR
            3,   # DMP
            4,   # Spotify Connect
            7,   # Melon
            8,   # vTuner
            9,   # TuneIn
            11,  # Playlist
            18,  # QPlay
            19,  # Bluetooth
            21,  # Deezer
            22,  # Tidal
            23,  # Favourites — confirmed blocking from log 2026-05-20
            24,  # Google Cast
            27,  # Roon
            28,  # Alexa
            30,  # Airable
        }
        current_src = self.state.source_id
        if current_src in _BLOCKING_SOURCES:
            _LOGGER.info(
                "play_url preflight: releasing source %d before "
                "PLAYITEM:DIRECT (LUCI §10.35 — external sources block)",
                current_src,
            )
            try:
                await self._send(0x02, MB_SOURCE, "0")  # release audio path
                # Give firmware a beat to release before requesting new source
                await asyncio.sleep(0.3)
            except Exception as e:
                _LOGGER.debug("Source release preflight failed: %s", e)

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

            Command:  0xAAAA SET 80 NA <"play N">     where N is 1..10
            Response: 0xAAAA SET 80 success/failure
                      data field = SUCCESS | NI | FILE_NOT_FOUND
              - SUCCESS         — valid play accepted, audio cue triggered
              - NI              — index out of range (No Index, 1..10 only)
              - FILE_NOT_FOUND  — index valid but no audio file installed

        That's the complete protocol. Single request, single response on
        MB#80. The earlier MB#82 AUDIOPATH_OPEN handshake (from vendor
        private support guidance) is NOT part of the documented spec —
        we no longer send it proactively and only respond if the speaker
        spontaneously sends MB#82 AUDIOCUE_START.

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
        # Per LUCI v14.1 spec §6.45: device supports up to 10 indexes.
        # Higher values return NI from the speaker — cap here so we
        # don't bother sending requests that will fail.
        n = max(1, min(10, int(chime_number)))
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
            # Send the documented chime command. Nothing else.
            # Per LUCI spec §9.33, this is the complete protocol.
            await self._send(0x02, MB_CHIME, f"play {n}")
            self._last_chime_mbid = MB_CHIME
        except Exception as e:
            _LOGGER.warning("Chime send failed: %s", e)

        self._last_chime_time = now

    async def async_bluetooth(self, command: str) -> None:
        """BT command: ON / OFF / ENTPAIR / DISCONNECT."""
        await self._send(0x02, MB_BLUETOOTH, command)

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

    async def async_dsp_command(self, sub_mb: int, value: int) -> None:
        """Send a DSP command via MB#112 tunnel (LS10 only).

        Sub-packet shape (6 bytes): 0x00 0x04 [sub_mb hi] [sub_mb lo] 0x02 [value]
        """
        byte_val = value & 0xFF if value >= 0 else (256 + value) & 0xFF
        sub = bytes([
            0x00, 0x04,
            (sub_mb >> 8) & 0xFF, sub_mb & 0xFF,
            0x02,
            byte_val,
        ])
        # DataLen counts payload bytes only — terminator is separate
        data_len = len(sub)
        header = struct.pack("<HBHBHH", 0xAAAA, 0x02, MB_DSP, 0, 0x0000, data_len)
        pkt = header + sub + b"\x00"  # terminator per vendor §10.2

        if not self._writer:
            _LOGGER.warning(
                "DSP TX sub=0x%02x val=%d DROPPED — no writer (not connected)",
                sub_mb, value,
            )
            return
        if self._writer.is_closing():
            _LOGGER.warning(
                "DSP TX sub=0x%02x val=%d DROPPED — writer closing",
                sub_mb, value,
            )
            return

        hex_preview = " ".join(f"{b:02X}" for b in pkt)
        _LOGGER.info(
            "TX DSP MB#112 sub=0x%02x val=%d (%d bytes): %s",
            sub_mb, value, len(pkt), hex_preview,
        )
        try:
            self._writer.write(pkt)
            await self._writer.drain()
        except Exception as e:
            _LOGGER.warning(
                "DSP TX sub=0x%02x val=%d write FAILED: %s",
                sub_mb, value, e,
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

            self._handle_push(mbid, payload)

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

    def _handle_push(self, mbid: int, payload: str) -> None:
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
                self.state.volume = int(payload)
            except ValueError:
                pass

        elif mbid == MB_MUTE:
            self.state.muted = (payload == "1" or payload.upper() == "MUTE")

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
                    self.state.wifi_band = self.state.wifi_band or "Wi-Fi"
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
            # Flow:
            #   1. We send MB#80 SET "play N"
            #   2. Speaker → us: MB#82 "AUDIOCUE_START"
            #   3. We → speaker: MB#82 SET "AUDIOPATH_OPEN" (acknowledge that
            #      the audio path is open — equivalent to an MCU enabling
            #      its audio DSP/codec for cue playback). For us as a
            #      software host, this is purely an acknowledgement; we
            #      don't have hardware to gate.
            #   4. Speaker plays the cue, then notifies success/failure.
            r = payload.strip()
            ru = r.upper()
            if self._last_chime_time:
                ms = (asyncio.get_event_loop().time() - self._last_chime_time) * 1000.0
                _LOGGER.info("CHIME-DIAG MB#82 +%.1fms: %r", ms, r)
            else:
                _LOGGER.info("CHIME-DIAG MB#82 (unsolicited): %r", r)

            if ru in ("AUDIOCUE_START", "AUDIO_CUE_START", "START"):
                # Speaker is requesting we open the audio path. Acknowledge
                # immediately so playback can proceed.
                _LOGGER.info(
                    "MB#82 AUDIOCUE_START received — responding with "
                    "AUDIOPATH_OPEN to permit cue playback"
                )
                asyncio.create_task(
                    self._send(0x02, MB_AUDIOCUE, "AUDIOPATH_OPEN")
                )
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
                    DSP_EQ, DSP_TREBLE, DSP_LOUDNESS, DSP_NIGHTMODE,
                    DSP_HIGHPASS, DSP_TUNING, DSP_BALANCE, DSP_OUTPUT,
                )

                # Map sub-MB ID → (SpeakerState attribute, decode function).
                #
                # The Lithe firmware uses ASYMMETRIC sub-MB codes AND
                # sometimes asymmetric value encodings:
                # - TX (controller → speaker) uses modern codes:
                #     0x16 (loudness, signed -10..+10)
                #     0x18 (night mode, 0/1)
                #     0x1A (highpass, 0..4)
                #     0x1C (output, 0..3)
                #     0x1D (tuning, 0/1)
                #     0x1E (balance, signed -6..+6)
                # - RX broadcast (speaker → all clients, fired when the
                #   *app* changes a setting via HOST MCU path) uses
                #   LEGACY codes with potentially DIFFERENT encodings.
                #
                # Sniffer-confirmed mappings (2026-05-18):
                #   Night Mode  TX 0x18  ⟷ RX 0x0C, 0/1                 ✓ proven
                #   Loudness    TX 0x16  ⟷ RX 0x34, 0..20 (offset +10)  ✓ proven
                #
                # Decode functions take the raw byte and return the
                # value to store in state.dsp_* (matching the displayed
                # scale that HA entities use).
                def _signed_8(b: int) -> int:
                    return b - 256 if b > 127 else b
                def _unsigned(b: int) -> int:
                    return b
                def _loudness_unsigned_offset(b: int) -> int:
                    # Speaker broadcast encoding: wire 0..20 → display -10..+10
                    return b - 10

                _DSP_MAP: dict[int, tuple[str, callable]] = {
                    DSP_EQ:        ("dsp_eq",        _unsigned),
                    DSP_TREBLE:    ("dsp_treble",    _signed_8),
                    DSP_LOUDNESS:  ("dsp_loudness",  _signed_8),  # TX echo (signed)
                    0x34:          ("dsp_loudness",  _loudness_unsigned_offset),  # RX broadcast 0..20 (sniffer-confirmed)
                    DSP_NIGHTMODE: ("dsp_nightmode", _unsigned),
                    0x0C:          ("dsp_nightmode", _unsigned),  # RX broadcast (sniffer-confirmed)
                    DSP_HIGHPASS:  ("dsp_highpass",  _unsigned),
                    DSP_TUNING:    ("dsp_tuning",    _unsigned),
                    DSP_BALANCE:   ("dsp_balance",   _signed_8),
                    DSP_OUTPUT:    ("dsp_output",    _unsigned),
                }

                raw = payload.encode("latin-1") if isinstance(payload, str) else payload
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
                        val_byte = raw[i+5]
                        parsed.append(f"resp sub=0x{sub_mb:02x}({sub_mb}) val={val_byte}")
                        if sub_mb in _DSP_MAP:
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

                if _LOGGER.isEnabledFor(logging.DEBUG) and parsed:
                    _LOGGER.debug(
                        "DSP MB#112 decoded: %s%s",
                        "; ".join(parsed),
                        f" → updated {len(updates)} fields" if updates else "",
                    )
            except Exception as e:
                _LOGGER.debug("DSP MB#112 parse error: %s", e)

        elif mbid == MB_BT_STATUS:
            self.state.bt_status = payload.strip()

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
            # MB#151 — payload is RSSI in dBm as a string (e.g. "-55" or "-55,-60" for dual antenna)
            p = payload.strip()
            if "," in p:
                # Multiple antennas — take the strongest (least negative)
                try:
                    vals = [int(v.strip()) for v in p.split(",") if v.strip()]
                    if vals:
                        self.state.wifi_rssi_dbm = max(vals)
                except ValueError:
                    pass
            else:
                try:
                    self.state.wifi_rssi_dbm = int(p)
                except ValueError:
                    pass

        elif mbid == MB_DEVICE_INFO:
            # MB#208 is dual-purpose: device info JSON OR NV-read response.
            # If the payload starts with a recognisable NV-read marker we treat
            # it specially. Otherwise fall through to the existing device-info
            # parser.
            p = payload.strip()
            if p and not p.startswith("{") and self._pending_nv_read:
                # We requested NV READ_<item> — this is the value
                nv_item = self._pending_nv_read
                self._pending_nv_read = None
                if nv_item.lower() == "ssid":
                    self.state.ssid = p
                _LOGGER.debug("NV read %r = %r", nv_item, p)
            else:
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
            self.state.artwork_url = art
        elif art:
            # Relative path — try to construct a URL using the speaker IP
            self.state.artwork_url = f"http://{self.host}{art}" if art.startswith("/") else f"http://{self.host}/{art}"
        else:
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
                setattr(self.state, attr, str(val).strip())

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
    def _build_packet(cmd_type: int, mbid: int, payload: str) -> bytes:
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
        header = struct.pack("<HBHBHH", 0xAAAA, cmd_type, mbid, 0, 0x0000, data_len)
        return header + data + b"\x00"

    async def _send(self, cmd_type: int, mbid: int, payload: str) -> None:
        if self._writer and not self._writer.is_closing():
            if _LOGGER.isEnabledFor(logging.DEBUG):
                op = "GET" if cmd_type == 0x01 else "SET"
                preview = payload[:80] + ("…" if len(payload) > 80 else "")
                _LOGGER.debug("TX %s MB#%d (%d bytes): %s", op, mbid, len(payload), preview)
            self._writer.write(self._build_packet(cmd_type, mbid, payload))
            await self._writer.drain()


class LitheClientLS9(LitheClient):
    """
    LS9 transactional client — connect, send, disconnect per command.
    LS9 firmware only allows one TCP connection at a time.
    """

    async def async_transact(
        self, mbid: int, payload: str, cmd_type: int = 0x02
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
            writer.write(self._build_packet(cmd_type, mbid, payload))
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
                        r_payload = buf[10:10 + data_len].decode("utf-8", "replace").rstrip("\x00")
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

    async def _send(self, cmd_type: int, mbid: int, payload: str) -> None:
        await self.async_transact(mbid, payload, cmd_type)

    async def async_connect(self) -> None:
        """LS9: no persistent connection — just mark connected and prime state."""
        self.state.connected = True
        await self.async_refresh()

    async def async_disconnect(self) -> None:
        self.state.connected = False
