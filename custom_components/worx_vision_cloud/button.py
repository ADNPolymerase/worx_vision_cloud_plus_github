"""Button platform for Worx Vision Cloud Plus."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Awaitable, Any

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import WorxVisionEntity


@dataclass(frozen=True, kw_only=True)
class WorxButtonDescription(ButtonEntityDescription):
    """Button description."""

    press_fn: Callable[[Any, str], Awaitable[None]]
    available_fn: Callable[[Any], bool] | None = None


async def _refresh(coordinator, serial_number: str) -> None:
    await coordinator.async_request_device_update(serial_number)


async def _reset_blade_counter(coordinator, serial_number: str) -> None:
    """Reset the mower blade runtime counter after blade replacement."""
    reset_blade_counter = getattr(coordinator.cloud, "reset_blade_counter", None)
    if reset_blade_counter is None:
        raise HomeAssistantError(
            "The installed pyworxcloud version does not support blade counter reset"
        )

    await reset_blade_counter(serial_number)
    await coordinator.async_request_device_update(serial_number)


async def _reset_battery_cycle_counter(coordinator, serial_number: str) -> None:
    """Reset the mower battery charge cycle counter after battery maintenance."""
    await coordinator.async_reset_charge_cycle_counter(serial_number)


async def _start_edge_cut(coordinator, serial_number: str) -> None:
    """Start an on-demand edge cutting task."""
    await coordinator.async_start_edge_cut(serial_number)


async def _start_one_time_mowing(coordinator, serial_number: str) -> None:
    """Start one-time mowing with the configured integration options."""
    await coordinator.async_start_configured_one_time_mowing(serial_number)


async def _restart_mower(coordinator, serial_number: str) -> None:
    """Reboot the mower baseboard, e.g. when it is stuck."""
    await coordinator.async_restart_mower(serial_number)


def _is_online(device) -> bool:
    """Return true when the mower is online and can receive commands."""
    return bool(getattr(device, "online", False))


BUTTONS: tuple[WorxButtonDescription, ...] = (
    WorxButtonDescription(
        key="refresh",
        translation_key="refresh",
        icon="mdi:refresh",
        entity_category=EntityCategory.CONFIG,
        press_fn=_refresh,
    ),
    WorxButtonDescription(
        key="reset_blade_counter",
        translation_key="reset_blade_counter",
        icon="mdi:timer-refresh-outline",
        entity_category=EntityCategory.CONFIG,
        press_fn=_reset_blade_counter,
    ),
    WorxButtonDescription(
        key="reset_battery_cycle_counter",
        translation_key="reset_battery_cycle_counter",
        icon="mdi:battery-sync",
        entity_category=EntityCategory.CONFIG,
        press_fn=_reset_battery_cycle_counter,
    ),
    WorxButtonDescription(
        key="start_edge_cut",
        translation_key="start_edge_cut",
        icon="mdi:border-outside",
        press_fn=_start_edge_cut,
        available_fn=_is_online,
    ),
    WorxButtonDescription(
        key="start_one_time_mowing",
        translation_key="start_one_time_mowing",
        icon="mdi:play-circle-outline",
        press_fn=_start_one_time_mowing,
        available_fn=_is_online,
    ),
    WorxButtonDescription(
        key="restart",
        translation_key="restart",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=_restart_mower,
        available_fn=_is_online,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            WorxVisionButton(runtime.coordinator, entry, serial_number, description)
            for serial_number in runtime.coordinator.data
            for description in BUTTONS
        ]
    )


class WorxVisionButton(WorxVisionEntity, ButtonEntity):
    """Worx button."""

    entity_description: WorxButtonDescription

    def __init__(self, coordinator, entry, serial_number: str, description) -> None:
        """Initialize button."""
        self.entity_description = description
        super().__init__(coordinator, entry, serial_number, description.key)

    @property
    def available(self) -> bool:
        """Return entity availability."""
        available_fn = self.entity_description.available_fn
        return super().available and (
            available_fn is None or available_fn(self.device)
        )

    async def async_press(self) -> None:
        """Handle press."""
        await self.entity_description.press_fn(self.coordinator, self._serial_number)
