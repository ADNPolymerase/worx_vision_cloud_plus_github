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
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.BUTTON,
]

ATTR_RAW_PATH = "raw_path"
ATTR_RAW_SOURCE = "raw_source"
ATTR_SERIAL_NUMBER = "serial_number"
