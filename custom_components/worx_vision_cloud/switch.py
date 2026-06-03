"""Switch platform for Worx Vision Cloud Plus."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import WorxVisionEntity
from .helpers import get_dict_value, get_nested_value


@dataclass(frozen=True, kw_only=True)
class WorxSwitchDescription(SwitchEntityDescription):
    """Switch description."""

    value_fn: Callable[[Any], bool | None]
    turn_fn: Callable[[Any, str, bool], Awaitable[None]]
    attrs_fn: Callable[[Any], dict[str, Any] | None] | None = None


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


def _product_item(device) -> dict[str, Any]:
    """Return cached product item details from the private API."""
    value = getattr(device, "_worx_vision_product_item", {}) or {}
    return value if isinstance(value, dict) else {}


def _firmware_info(device) -> dict[str, Any]:
    """Return cached firmware upgrade metadata."""
    value = getattr(device, "_worx_vision_firmware_upgrade", {}) or {}
    return value if isinstance(value, dict) else {}


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
    """Return whether Vision border cutting may cut over the border."""
    raw_value = get_nested_value(getattr(device, "raw_cfg", {}) or {}, "cut", "ob")
    value = _as_bool(raw_value)
    if value is not None:
        return value

    value = _as_bool(get_dict_value(_first_map_zone_metadata(device), "cut_over_border"))
    if value is not None:
        return value

    product_item = _product_item(device)
    for key in ("cut_over_border", "border_cut"):
        value = _as_bool(get_dict_value(product_item, key))
        if value is not None:
            return value
    return None


def _smart_edge_cut_attributes(device) -> dict[str, Any]:
    """Return map metadata related to intelligent edge cutting."""
    metadata = _first_map_zone_metadata(device)
    product_item = _product_item(device)
    capabilities = get_dict_value(product_item, "capabilities", []) or []
    if not isinstance(capabilities, list | tuple):
        capabilities = []

    return {
        "api_field": "cfg.cut.ob / layers.boundaries[].zones[].metadata.cut_over_border",
        "capability_border_cut": "border_cut" in capabilities,
        "capability_pause_over_border": "pause_over_border" in capabilities,
        "cut_type": get_dict_value(metadata, "cut_type"),
        "cut_direction": get_dict_value(metadata, "cut_direction"),
        "pattern_width": get_dict_value(metadata, "pattern_width"),
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


def _firmware_auto_upgrade_enabled(device) -> bool | None:
    """Return whether vendor firmware auto-upgrade is enabled."""
    value = _as_bool(get_dict_value(_firmware_info(device), "auto_upgrade"))
    if value is not None:
        return value

    firmware = getattr(device, "firmware", None)
    if isinstance(firmware, dict):
        value = _as_bool(firmware.get("auto_upgrade"))
        if value is not None:
            return value
    value = _as_bool(getattr(firmware, "auto_upgrade", None))
    if value is not None:
        return value

    return _as_bool(get_dict_value(_product_item(device), "firmware_auto_upgrade"))


def _firmware_auto_upgrade_attributes(device) -> dict[str, Any]:
    """Return firmware auto-upgrade metadata."""
    info = _firmware_info(device)
    product_item = _product_item(device)
    capabilities = get_dict_value(product_item, "capabilities", []) or []
    if not isinstance(capabilities, list | tuple):
        capabilities = []

    return {
        "api_method": "pyworxcloud.set_firmware_auto_upgrade",
        "ota_supported": info.get("ota_supported") or "ota_upgrade" in capabilities,
        "current_version": info.get("current_version")
        or get_dict_value(product_item, "firmware_version"),
        "latest_version": info.get("latest_version"),
        "update_available": info.get("update_available"),
    }


def _lock_enabled(device) -> bool | None:
    """Return current mower lock state."""
    value = _as_bool(getattr(device, "locked", None))
    if value is not None:
        return value
    return _as_bool(get_dict_value(_product_item(device), "locked"))


def _lock_attributes(device) -> dict[str, Any]:
    """Return lock metadata."""
    product_item = _product_item(device)
    capabilities = get_dict_value(product_item, "capabilities", []) or []
    if not isinstance(capabilities, list | tuple):
        capabilities = []

    return {
        "api_method": "pyworxcloud.set_lock",
        "capability_auto_lock": "auto_lock" in capabilities,
    }


def _native_schedule_enabled(device) -> bool | None:
    """Return whether the mower's native schedule is enabled."""
    schedules = getattr(device, "schedules", {}) or {}
    value = _as_bool(get_dict_value(schedules, "active"))
    if value is not None:
        return value

    schedule_model = get_dict_value(schedules, "schedule")
    value = _as_bool(get_dict_value(schedule_model, "enabled"))
    if value is not None:
        return value

    pause_mode = _as_bool(getattr(device, "pause_mode_enabled", None))
    if pause_mode is not None:
        return not pause_mode

    return None


