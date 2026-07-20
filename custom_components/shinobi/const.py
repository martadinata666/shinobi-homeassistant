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

DEFAULT_PORT = 8080
DEFAULT_SSL = False
DEFAULT_VERIFY_SSL = True
DEFAULT_SCAN_INTERVAL = 10  # seconds
DEFAULT_MOTION_TIMEOUT = 30  # seconds a motion sensor stays "on" after last event

MIN_SCAN_INTERVAL = timedelta(seconds=5)

# Shinobi monitor modes
MODE_START = "start"  # watch only
MODE_RECORD = "record"
MODE_STOP = "stop"  # disabled
MODE_IDLE = "idle"

MODES = [MODE_START, MODE_RECORD, MODE_STOP, MODE_IDLE]

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
ATTR_MODE = "mode"

PLATFORMS = ["camera", "binary_sensor", "switch", "sensor"]
