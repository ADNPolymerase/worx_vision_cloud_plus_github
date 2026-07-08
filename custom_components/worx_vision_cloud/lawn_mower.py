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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from pyworxcloud.exceptions import OfflineError

from .const import DOMAIN
from .entity import WorxVisionEntity
from .helpers import (
    DOCKED_STATUS_IDS,
    ERROR_STATUS_IDS,
    MOWING_STATUS_IDS,
    PAUSED_STATUS_IDS,
    RETURNING_STATUS_IDS,
    STARTING_STATUS_IDS,
    get_dict_value,
    rtk_at_station,
    rtk_distance_to_station_m,
)

_LOGGER = logging.getLogger(__name__)

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

    # This is the device's primary/main entity, so it has no name of its own
    # (HA convention): with has_entity_name=True its friendly_name becomes
    # exactly the device name, which is what lets companion cards like
    # landroid-card correctly strip the device name from other entities.
    _attr_name = None

    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize mower."""
        super().__init__(coordinator, entry, serial_number, "mower")

    @property
    def available(self) -> bool:
        """Return entity availability.

        Deliberately NOT gated on the mower's own `online` flag: this is the
        device's primary entity, and third-party cards such as landroid-card
        hide their entire body (falling back to a bare "not available"
        placeholder) the moment it goes unavailable. A connectivity blip
        should keep showing the last known status/activity, not blank the
        whole card. `online` is still exposed as a state attribute, and
        actions correctly fail with a clear error while offline (see below).
        """
        return super().available

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
        try:
            await self.coordinator.cloud.start(self._serial_number)
        except OfflineError as err:
            raise HomeAssistantError(
                "The mower is currently offline, no command was sent"
            ) from err
        await self.coordinator.async_request_device_update(self._serial_number)

    async def async_pause(self) -> None:
        """Pause mowing."""
        try:
            await self.coordinator.cloud.pause(self._serial_number)
        except OfflineError as err:
            raise HomeAssistantError(
                "The mower is currently offline, no command was sent"
            ) from err
        await self.coordinator.async_request_device_update(self._serial_number)

    async def async_dock(self) -> None:
        """Return mower to dock."""
        try:
            await self.coordinator.cloud.home(self._serial_number)
        except OfflineError as err:
            raise HomeAssistantError(
                "The mower is currently offline, no command was sent"
            ) from err
        await self.coordinator.async_request_device_update(self._serial_number)
