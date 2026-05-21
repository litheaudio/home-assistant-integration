# Lithe Audio for Home Assistant

Local, real-time control of Lithe Audio Wi-Fi speakers — **no cloud required**.

## ✨ Features

- 🎵 **Full media playback** — play/pause, volume, next/previous, shuffle, repeat
- 📻 **Browse Media tree** — favourites + 17 Adhan presets + 30 Quran Juz + BBC radio + HA media sources
- 🕋 **Prayer Scheduler** — automatic Adhan playback at calculated prayer times for your city
- ⏰ **Alarms** — daily/weekly/monthly with fade-in volume, multi-room targeting
- 🔊 **Multi-room Groups** — virtual group entities; play across multiple speakers simultaneously
- 🎙️ **Chime / Tannoy override** — built-in chimes, doorbell ducking, TTS announcements
- 🎛️ **DSP / EQ** — bass, treble, balance, loudness, night mode, output mode
- ❤️ **Heart-to-favourite** — one tap saves the currently playing track
- 📱 **Bluetooth pairing**, source switching, set-name from HA

## 🛠️ Supported speakers

WiFi PRO 2 · WiFi Speaker V3 · iO1 · WiFi Speaker V2 · WiFi PRO · Micro Subwoofer

## 🚀 Quick start

After install + HA restart:

1. **Settings → Devices & Services → + Add Integration → Lithe Audio**
2. Either auto-discovered via Cast, or enter the speaker's IP manually
3. Configure features via the **gear icon** on the integration card

## 📖 Documentation

Full setup guide, automation examples, and protocol details at the
[GitHub repository](https://github.com/litheaudio/ha-lithe-audio).

## 🔒 Local-only

100% local LUCI protocol over TCP/TLS on port 7777. No internet calls
(except optional `api.aladhan.com` for prayer time calculations, and the
audio URL streams you choose to play).
