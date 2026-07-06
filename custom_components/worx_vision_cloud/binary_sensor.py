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


def _error(device, key, default=None):
    return get_dict_value(getattr(device, "error", {}), key, default)


def _orientation(device, key, default=None):
    return get_dict_value(getattr(device, "orientation", {}), key, default)


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


def _normalized_text(value: Any) -> str:
    """Return normalized API text for robust comparisons."""
    return str(value or "").strip().lower().replace("_", " ").replace("-", " ")


def _robot_lifted(device) -> bool | None:
    """Return whether the mower currently reports a lifted/upside-down error."""
    error_id = _error(device, "id")
    description = _error(device, "description")
    normalized = _normalized_text(description)

    if error_id in (0, -1) or normalized in {"no error", "none"}:
        return False

    if description is None or normalized == "":
        return None

    return "lifted" in normalized or "upside down" in normalized


def _robot_lifted_attributes(device) -> dict[str, Any]:
    """Return lift-alarm context from the current API payload."""
    return {
        "error_id": _error(device, "id"),
        "error_description": _error(device, "description"),
        "pitch": _orientation(device, "pitch"),
        "roll": _orientation(device, "roll"),
        "yaw": _orientation(device, "yaw"),
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
        key="rain_triggered",
        translation_key="rain_triggered",
        device_class=BinarySensorDeviceClass.MOISTURE,
        value_fn=lambda d: _rain(d, "triggered"),
    ),
    WorxBinarySensorDescription(
        key="robot_lifted",
        translation_key="robot_lifted",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_robot_lifted,
        attrs_fn=_robot_lifted_attributes,
    ),
    WorxBinarySensorDescription(
        key="pause_mode_enabled",
        translation_key="pause_mode_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: getattr(d, "pause_mode_enabled", None),
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
