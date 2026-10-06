# Changelog

All notable changes to this project will be documented in this file.

## [1.4.31] - 2026-10-06

### Fixed

- Apply editable favourite names to the corresponding Save and Play button
  labels, with live entity refresh after a rename.
- Treat a renamed but empty slot as available, so naming slot 1 "Oskar"
  before saving stores the next favourite in slot 1 and preserves that name.
- Save URL playback locally as well as attempting the native MB#70 save;
  Spotify and AirPlay sessions without a reusable URL still use MB#70.

### Added

- Add a playable "Save Current Track" action to the Lithe speaker's Browse
  Media root, targeting the next empty or pre-named favourite slot.

## [1.4.30] - 2026-10-06

### Fixed

- Replace the unsupported PRO 2 MB#112 subcommands `0x1A` and `0x1D` with
  firmware-backed commands: `0x32` for high-pass frequency and `0x0D` for
  13L enclosure/open-back protection.
- Correct high-pass values to `0=60 Hz`, `1=80 Hz`, `2=100 Hz`, and
  `3=120 Hz`; the firmware also supports value `4=150 Hz`, which is not
  exposed because it is absent from the requested PRO 2 control surface.
- Record raw and decoded MB#112 feedback in integration diagnostics and log
  each decoded packet at info level for bench verification.

## [1.4.29] - 2026-10-06

### Fixed

- Detect current Music Assistant media players registered under the `mass`
  integration domain, restoring their library folders in Lithe Browse Media.
- Match Music Assistant players by friendly name or entity ID, with a safe
  fallback when Home Assistant contains exactly one Music Assistant player.

## [1.4.28] — 2026-10-06

### Added

- Restore the WiFi PRO 2 high-pass filter selector with Off, 60 Hz, 80 Hz,
  100 Hz, and 120 Hz options using the MCU-bound MB#112 `0x1A` command.
- Restore the WiFi PRO 2 speaker tuning selector with Enclosure Mode 13L and
  Open Back Mode using the MCU-bound MB#112 `0x1D` command.
- Add editable Home Assistant text entities for favourite slots 1-9. Names
  apply to both HA-stored URLs and native MB#70 favourites.

### Fixed

- Obtain LS10/PRO 2 signal strength from read-only Cast diagnostics when LUCI
  MB#151 is unavailable, as documented for LS10/11.
- Derive Wi-Fi band from reported frequency or channel and only expose real
  `2.4 GHz`, `5 GHz`, or `6 GHz` values instead of the generic `Wi-Fi` label.
- Apply favourite name overrides consistently in Browse Media, media-player
  attributes, and the source selector.

## [1.4.27] — 2026-10-06

### Added

- Read the `Model`, `Model_num`, and `ModelVariant` non-volatile values through
  LUCI MB#208 during startup.
- Expose the speaker-reported hardware model as a diagnostic sensor and include
  all model identifiers in integration diagnostics.
- Parse the modern LSSDP `CAST_MODEL` and `SPEAKERTYPE` discovery headers.
- Select the matching model capability profile when `Model_num` or
  `ModelVariant` identifies a known speaker without changing transport
  families; a generic platform label cannot overwrite a manual selection.

### Fixed

- Serialize MB#208 reads and correlate keyed responses so an unsolicited
  `Model:...` push cannot be mistaken for an SSID response.
- Prefer a specific `ModelVariant` such as PRO2 or iO1 over the generic
  `WiFi v3` platform identifier.

## [1.4.26] — 2026-10-06

### Added

- Show the matching Music Assistant player's Artists, Albums, Tracks,
  Playlists, Radio stations, Podcasts, and Audiobooks directly in the Lithe
  Browse Media root.
- Delegate Music Assistant folder navigation and selected-item playback back
  to its loaded media-player entity, preserving provider and library URIs.

### Fixed

- Keep expandable Home Assistant media-source folders during audio filtering;
  previously valid directory categories could disappear before users could
  browse their playable children.
- Restore Camera to the media root and retain camera/video streams that may
  contain a speaker-compatible audio track.

## [1.4.25] — 2026-10-06

### Fixed

- Serialize Bluetooth ON/OFF requests so rapid Home Assistant actions cannot
  race each other.
- Verify the confirmed `SetBluetoothmode` HTTP service flag after each change
  and retry once when the speaker still reports its previous value.
- Stop treating delayed MB210 `BT:READY` packets as authoritative radio state.
- Remove the immediate MB210 GET that could reverse a successful HTTP OFF.

## [1.4.24] — 2026-10-02

### Fixed

- Make MB63 `MUTE`/`UNMUTE` the sole authority for the displayed mute state.
- Remove optimistic mute-state changes after sending MB40 commands and while
  changing volume.
- Stop polling MB63 with an unsupported GET on connect/refresh.
- Keep MB64 volume feedback independent from mute state.
- Use exact RemoteID `0xAAAA` MB40 frames for normal sources and the
  bench-noted `0x0000` candidate only while Google Cast source 24 is active.

## [1.4.23] — 2026-10-02

### Fixed

