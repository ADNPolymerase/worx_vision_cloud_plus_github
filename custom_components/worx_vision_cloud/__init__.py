"""Worx Vision Cloud Plus integration.

A small custom integration focused on Worx Landroid Vision Cloud mowers.
It uses pyworxcloud for the reverse-engineered Positec/Worx cloud API and
adds curated Home Assistant entities, schedule support and RTK map rendering.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import entity_registry as er
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from pyworxcloud import WorxCloud
from pyworxcloud.exceptions import AuthorizationError, TooManyRequestsError

from .const import (
    ATTR_EDGE_CUT,
    ATTR_RUNTIME,
    ATTR_ZONES,
    CONF_CLOUD,
    CONF_EXPOSE_RAW,
    CONF_VERIFY_SSL,
    DEFAULT_CLOUD,
    DEFAULT_EXPOSE_RAW,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
    SERVICE_START_ONE_TIME_MOWING,
)
from .coordinator import WorxVisionCoordinator

_LOGGER = logging.getLogger(__name__)

START_ONE_TIME_MOWING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_RUNTIME, default=60): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=120)
        ),
        vol.Optional(ATTR_EDGE_CUT, default=False): cv.boolean,
        vol.Optional(ATTR_ZONES, default=[]): lambda value: _service_zone_ids(value),
    }
)


@dataclass
class WorxVisionRuntimeData:
    """Runtime objects kept for one config entry."""

    cloud: WorxCloud
    coordinator: WorxVisionCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Worx Vision Cloud Plus from a config entry."""
    username = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]
    cloud_name = entry.data.get(CONF_CLOUD, DEFAULT_CLOUD)
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

    cloud = WorxCloud(
        username=username,
        password=password,
        cloud=cloud_name,
        verify_ssl=verify_ssl,
        tz=hass.config.time_zone,
        command_timeout=30.0,
        deduplicate_inflight_commands=True,
    )

    try:
        await cloud.authenticate()
        await cloud.connect()
    except AuthorizationError as err:
        await _safe_disconnect(cloud)
        raise ConfigEntryAuthFailed("Invalid Worx/Landroid credentials") from err
    except TooManyRequestsError as err:
        await _safe_disconnect(cloud)
        raise ConfigEntryNotReady("Worx Cloud rate limit; try again later") from err
    except Exception as err:  # noqa: BLE001 - HA should retry setup
        await _safe_disconnect(cloud)
        raise ConfigEntryNotReady(f"Could not connect to Worx Cloud: {err}") from err

    coordinator = WorxVisionCoordinator(hass, cloud)
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()

    if not coordinator.data:
        await coordinator.async_shutdown()
        await _safe_disconnect(cloud)
        raise ConfigEntryNotReady("No cloud mower found on this Worx/Landroid account")

    _async_migrate_entity_registry(hass, coordinator.data)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = WorxVisionRuntimeData(
        cloud=cloud,
        coordinator=coordinator,
    )
    _async_setup_services(hass)

    if entry.data.get(CONF_EXPOSE_RAW, DEFAULT_EXPOSE_RAW):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_EXPOSE_RAW: False}
        )
    elif CONF_EXPOSE_RAW not in entry.data:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_EXPOSE_RAW: DEFAULT_EXPOSE_RAW}
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    runtime: WorxVisionRuntimeData | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id, None
    )

    if runtime is not None:
        await runtime.coordinator.async_shutdown()
        await _safe_disconnect(runtime.cloud)

    if not hass.data.get(DOMAIN):
        if hass.services.has_service(DOMAIN, SERVICE_START_ONE_TIME_MOWING):
            hass.services.async_remove(DOMAIN, SERVICE_START_ONE_TIME_MOWING)
        hass.data.pop(DOMAIN, None)

    return unload_ok


def _service_zone_ids(value: Any) -> list[int]:
    """Normalize service-provided zone IDs."""
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        values = [item.strip() for item in value.replace(";", ",").split(",")]
    elif isinstance(value, list | tuple | set):
        values = list(value)
    else:
        values = [value]

    zone_ids: list[int] = []
    for item in values:
        if item in (None, ""):
            continue
        try:
            zone_id = int(item)
        except (TypeError, ValueError) as err:
            raise vol.Invalid("Zone IDs must be positive numbers") from err
        if zone_id < 1:
            raise vol.Invalid("Zone IDs must be positive numbers")
        if zone_id not in zone_ids:
            zone_ids.append(zone_id)
    return zone_ids


def _async_setup_services(hass: HomeAssistant) -> None:
    """Register integration-level services."""
    if hass.services.has_service(DOMAIN, SERVICE_START_ONE_TIME_MOWING):
        return

    async def async_start_one_time_mowing(call) -> None:
        entity_id = call.data[ATTR_ENTITY_ID]
        entity_entry = er.async_get(hass).async_get(entity_id)
        if (
            entity_entry is None
            or entity_entry.platform != DOMAIN
            or not entity_entry.unique_id.endswith("_mower")
        ):
            raise HomeAssistantError(
                "Select a Worx Vision Cloud PLUS lawn_mower entity"
            )

        serial_number = entity_entry.unique_id.removesuffix("_mower")
        runtime_data = hass.data.get(DOMAIN, {}).get(entity_entry.config_entry_id)
        if (
            runtime_data is None
            or serial_number not in runtime_data.coordinator.data
        ):
            runtime_data = next(
                (
                    data
                    for data in hass.data.get(DOMAIN, {}).values()
                    if serial_number in data.coordinator.data
                ),
                None,
            )
        if runtime_data is None:
            raise HomeAssistantError(
                "Could not find runtime data for the selected Worx mower"
            )

        await runtime_data.coordinator.async_start_one_time_mowing(
            serial_number,
            call.data[ATTR_RUNTIME],
            call.data[ATTR_EDGE_CUT],
            call.data[ATTR_ZONES],
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_START_ONE_TIME_MOWING,
        async_start_one_time_mowing,
        schema=START_ONE_TIME_MOWING_SCHEMA,
    )


async def _safe_disconnect(cloud: WorxCloud) -> None:
    """Disconnect cloud object without failing HA unload/setup."""
    try:
        await cloud.disconnect()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Ignoring error while disconnecting Worx cloud", exc_info=True)


def _async_migrate_entity_registry(hass: HomeAssistant, devices: dict) -> None:
    """Clean up entity registry changes introduced by newer entity names."""
    registry = er.async_get(hass)
    for serial_number in devices:
        for domain, unique_id in (
            ("sensor", f"{serial_number}_distance_driven_total"),
            ("sensor", f"{serial_number}_distance_covered"),
            ("sensor", f"{serial_number}_lawn_perimeter"),
            ("binary_sensor", f"{serial_number}_battery_charging"),
            ("binary_sensor", f"{serial_number}_radio_link_pending"),
            ("binary_sensor", f"{serial_number}_schedule_border_cut"),
            ("switch", f"{serial_number}_auto_schedule"),
            ("switch", f"{serial_number}_schedule_border_cut"),
        ):
            entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
            if entity_id is not None:
                registry.async_remove(entity_id)

        rain_entity_id = registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"{serial_number}_rain_triggered"
        )
        if (
            rain_entity_id is not None
            and rain_entity_id.endswith("_czujnik_deszczu_aktywny")
        ):
            new_entity_id = rain_entity_id.removesuffix(
                "czujnik_deszczu_aktywny"
            ) + "czujnik_opadow_deszczu"
            if registry.async_get(new_entity_id) is None:
                registry.async_update_entity(
                    rain_entity_id, new_entity_id=new_entity_id
                )
