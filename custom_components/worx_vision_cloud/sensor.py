"""Sensor platform for Worx Vision Cloud Plus."""
from __future__ import annotations

from asyncio import Task
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfArea,
    UnitOfElectricPotential,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_RAW_PATH,
    ATTR_RAW_SOURCE,
    BATTERY_SERVICE_THRESHOLD_CYCLES,
    BLADE_SERVICE_THRESHOLD_MINUTES,
    CONF_EXPOSE_RAW,
    DEFAULT_EXPOSE_RAW,
    DOMAIN,
)
from .entity import WorxVisionEntity
from .helpers import (
    MAX_STRING_STATE_LENGTH,
    get_dict_value,
    next_schedule_start,
    raw_entity_path_map,
    raw_entity_values,
    raw_path_enabled_default,
    rtk_at_station,
    rtk_distance_to_station_m,
    rtk_map_attributes,
    rtk_position,
    schedule_attributes,
    schedule_summary,
)
from .nearlink import (
    NEARLINK_CONNECTION_OPTIONS,
    has_nearlink_module,
    nearlink_attributes,
    nearlink_connection_state,
)


@dataclass(frozen=True, kw_only=True)
class WorxSensorDescription(SensorEntityDescription):
    """Description for a regular sensor."""

    value_fn: Callable[[Any], Any]
    attrs_fn: Callable[[Any], dict[str, Any] | None] | None = None


# Map the raw descriptions reported by Worx to canonical, language-neutral state
# keys. The human-readable labels live in translations/*.json so Home Assistant can
# localize them per user (en/pl/fr/...), instead of being hard-coded here.
STATUS_STATE_KEYS = {
    "home": "home",
    "leaving home": "leaving_home",
    "going home": "going_home",
    "mowing": "mowing",
    "cutting edge": "edge_cutting",
    "edge cutting": "edge_cutting",
    "border cut": "edge_cutting",
    "charging": "charging",
    "paused": "paused",
    "pause": "paused",
    "idle": "idle",
    "manual stop": "manual_stop",
    "rain delay": "rain_delay",
    "rain_delay": "rain_delay",
    "locked": "locked",
    "error": "error",
    "no error": "no_error",
    "offline": "offline",
}

# Canonical option lists exposed as enum sensor states.
STATUS_STATE_OPTIONS = [
    "home",
    "leaving_home",
    "going_home",
    "mowing",
    "edge_cutting",
    "charging",
    "paused",
    "idle",
    "manual_stop",
    "rain_delay",
    "locked",
    "error",
    "no_error",
    "offline",
]

READINESS_STATE_OPTIONS = [
    "ready",
    "mowing",
    "charging",
    "battery_low",
    "rain_delay",
    "error",
    "locked",
    "offline",
]

CLOUD_CONNECTION_OPTIONS = ["ok", "check", "offline"]

MAINTENANCE_STATE_OPTIONS = ["ok", "blade_service_due", "battery_service_due"]

RAIN_DELAY_ERROR_DESCRIPTIONS = {"rain delay", "rain_delay"}


def _state_key(value: Any, mapping: dict[str, str]) -> str | None:
    """Map a raw Worx description to a canonical, translatable state key."""
    if value is None:
        return None
    return mapping.get(str(value).strip().lower())


def _battery(device, key, default=None):
    return get_dict_value(getattr(device, "battery", {}), key, default)


def _blades(device, key, default=None):
    return get_dict_value(getattr(device, "blades", {}), key, default)


def _rain(device, key, default=None):
    return get_dict_value(getattr(device, "rainsensor", {}), key, default)


def _orientation(device, key, default=None):
    return get_dict_value(getattr(device, "orientation", {}), key, default)


def _status(device, key, default=None):
    return get_dict_value(getattr(device, "status", {}), key, default)


def _status_state(device) -> str | None:
    if _is_rain_delay(device):
        return "rain_delay"
    return _state_key(_status(device, "description"), STATUS_STATE_KEYS)


def _error(device, key, default=None):
    return get_dict_value(getattr(device, "error", {}), key, default)


def _error_state(device) -> str | None:
    # Unmapped/rare device error descriptions surface via the raw_description
    # attribute; the enum state stays None to avoid noisy non-option warnings.
    return _state_key(_error(device, "description"), STATUS_STATE_KEYS)


def _is_rain_delay(device) -> bool:
    """Return whether Worx reports a rain delay as its current blocker."""
    error_description = _error(device, "description")
    if (
        error_description is not None
        and str(error_description).strip().lower() in RAIN_DELAY_ERROR_DESCRIPTIONS
    ):
        return True

    rain_remaining = _as_float(_rain(device, "remaining")) or 0
    return _rain(device, "triggered") is True or rain_remaining > 0


def _rtk_station_status_attrs(device) -> dict[str, Any]:
    """Return diagnostic attributes for RTK station proximity."""
    station_distance = rtk_distance_to_station_m(device)
    if station_distance is None:
        return {}
    return {
        "rtk_station_distance_m": round(station_distance, 2),
        "rtk_at_station": rtk_at_station(device),
    }


def _zone(device, key, default=None):
    return get_dict_value(getattr(device, "zone", {}), key, default)


