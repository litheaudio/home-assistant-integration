# Changelog

All notable changes to this project will be documented in this file.

## [1.4.11] — 2026-09-30

### Fixed

- Resolve the official Spotify entity through Home Assistant's typed media
  player `DATA_COMPONENT` key. Looking up `hass.data["media_player"]` could
  return no component even though `media_player.spotify` was loaded and its
  state was visible.
- Keep the legacy string-key lookup as a fallback for older Home Assistant
  releases.

## [1.4.10] — 2026-09-30

### Fixed

- Build the Spotify library root inside the Lithe integration instead of
  delegating an empty root request whose behavior varies between Home
  Assistant Spotify integration versions.
- Delegate only concrete Spotify category, album, playlist, artist, show, and
  track requests to the official Spotify entity.
- Log delegated browse exceptions with their media type, content ID, and
  Spotify entity, and return a useful Home Assistant browse error.

## [1.4.9] — 2026-09-30

### Fixed

- Translate the synthetic Lithe Spotify folder to `(None, None)` when opening
  the official Spotify browser root. Passing `spotify / None` caused Home
  Assistant to report `Media not found: spotify / None`.

## [1.4.8] — 2026-09-30

### Added

- Show every loaded official Home Assistant Spotify account as a folder in
  the Lithe media player's Browse Media tree.
- Delegate Spotify library browsing and playback to the official Spotify
  entity, preserving its OAuth and Premium account handling.
- Match the current Lithe speaker to the Spotify entity's Connect source list,
  transfer playback to it, and reject ambiguous device-name matches.
- Notify the user when the Lithe speaker is not currently visible as a Spotify
  Connect target instead of silently accepting a failed playback request.

## [1.4.7] — 2026-09-29

### Fixed

- Pass Home Assistant repeat enums by value and accept legacy enum strings, so
  `REPEAT:ALL` and `REPEAT:ONE` are no longer silently converted to OFF.
- Update shuffle/repeat state immediately and fetch MB#42 shortly afterwards
  to confirm the speaker state.
- Remember the pre-mute level and reapply it via MCU RemoteID `0x0000` after
  `UNMUTE`, fixing LS10 builds that leave the audible gain closed or at zero.
- Request track metadata at 0.15, 0.75, and 1.5 seconds after next/previous or
  an MB#49 rollover, reducing album-art delay at track boundaries.

## [1.4.6] — 2026-09-29

### Fixed

- Refresh track artwork using an explicit metadata-derived image hash. For
  firmware-generated `coverart.jpg`, use a per-track cache key and repeat the
  refresh after 3 and 6 seconds, matching the working Control4 driver.
- Use the confirmed `/goform/SetBluetoothmode` HTTP handler for Bluetooth
  service ON/OFF and mirror its web-page state. Pair/disconnect remain LUCI
  MB#209 operations.
- Query documented RSSI MB#151 through both observed RemoteIDs on LS10 and
  accept plain, dual-antenna, or labelled dBm responses. Diagnostics now show
  the responding route.

## [1.4.5] — 2026-09-29

### Fixed

- Preserve MB#49 millisecond precision in Home Assistant's `media_position`
  instead of flooring each push to a whole second. This prevents the playback
  clock repeatedly advancing and jumping backwards.
- Add raw position, fractional seconds, update timestamp, and packet age to
  integration diagnostics.

## [1.4.4] — 2026-09-29

### Fixed

- Send MB#112 DSP writes with RemoteID `0x0000`. Firmware acknowledges
  `0xAAAA` with `SUCCESS` but does not apply that path to the MCU/DSP.
- Corrected the captured EQ band selectors: Bass is field `0x06`, Mid is
  `0x04`, and Treble is `0x02`.
- Retained sign-magnitude encoding for negative values (`F1=-1` through
  `F6=-6`) and two-way MB#112 app broadcast parsing.

## [1.4.3] — 2026-09-28

### Fixed

- Replaced guessed DSP command IDs with values confirmed by a labelled live
  LUCI capture from the Lithe app: EQ bands `0x09`, EQ preset `0x0A`,
  Loudness `0x0B`, Night Mode `0x0C`, Balance `0x0E`, and Output `0x0F`.
- Added independent Bass, Mid, and Treble controls using field selectors
  `0x02`, `0x04`, and `0x06`.
- Removed the unverified High Pass, Speaker Tuning, and numeric Loudness
  controls. Loudness is now the captured on/off switch.

## [1.4.1] — 2026-05-21

### Changed

- **Controls panel ordering on the device page.** Entities now use grouped
  prefixes so the device page in Settings → Devices & Services groups
  related controls together in a predictable order:
  - `A Player` — the media_player card now sorts to the top
  - `Audio — …` — Balance, Cast Group, EQ Preset, High Pass Filter,
    Loudness, Night Mode, Speaker Output, Speaker Tuning
  - `Chimes — 01` … `Chimes — 15`
  - `Favourites — Play 1…9`, `Favourites — Save 1…9`,
    `Favourites — ♥ Save Current Track`
  - `Inputs — AUX In`, `Inputs — Bluetooth`, `Inputs — SPDIF In`
- Chime and favourite labels tidied: `Chimes — Chime 01` → `Chimes — 01`,
  `Favourites — Save to Favourite N` → `Favourites — Save N`,
  `Favourites — Play Favourite N` → `Favourites — Play N`.

### Notes

- `entity_id`s are unchanged — automations, scripts, and dashboards keep
  working without edits.
- Only display names changed. Users who renamed entities in the UI keep
  their custom names; to see the new prefixes they can reset the name
  (entity settings → reset to default).

## [0.1.0] — 2026-05-11

### Added

- Initial release
- Full local control of Lithe Audio speakers — no cloud dependency
- Push-driven state updates with automatic reconnection
- Network auto-discovery (LSSDP + zeroconf)
- One-click setup — no certificates or credentials required from the
  installer, even for models that use encrypted connections
- **Media player** with play / pause / stop / seek / volume / mute / source /
  now-playing / album art
- **Buttons** for built-in chimes (1-15 per model), preset slots 1-9, and
  speaker reboot
- **Switches** for mute, AUX line-in, and Bluetooth receiver mode
- **Sensors** for current source, now-playing string, firmware version, and
  raw play state
- **Number** entity for alternate volume control
- **Services**: `play_chime`, `play_preset`, `save_preset`, `delete_preset`,
  `play_direct`, `send_raw_command`, `reboot`
- **Brand assets** bundled with the integration (Home Assistant 2026.3+)
- **Diagnostics** support with automatic redaction of MAC addresses and
  serial numbers
# 1.4.2

- Match the working Control4 LUCI transport: lowercase `app_info` JSON
  registration on LS10/TLS, serialized 100 ms packet spacing, and selectable
  RemoteID values.
- Send LS10 volume writes with RemoteID `0x0000` so they reach the MCU gain
  path, and send mute/unmute through MB#40.
- Treat MB#50 as source feedback instead of a source-switch command.
- Keep chimes on the proven MB#80 `play N` path and treat MB#82 as feedback.
