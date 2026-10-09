"""Constants for the Lithe Audio integration."""

import os as _os

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
MAX_CHIME_SLOT = 14

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


def product_from_model(*values: str) -> str | None:
    """Map speaker-reported model fields to a known integration product.

    Model can contain only the platform generation (for example ``WiFi v3``),
    while ModelVariant or Model_num identifies the actual retail product.  The
    specific identifiers are therefore checked before the generic generation.
    """
    normalized = "".join(
        ch for value in values for ch in str(value).upper() if ch.isalnum()
    )
    if not normalized:
        return None
    if "PRO2" in normalized or "WIFIPRO2" in normalized:
        return PRODUCT_PRO2
    if "IO1" in normalized:
        return PRODUCT_IO1
    if "MICRO" in normalized:
        return PRODUCT_MICRO
    if "WIFIV3" in normalized or normalized.endswith("V3"):
        return PRODUCT_V3
    if "WIFIV2" in normalized or normalized.endswith("V2"):
        return PRODUCT_V2
    if "WIFIPRO" in normalized or normalized.endswith("PRO"):
        return PRODUCT_PRO
    return None


def capability_product_from_state(
    model_number: str,
    model_variant: str,
    detected_product: str,
) -> str | None:
    """Resolve capabilities from the most specific live model evidence."""
    specific = product_from_model(model_number, model_variant)
    if specific:
        return specific
    if detected_product in PRODUCT_NAMES:
        return detected_product
    return None

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

SPOTIFY_SOURCE_ID = 4

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
MB_TUNNEL_START   = 111
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
# Confirmed from labelled Lithe app captures and live audible testing against
# WiFi v3 firmware 20250512_0201_RC11 on 2026-09-28/29. The payload is:
#   00 04 00 <sub-MB> <field> <value>
# Values are signed 8-bit where noted (F1=-1, F2=-2, and so on).
DSP_REMOTE_ID = 0x0000 # Required to reach the MCU/DSP; 0xAAAA only ACKs
DSP_EQ_BANDS  = 0x09   # fields 06=Bass, 04=Mid, 02=Treble; values -5..+5
DSP_EQ        = 0x0A   # 0=Normal, 1=Acoustic, 2=Jazz, 3=Pop, 4=Hip-Hop
DSP_LOUDNESS  = 0x0B   # 0=OFF, 1=ON
DSP_LOUDNESS_GAIN = 0x34  # PRO 2 TX/RX 0..20, representing -10..+10 dB
DSP_LOUDNESS_GAIN_FEEDBACK = DSP_LOUDNESS_GAIN
DSP_NIGHTMODE = 0x0C   # 0=OFF, 1=ON
DSP_BALANCE   = 0x0E   # signed -6..+6
DSP_OUTPUT    = 0x0F   # 0=Mono, 1=Stereo, 2=Left, 3=Right
# PRO 2-only controls recovered from the CR443GP MCU dispatch table. The
# previous 0x1A/0x1D values land in the firmware's unsupported/default branch.
# 0x32 calls the DSP frequency setter; 0x0D controls open-back protection.
DSP_HIGHPASS  = 0x32   # 0=60Hz, 1=80Hz, 2=100Hz, 3=120Hz, 4=150Hz
DSP_TUNING    = 0x0D   # 0=13L enclosure, 1=open-back protection
DSP_STATUS_ALL = 0x15  # read-only MCU settings report trigger
DSP_TUNNEL_PORT = 4444 # vendor-documented MB#111 TCP tunnel example port

# WiFi Micro Subwoofer MCU profile (LS9 MB112 tunnel data). The vendor
# workbook defines command IDs 0x02..0x05 and the 0x01 feedback / 0x02 setter
# fields. PROFILE is included in the full-state report immediately before
# GAIN and follows the same command layout at ID 0x01.
SUB_DSP_PROFILE = 0x01
SUB_DSP_GAIN = 0x02
SUB_DSP_CROSSOVER = 0x03
SUB_DSP_PHASE = 0x04
SUB_DSP_LOWPASS = 0x05
SUB_DSP_FEEDBACK_FIELD = 0x01
SUB_DSP_SET_FIELD = 0x02
SUB_PROFILE_OPTIONS = ["Subwoofer", "Speaker", "Custom"]
SUB_CROSSOVER_OPTIONS = ["60 Hz", "120 Hz", "180 Hz", "240 Hz"]
SUB_PHASE_OPTIONS = ["0 degrees", "180 degrees"]

DSP_BASS_FIELD   = 0x06
DSP_MID_FIELD    = 0x04
DSP_TREBLE_FIELD = 0x02

# The MCU command table pairs odd GET selectors with even SET selectors:
#   01/02 = Treble Low, 03/04 = Treble Mid, 05/06 = Treble High.
# HA writes must use the even selector from each pair.  Using the odd feedback
# selector produces a successful MB112 envelope without changing the DSP.
IO1_TREBLE_LOW_FIELD  = 0x02
IO1_TREBLE_MID_FIELD  = 0x04
IO1_TREBLE_HIGH_FIELD = 0x06


def loudness_gain_to_wire(value: int | float) -> int:
    """Convert the -10..+10 dB UI scale to the MCU's 0..20 index."""
    return max(-10, min(10, int(value))) + 10

