# Changelog

## 1.4.74

- Apply the MB111 raw MCU tunnel transport to `WiFiMicroSubwoofer` as well as
  WiFi Speaker V2 and `WiFiPROCeilingSpeaker`.
- Send Micro profile, gain, crossover, phase and low-pass writes through the
  LS9 tunnel and read its complete live DSP report back through that tunnel.
- Retain the documented RemoteID `0xAAAA` MB112 fallback when an LS9 firmware
  build cannot create the MB111 listener.

## 1.4.73

- Restore the WiFi Speaker V2 primary media player to the top of Home
  Assistant's Controls panel using its original `A Player` entity sort name.
- Send WiFi Speaker V2 and `WiFiPROCeilingSpeaker` EQ, balance, output,
  loudness and night-mode commands through the vendor-documented MB111 raw
  MCU tunnel instead of treating network MB112 as the primary command path.
- Retain a documented RemoteID `0xAAAA` MB112 fallback for early LS9 firmware
  that does not expose the tunnel listener.
- Keep the WiFi Micro Subwoofer's verified direct MCU route and every LS10
  model path unchanged.

## 1.4.72

- Read the live MCU DSP state after a WiFi v3 or WiFi PRO 2 EQ preset change,
  so treble, mid and bass immediately reflect the values applied by the
  speaker instead of retaining the previous slider positions.
- Retry the non-blocking startup DSP read when the MCU tunnel is not ready on
  its first attempt, allowing Speaker Output and the other DSP controls to
  initialize from the live speaker state rather than restored HA state.
- Keep the LS9 transport and model-specific controls unchanged.

## 1.4.71

- Fix LS9/Micro Subwoofer MB112 EQ writes and live-state requests by routing
  them to the MCU with RemoteID `0x0000`; LS10 transport remains unchanged.
- Stop creating all favourite-name and save-name text entities for
  `WiFiMicroSubwoofer`, and remove stale copies from the entity registry.
- Remove Alarms and Prayer Schedule from the Micro Subwoofer options menu,
  exclude the model from alarm target choices and prevent prayer schedules
  from starting for its config entry.
- Preserve grouping and ordinary media playback for the Micro Subwoofer.

## 1.4.70

- Remove the sunrise/light-ramp section from the alarm create/edit form.
- Remove the light-ramp scheduler so Lithe speaker alarms remain audio-only.
- Migrate saved alarms by deleting legacy lighting fields during startup,
  preventing old configurations from controlling lights invisibly.

## 1.4.69

- Add dedicated `WiFiMicroSubwoofer` EQ profiles: Subwoofer, Speaker and
  Custom, backed by the Micro's MB112 profile command.
- Expose Custom-only gain (0-100), crossover (60/120/180/240 Hz), phase
  (0/180 degrees), low-pass filter and Save Changes controls.
- Request the complete live Micro DSP state on refresh and decode its MB112
  profile, gain, crossover, phase and low-pass feedback independently from
  the LS10 speaker EQ protocol.
- Make Save Changes re-apply the complete Custom profile atomically from the
  user's perspective; the firmware API has no separate persistence opcode.
- Show the Micro profiles in the media player's sound-mode control and keep
  all existing LS10 model behaviour unchanged.

## 1.4.68

- Remove favourites from `WiFiMicroSubwoofer`: no MB70 polling, play/save
  buttons, save-slot selector, source-menu actions, Browse Media entries or
  custom-card favourite controls are exposed for that model.
- Remove stale Micro Subwoofer favourite entities from Home Assistant's
  entity registry during integration reload; other models are unchanged.

## 1.4.67

- Fix EQ/DSP writes on the plain-TCP LS9 products
  `WiFiPROCeilingSpeaker`, `WiFiCeilingSpeakerV2` and
  `WiFiMicroSubwoofer`: MB112 binary commands now use an LS9 transactional
  connection and the documented `0xAAAA` route instead of being dropped by
  the LS10 persistent-writer guard.
- Keep the TLS/client-certificate LS10 DSP path unchanged.

## 1.4.66

- Send both mute and unmute as explicit `MUTE` / `UNMUTE` SET commands on
  MB40 (`PLAYCONTROL`) for every source.
