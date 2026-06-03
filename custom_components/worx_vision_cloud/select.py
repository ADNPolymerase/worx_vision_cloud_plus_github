"""Select platform for Worx Vision Cloud Plus."""
from __future__ import annotations

from itertools import combinations
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import WorxVisionEntity
from .helpers import get_dict_value, rtk_map_attributes

ALL_ZONES_OPTION = "Wszystkie strefy"
MAX_COMBINATION_ZONES = 5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up select entities."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            OneTimeMowingZonesSelect(runtime.coordinator, entry, serial_number)
            for serial_number in runtime.coordinator.data
        ]
    )


def _zone_ids(device: Any) -> list[int]:
    """Return available RTK zone IDs from the current mower payload."""
    zones = rtk_map_attributes(device).get("zones", []) or []
    zone_ids: list[int] = []
    for zone in zones:
        zone_id = get_dict_value(zone, "id")
        try:
            zone_id = int(zone_id)
        except (TypeError, ValueError):
            continue
        if zone_id > 0 and zone_id not in zone_ids:
            zone_ids.append(zone_id)
    return sorted(zone_ids)


def _option_label(zone_ids: list[int]) -> str:
    """Return a user-facing label for one zone selection."""
    if not zone_ids:
        return ALL_ZONES_OPTION
    if len(zone_ids) == 1:
        return f"Strefa {zone_ids[0]}"
    return "Strefy " + ", ".join(str(zone_id) for zone_id in zone_ids)


def _option_map(zone_ids: list[int]) -> dict[str, list[int]]:
    """Return select option label to zone ID list mapping."""
    result: dict[str, list[int]] = {ALL_ZONES_OPTION: []}
    if len(zone_ids) <= MAX_COMBINATION_ZONES:
        for count in range(1, len(zone_ids) + 1):
            for combo in combinations(zone_ids, count):
                selected = list(combo)
                result[_option_label(selected)] = selected
    else:
        for zone_id in zone_ids:
            result[_option_label([zone_id])] = [zone_id]
    return result


class OneTimeMowingZonesSelect(WorxVisionEntity, SelectEntity):
    """Local RTK zone selection for one-time mowing."""

    _attr_translation_key = "one_time_mowing_zones"
    _attr_icon = "mdi:map-marker-path"

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize one-time mowing zones select."""
        super().__init__(coordinator, entry, serial_number, "one_time_mowing_zones")

    @property
    def options(self) -> list[str]:
        """Return available zone choices."""
        options = _option_map(_zone_ids(self.device))
        current_label = _option_label(
            self.coordinator.one_time_mowing_zones(self._serial_number)
        )
        if current_label not in options:
            options[current_label] = self.coordinator.one_time_mowing_zones(
                self._serial_number
            )
        return list(options.keys())

    @property
    def current_option(self) -> str | None:
        """Return selected zone choice."""
        return _option_label(self.coordinator.one_time_mowing_zones(self._serial_number))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return selected and available zone IDs."""
        return {
            "selected_zone_ids": self.coordinator.one_time_mowing_zones(
                self._serial_number
            ),
            "available_zone_ids": _zone_ids(self.device),
        }

    async def async_select_option(self, option: str) -> None:
        """Select one zone choice."""
        options = _option_map(_zone_ids(self.device))
        current_zones = self.coordinator.one_time_mowing_zones(self._serial_number)
        current_label = _option_label(current_zones)
        if current_label not in options:
            options[current_label] = current_zones
        if option not in options:
            raise HomeAssistantError(f"Unknown one-time mowing zone option: {option}")
        await self.coordinator.async_set_one_time_mowing_zones(
            self._serial_number, options[option]
        )
