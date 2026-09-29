# Lithe Audio — Home Assistant Integration

Direct, local control of Lithe Audio Wi-Fi speakers over the LUCI protocol on port 7777. No cloud, no bridge process, no portal — Home Assistant talks to each speaker directly.

**Latest: 1.4.5** — Smooth millisecond-accurate track position plus the audible-test-verified MCU/DSP controls from 1.4.4.

---

## ✨ Features

| | |
|---|---|
| 🎵 **Media playback** | Play/pause, volume, next/prev, shuffle, repeat, position tracking |
| 📻 **Browse media** | Favourites + Adhan + Quran + BBC + HA media sources (Radio Browser, TTS, local files) |
| 🕋 **Prayer Scheduler** | Daily Adhan at calculated prayer times for your city — 6 prayers including Sunrise & Sunset |
| ⏰ **Alarms** | Daily / weekly / monthly with per-day toggles, fade-in volume, multi-room targeting |
| 🔊 **Multi-room Groups** | Virtual group entities — play across multiple speakers simultaneously |
| 🎙️ **Chimes & Tannoy** | 10-15 built-in chimes per model, doorbell ducking, TTS announcements |
| 🎛️ **DSP / EQ** | Bass, mid, treble, balance, loudness, night mode, output mode |
| ❤️ **Favourites 1-9** | Heart button auto-saves currently playing track to next free slot |
| 🔵 **Bluetooth** | Pair / disconnect / status per speaker |
| 🔧 **Diagnostics** | Firmware, MAC, RSSI, SSID, network mode, uptime sensors |

---

## Supported speakers

| Product | Platform | TLS | Chimes | EQ/DSP | Loudness | Bluetooth |
|---|---|---|---|---|---|---|
| WiFi PRO 2 | LS10 | ✅ | 15 | EQ, Output, Balance | On/Off | ✅ |
| WiFi Speaker V3 | LS10 | ✅ | 6 | EQ, Output, Balance | On/Off | ✅ |
| iO1 | LS10 | ✅ | 10 | EQ, Output, Balance | On/Off | ✅ |
| WiFi Speaker V2 | LS9 | — | 0 | EQ, Output, Balance | On/Off | ✅ |
| WiFi PRO | LS9 | — | 6 | EQ, Output, Balance | On/Off | ✅ |
| Micro Subwoofer | LS9 | — | 0 | — | — | ✅ |

---

## Installation

### HACS

1. HACS → ⋮ → **Custom repositories**
2. Add `https://github.com/LitheAudio-Official/home-assistant-integration` as category **Integration**
3. Search **Lithe Audio**, install, restart HA
4. **Settings → Devices & Services → + Add Integration → Lithe Audio**

### Manual

Copy `custom_components/lithe_audio/` into your HA `config/custom_components/` and restart.

---

## Quick start

After installing, your Lithe speakers appear in **Settings → Devices & Services → Lithe Audio**.

Click **Configure** (gear icon) for:

```
📅  Prayer Schedule — Location & defaults
🕋  Prayer Schedule — Per-prayer settings
▶️  Test play an Adhan / Quran URL
📋  View today's schedule
⏰  Alarms — view, add, edit
🔊  Multi-room Groups — view, add, edit
🐞  Debug logging (for support)
```

### Multi-room groups

1. Configure → 🔊 Multi-room Groups → ➕ Add new group
2. Name it ("Downstairs"), pick member speakers
3. Save → reload integration
4. New entity: `media_player.lithe_group_downstairs`
5. Control like any media_player — play, volume, source all fan out to members

---

## Services

| Service | Description |
|---|---|
| `lithe_audio.play_chime` | Play built-in chime |
| `lithe_audio.play_url` | Stream any HTTP URL |
| `lithe_audio.play_favourite` | Play saved favourite (1-9) |
| `lithe_audio.play_quran_juz` | Play any of 30 Juz |
| `lithe_audio.play_adhan` | Play Adhan from preset dropdown |
| `lithe_audio.set_volume_preset` | Quick 0/20/40/60/80/100% |
| `lithe_audio.select_source_type` | Switch source by friendly name |
| `lithe_audio.alarm_*` | Alarm management (create/update/delete/toggle/snooze/dismiss) |
| `lithe_audio.group_*` | Group management (create/update/delete) |

---

## Local-only

100% local LUCI protocol. No cloud. Network access: outbound TCP/7777 per speaker, optional `api.aladhan.com` for prayer times, audio URLs you choose.

---

## License

MIT