- Use MB63 (`CASTMUTE_STATUS`) as the sole mute-state authority; volume
  feedback no longer fabricates or overrides mute state.
- Remove the Spotify MB64 volume-zero mute workaround introduced in 1.4.64.

## 1.4.65

- Fix all three iO1 EQ band writes by using the documented MB112 SET
  selectors `0x02`, `0x04` and `0x06`; `0x01`, `0x03` and `0x05` are the
  corresponding GET selectors and do not apply slider changes.
- Add byte-exact coverage for the iO1 preset, balance, speaker-output and EQ
  band setter payloads.

## 1.4.64

- Avoid the CR443GP_4083 Spotify firmware mute latch that acknowledges MB40
  `UNMUTE` while leaving audible output closed.
- Mute Spotify through MCU-routed MB64 volume `0` and restore the captured
  non-zero volume on unmute; AirPlay and Cast retain their verified MB40
  command routes.
- Make redundant Spotify unmute requests no-ops so alarm and notification
  preparation cannot silence an already-playing stream.

## 1.4.63

- Fix iO1 Treble Low, Treble Mid and Treble High writes by using the iO1
  MB112 band selectors `0x01`, `0x03` and `0x05` before the gain value.
- Preserve the generic `0x02`, `0x04` and `0x06` EQ selectors for WiFi PRO 2,
  WiFi V3 and the other existing model profiles.

## 1.4.62

- Remove the unsupported Loudness and Night Mode controls from iO1 speakers.
- Remove stale iO1 Loudness and Night Mode entities created by older releases
  when the integration reloads. WiFi PRO 2 and WiFi V3 controls are unchanged.

## 1.4.61

- Restore the iO1-specific EQ preset choices: Outdoor, Indoor and Pendent.
- Replace the generic iO1 Treble/Mid/Bass labels with Treble Low 2 kHz,
  Treble Mid 4 kHz and Treble High 6 kHz controls.
- Use the documented iO1 EQ range of -6 to +6 dB and keep all three bands
  adjustable for every iO1 preset. Existing entity IDs are preserved.
- Keep the iO1 balance command on the documented MB112 balance path while
  isolating all iO1 EQ behavior from the WiFi PRO 2 and WiFi V3 profiles.

## 1.4.60

- Hide the Cast Group selector when Home Assistant has no real Google Cast
  group available, and remove stale selector entities from earlier versions.
- Keep the selector when a group is available; selecting it overrides future
  regular URL playback by forwarding it through that Cast group.

## 1.4.59

- Restore all fourteen embedded chime controls for WiFi V3 speakers.
- Add Edit and Delete actions to the saved Prayer Schedule details screen.
- Keep live EQ band positions and dB values visible for non-Normal presets
  while continuing to reject manual band writes outside Normal mode.
- Confirm that PRO 2 and V3 Cast diagnostics omit RSSI fields; these models
  continue to report Unknown rather than displaying fabricated signal data.

## 1.4.58

- Fixed scheduled alarms, sunrise ramps, snoozes, and prayers using timer
  callbacks that Home Assistant can safely execute on its event loop.
- Added a Prayer Schedule details view showing saved prayers, resolved times,
  recurrence, volume, audio, speaker, and live timer registration status.

## 1.4.57

- Restored alarm chime selection to the full LUCI range of slots 1 through 14.
- Decoupled alarm slot validation from the model-specific number of visible chime buttons.

## 1.4.56

- Fix enabled one-off alarms with a blank date silently receiving no scheduler
  callback. Existing alarms now fire at the next occurrence of their selected
  time, and newly saved alarms persist that calculated date explicitly.

## 1.4.55

- Fix prayer, Adhan, Quran and tannoy playback resolving IPv4 speaker targets
  as if they were Home Assistant entity IDs. This previously rejected every
  IP address before sending a LUCI command.
- Send Direct URL playback to every selected Lithe speaker instead of only
  preparing all speakers and playing on the first one.
- Verify each target's transition to Direct URL and identify the failed host
  in Home Assistant logs when firmware does not switch its audio source.

## 1.4.54

