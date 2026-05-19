"""Constants for the Lithe Audio integration."""

DOMAIN = "lithe_audio"

# Config entry keys
CONF_HOST       = "host"
CONF_PORT       = "port"
CONF_PRODUCT    = "product"
CONF_USE_TLS    = "use_tls"
CONF_CERT_PATH  = "cert_path"
CONF_KEY_PATH   = "key_path"

# Default values
DEFAULT_PORT = 7777
DEFAULT_TLS  = True

# ── Products ────────────────────────────────────────────────────────────────
PRODUCT_PRO2   = "pro2"
PRODUCT_V3     = "wifiv3"
PRODUCT_IO1    = "io1"
PRODUCT_V2     = "wifiv2"
PRODUCT_PRO    = "wifipro"
PRODUCT_MICRO  = "micro"

PRODUCT_NAMES = {
    PRODUCT_PRO2:  "WiFi PRO 2",
    PRODUCT_V3:    "WiFi Speaker V3",
    PRODUCT_IO1:   "iO1",
    PRODUCT_V2:    "WiFi Speaker V2",
    PRODUCT_PRO:   "WiFi PRO",
    PRODUCT_MICRO: "Micro Subwoofer",
}

# LS10 = TLS 1.2, LS9 = plain TCP
LS10_PRODUCTS = {PRODUCT_PRO2, PRODUCT_V3, PRODUCT_IO1}
LS9_PRODUCTS  = {PRODUCT_V2, PRODUCT_PRO, PRODUCT_MICRO}

# ── Sources (MB#50 payload) ─────────────────────────────────────────────────
# Corrected to match LUCI API spec (v15.0.7)
SOURCES = {
    0:  "No Source",
    1:  "AirPlay",
    2:  "DMR",
    3:  "DMP",
    4:  "Spotify",
    5:  "USB",
    7:  "Melon",
    8:  "vTuner",
    9:  "TuneIn",
    11: "Playlist",
    13: "AUX In",
    14: "SPDIF In",
    17: "Direct URL",
    18: "QPlay",
    19: "Bluetooth",
    21: "Deezer",
    22: "Tidal",
    23: "Favourites",
    24: "Google Cast",
    27: "Roon",
    28: "Alexa",
    30: "Airable",
}

# Sources actually supported per product (used for source_list)
PRODUCT_SOURCES = {
    PRODUCT_PRO2:  [0, 1, 4, 9, 13, 14, 19, 21, 22, 23, 24, 27, 28, 30],
    PRODUCT_V3:    [0, 1, 4, 9, 19, 21, 22, 23, 24, 27, 28, 30],
    PRODUCT_IO1:   [0, 1, 4, 9, 21, 22, 23, 24, 27, 28, 30],
    PRODUCT_V2:    [0, 1, 4, 19, 24, 30],
    PRODUCT_PRO:   [0, 1, 4, 24, 30],
    PRODUCT_MICRO: [0, 1, 24],
}

# ── Message Box IDs ─────────────────────────────────────────────────────────
MB_REGISTER       = 3
MB_FIRMWARE       = 5
MB_HOST_PRESENT   = 9
MB_PLAYBACK_AUTH  = 10    # LSx → HOST: requests permission to switch source
MB_PLAYBACK_GRANT = 11    # HOST → LSx: grants (1) or denies (0) the source switch
MB_TRANSPORT      = 40
MB_BROWSE         = 41
MB_NOW_PLAYING    = 42
MB_ARTWORK        = 43
MB_POSITION       = 49
MB_SOURCE         = 50
MB_PLAY_STATE     = 51
MB_MUTE           = 63
MB_VOLUME         = 64
MB_FAVOURITES     = 70
MB_CHIME          = 80
MB_AUDIOCUE       = 82   # NEW: Audiocue lifecycle notifications (newer firmware)
                          # Speaker→host. Payloads observed:
                          #   "AUDIOCUE_START"  — chime is about to play,
                          #                       speaker pauses any music
                          #   "SUCCESS"         — chime finished, music resumes
                          #   "FAILURE" / "NI"  — slot empty or playback failed
MB_DEVICE_NAME    = 90
MB_NETWORK_INFO   = 91
MB_DSP            = 112
MB_REBOOT_REQ     = 114   # Reboot Request (was incorrectly 37)
MB_REBOOT_CMD     = 115
MB_INTERFACE_IP   = 123   # RxTx_MB#123 — current network interface + IP address
MB_NETWORK_STATUS = 124   # RxTx_MB#124 — WLAN/ETH/P2P active interface status
MB_FACTORY_RESET  = 150
MB_RSSI           = 151   # RxTx_MB#151 — WiFi signal strength (dBm)
MB_DEVICE_INFO    = 208   # Also used for NV Read/Write (READ_<NVitem>)
MB_BLUETOOTH      = 209
MB_BT_STATUS      = 210
MB_CAST_STATUS    = 572
MB_TIMEZONE       = 573

