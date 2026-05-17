"""Binary sensor platform for Worx Vision Cloud Plus."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
    get_dict_value,
    raw_entity_path_map,
    raw_entity_values,
    raw_path_enabled_default,
)


@dataclass(frozen=True, kw_only=True)
class WorxBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor description."""

    value_fn: Callable[[Any], bool | None]
    attrs_fn: Callable[[Any], dict[str, Any] | None] | None = None


def _battery(device, key, default=None):
    return get_dict_value(getattr(device, "battery", {}), key, default)


def _rain(device, key, default=None):
    return get_dict_value(getattr(device, "rainsensor", {}), key, default)


def _product_item(device) -> dict[str, Any]:
    """Return cached product item details from the private API."""
    value = getattr(device, "_worx_vision_product_item", {}) or {}
    return value if isinstance(value, dict) else {}


def _as_bool(value: Any) -> bool | None:
    """Return a bool from common API bool/int/string values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "on", "yes"}:
            return True
        if lowered in {"0", "false", "off", "no"}:
            return False
    return None


def _rtk_map_data(device) -> dict[str, Any]:
    """Return cached RTK map payload from the private API."""
    value = getattr(device, "_worx_vision_rtk_map", {}) or {}
    return value if isinstance(value, dict) else {}


def _first_map_zone_metadata(device) -> dict[str, Any]:
    """Return metadata from the first RTK map boundary zone."""
    layers = get_dict_value(_rtk_map_data(device), "layers", {}) or {}
    boundaries = get_dict_value(layers, "boundaries", []) or []
    for boundary in boundaries:
        zones = get_dict_value(boundary, "zones", []) or []
        for zone in zones:
            metadata = get_dict_value(zone, "metadata", {}) or {}
            if isinstance(metadata, dict):
                return metadata
    return {}


def _smart_edge_cut_enabled(device) -> bool | None:
    """Return the Vision map setting that allows cutting over the border."""
    value = get_dict_value(_first_map_zone_metadata(device), "cut_over_border")
    return value if isinstance(value, bool) else None


def _smart_edge_cut_attributes(device) -> dict[str, Any]:
    """Return map metadata related to intelligent edge cutting."""
    metadata = _first_map_zone_metadata(device)
    product_item = _product_item(device)
    capabilities = get_dict_value(product_item, "capabilities", []) or []
    if not isinstance(capabilities, list | tuple):
        capabilities = []

    return {
        "api_field": "layers.boundaries[].zones[].metadata.cut_over_border",
        "capability_border_cut": "border_cut" in capabilities,
        "capability_pause_over_border": "pause_over_border" in capabilities,
        "cut_type": get_dict_value(metadata, "cut_type"),
        "cut_direction": get_dict_value(metadata, "cut_direction"),
        "pattern_width": get_dict_value(metadata, "pattern_width"),
    }


def _schedule_entries(device) -> list[Any]:
    """Return schedule entries from map API or normalized pyworxcloud slots."""
    map_schedule = get_dict_value(_rtk_map_data(device), "schedule", []) or []
    if isinstance(map_schedule, list | tuple) and map_schedule:
        return list(map_schedule)

    schedules = getattr(device, "schedules", {}) or {}
    slots = get_dict_value(schedules, "slots", []) or []
    return list(slots) if isinstance(slots, list | tuple) else []


def _schedule_border_cut_enabled(device) -> bool | None:
    """Return true when at least one schedule entry has border cut enabled."""
    entries = _schedule_entries(device)
    if not entries:
        return None
    for entry in entries:
        value = get_dict_value(entry, "border_cut")
        if value is None:
            value = get_dict_value(entry, "boundary")
        if value is True:
            return True
    return False


def _schedule_border_cut_attributes(device) -> dict[str, Any]:
    """Return per-slot edge procedure details."""
    entries = _schedule_entries(device)
    border_entries: list[dict[str, Any]] = []
    for entry in entries:
        border_cut = get_dict_value(entry, "border_cut")
        if border_cut is None:
            border_cut = get_dict_value(entry, "boundary")
        if border_cut is not True:
            continue
        border_entries.append(
            {
                "day_of_week": get_dict_value(entry, "day_of_week"),
                "day": get_dict_value(entry, "day"),
                "starts_at": get_dict_value(entry, "starts_at")
                or get_dict_value(entry, "start"),
                "duration": get_dict_value(entry, "duration"),
                "zones": get_dict_value(entry, "zones"),
                "source": get_dict_value(entry, "source"),
            }
        )

    return {
        "api_field": "schedule[].border_cut / schedules.slots[].boundary",
        "border_cut_slots": border_entries,
        "border_cut_slot_count": len(border_entries),
        "schedule_entry_count": len(entries),
    }


def _auto_schedule_settings(device) -> dict[str, Any]:
    """Return automatic schedule settings from pyworxcloud or product item data."""
    schedules = getattr(device, "schedules", {}) or {}
    auto_schedule = get_dict_value(schedules, "auto_schedule", {}) or {}
    settings = get_dict_value(auto_schedule, "settings", {}) or {}
    if isinstance(settings, dict) and settings:
        return settings

    product_settings = get_dict_value(_product_item(device), "auto_schedule_settings", {})
    return product_settings if isinstance(product_settings, dict) else {}


def _save_hedgehogs_enabled(device) -> bool | None:
    """Return the app option commonly shown as Save the hedgehogs."""
    settings = _auto_schedule_settings(device)
    for key in ("exclude_nights", "save_hedgehogs", "hedgehog_mode"):
        value = get_dict_value(settings, key)
        if isinstance(value, bool):
            return value

    exclusion_scheduler = get_dict_value(settings, "exclusion_scheduler", {}) or {}
    if isinstance(exclusion_scheduler, dict):
        value = get_dict_value(exclusion_scheduler, "exclude_nights")
        if isinstance(value, bool):
            return value
    return None


def _save_hedgehogs_attributes(device) -> dict[str, Any]:
    """Return auto-schedule details related to Save the hedgehogs."""
    settings = _auto_schedule_settings(device)
    schedules = getattr(device, "schedules", {}) or {}
    auto_schedule = get_dict_value(schedules, "auto_schedule", {}) or {}
    product_item = _product_item(device)

    return {
        "api_field": "auto_schedule.settings.exclude_nights",
        "auto_schedule_enabled": get_dict_value(auto_schedule, "enabled")
        if isinstance(auto_schedule, dict)
        else get_dict_value(product_item, "auto_schedule"),
        "auto_schedule": get_dict_value(product_item, "auto_schedule"),
        "exclude_nights": get_dict_value(settings, "exclude_nights"),
        "exclusion_scheduler": get_dict_value(settings, "exclusion_scheduler"),
    }


BINARY_SENSORS: tuple[WorxBinarySensorDescription, ...] = (
    WorxBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda d: bool(getattr(d, "online", False)),
    ),
    WorxBinarySensorDescription(
        key="iot_registered",
        translation_key="iot_registered",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _as_bool(get_dict_value(_product_item(d), "iot_registered")),
        attrs_fn=lambda d: {
            "mqtt_endpoint": get_dict_value(_product_item(d), "mqtt_endpoint"),
        },
    ),
    WorxBinarySensorDescription(
        key="mqtt_registered",
        translation_key="mqtt_registered",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _as_bool(get_dict_value(_product_item(d), "mqtt_registered")),
        attrs_fn=lambda d: {
            "mqtt_endpoint": get_dict_value(_product_item(d), "mqtt_endpoint"),
            "mqtt_topics": get_dict_value(_product_item(d), "mqtt_topics"),
        },
    ),
    WorxBinarySensorDescription(
        key="radio_link_pending",
        translation_key="radio_link_pending",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _as_bool(
            get_dict_value(_product_item(d), "pending_radio_link_validation")
        ),
    ),
    WorxBinarySensorDescription(
        key="locked",
        translation_key="locked",
        device_class=BinarySensorDeviceClass.LOCK,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: getattr(d, "locked", None),
    ),
    WorxBinarySensorDescription(
        key="rain_triggered",
        translation_key="rain_triggered",
        device_class=BinarySensorDeviceClass.MOISTURE,
        value_fn=lambda d: _rain(d, "triggered"),
    ),
    WorxBinarySensorDescription(
        key="party_mode_enabled",
        translation_key="party_mode_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: getattr(d, "partymode_enabled", None),
    ),
    WorxBinarySensorDescription(
        key="pause_mode_enabled",
        translation_key="pause_mode_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: getattr(d, "pause_mode_enabled", None),
    ),
    WorxBinarySensorDescription(
        key="smart_edge_cut",
        translation_key="smart_edge_cut",
        icon="mdi:vector-polyline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_smart_edge_cut_enabled,
        attrs_fn=_smart_edge_cut_attributes,
    ),
    WorxBinarySensorDescription(
        key="save_hedgehogs",
        translation_key="save_hedgehogs",
        icon="mdi:weather-night",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_save_hedgehogs_enabled,
        attrs_fn=_save_hedgehogs_attributes,
    ),
    WorxBinarySensorDescription(
        key="schedule_border_cut",
        translation_key="schedule_border_cut",
        icon="mdi:border-outside",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_schedule_border_cut_enabled,
        attrs_fn=_schedule_border_cut_attributes,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator

    entities: list[BinarySensorEntity] = []
    known_raw: set[str] = set()

    for serial_number in coordinator.data:
        entities.extend(
            WorxVisionBinarySensor(coordinator, entry, serial_number, description)
            for description in BINARY_SENSORS
        )

    def add_raw_entities() -> None:
        raw_entities: list[BinarySensorEntity] = []
        if not entry.data.get(CONF_EXPOSE_RAW, DEFAULT_EXPOSE_RAW):
            return

        for serial_number, device in (coordinator.data or {}).items():
            paths = raw_entity_path_map(device)
            for key, value in raw_entity_values(device).items():
                if not isinstance(value, bool):
                    continue
                unique = f"{serial_number}_raw_binary_{key}"
                if unique in known_raw:
                    continue
                known_raw.add(unique)
                raw_entities.append(
                    WorxVisionRawBinarySensor(
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


class WorxVisionBinarySensor(WorxVisionEntity, BinarySensorEntity):
    """Regular binary sensor."""

    entity_description: WorxBinarySensorDescription

    def __init__(
        self,
        coordinator,
        entry,
        serial_number: str,
        description: WorxBinarySensorDescription,
    ) -> None:
        """Initialize binary sensor."""
        self.entity_description = description
        super().__init__(coordinator, entry, serial_number, description.key)

    @property
    def is_on(self) -> bool | None:
        """Return current state."""
        value = self.entity_description.value_fn(self.device)
        return value if isinstance(value, bool) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        attrs = self.entity_description.attrs_fn(self.device)
        return {key: value for key, value in (attrs or {}).items() if value is not None}


class WorxVisionRawBinarySensor(WorxVisionEntity, BinarySensorEntity):
    """Dynamic raw bool sensor."""

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
        """Initialize raw bool sensor."""
        super().__init__(coordinator, entry, serial_number, f"raw_binary_{key}")
        self._raw_key = key
        self._raw_path = raw_path
        self._attr_name = f"Raw {raw_path}"
        self._attr_entity_registry_enabled_default = raw_path_enabled_default(raw_path)

    @property
    def is_on(self) -> bool | None:
        """Return current raw bool."""
        value = raw_entity_values(self.device).get(self._raw_key)
        return value if isinstance(value, bool) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return raw path metadata."""
        return {
            ATTR_RAW_PATH: self._raw_path,
            ATTR_RAW_SOURCE: self._raw_path.split(".", 1)[0],
        }