- Move the Spotify compatibility probe off Home Assistant's event loop, fixing
  the blocking `listdir`, `read_text` and `open` warnings on Core 2026.10.
- Stop issuing a second full speaker refresh immediately after connection.
- Update device-registry iteration for the Home Assistant 2027.9 API removal.
- Treat unsupported/empty DSP startup tunnels as debug diagnostics instead of
  repeated user-facing warnings; live MB#112 feedback remains active.
- Create the group manager before media-player entities and reset its lifecycle
  flags on the final unload, so saved groups survive integration reloads.
- Correct alarm snooze and fade callback cleanup during dismiss, edit and
  shutdown.
- Prefer the product detected from live LUCI model data when numeric model
  fields are empty, preventing WiFi v3 speakers from inheriting PRO 2-only
  controls and chimes.
- Remove stale chime entities above the detected model's supported slot count.
- Keep restored DSP controls internally coherent when firmware returns no
  startup state records, without transmitting restored values to the speaker;
  subsequent live MB#112 feedback remains authoritative.
- Log the install/version check once, move local-IP detection off the event
  loop, and point documentation and issue links at the current official repo.

## 1.4.53

- Move the live HOST-MCU DSP read out of the blocking integration setup path.
  It now runs once as a bounded delayed task after connection instead of on
  every 30-second coordinator refresh.
- Continue using LUCI MB#112 pushes for immediate changes made in the Lithe
  app after the initial live state has been read.

## 1.4.52

- Keep Loudness, High Pass Filter and Night Mode operable whenever the speaker
  is connected, even while their first live DSP feedback record is pending.
- Keep non-Normal preset EQ bands visible at their live dB positions while
  locking their slider ranges against manual changes.

## 1.4.51

- Remove the visible numeric prefixes from audio control names while keeping
  their requested order on Home Assistant's alphabetically sorted device page.
- Change the manual EQ order to Preset, Treble, Mid and Bass.

## 1.4.50

- Order PRO 2 audio controls as EQ preset, bass, mid, treble, balance,
  speaker output, loudness, high pass, night mode and Cast group.
- Lock the manual bass, mid and treble sliders whenever the active EQ preset
  is not Normal, while retaining their live displayed values.
- Preserve the existing, proven MB#112 write path for high-pass frequency and
  loudness gain; the MB#111 tunnel remains read-only for live startup sync.

## 1.4.49

- Read the current DSP/EQ state live from the speaker's HOST MCU through the
  documented MB#111 raw TCP tunnel and `All_informationGET` request. The live
  read runs on initial connection and each coordinator refresh.
- Stop restoring DSP controls from Home Assistant storage at startup.
- Treat `00 03` MCU records as GET requests, not state values, preventing
  request field IDs from appearing as false ON states.
- Decode the live report for EQ bands/preset, loudness and gain, night mode,
  balance, output mode, high-pass frequency, and high-pass enable/tuning.

All notable changes to this project will be documented in this file.

## [1.4.48] - 2026-10-08

### Fixed

- Restore High Pass, Loudness and Night Mode into the shared DSP state used
  by their dependent entities, without transmitting anything to the speaker.
- Make High Pass Frequency available whenever the restored High Pass Filter
  is on.
- Disable Loudness Gain whenever the restored Loudness switch is off.
- Group the tone controls alphabetically as EQ Bass, EQ Mid, EQ Preset and
  EQ Treble on the Home Assistant device page.

## [1.4.47] - 2026-10-08

### Fixed

- Persist successfully transmitted Home Assistant DSP changes as well as
  live MB112 broadcasts. The firmware returns only `SUCCESS` to the sending
  client and does not echo the binary state record back to it.
- Use Home Assistant's native delayed `Store` writer for MB112 bursts instead
  of cancelling and recreating background save tasks.
- Flush the current DSP snapshot when the integration unloads.

## [1.4.46] - 2026-10-08

### Fixed

- Stop treating MB112 subcommand `0x15` as a startup settings read. Direct
  testing on CR443GP_4083 confirms that it returns only `SUCCESS`, not the
  current DSP values.
- Persist every genuine MB112 DSP update received from the speaker or Lithe
  app and restore that speaker-confirmed snapshot across Home Assistant
  restarts without transmitting it back to the speaker.
