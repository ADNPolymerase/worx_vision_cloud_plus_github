"""Constants for Worx Vision Cloud Plus."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "worx_vision_cloud"

CONF_CLOUD = "cloud"
CONF_VERIFY_SSL = "verify_ssl"
CONF_EXPOSE_RAW = "expose_raw_entities"

DEFAULT_CLOUD = "worx"
DEFAULT_VERIFY_SSL = True
DEFAULT_EXPOSE_RAW = False

CLOUDS = ["worx", "kress", "landxcape"]

PLATFORMS = [
    Platform.LAWN_MOWER,
    Platform.CALENDAR,
    Platform.CAMERA,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.SELECT,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.UPDATE,
]

ATTR_RAW_PATH = "raw_path"
ATTR_RAW_SOURCE = "raw_source"
ATTR_SERIAL_NUMBER = "serial_number"

# Maintenance thresholds shared by the maintenance sensor and HA repairs.
BLADE_SERVICE_THRESHOLD_MINUTES = 720
BATTERY_SERVICE_THRESHOLD_CYCLES = 500

# Vision border distances accepted by the Worx API (millimeters).
BORDER_DISTANCE_OPTIONS_MM = (50, 100, 150, 200)

SERVICE_START_ONE_TIME_MOWING = "start_one_time_mowing"
SERVICE_SET_RTK_MAP_ID = "set_rtk_map_id"

ATTR_MAP_ID = "map_id"

ATTR_EDGE_CUT = "edge_cut"
ATTR_RUNTIME = "runtime"
ATTR_ZONES = "zones"
