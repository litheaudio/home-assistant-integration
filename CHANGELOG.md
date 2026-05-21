# Changelog

All notable changes to this project will be documented in this file.

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