- Mark diagnostics as `restored` or `speaker` so startup memory can be
  distinguished from fresh LUCI feedback.

## [1.4.45] - 2026-10-08

### Fixed

- Register Home Assistant as its own LUCI app instead of reusing the
  `control4` app ID, preventing the two clients from replacing each other in
  the speaker's MB112 push-registration table.
- Decode the documented `All_informationGET` field IDs for Bass, Mid and
  Treble so the startup DSP report populates the corresponding sliders.
- Restore the last speaker-confirmed EQ, output, balance, high-pass and
  loudness values as a temporary UI fallback while waiting for the startup
  report.
- Never transmit restored high-pass or loudness values during startup; the
  speaker report remains authoritative.
- Repair the partial-install freshness markers so current files are not
  falsely reported as stale.

## [1.4.44] - 2026-10-07

### Fixed

- Remove stale High Pass Filter, High Pass protection and Loudness Gain
  entities from WiFi V3 devices after upgrading from an older release.
- Preserve the working WiFi PRO 2 High Pass Filter and Loudness Gain entities
  without deleting and recreating them during every integration reload.

## [1.4.43] - 2026-10-07

### Changed

- Label PRO 2 chimes 11-14 as Warning, Dinner, Breach and Warning.
- Show Spotify, No Source, Bluetooth and Aux in the player source chooser when
  the speaker model supports them.
- Add Save Current Track and named favourite playback entries to the source
  chooser, and remove the misleading stock Home Assistant Join button.
- Transfer the current official Spotify session when Spotify is selected and
  request the LUCI AUX source when Aux is selected.

## [1.4.42] - 2026-10-07

### Fixed

- Inline only Music Assistant's Artists, Albums, Tracks, Playlists, Radio
  Stations, Podcasts and Audiobooks folders. Music Assistant's copies of
  Camera, My Media, Radio Browser, Text-to-speech and image sources are now
  ignored because the native Home Assistant media-source root adds them once.

## [1.4.41] - 2026-10-07

### Fixed

- Recreate and re-register the alarm manager and its services after a full
  integration reload. Previously the services were removed while the stale
  manager remained, causing alarm management and scheduling to stop working.
- Cancel the alarm manager's real Home Assistant event subscriptions during
  shutdown, then create fresh callbacks when alarms are reloaded from storage.
- Keep independent prayer schedulers per Lithe config entry so configuring or
  reloading one speaker no longer replaces every other speaker's schedule.
- Remove disabled or unloaded prayer schedules without disturbing schedules
  belonging to other speakers.
- Reset service-registration guards on final unload so Tannoy, announcements,
  snapshots and other playback helpers return after an options reload.

### Added

- Add a `Save and test now` alarm action that exercises the same playback
  configuration immediately before relying on its scheduled fire time.

## [1.4.40] - 2026-10-07

### Fixed

- Persist and reapply the last confirmed PRO 2 High Pass Frequency and
  Loudness Gain values after integration or speaker startup.
- Make High Pass Frequency available only while the separate High Pass
  Filter switch is on.

### Added

- Expose PRO 2 chime slots 11-14. Slots 10-14 use the working legacy
  `songN.wav` MB#80 payload required by the firmware's two-digit parser.

## [1.4.39] - 2026-10-07

### Fixed

- Reinstate the PRO 2 High Pass Frequency selector with 60, 80, 100 and
  120 Hz choices alongside the separate High Pass Filter on/off switch.
- Restore and reapply the last confirmed PRO 2 Loudness Gain after startup
  when firmware omits gain subcommand `0x34` from its MB#112 state report.
- Prepare alarm playback by explicitly unmuting the hardware audio path,
  pausing an active stream when required, setting volume, and rejecting
  empty URL alarms with a clear log message.
- Run scheduled and test call-to-prayer playback through the blocking
  `lithe_audio.tannoy` service so target, URL and playback errors are logged
  instead of being lost through the legacy fire-and-forget notify wrapper.
