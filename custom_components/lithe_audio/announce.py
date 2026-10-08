"""High-level announcement services for Lithe Audio.

Provides three convenience services that wrap the existing tannoy
override pattern with friendlier APIs:

  lithe_audio.announce  — TTS or URL announcement on selected speakers,
                          with auto-resume of prior playback
  lithe_audio.broadcast — same as announce but targets ALL Lithe speakers
  lithe_audio.doorbell  — short chime + optional TTS, fast ducking

Internally each routes through the existing notify.lithe_tannoy code
path (start → play → wait → end), with TTS rendered via HA's standard
TTS pipeline.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant, ServiceCall

from .const import DATA_COORDINATOR, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Default per-speaker per-announcement waits, before restoring previous
# playback state. Used when caller doesn't specify a duration.
DEFAULT_ANNOUNCE_WAIT_S = 8
DEFAULT_CHIME_WAIT_S    = 4


async def _all_lithe_hosts(hass: HomeAssistant) -> list[str]:
    """Return all connected Lithe speaker host IPs."""
    hosts: list[str] = []
    for _entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
        if not isinstance(entry_data, dict):
            continue
        coord = entry_data.get(DATA_COORDINATOR) or entry_data.get("coordinator")
        if coord and coord.client.state.connected:
            hosts.append(coord.client.host)
    return hosts


async def _render_tts(
    hass: HomeAssistant,
    message: str,
    language: str | None = None,
    tts_service: str | None = None,
) -> str | None:
    """Render TTS to a temporary URL using HA's TTS pipeline.

    Returns the resolved HTTP URL or None on failure. Uses media_source
    so the same TTS works regardless of which TTS engine is configured
    (Piper, Cloud, Google, etc.).
    """
    try:
        from homeassistant.components import media_source

        # Pick a TTS service. Prefer the user-supplied one, else first
        # configured TTS entity, else the deprecated tts.google_translate
        # fallback.
        if not tts_service:
            tts_states = hass.states.async_entity_ids("tts")
            if tts_states:
                tts_service = tts_states[0].split(".", 1)[1]
            else:
                tts_service = "google_translate_say"  # built-in fallback

        # Build a media_source TTS URI. The URI format works for all
        # TTS providers and resolves to a fresh URL each call.
        uri = f"media-source://tts/{tts_service}?message={message}"
        if language:
            uri += f"&language={language}"
        resolved = await media_source.async_resolve_media(
            hass, uri, target_media_player=None,
        )
        # Convert relative URL to absolute so the speaker can reach it
        url = resolved.url
        if url.startswith("/"):
            from homeassistant.helpers.network import get_url
            base = get_url(hass, prefer_external=False, allow_internal=True)
            url = f"{base}{url}"
        _LOGGER.debug("TTS rendered: %s → %s", message[:40], url)
        return url
    except Exception as e:
        _LOGGER.warning("TTS render failed for %r: %s", message[:40], e)
        return None


async def _do_announcement(
    hass: HomeAssistant,
    speakers: list[str],
    url: str,
    volume: int,
    wait_seconds: float,
    duck_resume: bool,
) -> None:
    """Run the duck → play → wait → restore sequence on each speaker.

    Speakers may be a mix of:
      - Lithe IP addresses → uses notify.lithe_tannoy (LUCI protocol with
        full save/restore)
      - media_player.* entity IDs (e.g. Google Cast Groups) → uses HA's
        media_player.play_media with announce:true (HA handles ducking
        for cast-aware media players automatically)
    """
    if not speakers or not url:
        return

    lithe_ips = [s for s in speakers if not s.startswith("media_player.")]
    media_ents = [s for s in speakers if s.startswith("media_player.")]

    # Lithe LUCI path: tannoy save/restore
    if lithe_ips:
        try:
            await hass.services.async_call(
                "notify", "lithe_tannoy",
                {
                    "message":  url,
                    "data": {
                        "mode":     "start",
                        "speakers": lithe_ips,
                        "volume":   volume,
                    },
                },
                blocking=True,
            )
        except Exception as e:
            _LOGGER.error("Announce start (Lithe) failed: %s", e)

    # Cast group / generic media_player path: announce:true ducks natively
    for ent_id in media_ents:
        try:
            # Set volume first (some Cast targets honour this, others
            # restore their own volume after announce:true)
            await hass.services.async_call(
                "media_player", "volume_set",
                {"entity_id": ent_id, "volume_level": volume / 100.0},
                blocking=False,
            )
            await hass.services.async_call(
                "media_player", "play_media",
                {
                    "entity_id":          ent_id,
                    "media_content_type": "music",
                    "media_content_id":   url,
                    "announce":           True,
                },
                blocking=False,
            )
            _LOGGER.info("Announce → play_media on %s: %s", ent_id, url)
        except Exception as e:
            _LOGGER.error("Announce on %s failed: %s", ent_id, e)

    # Wait for the audio to play through
    await asyncio.sleep(wait_seconds)

    # End the Lithe tannoy override (restores prior state on IP targets)
    if duck_resume and lithe_ips:
        try:
            await hass.services.async_call(
                "notify", "lithe_tannoy",
                {
                    "message":  "",
                    "data": {"mode": "end", "speakers": lithe_ips},
                },
                blocking=False,
            )
        except Exception as e:
            _LOGGER.error("Announce end failed: %s", e)


def _register_services(hass: HomeAssistant) -> None:
    """Register lithe_audio.announce / broadcast / doorbell."""

    async def svc_announce(call: ServiceCall) -> None:
        """Speak a message on one or more speakers, ducking current playback.

        Fields:
          message        — text to speak (TTS), OR a URL to play directly
          speakers       — list of host IPs (default: all)
          volume         — 0-100 (default 80)
          language       — TTS language code (default: integration default)
          tts            — TTS service name (e.g. 'piper', 'google_translate_say')
          wait_seconds   — how long to play before resuming (default 8)
          duck_resume    — auto-restore previous playback (default true)
        """
        msg = (call.data.get("message") or "").strip()
        if not msg:
            _LOGGER.error("announce: 'message' is required")
            return

        speakers = call.data.get("speakers") or await _all_lithe_hosts(hass)
        if isinstance(speakers, str):
            speakers = [s.strip() for s in speakers.split(",") if s.strip()]
        if not speakers:
            _LOGGER.warning("announce: no speakers available")
            return

        volume = int(call.data.get("volume", 80))
        volume = max(0, min(100, volume))
        wait_s = float(call.data.get("wait_seconds", DEFAULT_ANNOUNCE_WAIT_S))
        duck_resume = bool(call.data.get("duck_resume", True))

        # If message looks like a URL, play directly; otherwise render TTS
        if msg.startswith(("http://", "https://", "media-source://")):
            url = msg
            if url.startswith("media-source://"):
                # Resolve via media_source helper
                try:
                    from homeassistant.components import media_source
                    resolved = await media_source.async_resolve_media(
                        hass, url, target_media_player=None,
                    )
                    url = resolved.url
                    if url.startswith("/"):
                        from homeassistant.helpers.network import get_url
                        base = get_url(hass, prefer_external=False, allow_internal=True)
                        url = f"{base}{url}"
                except Exception as e:
                    _LOGGER.error("Failed to resolve media_source URL: %s", e)
                    return
        else:
            url = await _render_tts(
                hass, msg,
                language=call.data.get("language"),
                tts_service=call.data.get("tts"),
            )
            if not url:
                return

        _LOGGER.info("announce: %r on %s @ vol %d", msg[:40], speakers, volume)
        await _do_announcement(hass, speakers, url, volume, wait_s, duck_resume)

    async def svc_broadcast(call: ServiceCall) -> None:
        """Announce on EVERY Lithe speaker.

        Same fields as announce, minus 'speakers' (always all).
        """
        # Inject 'speakers = all' and delegate
        speakers = await _all_lithe_hosts(hass)
        if not speakers:
            _LOGGER.warning("broadcast: no Lithe speakers found")
            return
        new_data = dict(call.data)
        new_data["speakers"] = speakers
        # Fake a ServiceCall-like by re-invoking svc_announce
        # NB: ServiceCall is read-only so we route through the service registry
        await hass.services.async_call(
            DOMAIN, "announce", new_data, blocking=True,
        )

    async def svc_doorbell(call: ServiceCall) -> None:
        """Doorbell-style notification: play a chime, optionally followed
        by a TTS message, ducking prior playback briefly.

        Fields:
          chime          — chime slot (1-14) — default 1
          message        — optional TTS to follow the chime
          speakers       — host list (default: all)
          volume         — 0-100 (default 75)
          language, tts  — as in announce
        """
        speakers = call.data.get("speakers") or await _all_lithe_hosts(hass)
        if isinstance(speakers, str):
            speakers = [s.strip() for s in speakers.split(",") if s.strip()]
        if not speakers:
            return
        chime_slot = int(call.data.get("chime", 1))
        volume = int(call.data.get("volume", 75))

        # Step 1: chime via tannoy override (uses MB#80 play N)
        # We construct a special URL form recognised by tannoy as a chime
        # request via the existing play_chime helper instead.
        bucket = hass.data.get(DOMAIN, {})
        coords = []
        for _entry_id, entry_data in bucket.items():
            if not isinstance(entry_data, dict):
                continue
            coord = entry_data.get(DATA_COORDINATOR) or entry_data.get("coordinator")
            if coord and coord.client.host in speakers:
                coords.append(coord)

        # Save current vol per speaker, set chime vol, play chime
        prior_vols: dict[str, int] = {}
        for c in coords:
            prior_vols[c.client.host] = c.client.state.volume
            try:
                await c.client.async_set_volume(volume)
                await c.client.async_play_chime(chime_slot)
            except Exception as e:
                _LOGGER.warning("Doorbell chime on %s failed: %s",
                                c.client.host, e)

        await asyncio.sleep(DEFAULT_CHIME_WAIT_S)

        # Step 2: TTS announcement (if message provided)
        msg = (call.data.get("message") or "").strip()
        if msg:
            url = await _render_tts(
                hass, msg,
                language=call.data.get("language"),
                tts_service=call.data.get("tts"),
            )
            if url:
                # Use direct URL via play_url on each speaker
                for c in coords:
                    try:
                        await c.client.async_play_url(url)
                    except Exception as e:
                        _LOGGER.warning("Doorbell TTS on %s failed: %s",
                                        c.client.host, e)
                await asyncio.sleep(float(call.data.get(
                    "wait_seconds", DEFAULT_ANNOUNCE_WAIT_S)))

        # Step 3: restore prior volumes
        for c in coords:
            prev = prior_vols.get(c.client.host)
            if prev is not None:
                try:
                    await c.client.async_set_volume(prev)
                except Exception:
                    pass

    hass.services.async_register(DOMAIN, "announce",  svc_announce)
    hass.services.async_register(DOMAIN, "broadcast", svc_broadcast)
    hass.services.async_register(DOMAIN, "doorbell",  svc_doorbell)


def register_announce_services(hass: HomeAssistant) -> None:
    """Entry point called from __init__."""
    _register_services(hass)