EQ_PRESETS  = ["Normal", "Acoustic", "Jazz", "Pop", "Hip-Hop"]
IO1_EQ_PRESETS = ["Outdoor", "Indoor", "Pendent"]
# iO1 reuses the three 0x09 setter fields, but they address three treble
# crossover bands rather than the generic Treble/Mid/Bass controls.
# Keep the legacy state/unique-id keys so existing HA entities are renamed
# in place instead of being duplicated during upgrade.
IO1_EQ_BANDS = (
    ("Treble Low 2 kHz", "dsp_treble", IO1_TREBLE_LOW_FIELD, "treble"),
    ("Treble Mid 4 kHz", "dsp_mid", IO1_TREBLE_MID_FIELD, "mid"),
    ("Treble High 6 kHz", "dsp_bass", IO1_TREBLE_HIGH_FIELD, "bass"),
)
OUT_OPTIONS = ["Mono", "Stereo", "Left", "Right"]
HP_OPTIONS = ["60 Hz", "80 Hz", "100 Hz", "120 Hz"]
TUNING_OPTIONS = ["Enclosure Mode 13L", "Open Back Mode"]


def audio_control_name(order: int, label: str) -> str:
    """Return an invisibly sortable name for HA's alphabetical device page.

    HTML collapses the repeated spaces to one visible space, while Home
    Assistant's raw-name sort preserves the requested control sequence.
    """
    return f"Audio — {' ' * max(1, 11 - order)}{label}"

# ── Per-product chime counts ────────────────────────────────────────────────
PRODUCT_CHIMES = {
    # PRO 2 slots 10-14 use legacy embedded asset payloads (songN.wav)
    # because its two-digit indexed parser does not accept "play N".
    PRODUCT_PRO2:  14,
    PRODUCT_V3:    14,
    PRODUCT_IO1:   10,
    PRODUCT_V2:    0,
    PRODUCT_PRO:   6,
    PRODUCT_MICRO: 0,
}

CHIME_NAMES = {
    1: "Bell",
    2: "Bell",
    3: "Bell",
    4: "Bell",
    5: "Alarm",
    6: "Alarm",
    7: "Siren",
    8: "Siren",
    9: "Siren",
    10: "Siren",
    11: "Warning",
    12: "Dinner",
    13: "Breach",
    14: "Warning",
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
#   highpass_select  — PRO 2 high-pass frequency selector
#   tuning_switch    — PRO 2 enclosure/open-back protection switch
#   balance_number   — -6..+6 balance slider
#   loudness_switch  — on/off loudness
#   nightmode_switch — Night Mode on/off
#   bluetooth_switch — BT on/off + pair/disconnect (all products)
#   favourites       — native/local favourite controls and media entries
PRODUCT_CAPS = {
    PRODUCT_PRO2: {
        "chimes":           14,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  True,
        "tuning_select":    False,
        "tuning_switch":    True,
        "balance_number":   True,
        "loudness_number":  True,
        "loudness_switch":  True,
        "nightmode_switch": True,
        "bluetooth_switch": True,
        "aux_in_switch":    True,
        "spdif_in_switch":  True,
        "favourites":       True,
        "subwoofer_controls": False,
        "scheduling":       True,
    },
    PRODUCT_V3: {
        "chimes":           14,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  False,
        "tuning_select":    False,
        "tuning_switch":    False,
        "balance_number":   True,
        "loudness_number":  False,
        "loudness_switch":  True,
        "nightmode_switch": True,
        "bluetooth_switch": True,
        "aux_in_switch":    False,
        "spdif_in_switch":  False,
        "favourites":       True,
        "subwoofer_controls": False,
        "scheduling":       True,
    },
    PRODUCT_IO1: {
        "chimes":           10,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  False,
        "tuning_select":    False,
        "tuning_switch":    False,
        "balance_number":   True,
        "loudness_number":  False,
        "loudness_switch":  False,
        "nightmode_switch": False,
        "bluetooth_switch": True,
        "aux_in_switch":    False,
        "spdif_in_switch":  False,
        "favourites":       True,
        "subwoofer_controls": False,
        "scheduling":       True,
    },
    PRODUCT_V2: {
        "chimes":           0,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  False,
        "tuning_select":    False,
        "tuning_switch":    False,
        "balance_number":   True,
        "loudness_number":  False,
        "loudness_switch":  True,
        "nightmode_switch": True,
        "bluetooth_switch": True,
        "aux_in_switch":    False,
        "spdif_in_switch":  False,
        "favourites":       True,
        "subwoofer_controls": False,
        "scheduling":       True,
    },
    PRODUCT_PRO: {
        "chimes":           6,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  False,
        "tuning_select":    False,
        "tuning_switch":    False,
        "balance_number":   True,
        "loudness_number":  False,
        "loudness_switch":  True,
        "nightmode_switch": True,
        "bluetooth_switch": True,
        "aux_in_switch":    False,
        "spdif_in_switch":  False,
        "favourites":       True,
        "subwoofer_controls": False,
        "scheduling":       True,
    },
    PRODUCT_MICRO: {
        "chimes":           0,
        "eq_select":        False,
        "output_select":    False,
        "highpass_select":  False,
        "tuning_select":    False,
        "tuning_switch":    False,
        "balance_number":   False,
        "loudness_number":  False,
        "loudness_switch":  False,
        "nightmode_switch": False,
        "bluetooth_switch": True,
        "aux_in_switch":    False,
        "spdif_in_switch":  False,
        "favourites":       False,
        "subwoofer_controls": True,
        "scheduling":       False,
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
        "tuning_switch":    False,
        "balance_number":   False,
        "loudness_number":  False,
        "loudness_switch":  False,
        "nightmode_switch": False,
        "bluetooth_switch": False,
        "favourites":       False,
        "subwoofer_controls": False,
        "scheduling":       False,
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
DATA_PRAYER_STATE = "prayer_state"

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