- Schedule prayer days using Home Assistant's configured local timezone.
- Keep the live PrayerScheduler object separate from its displayed prayer
  times. The previous shared `hass.data` key broke the next midnight refresh
  and could also break the Prayer options screen.

## [1.4.38] - 2026-10-07

### Fixed

- Fall back to LUCI MB#209 `ON`/`OFF` when the speaker's Bluetooth HTTP
  handler times out, rejects the request, or its web state cannot confirm the
  change. Query MB#210 afterwards and protect the requested state from stale
  status packets while the Bluetooth service starts or stops.
- Prevent a stale cached `getbtvalue` from turning a successful Bluetooth
  action into a Home Assistant service failure.

## [1.4.37] - 2026-10-07

### Fixed

- Send PRO 2 chime slot 10 as the legacy working MB#80 payload
  `song10.wav`. Its older firmware parser does not reliably handle the
  two-digit indexed `play 10` form used by slots 1-9.

## [1.4.36] - 2026-10-07

### Fixed

- Request the MCU's complete current settings report with the firmware-backed,
  non-mutating MB#112 subcommand `0x15` during refresh. High Pass Filter,
  Loudness, Night Mode and Loudness Gain now initialise from the speaker's
  actual state instead of waiting for a later app change.
- Send and receive PRO 2 Loudness Gain on subcommand `0x34`. Firmware
  disassembly confirms `0x34` invokes `SetLoudness` with its `0..20` input;
  the previous `0x16` write is routed to the unsupported-command path.

## [1.4.35] - 2026-10-07

### Fixed

- Decode both binary and ASCII MB#112 state broadcasts for Loudness, Night
  Mode and the PRO 2 High Pass Filter switch, and restore their last confirmed
  Home Assistant state while waiting for the first speaker broadcast.
- Decode both PRO 2 loudness-gain feedback subcommands on the MCU's unsigned
  `0..20` scale so the `-10..+10 dB` slider no longer jumps to a false value.
- Remove obsolete Cast group proxy entities and the superseded PRO 2 high-pass
  frequency selector from the device page.
- Label chime slots 1-4 Bell, 5-6 Alarm and 7-10 Siren. Slot 10 is sent as the
  vendor-specified ASCII command `play 10` with a seven-byte payload.

## [1.4.34] - 2026-10-07

### Fixed

- Prevent the dynamic Favourite Save Slot selector from aborting the complete
  select platform. EQ Preset, High Pass Frequency, Speaker Output and Cast
  Group controls now load normally again.
- Translate the PRO 2 Loudness Gain UI range from `-10..+10 dB` to the MCU's
  required unsigned `0..20` table index before sending MB#112 subcommand
  `0x16`. Keep the selected value stable while awaiting device feedback.

## [1.4.33] - 2026-10-07

### Fixed

- Restore Music Assistant Artists, Albums, Tracks, Playlists, Radio Stations,
  Podcasts and Audiobooks when multiple loaded Music Assistant players make
  an exact Lithe-to-Music-Assistant name match ambiguous. The global library
  browse now uses a deterministic loaded Music Assistant entity as fallback.
- Discover Music Assistant players from Home Assistant's live state machine
  when a release does not expose them through the media-player component, and
  log a clear prerequisite warning when no loaded player entity exists.

## [1.4.32] - 2026-10-07

### Added

- Restore the PRO 2 loudness gain slider (`-10..+10 dB`) using the verified
  `0x16` TX command and `0x34` offset feedback, available while Loudness is on.
- Add a named Favourite Save Slot selector covering slots 1-10 and a Save
  Name field tied to the selected slot.
- Make Browse Media's Save Current Track entry expand into all ten named slots.
- Detect loaded Music Assistant players in both the entity registry and the
  live media-player component.

### Changed

- Present the PRO 2 `High Pass Filter` as a plain ON/OFF switch and rename the
  frequency selector to High Pass Frequency.
- Keep favourite play controls in slot order, displaying custom names as a
  suffix such as `Play 1 — Oskar`.

### Fixed

- Load favourite storage before entity platforms so custom names are present
  when Play controls and the save-slot selector are added to Home Assistant.
- Remove obsolete per-slot Save buttons and the replaced Speaker Tuning select
  from the entity registry during setup.

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
