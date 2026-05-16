"""Sensor platform for Worx Vision Cloud Plus."""
from __future__ import annotations

from asyncio import Task
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from homeassistant.components.sensor import (
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

from .const import (
    ATTR_RAW_PATH,
    ATTR_RAW_SOURCE,
    CONF_EXPOSE_RAW,
    DEFAULT_EXPOSE_RAW,
    DOMAIN,
)
from .entity import WorxVisionEntity
from .helpers import (
    MAX_STRING_STATE_LENGTH,
    get_dict_value,
    raw_entity_path_map,
    raw_entity_values,
    raw_path_enabled_default,
    rtk_map_attributes,
    rtk_map_id,
    rtk_position,
    schedule_attributes,
    schedule_summary,
)


@dataclass(frozen=True, kw_only=True)
class WorxSensorDescription(SensorEntityDescription):
    """Description for a regular sensor."""

    value_fn: Callable[[Any], Any]
    attrs_fn: Callable[[Any], dict[str, Any] | None] | None = None


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


def _error(device, key, default=None):
    return get_dict_value(getattr(device, "error", {}), key, default)


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


def _area_mowed_today(device):
    value = _product_item(device, "area_mowed")
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


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


def _daily_progress(device):
    area_mowed = _area_mowed_today(device)
    lawn_area = _lawn_area(device)
    if area_mowed is None or lawn_area in (None, 0):
        return None
    return round(max(0, min(100, area_mowed / lawn_area * 100)), 1)


def _remaining_progress(device):
    progress = _daily_progress(device)
    if progress is None:
        return None
    return round(max(0, 100 - progress), 1)


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
        "privacy_note": "Entity disabled by default; enabling it sends rounded RTK coordinates to Nominatim.",
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
        value_fn=lambda d: _status(d, "description"),
        attrs_fn=lambda d: {"id": _status(d, "id")},
    ),
    WorxSensorDescription(
        key="error",
        translation_key="error",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _error(d, "description"),
        attrs_fn=lambda d: {"id": _error(d, "id")},
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
        key="schedule",
        translation_key="schedule",
        icon="mdi:calendar-clock",
        value_fn=schedule_summary,
        attrs_fn=schedule_attributes,
    ),
    WorxSensorDescription(
        key="daily_progress",
        translation_key="daily_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:progress-check",
        value_fn=_daily_progress,
        attrs_fn=lambda d: {
            "area_mowed": _area_mowed_today(d),
            "lawn_area": _lawn_area(d),
        },
    ),
    WorxSensorDescription(
        key="remaining_progress",
        translation_key="remaining_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:progress-clock",
        value_fn=_remaining_progress,
        attrs_fn=lambda d: {
            "daily_progress": _daily_progress(d),
            "area_mowed": _area_mowed_today(d),
            "lawn_area": _lawn_area(d),
        },
    ),
    WorxSensorDescription(
        key="area_mowed_today",
        translation_key="area_mowed_today",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:grass",
        value_fn=_area_mowed_today,
    ),
    WorxSensorDescription(
        key="rtk_map",
        translation_key="rtk_map",
        icon="mdi:map-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=rtk_map_id,
        attrs_fn=rtk_map_attributes,
    ),
    WorxSensorDescription(
        key="rain_delay",
        translation_key="rain_delay",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _rain(d, "delay"),
        attrs_fn=lambda d: {
            "triggered": _rain(d, "triggered"),
            "remaining": _rain(d, "remaining"),
        },
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
        value_fn=lambda d: get_dict_value(_battery(d, "cycles", {}), "total"),
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
        value_fn=lambda d: _blades(d, "current_on"),
    ),
    WorxSensorDescription(
        key="mower_runtime_total",
        translation_key="mower_runtime_total",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _statistics(d, "worktime_total"),
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
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-check",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_update,
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
        entities.extend(
            WorxVisionSensor(coordinator, entry, serial_number, description)
            for description in STANDARD_SENSORS
        )
        entities.append(WorxVisionAddressSensor(coordinator, entry, serial_number))

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
