"""Device tracker platform for Worx Vision Cloud Plus."""
from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_BATTERY_LEVEL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import WorxVisionEntity
from .helpers import get_dict_value, rtk_location_attributes, rtk_position


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up RTK location trackers."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator

    async_add_entities(
        WorxVisionLocationTracker(coordinator, entry, serial_number)
        for serial_number in coordinator.data
    )


class WorxVisionLocationTracker(WorxVisionEntity, TrackerEntity):
    """GPS/RTK location tracker for one mower."""

    _attr_icon = "mdi:map-marker-radius-outline"
    _attr_translation_key = "rtk_position"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize RTK location tracker."""
        super().__init__(coordinator, entry, serial_number, "rtk_location")

    @property
    def available(self) -> bool:
        """Return entity availability."""
        return super().available and rtk_position(self.device) is not None

    @property
    def latitude(self) -> float | None:
        """Return latitude."""
        position = rtk_position(self.device)
        return None if position is None else position[0]

    @property
    def longitude(self) -> float | None:
        """Return longitude."""
        position = rtk_position(self.device)
        return None if position is None else position[1]

    @property
    def location_accuracy(self) -> float:
        """Return location accuracy in meters."""
        return 1.0

    def _battery_level(self) -> int | None:
        """Return mower battery level for compatibility attributes.

        Not a `battery_level` property override: BaseTrackerEntity deprecated
        that property (removal planned for Home Assistant 2027.7), so the value
        is exposed as a plain state attribute instead.
        """
        value = get_dict_value(getattr(self.device, "battery", {}), "percent")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return RTK map and receiver metadata."""
        attributes = {
            key: value
            for key, value in rtk_location_attributes(self.device).items()
            if value is not None
        }
        if (battery_level := self._battery_level()) is not None:
            attributes[ATTR_BATTERY_LEVEL] = battery_level
        return attributes