# ── MB#124 active network values ────────────────────────────────────────────
NETWORK_STATUS = {
    "1": "WLAN",
    "2": "Ethernet",
    "3": "P2P",
    "4": "WAC/SAC/LS-Connect",
}

# ── Transport commands (MB#40 payload) ──────────────────────────────────────
TRANSPORT_PLAY    = "PLAY"
TRANSPORT_PAUSE   = "PAUSE"
TRANSPORT_STOP    = "STOP"
TRANSPORT_RESUME  = "RESUME"
TRANSPORT_NEXT    = "NEXT"
TRANSPORT_PREV    = "PREV"

# ── Play states (MB#51 payload) ─────────────────────────────────────────────
PLAY_STATES = {
    "0": "playing",
    "1": "stopped",
    "2": "paused",
    "3": "connecting",
    "4": "buffering",   # receiving
    "5": "buffering",
}

# ── Mute commands (MB#63 payload) ───────────────────────────────────────────
MUTE_ON     = "MUTE"
MUTE_OFF    = "UNMUTE"
MUTE_TOGGLE = "MUTETOGGLE"

# ── Bluetooth commands (MB#209 payload) ─────────────────────────────────────
BT_ON      = "ON"
BT_OFF     = "OFF"
BT_PAIR    = "ENTPAIR"
BT_DISC    = "DISCONNECT"

# ── DSP sub-MB IDs (LS10 MB#112 tunnel) ─────────────────────────────────────
# Confirmed from live capture: PRO2 firmware CR443GP_3713
# ── DSP sub-MB IDs (tunneled inside MB#112) ─────────────────────────────────
# Verified against real firmware CR443GP_3713 packet captures.
# ── DSP sub-MB IDs (MB#112 payload first byte) ──────────────────────────────
# Confirmed via Lithe app packet capture (dsp-sniffer, 2026-05-17/18):
#   TX eq        sub-MB=0x000A val=0..4
#   TX loudness  sub-MB=0x0016 val=-10..+10 (signed byte)
#   TX nightmode sub-MB=0x0018 val=0/1
#   TX highpass  sub-MB=0x001A val=0(OFF), 1=60Hz, 2=80Hz, 3=100Hz, 4=120Hz
#   TX output    sub-MB=0x001C val=0=Mono, 1=Stereo, 2=Left, 3=Right
#   TX tuning    sub-MB=0x001D val=0/1
#   TX balance   sub-MB=0x001E val=-6..+6 (signed byte)
# Previous values (0x0C, 0x0D, 0x0F, 0x29, 0xFF, 0xFE) were guesses from
# earlier testing and incorrect — replaced with sniffed values.
DSP_EQ        = 0x0A   # 0=Normal 1=Acoustic 2=Jazz 3=Pop 4=HipHop (sniffed)
DSP_TREBLE    = 0x09   # Treble cut/boost
DSP_LOUDNESS  = 0x16   # signed byte -10..+10 (sniffed from app)
DSP_NIGHTMODE = 0x18   # 0=OFF 1=ON (sniffed from app)
DSP_HIGHPASS  = 0x1A   # 0=OFF, 1=60Hz, 2=80Hz, 3=100Hz, 4=120Hz (sniffed)
DSP_OUTPUT    = 0x1C   # 0=Mono, 1=Stereo, 2=Left, 3=Right (sniffed from app)
DSP_TUNING    = 0x1D   # 0=Enclosure 13L, 1=Open Back (sniffed)
DSP_BALANCE   = 0x1E   # signed byte -6..+6 (sniffed from app)

EQ_PRESETS  = ["Normal", "Acoustic", "Jazz", "Pop", "Hip-Hop"]
# High-pass values per sniffed app traffic:
#   0=OFF, 1=60Hz, 2=80Hz, 3=100Hz, 4=120Hz
HP_OPTIONS  = ["OFF", "60Hz", "80Hz", "100Hz", "120Hz"]
OUT_OPTIONS = ["Mono", "Stereo", "Left", "Right"]