def _statistics(device, key, default=None):
    return get_dict_value(getattr(device, "statistics", {}), key, default)


def _last_update(device):
    value = getattr(device, "updated", None)
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return None


def _product_item(device, key, default=None):
    return get_dict_value(
        getattr(device, "_worx_vision_product_item", {}) or {}, key, default
    )


def _product_item_dict(device) -> dict[str, Any]:
    value = getattr(device, "_worx_vision_product_item", {}) or {}
    return value if isinstance(value, dict) else {}


def _firmware_info(device) -> dict[str, Any]:
    value = getattr(device, "_worx_vision_firmware_upgrade", {}) or {}
    return value if isinstance(value, dict) else {}


def _rtk_map_data(device):
    return getattr(device, "_worx_vision_rtk_map", {}) or {}


def _first_map_zone(device):
    boundaries = get_dict_value(
        get_dict_value(_rtk_map_data(device), "layers", {}) or {}, "boundaries", []
    )
    for boundary in boundaries or []:
        zones = get_dict_value(boundary, "zones", []) or []
        for zone in zones:
            if isinstance(zone, dict):
                return zone
    return {}


def _area_mowed_total(device):
    """Return the lifetime total mowed area reported by the mower (m²)."""
    value = _product_item(device, "area_mowed")
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any, precision: int | None = None) -> float | None:
    """Return a float from API scalar values."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if precision is not None:
        return round(result, precision)
    return result


def _as_int(value: Any) -> int | None:
    """Return an int from API scalar values."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_datetime(value: Any) -> datetime | None:
    """Return a timezone-aware datetime from API timestamp values."""
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _since_reset(device, total_key: str, reset_key: str) -> int | None:
    """Return product item counter value since the last reset marker."""
    total = _as_int(_product_item(device, total_key))
    reset = _as_int(_product_item(device, reset_key))
    if total is None:
        return None
    if reset is None:
        return total
    return max(0, total - reset)


def _work_time_total_minutes(device) -> float | None:
    """Return the lifetime mower work time in minutes.

    Prefers the MQTT-pushed statistics value (updates live while mowing) and
    falls back to the REST product-item field when statistics are unavailable.
    """
    value = _statistics(device, "worktime_total")
    if value is None:
        value = _product_item(device, "mower_work_time")
    return _as_float(value)


def _mowing_efficiency(device) -> float | None:
    """Return lifetime covered area per hour of blade-active time (m²/h).

    Prefers blade-active time over total mower runtime: runtime includes
    driving to zones, docking and idling with blades off, which understates
    the real coverage rate the estimated daily sensors multiply by. Falls
    back to total work time when no blade figure is available.
    """
    area = _area_mowed_total(device)
    work_minutes = _as_float(_blades(device, "total_on"))
    if work_minutes in (None, 0):
        work_minutes = _as_float(_statistics(device, "worktime_blades_on"))
    if work_minutes in (None, 0):
        work_minutes = _as_float(_product_item(device, "mower_work_time"))
    if area is None or work_minutes in (None, 0):
        return None
    return round(area / (work_minutes / 60), 2)


def _cloud_statistics_updated(device) -> datetime | None:
    """Return when the cumulative Worx product statistics were fetched."""
    value = getattr(device, "_worx_vision_product_item_updated_at", None)
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return None


def _last_update_age(device) -> int | None:
    updated = _last_update(device)
    if updated is None:
        return None
    return max(0, round((datetime.now(UTC) - updated).total_seconds() / 60))


def _capability_summary(device) -> str | None:
    capabilities = _product_item(device, "capabilities", []) or []
    if not isinstance(capabilities, list | tuple):
        return None
    return f"{len(capabilities)} capabilities"


def _capability_attributes(device) -> dict[str, Any]:
    product_item = _product_item_dict(device)
    return {
        "capabilities": get_dict_value(product_item, "capabilities"),
        "capabilities_available": get_dict_value(
            product_item, "capabilities_available"
        ),
    }


def _cloud_connection_state(device) -> str | None:
    product_item = _product_item_dict(device)
    if not product_item:
        return None
    if (
        get_dict_value(product_item, "iot_registered") is True
        and get_dict_value(product_item, "mqtt_registered") is True
        and getattr(device, "online", None) is True
    ):
        return "ok"
    if getattr(device, "online", None) is False:
        return "offline"
    return "check"


def _cloud_connection_attributes(device) -> dict[str, Any]:
    product_item = _product_item_dict(device)
    return {
        "online": getattr(device, "online", None),
        "iot_registered": get_dict_value(product_item, "iot_registered"),
        "mqtt_registered": get_dict_value(product_item, "mqtt_registered"),
        "mqtt_endpoint": get_dict_value(product_item, "mqtt_endpoint"),
    }


def _push_notification_state(device) -> str | None:
    product_item = _product_item_dict(device)
    enabled = get_dict_value(product_item, "push_notifications")
    level = get_dict_value(product_item, "push_notifications_level")
    if enabled is False:
        return "disabled"
    if level:
        return str(level)
    if enabled is True:
        return "enabled"
    return None


