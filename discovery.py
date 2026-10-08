"""Lightweight SSDP (LSSDP) discovery for Lithe Audio speakers.

Lithe uses a proprietary SSDP variant on the standard 239.255.255.250
multicast group but port 1800 (not 1900). We send an M-SEARCH and
harvest responses for ~3 seconds to enumerate speakers on the local
network without parsing SSDP XML.
"""
from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass, field

from .const import (
    LS10_MODELS,
    LSSDP_MSEARCH,
    LSSDP_MULTICAST_ADDR,
    LSSDP_PORT,
    PLATFORM_LS9,
    PLATFORM_LS10,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class DiscoveredDevice:
    """One LSSDP response from a Lithe Audio speaker."""

    host: str
    port: int
    name: str
    model: str
    mac: str
    platform: str            # "LS9" or "LS10"
    firmware: str = ""
    cast_firmware: str = ""
    net_mode: str = ""
    speaker_type: str = ""
    raw_headers: dict[str, str] = field(default_factory=dict)

    @property
    def unique_id(self) -> str:
        """Stable per-device identifier — MAC if available, otherwise IP."""
        return (self.mac or self.host).lower().replace(":", "")


class _LSSDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, sink: list) -> None:
        self._sink = sink

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        self._sink.append((data, addr))


def _parse_response(data: bytes, src_host: str) -> DiscoveredDevice | None:
    """Parse one LSSDP HTTP/1.1 response into a DiscoveredDevice."""
    try:
        text = data.decode("utf-8", "replace")
    except Exception:
        return None

    headers: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().upper()] = v.strip()

    if not headers:
        return None

    # Some Lithe responses carry MAC under different keys
    mac = (
        headers.get("MAC")
        or headers.get("USN")
        or headers.get("DEVICE_ID")
        or ""
    )
    # Current LS10 discovery advertises CAST_MODEL rather than MODEL.
    model = (
        headers.get("MODEL")
        or headers.get("CAST_MODEL")
        or headers.get("ST")
        or ""
    )
    speaker_type = (
        headers.get("SPEAKER_TYPE") or headers.get("SPEAKERTYPE") or ""
    )

    # Determine platform
    source_list = headers.get("SOURCE_LIST", "")
    if source_list.startswith("LS10") or any(m in model for m in LS10_MODELS):
        platform = PLATFORM_LS10
    else:
        platform = PLATFORM_LS9

    # Format MAC nicely (12 hex chars -> AA:BB:CC:DD:EE:FF)
    mac_clean = "".join(c for c in mac if c in "0123456789abcdefABCDEF")
    if len(mac_clean) == 12:
        mac_pretty = ":".join(mac_clean[i:i + 2] for i in range(0, 12, 2)).upper()
    else:
        mac_pretty = mac.upper()

    try:
        port = int(headers.get("PORT", "7777") or 7777)
    except ValueError:
        port = 7777

    return DiscoveredDevice(
        host=src_host,
        port=port,
        name=headers.get("DEVICENAME") or model or src_host,
        model=model,
        mac=mac_pretty,
        platform=platform,
        firmware=headers.get("FWVERSION", ""),
        cast_firmware=headers.get("CAST_FWVERSION", ""),
        net_mode=headers.get("NETMODE", ""),
        speaker_type=speaker_type,
        raw_headers=headers,
    )


async def async_discover(timeout: float = 3.0) -> list[DiscoveredDevice]:
    """Send LSSDP M-SEARCH and collect responses for ``timeout`` seconds.

    Returns one ``DiscoveredDevice`` per unique MAC. Safe to call from
    the Home Assistant event loop.
    """
    loop = asyncio.get_running_loop()
    responses: list[tuple[bytes, tuple]] = []

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
    sock.setblocking(False)
    sock.bind(("", 0))

    transport: asyncio.BaseTransport | None = None
    try:
        transport, _proto = await loop.create_datagram_endpoint(
            lambda: _LSSDPProtocol(responses), sock=sock,
        )
        sock.sendto(LSSDP_MSEARCH, (LSSDP_MULTICAST_ADDR, LSSDP_PORT))
        await asyncio.sleep(timeout)
    except Exception as e:
        _LOGGER.debug("LSSDP discovery error: %s", e)
    finally:
        if transport is not None:
            transport.close()

    seen: dict[str, DiscoveredDevice] = {}
    for data, addr in responses:
        dev = _parse_response(data, addr[0])
        if dev is None:
            continue
        key = dev.unique_id
        # Prefer LS10 detection over LS9 if duplicate frames arrive
        if key not in seen or dev.platform == PLATFORM_LS10:
            seen[key] = dev
    return list(seen.values())