def _native_schedule_attributes(device) -> dict[str, Any]:
    """Return native schedule metadata."""
    schedules = getattr(device, "schedules", {}) or {}
    product_item = _product_item(device)
    capabilities = get_dict_value(product_item, "capabilities", []) or []
    if not isinstance(capabilities, list | tuple):
        capabilities = []

    return {
        "api_method": "pyworxcloud.toggle_schedule",
        "capability_schedule_disable": "schedule_disable" in capabilities,
        "capability_unrestricted_mowing_time": "unrestricted_mowing_time"
        in capabilities,
        "pause_mode_enabled": getattr(device, "pause_mode_enabled", None),
        "time_extension": get_dict_value(schedules, "time_extension"),
    }


def _save_hedgehogs_enabled(device) -> bool | None:
    """Return the app option commonly shown as Save the hedgehogs."""
    settings = _auto_schedule_settings(device)
    for key in ("exclude_nights", "save_hedgehogs", "hedgehog_mode"):
        value = _as_bool(get_dict_value(settings, key))
        if value is not None:
            return value

    exclusion_scheduler = get_dict_value(settings, "exclusion_scheduler", {}) or {}
    if isinstance(exclusion_scheduler, dict):
        return _as_bool(get_dict_value(exclusion_scheduler, "exclude_nights"))
    return None


def _save_hedgehogs_attributes(device) -> dict[str, Any]:
    """Return auto-schedule details related to Save the hedgehogs."""
    settings = _auto_schedule_settings(device)
    schedules = getattr(device, "schedules", {}) or {}
    auto_schedule = get_dict_value(schedules, "auto_schedule", {}) or {}
    product_item = _product_item(device)

    return {
        "api_field": "auto_schedule.settings.exclusion_scheduler.exclude_nights",
        "auto_schedule_enabled": get_dict_value(auto_schedule, "enabled")
        if isinstance(auto_schedule, dict)
        else get_dict_value(product_item, "auto_schedule"),
        "auto_schedule": get_dict_value(product_item, "auto_schedule"),
        "exclude_nights": get_dict_value(settings, "exclude_nights"),
        "exclusion_scheduler": get_dict_value(settings, "exclusion_scheduler"),
    }


async def _set_smart_edge_cut(coordinator, serial_number: str, enabled: bool) -> None:
    await coordinator.async_set_cut_over_border(serial_number, enabled)


async def _set_save_hedgehogs(coordinator, serial_number: str, enabled: bool) -> None:
    set_exclude_nights = getattr(
        coordinator.cloud, "set_auto_schedule_exclude_nights", None
    )
    if set_exclude_nights is None:
        raise HomeAssistantError(
            "The installed pyworxcloud version does not support hedgehog protection"
        )
    await set_exclude_nights(serial_number, enabled)
    await coordinator.async_request_device_update(serial_number)


async def _set_firmware_auto_upgrade(
    coordinator, serial_number: str, enabled: bool
) -> None:
    await coordinator.async_set_firmware_auto_upgrade(serial_number, enabled)


async def _set_lock(coordinator, serial_number: str, enabled: bool) -> None:
    await coordinator.async_set_lock(serial_number, enabled)