def _maintenance_state(device) -> str | None:
    blade_minutes = _since_reset(device, "blade_work_time", "blade_work_time_reset")
    battery_cycles = _since_reset(
        device, "battery_charge_cycles", "battery_charge_cycles_reset"
    )
    if blade_minutes is None and battery_cycles is None:
        return None
    if blade_minutes is not None and blade_minutes >= BLADE_SERVICE_THRESHOLD_MINUTES:
        return "blade_service_due"
    if (
        battery_cycles is not None
        and battery_cycles >= BATTERY_SERVICE_THRESHOLD_CYCLES
    ):
        return "battery_service_due"
    return "ok"


def _maintenance_attributes(device) -> dict[str, Any]:
    blade_minutes = _since_reset(device, "blade_work_time", "blade_work_time_reset")
    battery_cycles = _since_reset(
        device, "battery_charge_cycles", "battery_charge_cycles_reset"
    )
    blade_reset_at = _as_datetime(_product_item(device, "blade_work_time_reset_at"))
    battery_reset_at = _as_datetime(
        _product_item(device, "battery_charge_cycles_reset_at")
    )
    return {
        "blade_runtime_since_reset": blade_minutes,
        "blade_service_threshold_minutes": BLADE_SERVICE_THRESHOLD_MINUTES,
        "battery_cycles_since_reset": battery_cycles,
        "battery_service_threshold_cycles": BATTERY_SERVICE_THRESHOLD_CYCLES,
        "blade_runtime_reset_at": blade_reset_at.isoformat()
        if blade_reset_at
        else None,
        "battery_cycles_reset_at": battery_reset_at.isoformat()
        if battery_reset_at
        else None,
    }


def _mowing_readiness_code(device) -> str | None:
    if getattr(device, "online", None) is False:
        return "offline"
    if getattr(device, "locked", None) is True:
        return "locked"

    if _is_rain_delay(device):
        return "rain_delay"

    error_id = _error(device, "id")
    if error_id not in (None, 0, -1):
        return "error"

    battery_percent = _battery(device, "percent")
    if battery_percent is not None and battery_percent < 20:
        return "battery_low"

    if _battery(device, "charging") is True:
        return "charging"

    status_id = _status(device, "id")
    if status_id in (7, 8, 12, 32, 110, 111):
        return "mowing"
    return "ready"


def _mowing_readiness_state(device) -> str | None:
    return _mowing_readiness_code(device)


def _mowing_readiness_attributes(device) -> dict[str, Any]:
    attrs = {
        "online": getattr(device, "online", None),
        "locked": getattr(device, "locked", None),
        "readiness_code": _mowing_readiness_code(device),
        "status_id": _status(device, "id"),
        "status_description": _status_state(device),
        "raw_status_description": _status(device, "description"),
        "error_id": _error(device, "id"),
        "error_description": _error(device, "description"),
        "rain_delay": _is_rain_delay(device),
        "battery_percent": _battery(device, "percent"),
        "rain_triggered": _rain(device, "triggered"),
        "rain_remaining": _rain(device, "remaining"),
    }
    attrs.update(_rtk_station_status_attrs(device))
    return attrs


def _status_attributes(device) -> dict[str, Any]:
    """Return status sensor attributes."""
    attrs = {
        "id": _status(device, "id"),
        "raw_description": _status(device, "description"),
        "error_id": _error(device, "id"),
        "error_description": _error(device, "description"),
        "rain_delay": _is_rain_delay(device),
    }
    attrs.update(_rtk_station_status_attrs(device))
    return attrs


def _rtk_trail(device) -> list[tuple[datetime, float, float]]:
    value = getattr(device, "_worx_vision_rtk_trail", []) or []
    return list(value) if isinstance(value, list | tuple) else []


def _rtk_trail_count(device) -> int | None:
    trail = _rtk_trail(device)
    return len(trail) if trail else None


def _rtk_trail_attributes(device) -> dict[str, Any]:
    trail = _rtk_trail(device)
    recent = trail[-50:]
    return {
        "points": [
            {
                "time": time.isoformat(),
                "latitude": latitude,
                "longitude": longitude,
            }
            for time, latitude, longitude in recent
        ],
        "resets_at_local_midnight": True,
    }


def _lawn_area(device):
    value = _product_item(device, "lawn_size")
    try:
        area = float(value)
        if area > 0:
            return round(area, 2)
    except (TypeError, ValueError):
        pass

    value = get_dict_value(_first_map_zone(device), "area")
    try:
        area = float(value) / 1_000_000
    except (TypeError, ValueError):
        return None
    return round(area, 2) if area > 0 else None


