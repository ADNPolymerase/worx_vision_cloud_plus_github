"""Lawn mower platform for Worx Vision Cloud Plus."""
from __future__ import annotations

import logging

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import WorxVisionEntity
from .helpers import get_dict_value, rtk_at_station, rtk_distance_to_station_m

_LOGGER = logging.getLogger(__name__)

MOWING_STATUS_IDS = {7, 8, 12, 32, 110, 111}
RETURNING_STATUS_IDS = {4, 5, 6, 30, 104}
STARTING_STATUS_IDS = {2, 3, 33, 103}
PAUSED_STATUS_IDS = {34}
DOCKED_STATUS_IDS = {1}
ERROR_STATUS_IDS = {9, 10, 13}
RAIN_DELAY_ERROR_DESCRIPTIONS = {"rain delay", "rain_delay"}


def _is_rain_delay(device) -> bool:
    """Return whether the mower reports rain delay instead of a real fault."""
    error_description = get_dict_value(getattr(device, "error", {}), "description")
    if (
        error_description is not None
        and str(error_description).strip().lower() in RAIN_DELAY_ERROR_DESCRIPTIONS
    ):
        return True

    rain = getattr(device, "rainsensor", {})
    rain_remaining = get_dict_value(rain, "remaining")
    try:
        remaining_minutes = float(rain_remaining or 0)
    except (TypeError, ValueError):
        remaining_minutes = 0

    return get_dict_value(rain, "triggered") is True or remaining_minutes > 0


def _has_real_error(device) -> bool:
    """Return true when the mower reports a real error state."""
    error_id = get_dict_value(getattr(device, "error", {}), "id", 0)
    return error_id not in (None, 0, -1) and not _is_rain_delay(device)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up lawn mower entities."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            WorxVisionLawnMower(runtime.coordinator, entry, serial_number)
            for serial_number in runtime.coordinator.data
        ]
    )


class WorxVisionLawnMower(WorxVisionEntity, LawnMowerEntity):
    """Worx Landroid mower entity."""

    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize mower."""
        super().__init__(coordinator, entry, serial_number, "mower")
        self._attr_translation_key = "mower"

    @property
    def available(self) -> bool:
        """Mower commands require the device to be online."""
        return super().available and bool(getattr(self.device, "online", False))

    @property
    def activity(self) -> LawnMowerActivity | None:
        """Return current mower activity."""
        device = self.device
        status_id = get_dict_value(getattr(device, "status", {}), "id", -1)

        if _has_real_error(device):
            return LawnMowerActivity.ERROR
        if status_id in DOCKED_STATUS_IDS:
            return LawnMowerActivity.DOCKED
        if status_id in PAUSED_STATUS_IDS:
            return LawnMowerActivity.PAUSED
        if status_id in RETURNING_STATUS_IDS:
            return LawnMowerActivity.RETURNING
        if status_id in MOWING_STATUS_IDS or status_id in STARTING_STATUS_IDS:
            return LawnMowerActivity.MOWING
        if _is_rain_delay(device):
            return LawnMowerActivity.DOCKED
        if status_id in ERROR_STATUS_IDS:
            return LawnMowerActivity.ERROR
        return None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return useful attributes."""
        device = self.device
        attrs: dict[str, object] = {
            "serial_number": self._serial_number,
            "online": bool(getattr(device, "online", False)),
            "status_id": get_dict_value(getattr(device, "status", {}), "id"),
            "status_description": get_dict_value(
                getattr(device, "status", {}), "description"
            ),
            "error_id": get_dict_value(getattr(device, "error", {}), "id"),
            "error_description": get_dict_value(
                getattr(device, "error", {}), "description"
            ),
            "rain_delay": _is_rain_delay(device),
            "battery_percent": get_dict_value(getattr(device, "battery", {}), "percent"),
        }

        station_distance = rtk_distance_to_station_m(device)
        if station_distance is not None:
            attrs["rtk_station_distance_m"] = round(station_distance, 2)
            attrs["rtk_at_station"] = rtk_at_station(device)

        gps = getattr(device, "gps", None)
        if gps is not None:
            latitude = get_dict_value(gps, "latitude")
            longitude = get_dict_value(gps, "longitude")
            if latitude is not None and longitude is not None:
                attrs["latitude"] = latitude
                attrs["longitude"] = longitude

        return attrs

    async def async_start_mowing(self) -> None:
        """Start or resume mowing."""
        await self.coordinator.cloud.start(self._serial_number)
        await self.coordinator.async_request_device_update(self._serial_number)

    async def async_pause(self) -> None:
        """Pause mowing."""
        await self.coordinator.cloud.pause(self._serial_number)
        await self.coordinator.async_request_device_update(self._serial_number)

    async def async_dock(self) -> None:
        """Return mower to dock."""
        await self.coordinator.cloud.home(self._serial_number)
        await self.coordinator.async_request_device_update(self._serial_number)
