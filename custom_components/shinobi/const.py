"""Constants for the Shinobi NVR integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "shinobi"

# Config entry keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_SSL = "ssl"
CONF_API_KEY = "api_key"
CONF_GROUP_KEY = "group_key"
CONF_VERIFY_SSL = "verify_ssl"

# Options
CONF_SCAN_INTERVAL = "scan_interval"
CONF_MOTION_TIMEOUT = "motion_timeout"
CONF_OBJECT_COUNT_HOURS = "object_count_hours"

DEFAULT_PORT = 8080
DEFAULT_SSL = False
DEFAULT_VERIFY_SSL = True
DEFAULT_SCAN_INTERVAL = 10  # seconds
DEFAULT_MOTION_TIMEOUT = 30  # seconds a motion sensor stays "on" after last event
DEFAULT_OBJECT_COUNT_HOURS = 24  # rolling lookback window for tag counts

MIN_SCAN_INTERVAL = timedelta(seconds=5)

# Object-count tag tallying is a per-monitor windowed /events query (can
# return a lot of rows) — only recompute every Nth coordinator poll rather
# than every scan_interval tick (default 10s), and cap rows fetched per
# monitor regardless of how wide the lookback window is.
OBJECT_COUNT_RECOMPUTE_EVERY_N_POLLS = 30
OBJECT_COUNT_ROW_CAP = 500

# Real-time detector-event relay over Shinobi's own socket.io server (see
# socket_client.py) — HA bus event fired for every detector_trigger.
EVENT_DETECTOR_TRIGGER = f"{DOMAIN}_detector_trigger"

# A detector_trigger only carries a timestamp, not a snapshot — resolving
# one means searching Shinobi's timelapse-frame index around that moment.
# Verified live: a zero-width and even a +/-5min window found nothing on a
# monitor whose frames are captured roughly every ~15min, but +/-20min did.
# Widen further than that observed cadence to keep this a reasonably
# reliable best-effort lookup across differently-configured monitors, not
# a guarantee — sparse capture intervals can still legitimately yield none.
DETECTOR_SNAPSHOT_WINDOW_MINUTES = 20

# Shinobi monitor modes
MODE_START = "start"  # watch only
MODE_RECORD = "record"
MODE_STOP = "stop"  # disabled
MODE_IDLE = "idle"

MODES = [MODE_START, MODE_RECORD, MODE_STOP, MODE_IDLE]

# Modes a monitor can be in while "enabled" — re-enabling a stopped monitor
# restores whichever of these it was last in (see ShinobiEnableSwitch).
ACTIVE_MODES = (MODE_START, MODE_RECORD)

# Shinobi status strings (monitor.status)
STATUS_WATCHING = "Watching"
STATUS_RECORDING = "Recording"
STATUS_IDLE = "Idle"
STATUS_DISABLED = "Disabled"
# "on" (active capture) statuses
ACTIVE_STATUSES = {STATUS_WATCHING, STATUS_RECORDING}

MANUFACTURER = "Shinobi Systems"

# Services
SERVICE_SET_MODE = "set_mode"
SERVICE_TRIGGER_MOTION = "trigger_motion"
SERVICE_PTZ = "ptz"
ATTR_MODE = "mode"
ATTR_DIRECTION = "direction"

PLATFORMS = ["camera", "binary_sensor", "switch", "sensor", "button", "image"]

# PTZ: monitor.details.control must equal this (as a string) for PTZ to be
# available at all. Shinobi's own /control/{ke}/{mid}/{direction} route hangs
# until client timeout when control is disabled (ptzControl() early-returns
# without ever invoking the callback res.end() depends on) — always check
# this before issuing a PTZ command, don't rely on the server to reject fast.
PTZ_CONTROL_ENABLED = "1"

PTZ_UP = "up"
PTZ_DOWN = "down"
PTZ_LEFT = "left"
PTZ_RIGHT = "right"
PTZ_ZOOM_IN = "zoom_in"
PTZ_ZOOM_OUT = "zoom_out"
PTZ_CENTER = "center"
PTZ_STOP = "stopMove"
PTZ_SET_HOME = "setHome"

PTZ_DIRECTIONS = [
    PTZ_UP,
    PTZ_DOWN,
    PTZ_LEFT,
    PTZ_RIGHT,
    PTZ_ZOOM_IN,
    PTZ_ZOOM_OUT,
    PTZ_CENTER,
    PTZ_STOP,
    PTZ_SET_HOME,
]

# (direction, button name suffix, icon)
PTZ_BUTTONS = [
    (PTZ_UP, "Up", "mdi:arrow-up-bold"),
    (PTZ_DOWN, "Down", "mdi:arrow-down-bold"),
    (PTZ_LEFT, "Left", "mdi:arrow-left-bold"),
    (PTZ_RIGHT, "Right", "mdi:arrow-right-bold"),
    (PTZ_ZOOM_IN, "Zoom In", "mdi:magnify-plus"),
    (PTZ_ZOOM_OUT, "Zoom Out", "mdi:magnify-minus"),
    (PTZ_CENTER, "Home", "mdi:home"),
    (PTZ_SET_HOME, "Set Home", "mdi:home-plus"),
]


def monitor_ptz_enabled(monitor: dict) -> bool:
    """Return True if a monitor payload has PTZ control enabled."""
    details = monitor.get("details") or {}
    return str(details.get("control")) == PTZ_CONTROL_ENABLED