# ── Per-product chime counts ────────────────────────────────────────────────
PRODUCT_CHIMES = {
    # Per official LUCI v14.1 spec §6.45 (Tx_MB#80 Play Audio Index):
    # "Device supports up to 10 indexes" (play 1 .. play 10).
    # Earlier customer-facing API doc claimed 15 for PRO2; that's not
    # what the wire protocol exposes — slots 11+ return NI (No Index).
    PRODUCT_PRO2:  10,
    PRODUCT_V3:    6,
    PRODUCT_IO1:   10,
    PRODUCT_V2:    0,
    PRODUCT_PRO:   6,
    PRODUCT_MICRO: 0,
}

# ── Per-product capability matrix ───────────────────────────────────────────
# Single source of truth for which entities get created per product.
# Every entity platform reads from this — no scattered ``if product in (…)``
# checks anywhere else.
#
# Capability keys:
#   chimes           — number of chime slots (0 = no chime buttons)
#   eq_select        — EQ preset selector
#   output_select    — Stereo/Mono/Left/Right selector
#   highpass_select  — HPF frequency selector (PRO2 only)
#   tuning_select    — 13L Enclosure / Open Back (PRO2 only)
#   balance_number   — -6..+6 balance slider
#   loudness_number  — -10..+10 dB slider (PRO2 only)
#   loudness_switch  — on/off loudness (V3/iO1/V2/PRO)
#   nightmode_switch — Night Mode on/off
#   bluetooth_switch — BT on/off + pair/disconnect (all products)
PRODUCT_CAPS = {
    PRODUCT_PRO2: {
        "chimes":           15,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  True,
        "tuning_select":    True,
        "balance_number":   True,
        "loudness_number":  True,
        "loudness_switch":  False,
        "nightmode_switch": True,
        "bluetooth_switch": True,
    },
    PRODUCT_V3: {
        "chimes":           6,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  False,
        "tuning_select":    False,
        "balance_number":   True,
        "loudness_number":  False,
        "loudness_switch":  True,
        "nightmode_switch": True,
        "bluetooth_switch": True,
    },
    PRODUCT_IO1: {
        "chimes":           10,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  False,
        "tuning_select":    False,
        "balance_number":   True,
        "loudness_number":  False,
        "loudness_switch":  True,
        "nightmode_switch": True,
        "bluetooth_switch": True,
    },
    PRODUCT_V2: {
        "chimes":           0,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  False,
        "tuning_select":    False,
        "balance_number":   True,
        "loudness_number":  False,
        "loudness_switch":  True,
        "nightmode_switch": True,
        "bluetooth_switch": True,
    },
    PRODUCT_PRO: {
        "chimes":           6,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  False,
        "tuning_select":    False,
        "balance_number":   True,
        "loudness_number":  False,
        "loudness_switch":  True,
        "nightmode_switch": True,
        "bluetooth_switch": True,
    },
    PRODUCT_MICRO: {
        "chimes":           0,
        "eq_select":        False,
        "output_select":    False,
        "highpass_select":  False,
        "tuning_select":    False,
        "balance_number":   False,
        "loudness_number":  False,
        "loudness_switch":  False,
        "nightmode_switch": False,
        "bluetooth_switch": True,    # BT on; no DSP for now
    },
}


def caps(product: str) -> dict:
    """Return the capability dict for a product, or an all-False dict."""
    return PRODUCT_CAPS.get(product, {
        "chimes":           0,
        "eq_select":        False,
        "output_select":    False,
        "highpass_select":  False,
        "tuning_select":    False,
        "balance_number":   False,
        "loudness_number":  False,
        "loudness_switch":  False,
        "nightmode_switch": False,
        "bluetooth_switch": False,
    })

# ── LSSDP discovery ─────────────────────────────────────────────────────────
LSSDP_MULTICAST_ADDR = "239.255.255.250"
LSSDP_PORT           = 1800
LSSDP_MSEARCH = (
    b"M-SEARCH * HTTP/1.1\r\n"
    b"HOST: 239.255.255.250:1800\r\n"
    b"MAN: \"ssdp:discover\"\r\n"
    b"MX: 3\r\n"
    b"ST: urn:LinkPlay:device:LinkPlay:1\r\n\r\n"
)

# Platform labels (used by discovery)
PLATFORM_LS9   = "LS9"
PLATFORM_LS10  = "LS10"

# LS10 model name fragments (used to classify LSSDP responses)
LS10_MODELS = ("PRO2", "WiFiV3", "WIFIV3", "IO1", "io1", "iO1")

# ── Bundled client certificate ──────────────────────────────────────────────
# LS10 speakers use TLS 1.2 mutual auth with a single per-developer cert
# issued by Lithe Audio. The cert is bundled with the integration so users
# never have to obtain or paste it.
import os as _os
_CERTS_DIR = _os.path.join(_os.path.dirname(__file__), "certs")
BUNDLED_CERT_PEM = _os.path.join(_CERTS_DIR, "client.pem")
BUNDLED_CERT_KEY = _os.path.join(_CERTS_DIR, "client.key")