- Stop marking the speaker unmuted optimistically; MB63 `UNMUTE` is now the
  authoritative confirmation.
- Retry an unconfirmed Spotify/AirPlay MB40 `UNMUTE` through RemoteID `0x0000`
  while retaining `0xAAAA` as the first, capture-verified route.
- Repair the alarm add/edit form by importing its coordinator data key.
- Use each product's actual chime-slot capability in the alarm editor, including
  all 15 slots on PRO 2.

## [1.4.22] — 2026-10-02

### Fixed

- Send MB40 ASCII `MUTE`/`UNMUTE` with RemoteID `0x0000` while Google Cast
  (source 24) is active; retain the verified `0xAAAA` route for Spotify,
  AirPlay, and other sources.
- Apply the same source-aware route when a non-zero volume command first
  unmutes the speaker.
- Add byte-exact protocol coverage for both routes and MB63 text feedback.

## [1.4.21] — 2026-10-02

### Fixed

- Remove the zero-volume and duplicate-UNMUTE fallback that raced Spotify and
  left its stored volume at zero after the initial UNMUTE had already worked.
- Use the log-confirmed MB40 `UNMUTE` on RemoteID `0xAAAA`; restore MB64 only
  when device feedback still reports zero after a settling delay.

## [1.4.20] — 2026-10-02

### Removed

- Remove Apple TV entities and app folders from Lithe Browse Media because
  selecting them controls the Apple TV rather than playing through Lithe.
- Remove the unused Apple TV content bridge and optional startup dependency.

## [1.4.19] — 2026-09-30

### Fixed

- Clear both the application-side (`0xAAAA`) and MCU-side (`0x0000`) mute
  latches when unmuting.
- Restore volume using the captured working `0 -> saved level` MB64 sequence,
  followed by a final MCU-routed `UNMUTE`.
- Apply the dual-route unmute before a non-zero volume change while muted.

## [1.4.18] — 2026-09-30

### Fixed

- Restore audible output after unmute with an MCU-routed MB64 wake step followed
  by the saved volume, preventing firmware from discarding an unchanged value.
- Use RemoteID `0x0000` for audible volume writes on both LS9 and LS10.
- Preserve the last non-zero volume for externally initiated mute states and
  ignore empty MB63 acknowledgements instead of treating them as unmuted.
- Setting a non-zero volume while muted now explicitly unmutes first.

## [1.4.17] — 2026-09-30

### Fixed

- Add a version-gated `spotifyaio 2.0.2` compatibility shim for Spotify's
  2026 playlist schema: metadata-only playlists no longer fail on a missing
  nested `items` list, and playlist entry `item` fields are normalized to the
  legacy `track` field expected by the library.
- Automatically retry failed official Spotify config entries after applying
  the shim, restoring account-scoped Browse Media without a manual reload.
- Stop deleting the Cast group proxy entities used by the Join picker during
  each Lithe speaker setup.

## [1.4.16] — 2026-09-30

### Fixed

- Add repository-level `brand/` icon and logo assets for the HACS repository
  dashboard, including light, dark, and high-resolution variants.
- Retain the same assets under `custom_components/lithe_audio/brand/` for
  Home Assistant's local brands API on Home Assistant 2026.3 and later.

## [1.4.15] — 2026-09-30

### Added

- Show each loaded Home Assistant Apple TV entity in Lithe Browse Media and
  expose its official Apps/source list. Selecting an app delegates to the
  Apple TV entity's `select_source` action.
- Create hidden proxy entities for discovered Google Cast speaker groups so
  Home Assistant's Join picker displays Cast groups alongside Lithe speakers.

### Notes

- Apple TV exposes installed apps, not an Apple Music library catalogue.
- Selecting a Cast group routes subsequent compatible media through its
  underlying Home Assistant Cast media-player entity.

## [1.4.14] — 2026-09-30

### Fixed

- Name the primary media-player entity after its speaker instead of the
  hard-coded sorting label `A Player`.
- Populate the source selector with verified actions: release the audio path,
  enable Bluetooth on supported products, and play populated favourites.
- Keep an externally-owned current source visible without pretending MB#50
  can launch Spotify, AirPlay, Cast, or other provider playback pipelines.

## [1.4.13] — 2026-09-30

### Fixed

- Work around the upstream `spotifyaio` artist-albums and playlist-tracks
  parsing failures by exposing artist and playlist results as playable Spotify
  contexts instead of broken expandable folders.
- Remove Home Assistant's account scope from delegated browse IDs before
  playback, producing the native `spotify:artist:...` and
  `spotify:playlist:...` URIs expected by the official Spotify player.

## [1.4.12] — 2026-09-30

### Fixed

- Remove the version-sensitive media-player `DATA_COMPONENT` import added in
  1.4.11, which could prevent the entire Lithe integration from loading.
- Browse Spotify through its official account-scoped browse helper using the
  Spotify entity registry entry's config-entry ID. This no longer depends on
  retrieving another integration's live entity object.
- Keep Spotify imports inside the browse action so missing or incompatible
  optional Spotify code cannot break normal Lithe setup.

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