async def _set_native_schedule(
    coordinator, serial_number: str, enabled: bool
) -> None:
    await coordinator.async_toggle_schedule(serial_number, enabled)


SWITCHES: tuple[WorxSwitchDescription, ...] = (
    WorxSwitchDescription(
        key="firmware_auto_upgrade",
        translation_key="firmware_auto_upgrade",
        icon="mdi:update",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        value_fn=_firmware_auto_upgrade_enabled,
        turn_fn=_set_firmware_auto_upgrade,
        attrs_fn=_firmware_auto_upgrade_attributes,
    ),
    WorxSwitchDescription(
        key="lock",
        translation_key="lock",
        icon="mdi:lock",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        value_fn=_lock_enabled,
        turn_fn=_set_lock,
        attrs_fn=_lock_attributes,
    ),
    WorxSwitchDescription(
        key="native_schedule",
        translation_key="native_schedule",
        icon="mdi:calendar-check",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        value_fn=_native_schedule_enabled,
        turn_fn=_set_native_schedule,
        attrs_fn=_native_schedule_attributes,
    ),
    WorxSwitchDescription(
        key="smart_edge_cut",
        translation_key="smart_edge_cut",
        icon="mdi:vector-polyline",
        entity_category=EntityCategory.CONFIG,
        value_fn=_smart_edge_cut_enabled,
        turn_fn=_set_smart_edge_cut,
        attrs_fn=_smart_edge_cut_attributes,
    ),
    WorxSwitchDescription(
        key="save_hedgehogs",
        translation_key="save_hedgehogs",
        icon="mdi:weather-night",
        entity_category=EntityCategory.CONFIG,
        value_fn=_save_hedgehogs_enabled,
        turn_fn=_set_save_hedgehogs,
        attrs_fn=_save_hedgehogs_attributes,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switches."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [
        WorxVisionSwitch(runtime.coordinator, entry, serial_number, description)
        for serial_number in runtime.coordinator.data
        for description in SWITCHES
    ]
    entities.extend(
        OneTimeMowingEdgeCutSwitch(runtime.coordinator, entry, serial_number)
        for serial_number in runtime.coordinator.data
    )
    async_add_entities(entities)


class WorxVisionSwitch(WorxVisionEntity, SwitchEntity):
    """Worx setting switch."""

    entity_description: WorxSwitchDescription

    def __init__(self, coordinator, entry, serial_number: str, description) -> None:
        """Initialize switch."""
        self.entity_description = description
        super().__init__(coordinator, entry, serial_number, description.key)

    @property
    def available(self) -> bool:
        """Return entity availability."""
        return (
            super().available
            and self.entity_description.value_fn(self.device) is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return current switch state."""
        return self.entity_description.value_fn(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        attrs = self.entity_description.attrs_fn(self.device)
        return {
            key: value for key, value in (attrs or {}).items() if value is not None
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the setting on."""
        del kwargs
        await self.entity_description.turn_fn(
            self.coordinator, self._serial_number, True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the setting off."""
        del kwargs
        await self.entity_description.turn_fn(
            self.coordinator, self._serial_number, False
        )


class OneTimeMowingEdgeCutSwitch(WorxVisionEntity, SwitchEntity):
    """Local edge-cut setting for one-time mowing."""

    _attr_translation_key = "one_time_mowing_edge_cut"
    _attr_icon = "mdi:border-outside"

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize one-time mowing edge-cut switch."""
        super().__init__(coordinator, entry, serial_number, "one_time_mowing_edge_cut")

    @property
    def is_on(self) -> bool | None:
        """Return configured edge-cut state."""
        return self.coordinator.one_time_mowing_edge_cut(self._serial_number)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable edge cutting for one-time mowing."""
        del kwargs
        await self.coordinator.async_set_one_time_mowing_edge_cut(
            self._serial_number, True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable edge cutting for one-time mowing."""
        del kwargs
        await self.coordinator.async_set_one_time_mowing_edge_cut(
            self._serial_number, False
        )