# ── Update intervals ────────────────────────────────────────────────────────
SCAN_INTERVAL_S = 30

# ── Coordinator data keys ───────────────────────────────────────────────────
DATA_COORDINATOR = "coordinator"
DATA_DEVICE_INFO = "device_info"
DATA_TANNOY_SAVED = "tannoy_saved"
DATA_PRAYER       = "prayer"

# ── Prayer scheduler ────────────────────────────────────────────────────────
ALADHAN_URL = "https://api.aladhan.com/v1/timingsByCity"

PRAYER_NAMES = [
    "fajr", "sunrise", "dhuhr", "asr", "sunset", "maghrib", "isha", "midnight",
]

# Adhan (Call-to-Prayer) audio URLs.
#
# Source: praytimes.org/audio/ — a well-known Islamic prayer-times site
# that hosts direct MP3 recordings of renowned Adhan recitations.
#
# Note: the previous URLs (islamcan.com/audio/adhan/azan*.mp3) used by
# older Lithe app versions are now returning HTTP 403 — those endpoints
# no longer allow direct streaming. The praytimes.org URLs below are
# direct .mp3 URLs and should stream cleanly through the speaker.
ADHAN_PRESETS: dict[str, str] = {
    # The 6 most popular adhans (these match what the standalone
    # Prayer Scheduler webapp also uses)
    "Adhan — Makkah":          "https://praytimes.org/audio/sunni/Adhan-Makkah.mp3",
    "Adhan — Madinah":         "https://praytimes.org/audio/sunni/Adhan-Madinah.mp3",
    "Adhan — Al-Aqsa":         "https://praytimes.org/audio/sunni/Adhan-Alaqsa.mp3",
    "Adhan — Egyptian":        "https://praytimes.org/audio/sunni/Adhan-Egypt.mp3",
    "Adhan — Halab (Aleppo)":  "https://praytimes.org/audio/sunni/Adhan-Halab.mp3",
    # Famous reciters
    "Abdul Basit":             "https://praytimes.org/audio/sunni/Abdul-Basit.mp3",
    "Abdul Ghaffar":           "https://praytimes.org/audio/sunni/Abdul-Ghaffar.mp3",
    "Abdul Hakam":             "https://praytimes.org/audio/sunni/Abdul-Hakam.mp3",
    "Al-Hussaini":             "https://praytimes.org/audio/sunni/Al-Hussaini.mp3",
    "Bakir Bash":              "https://praytimes.org/audio/sunni/Bakir-Bash.mp3",
    "Hafez":                   "https://praytimes.org/audio/sunni/Hafez.mp3",
    "Hafiz Murad":             "https://praytimes.org/audio/sunni/Hafiz-Murad.mp3",
    "Minshawi":                "https://praytimes.org/audio/sunni/Minshawi.mp3",
    "Naghshbandi":             "https://praytimes.org/audio/sunni/Naghshbandi.mp3",
    "Saber":                   "https://praytimes.org/audio/sunni/Saber.mp3",
    "Sharif Doman":            "https://praytimes.org/audio/sunni/Sharif-Doman.mp3",
    "Yusuf Islam (Cat Stevens)": "https://praytimes.org/audio/sunni/Yusuf-Islam.mp3",
}

# Quran — all 30 Juz by Sheikh Mishary Rashid Alafasy.
#
# Hosted on Internet Archive for long-term stability:
#   https://archive.org/details/quran-juz-para-1-to-30-alafasy-audio-mp3-download
#
# Note: the original Bitly (j.mp/...) shortlinks used by older Lithe app
# versions are now returning HTTP 403 — those shortlinks appear to have
# been retired by Bitly. The Internet Archive URLs below resolve directly
# to MP3 files (no redirects), which is what the speaker firmware needs.
QURAN_JUZ: dict[int, str] = {
    juz: f"https://archive.org/download/quran-juz-para-1-to-30-alafasy-audio-mp3-download/{juz:02d}.mp3"
    for juz in range(1, 31)
}


def quran_juz_label(juz: int) -> str:
    """Display label for a Juz preset in dropdowns."""
    return f"Juz {juz} — Quran"


# Combined dropdown for the Options Flow: Adhan presets + 30 Quran Juz + Custom
def all_preset_options() -> dict[str, str]:
    """Return {label: url} mapping for the preset dropdown."""
    opts: dict[str, str] = dict(ADHAN_PRESETS)
    for juz, url in QURAN_JUZ.items():
        opts[quran_juz_label(juz)] = url
    return opts