def _first_address_text(address: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty text value from an address dict."""
    for key in keys:
        value = address.get(key)
        if value:
            return str(value)
    return None


def _short_rtk_address(address_data: dict[str, Any] | None) -> str | None:
    """Return a readable address short enough for a Home Assistant state."""
    if not isinstance(address_data, dict):
        return None

    address = address_data.get("address")
    if not isinstance(address, dict):
        address = {}

    road = _first_address_text(address, "road", "pedestrian", "footway", "path")
    house_number = _first_address_text(address, "house_number")
    if road and house_number:
        street = f"{road} {house_number}"
    else:
        street = road or house_number

    locality = _first_address_text(
        address,
        "city",
        "town",
        "village",
        "municipality",
        "suburb",
        "hamlet",
        "county",
    )
    postcode = _first_address_text(address, "postcode")
    country = _first_address_text(address, "country")

    city_line = " ".join(part for part in (postcode, locality) if part)
    parts = [part for part in (street, city_line, country) if part]
    if not parts:
        display_name = address_data.get("display_name")
        if display_name:
            parts = [str(display_name)]

    value = ", ".join(dict.fromkeys(parts))
    if not value:
        return None
    return value[:MAX_STRING_STATE_LENGTH]


def _rtk_address_attributes(
    address_data: dict[str, Any] | None, lookup_time: datetime | None
) -> dict[str, Any] | None:
    """Return structured address metadata from Nominatim."""
    if not isinstance(address_data, dict):
        return None

    address = address_data.get("address")
    if not isinstance(address, dict):
        address = {}

    attrs = {
        "provider": "OpenStreetMap Nominatim",
        "display_name": address_data.get("display_name"),
        "category": address_data.get("category"),
        "type": address_data.get("type"),
        "osm_type": address_data.get("osm_type"),
        "osm_id": address_data.get("osm_id"),
        "place_id": address_data.get("place_id"),
        "road": _first_address_text(address, "road", "pedestrian", "footway", "path"),
        "house_number": _first_address_text(address, "house_number"),
        "postcode": _first_address_text(address, "postcode"),
        "city": _first_address_text(address, "city", "town", "village", "municipality"),
        "suburb": _first_address_text(address, "suburb", "hamlet"),
        "county": _first_address_text(address, "county"),
        "state": _first_address_text(address, "state"),
        "country": _first_address_text(address, "country"),
        "country_code": _first_address_text(address, "country_code"),
        "attribution": address_data.get("licence"),
        "lookup_time": lookup_time.isoformat() if lookup_time else None,
        "privacy_note": "Entity disabled by default; enabling it sends RTK coordinates rounded to 7 decimal places to Nominatim.",
    }
    return {key: value for key, value in attrs.items() if value is not None}


STANDARD_SENSORS: tuple[WorxSensorDescription, ...] = (
    WorxSensorDescription(
        key="battery_percent",
        translation_key="battery_percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _battery(d, "percent"),
        attrs_fn=lambda d: {"charging": _battery(d, "charging")},
    ),
    WorxSensorDescription(
        key="status",
        translation_key="status",
        icon="mdi:robot-mower",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_STATE_OPTIONS,
        value_fn=_status_state,
        attrs_fn=_status_attributes,
    ),
    WorxSensorDescription(
        key="error",
        translation_key="error",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_STATE_OPTIONS,
        value_fn=_error_state,
        attrs_fn=lambda d: {
            "id": _error(d, "id"),
            "raw_description": _error(d, "description"),
            "rain_delay": _is_rain_delay(d),
        },
    ),
    WorxSensorDescription(
        key="rssi",
        translation_key="rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: getattr(d, "rssi", None),
    ),
    WorxSensorDescription(
        key="nearlink_connection",
        translation_key="nearlink_connection",
        icon="mdi:access-point-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=NEARLINK_CONNECTION_OPTIONS,
        value_fn=nearlink_connection_state,
        attrs_fn=nearlink_attributes,
    ),
    WorxSensorDescription(
        key="zone_current",
        translation_key="zone_current",
        icon="mdi:map-marker-path",
        value_fn=lambda d: _zone(d, "current"),
        attrs_fn=lambda d: {
            "index": _zone(d, "index"),
            "ids": _zone(d, "ids"),
            "starting_point": _zone(d, "starting_point"),
        },
    ),
    WorxSensorDescription(
        key="mowing_readiness",
        translation_key="mowing_readiness",
        icon="mdi:clipboard-check-outline",
        device_class=SensorDeviceClass.ENUM,
        options=READINESS_STATE_OPTIONS,
        value_fn=_mowing_readiness_state,
        attrs_fn=_mowing_readiness_attributes,
    ),
    WorxSensorDescription(
        key="cloud_connection",
        translation_key="cloud_connection",
        icon="mdi:cloud-check-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=CLOUD_CONNECTION_OPTIONS,
        value_fn=_cloud_connection_state,
        attrs_fn=_cloud_connection_attributes,
    ),
    WorxSensorDescription(
        key="api_capabilities",
        translation_key="api_capabilities",
        icon="mdi:api",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_capability_summary,
        attrs_fn=_capability_attributes,
    ),
    WorxSensorDescription(
        key="push_notifications",
        translation_key="push_notifications",
        icon="mdi:bell-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_push_notification_state,
        attrs_fn=lambda d: {
            "enabled": _product_item(d, "push_notifications"),
            "level": _product_item(d, "push_notifications_level"),
        },
    ),
    WorxSensorDescription(
        key="area_mowed_total",
        translation_key="area_mowed_total",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:grass",
        value_fn=_area_mowed_total,
        attrs_fn=lambda d: {
            "lawn_area": _lawn_area(d),
            "source": "worx_cloud_cumulative_counter",
            "cloud_data_updated_at": _cloud_statistics_updated(d),
        },
    ),
    WorxSensorDescription(
        key="lawn_area",
        translation_key="lawn_area",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:set-square",
        value_fn=_lawn_area,
    ),
    WorxSensorDescription(
        key="mowing_efficiency",
        translation_key="mowing_efficiency",
        native_unit_of_measurement="m2/h",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:speedometer",
        value_fn=_mowing_efficiency,
        attrs_fn=lambda d: {
            "area_mowed_total": _area_mowed_total(d),
            "blade_runtime_total": _blades(d, "total_on")
            or _statistics(d, "worktime_blades_on"),
            "fallback_mower_work_time": _product_item(d, "mower_work_time"),
        },
    ),
    WorxSensorDescription(
        key="cloud_statistics_updated",
        translation_key="cloud_statistics_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:cloud-clock-outline",
        value_fn=_cloud_statistics_updated,
    ),
    WorxSensorDescription(
        key="rtk_trail_points",
        translation_key="rtk_trail_points",
        icon="mdi:map-marker-path",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_rtk_trail_count,
        attrs_fn=_rtk_trail_attributes,
    ),
    WorxSensorDescription(
        key="rain_remaining",
        translation_key="rain_remaining",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _rain(d, "remaining"),
    ),
    WorxSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _battery(d, "voltage"),
    ),
    WorxSensorDescription(
        key="battery_temperature",
        translation_key="battery_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _battery(d, "temperature"),
    ),
    WorxSensorDescription(
        key="battery_cycles_total",
        translation_key="battery_cycles_total",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-sync",
        value_fn=lambda d: get_dict_value(_battery(d, "cycles", {}), "total")
        or _product_item(d, "battery_charge_cycles"),
    ),
    WorxSensorDescription(
        key="battery_cycles_since_reset",
        translation_key="battery_cycles_since_reset",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-heart",
        value_fn=lambda d: _since_reset(
            d, "battery_charge_cycles", "battery_charge_cycles_reset"
        ),
    ),
    WorxSensorDescription(
        key="battery_cycles_reset_at",
        translation_key="battery_cycles_reset_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-clock",
        value_fn=lambda d: _as_datetime(_product_item(d, "battery_charge_cycles_reset_at")),
    ),
    WorxSensorDescription(
        key="blade_runtime_total",
        translation_key="blade_runtime_total",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _blades(d, "total_on"),
    ),
    WorxSensorDescription(
        key="blade_runtime_current",
        translation_key="blade_runtime_current",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _blades(d, "current_on")
        or _since_reset(d, "blade_work_time", "blade_work_time_reset"),
    ),
    WorxSensorDescription(
        key="blade_runtime_reset_at",
        translation_key="blade_runtime_reset_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:timer-check-outline",
        value_fn=lambda d: _as_datetime(_product_item(d, "blade_work_time_reset_at")),
    ),
    WorxSensorDescription(
        key="mower_runtime_total",
        translation_key="mower_runtime_total",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_work_time_total_minutes,
    ),
    WorxSensorDescription(
        key="mower_home_time_total",
        translation_key="mower_home_time_total",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:home-clock",
        value_fn=lambda d: _product_item(d, "mower_home_time"),
    ),
    WorxSensorDescription(
        key="mower_charging_time_total",
        translation_key="mower_charging_time_total",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:battery-clock-outline",
        value_fn=lambda d: _product_item(d, "mower_charging_time"),
    ),
    WorxSensorDescription(
        key="mower_error_time_total",
        translation_key="mower_error_time_total",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:alert-clock-outline",
        value_fn=lambda d: _product_item(d, "mower_error_time"),
    ),
    WorxSensorDescription(
        key="maintenance_status",
        translation_key="maintenance_status",
        icon="mdi:wrench-clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=MAINTENANCE_STATE_OPTIONS,
        value_fn=_maintenance_state,
        attrs_fn=_maintenance_attributes,
    ),
    WorxSensorDescription(
        key="pitch",
        translation_key="pitch",
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:axis-x-rotate-clockwise",
        value_fn=lambda d: _orientation(d, "pitch"),
    ),
    WorxSensorDescription(
        key="roll",
        translation_key="roll",
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:axis-y-rotate-clockwise",
        value_fn=lambda d: _orientation(d, "roll"),
    ),
    WorxSensorDescription(
        key="yaw",
        translation_key="yaw",
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:axis-z-rotate-clockwise",
        value_fn=lambda d: _orientation(d, "yaw"),
    ),
    WorxSensorDescription(
        key="last_update_age",
        translation_key="last_update_age",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-sand",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_update_age,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator

    entities: list[SensorEntity] = []
    known_raw: set[str] = set()

    for serial_number in coordinator.data:
        device = coordinator.data[serial_number]
        entities.extend(
            WorxVisionSensor(coordinator, entry, serial_number, description)
            for description in STANDARD_SENSORS
            if description.key != "nearlink_connection" or has_nearlink_module(device)
        )
        entities.append(WorxVisionAddressSensor(coordinator, entry, serial_number))
        entities.append(WorxScheduleSensor(coordinator, entry, serial_number))
        entities.append(WorxNextScheduleSensor(coordinator, entry, serial_number))
        entities.append(WorxRtkMapSensor(coordinator, entry, serial_number))
        entities.append(WorxLastUpdateSensor(coordinator, entry, serial_number))
        entities.append(WorxAreaMowedTodaySensor(coordinator, entry, serial_number))
        entities.append(WorxMowingTimeTodaySensor(coordinator, entry, serial_number))
        entities.append(WorxDailyProgressSensor(coordinator, entry, serial_number))
        entities.append(WorxRemainingProgressSensor(coordinator, entry, serial_number))
        entities.append(WorxEstimatedAreaTodaySensor(coordinator, entry, serial_number))
        entities.append(WorxEstimatedDailyProgressSensor(coordinator, entry, serial_number))

    def add_raw_entities() -> None:
        raw_entities: list[SensorEntity] = []
        if not entry.data.get(CONF_EXPOSE_RAW, DEFAULT_EXPOSE_RAW):
            return

        for serial_number, device in (coordinator.data or {}).items():
            paths = raw_entity_path_map(device)
            for key, value in raw_entity_values(device).items():
                if isinstance(value, bool):
                    continue
                unique = f"{serial_number}_raw_{key}"
                if unique in known_raw:
                    continue
                known_raw.add(unique)
                raw_entities.append(
                    WorxVisionRawSensor(
                        coordinator,
                        entry,
                        serial_number,
                        key,
                        paths.get(key, key),
                    )
                )

        if raw_entities:
            async_add_entities(raw_entities)

    add_raw_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_raw_entities))
    async_add_entities(entities)


class WorxVisionSensor(WorxVisionEntity, SensorEntity):
    """Regular sensor."""

    entity_description: WorxSensorDescription

    def __init__(
        self,
        coordinator,
        entry,
        serial_number: str,
        description: WorxSensorDescription,
    ) -> None:
        """Initialize sensor."""
        self.entity_description = description
        super().__init__(coordinator, entry, serial_number, description.key)

    @property
    def native_value(self) -> Any:
        """Return native value."""
        return self.entity_description.value_fn(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        attrs = self.entity_description.attrs_fn(self.device)
        return {key: value for key, value in (attrs or {}).items() if value is not None}


class WorxNextScheduleSensor(WorxVisionEntity, SensorEntity):
    """Timestamp of the next scheduled mowing start."""

    _attr_translation_key = "next_schedule"
    _attr_icon = "mdi:calendar-arrow-right"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize the next schedule sensor."""
        super().__init__(coordinator, entry, serial_number, "next_schedule")

    @property
    def native_value(self) -> datetime | None:
        """Return the next scheduled mowing start."""
        return next_schedule_start(self.device, dt_util.now())


class WorxRtkMapSensor(WorxVisionEntity, SensorEntity):
    """RTK map id, from the coordinator's cache rather than the live device.

    A partial MQTT cfg push from Worx can momentarily omit the rtk block on
    the live device object, and pyworxcloud mutates that object in place
    (no real "previous" snapshot survives at the object level to fall back
    to). The coordinator keeps its own independent last-known-value cache
    for exactly this reason, so this sensor uses that instead of reading
    raw_cfg directly.
    """

    _attr_translation_key = "rtk_map"
    _attr_icon = "mdi:map-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize the RTK map sensor."""
        super().__init__(coordinator, entry, serial_number, "rtk_map")

    @property
    def native_value(self) -> str | None:
        """Return the cached RTK map id."""
        return self.coordinator.rtk_map_id(self._serial_number)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return RTK map metadata best-effort from the live device."""
        attrs = rtk_map_attributes(self.device)
        return {key: value for key, value in attrs.items() if value is not None}


LAST_UPDATE_REPORT_INTERVAL = timedelta(hours=24)


class WorxLastUpdateSensor(WorxVisionEntity, RestoreSensor):
    """Timestamp of the last data received from the mower.

    The underlying value changes on every push (as often as every ~20
    seconds), which would make Home Assistant's logbook narrate a "changed"
    entry that often. Since this sensor is meant as an occasional heartbeat
    check rather than a live clock, it only accepts a new value once per
    LAST_UPDATE_REPORT_INTERVAL and otherwise keeps reporting the previous
    one, so the logbook only sees one real change per interval.
    """

    _attr_translation_key = "last_update"
    _attr_icon = "mdi:clock-check"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize the last update sensor."""
        super().__init__(coordinator, entry, serial_number, "last_update")
        self._reported_value: datetime | None = None
        self._reported_at: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last reported value and when it was accepted."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None:
            return
        self._reported_value = _as_datetime(last_state.state)
        self._reported_at = _as_datetime(last_state.attributes.get("reported_at"))

    @property
    def native_value(self) -> datetime | None:
        """Return the last-reported update time, refreshed at most once a day."""
        current = _last_update(self.device)
        now = dt_util.utcnow()
        if (
            self._reported_value is None
            or self._reported_at is None
            or now - self._reported_at >= LAST_UPDATE_REPORT_INTERVAL
        ):
            self._reported_value = current
            self._reported_at = now
        return self._reported_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Persist when the reported value was accepted, for restarts."""
        return {
            "reported_at": self._reported_at.isoformat() if self._reported_at else None,
        }


class WorxScheduleSensor(WorxVisionEntity, SensorEntity):
    """Compact weekly schedule summary, localized to the UI language."""

    _attr_translation_key = "schedule"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize the schedule sensor."""
        super().__init__(coordinator, entry, serial_number, "schedule")

    @property
    def _language(self) -> str:
        """Return the active Home Assistant UI language."""
        config = getattr(self.hass, "config", None)
        return getattr(config, "language", None) or "en"

    @property
    def native_value(self) -> str | None:
        """Return the localized schedule summary."""
        return schedule_summary(self.device, self._language)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return structured schedule data."""
        attrs = schedule_attributes(self.device, self._language)
        return {key: value for key, value in attrs.items() if value is not None}


class WorxAreaMowedTodaySensor(WorxVisionEntity, SensorEntity):
    """Area added to the cumulative cloud counter since local midnight.

    The daily baseline lives in the coordinator's persisted statistics
    tracker (keyed by serial number), so every daily sensor shares the same
    baseline and the value survives restarts, entity renames and entity
    re-creation.
    """

    _attr_translation_key = "area_mowed_today"
    _attr_device_class = SensorDeviceClass.AREA
    _attr_native_unit_of_measurement = UnitOfArea.SQUARE_METERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:grass"

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize area mowed today."""
        super().__init__(coordinator, entry, serial_number, "area_mowed_today")

    @property
    def native_value(self) -> float | None:
        """Return today's cloud-counter delta."""
        return self.coordinator.area_mowed_today(self._serial_number)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Explain the source and baseline behind the daily value."""
        return {
            "source": "worx_cloud_cumulative_counter_delta",
            **self.coordinator.daily_area_details(self._serial_number),
            "lawn_area": _lawn_area(self.device),
            "cloud_data_updated_at": _cloud_statistics_updated(self.device),
        }


class WorxDailyProgressSensor(WorxVisionEntity, SensorEntity):
    """Percentage of the lawn covered today, from the cloud counter delta."""

    _attr_translation_key = "daily_progress"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:progress-check"

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize daily progress."""
        super().__init__(coordinator, entry, serial_number, "daily_progress")

    def _progress(self) -> float | None:
        """Return today's covered area as a percentage of the lawn size."""
        area = self.coordinator.area_mowed_today(self._serial_number)
        lawn_area = _lawn_area(self.device)
        if area is None or lawn_area in (None, 0):
            return None
        return round(max(0, min(100, area / lawn_area * 100)), 1)

    @property
    def native_value(self) -> float | None:
        """Return today's progress in percent."""
        return self._progress()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the values used by the progress calculation."""
        return {
            "source": "worx_cloud_cumulative_counter_delta",
            "area_mowed_today": self.coordinator.area_mowed_today(
                self._serial_number
            ),
            "lawn_area": _lawn_area(self.device),
            "cloud_data_updated_at": _cloud_statistics_updated(self.device),
        }


class WorxRemainingProgressSensor(WorxDailyProgressSensor):
    """Percentage of the lawn still to mow today."""

    _attr_translation_key = "remaining_progress"
    _attr_icon = "mdi:progress-clock"

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize remaining progress."""
        WorxVisionEntity.__init__(
            self,
            coordinator,
            entry,
            serial_number,
            "remaining_progress",
        )

    @property
    def native_value(self) -> float | None:
        """Return the complement of today's progress."""
        progress = self._progress()
        return None if progress is None else round(max(0, 100 - progress), 1)


class WorxMowingTimeTodaySensor(WorxVisionEntity, SensorEntity):
    """Locally observed time spent mowing since local midnight.

    Worx's own work-time statistics are only included in some MQTT payloads
    and can go stale for hours during active mowing, so the coordinator
    tracks wall-clock time spent in the mowing/starting statuses itself.
    This exposes that figure directly; the estimated area/progress sensors
    multiply it by the average mowing efficiency.
    """

    _attr_translation_key = "mowing_time_today"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-play-outline"

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize mowing time today."""
        super().__init__(coordinator, entry, serial_number, "mowing_time_today")

    @property
    def native_value(self) -> float:
        """Return today's mowing minutes."""
        return self.coordinator.mowing_minutes_today(self._serial_number)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Explain how the value is obtained."""
        return {"source": "local_mowing_status_wall_clock"}


class WorxEstimatedAreaTodaySensor(WorxVisionEntity, SensorEntity):
    """Estimated area mowed today, from today's mowing time and average efficiency.

    area_mowed only refreshes when Worx's REST product-item endpoint reports a
    new figure, which can lag for hours during active mowing. This sensor
    estimates today's coverage instead as time actually spent mowing today
    (tracked by the coordinator, independent of Worx's own statistics
    reporting) multiplied by the mower's average mowing efficiency (m²/h),
    so it moves during the day even when Total/Today area mowed are stuck
    waiting for Worx to recompute the real figure.
    """

    _attr_translation_key = "estimated_area_mowed_today"
    _attr_device_class = SensorDeviceClass.AREA
    _attr_native_unit_of_measurement = UnitOfArea.SQUARE_METERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:grass"

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize estimated area mowed today."""
        super().__init__(coordinator, entry, serial_number, "estimated_area_mowed_today")

    def _estimated_area(self) -> float | None:
        """Return today's mowing time multiplied by the average efficiency."""
        efficiency = _mowing_efficiency(self.device)
        if efficiency is None:
            return None
        minutes = self.coordinator.mowing_minutes_today(self._serial_number)
        return round(minutes / 60 * efficiency, 2)

    @property
    def native_value(self) -> float | None:
        """Return today's estimated mowed area."""
        return self._estimated_area()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the figures used for the estimate."""
        return {
            "source": "local_mowing_time_x_lifetime_efficiency",
            "mowing_minutes_today": self.coordinator.mowing_minutes_today(
                self._serial_number
            ),
            "mowing_efficiency": _mowing_efficiency(self.device),
        }


class WorxEstimatedDailyProgressSensor(WorxEstimatedAreaTodaySensor):
    """Estimated percentage of the lawn mowed today.

    Same estimate as WorxEstimatedAreaTodaySensor (today's mowing time x
    average efficiency), expressed as a percentage of the known lawn area, so
    it moves during the day even when the cloud-based daily progress is stuck.
    """

    _attr_translation_key = "estimated_daily_progress"
    _attr_device_class = None
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:progress-check"

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize estimated daily progress."""
        WorxVisionEntity.__init__(
            self,
            coordinator,
            entry,
            serial_number,
            "estimated_daily_progress",
        )

    @property
    def native_value(self) -> float | None:
        """Return today's estimated progress in percent."""
        area = self._estimated_area()
        lawn_area = _lawn_area(self.device)
        if area is None or lawn_area in (None, 0):
            return None
        return round(max(0, min(100, area / lawn_area * 100)), 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the figures used for the estimate."""
        return {
            **super().extra_state_attributes,
            "lawn_area": _lawn_area(self.device),
        }


class WorxVisionAddressSensor(WorxVisionEntity, SensorEntity):
    """Reverse-geocoded RTK address sensor."""

    _attr_translation_key = "rtk_address"
    _attr_icon = "mdi:map-marker-account"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize address sensor."""
        super().__init__(coordinator, entry, serial_number, "rtk_address")
        self._address_data: dict[str, Any] | None = None
        self._address_cache_key: str | None = None
        self._address_lookup_time: datetime | None = None
        self._address_task: Task | None = None

    async def async_added_to_hass(self) -> None:
        """Schedule the first address lookup when the enabled entity is added."""
        await super().async_added_to_hass()
        self._schedule_address_lookup()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel an in-flight lookup when Home Assistant removes the entity."""
        if self._address_task is not None:
            self._address_task.cancel()
        await super().async_will_remove_from_hass()

    @property
    def available(self) -> bool:
        """Return entity availability."""
        return super().available and rtk_position(self.device) is not None

    @property
    def native_value(self) -> str | None:
        """Return the short reverse-geocoded address."""
        return _short_rtk_address(self._address_data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return structured address attributes."""
        return _rtk_address_attributes(self._address_data, self._address_lookup_time)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh address when RTK position changes enough to need a new lookup."""
        self._schedule_address_lookup()
        super()._handle_coordinator_update()

    def _schedule_address_lookup(self) -> None:
        """Schedule reverse-geocoding if the rounded RTK position changed."""
        if not self.hass or not self.available:
            return

        position = rtk_position(self.device)
        if position is None:
            return

        cache_key = self.coordinator.rtk_address_cache_key(position)
        if self._address_data is not None and self._address_cache_key == cache_key:
            return

        if self._address_task is not None and not self._address_task.done():
            return

        self._address_task = self.hass.async_create_task(
            self._async_lookup_address(position, cache_key)
        )

    async def _async_lookup_address(
        self, position: tuple[float, float], cache_key: str
    ) -> None:
        """Look up and store a reverse-geocoded address."""
        address_data = await self.coordinator.async_reverse_geocode_rtk_position(position)
        if address_data is None:
            return

        self._address_data = address_data
        self._address_cache_key = cache_key
        self._address_lookup_time = datetime.now(UTC)
        self.async_write_ha_state()


class WorxVisionRawSensor(WorxVisionEntity, SensorEntity):
    """Dynamic raw scalar sensor."""

    _attr_icon = "mdi:code-json"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        entry,
        serial_number: str,
        key: str,
        raw_path: str,
    ) -> None:
        """Initialize raw sensor."""
        super().__init__(coordinator, entry, serial_number, f"raw_{key}")
        self._raw_key = key
        self._raw_path = raw_path
        self._attr_name = f"Raw {raw_path}"
        self._attr_entity_registry_enabled_default = raw_path_enabled_default(raw_path)

    @property
    def native_value(self) -> Any:
        """Return current raw value."""
        value = raw_entity_values(self.device).get(self._raw_key)
        if isinstance(value, bool):
            return None
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return raw path metadata."""
        return {
            ATTR_RAW_PATH: self._raw_path,
            ATTR_RAW_SOURCE: self._raw_path.split(".", 1)[0],
        }
