"""Coordinator for Worx Vision Cloud Plus."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any, Callable

from aiohttp import ClientError, ClientTimeout

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from pyworxcloud import DeviceHandler, LandroidEvent, WorxCloud
from pyworxcloud.exceptions import (
    NoACSModuleError,
    NoCuttingHeightError,
    NoOfflimitsError,
    NoPartymodeError,
)
from pyworxcloud.utils.requests import AGET, HEADERS

from .const import DOMAIN
from .helpers import rtk_position

_LOGGER = logging.getLogger(__name__)

RTK_MAP_CACHE_TTL = timedelta(minutes=30)
RTK_ADDRESS_CACHE_TTL = timedelta(hours=24)
RTK_ADDRESS_COORD_PRECISION = 7
RTK_ADDRESS_ENDPOINT = "https://nominatim.openstreetmap.org/reverse"
RTK_ADDRESS_USER_AGENT = (
    "Worx Vision Cloud PLUS Home Assistant custom integration "
    "(https://github.com/SmartServicePL/Worx-Vision-Cloud-PLUS)"
)
PRODUCT_ITEM_CACHE_TTL = timedelta(minutes=5)
LIVE_REFRESH_INTERVAL = timedelta(minutes=5)
FIRMWARE_UPGRADE_CACHE_TTL = timedelta(minutes=30)
RTK_TRAIL_MAX_POINTS = 300
DEFAULT_ONE_TIME_MOWING_RUNTIME = 60
DEFAULT_ONE_TIME_MOWING_EDGE_CUT = False


def _device_map(cloud: WorxCloud) -> dict[str, DeviceHandler]:
    """Build a serial-number-indexed map of devices from pyworxcloud."""
    devices: dict[str, DeviceHandler] = {}
    for device in cloud.devices.values():
        serial = getattr(device, "serial_number", None)
        if serial is not None:
            devices[str(serial)] = device
    return devices


def _normalize_zone_ids(zones: list[int] | None) -> list[int]:
    """Return ordered, de-duplicated positive zone identifiers."""
    normalized: list[int] = []
    for zone in zones or []:
        zone_id = int(zone)
        if zone_id > 0 and zone_id not in normalized:
            normalized.append(zone_id)
    return normalized


class WorxVisionCoordinator(DataUpdateCoordinator[dict[str, DeviceHandler]]):
    """Coordinate push and manual updates."""

    def __init__(self, hass: HomeAssistant, cloud: WorxCloud) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
            # `_device_map()` returns the same DeviceHandler instances pyworxcloud
            # already holds, and `_enrich_device()` mutates them in place (e.g. the
            # product-item area_mowed figure). With always_update=False the
            # coordinator compares data by equality, which is always True here
            # (same object references), so it silently skips notifying entities
            # even when the mutated attributes actually changed.
            always_update=True,
        )
        self.cloud = cloud
        self._event_lock = asyncio.Lock()
        self._rtk_address_lock = asyncio.Lock()
        self._last_rtk_address_lookup: datetime | None = None
        self._rtk_map_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._rtk_address_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._product_item_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._firmware_upgrade_cache: dict[
            str, tuple[datetime, dict[str, Any]]
        ] = {}
        self._rtk_position_trails: dict[
            str, deque[tuple[datetime, float, float]]
        ] = {}
        self._one_time_mowing_options: dict[str, dict[str, Any]] = {}
        self._unsub_periodic_refresh: Callable[[], None] | None = None

    async def async_setup(self) -> None:
        """Attach pyworxcloud callbacks."""
        def _on_data_received(name: str, device: DeviceHandler) -> None:
            del name
            self._schedule_push_update(device)

        def _on_api_update(api_data: dict[str, Any], **_: Any) -> None:
            del api_data
            self._schedule_api_refresh()

        self.cloud.set_callback(LandroidEvent.DATA_RECEIVED, _on_data_received)
        self.cloud.set_callback(LandroidEvent.API, _on_api_update)

        self._unsub_periodic_refresh = async_track_time_interval(
            self.hass, self._async_periodic_device_refresh, LIVE_REFRESH_INTERVAL
        )

    async def async_shutdown(self) -> None:
        """Detach callbacks."""
        self.cloud.set_callback(LandroidEvent.DATA_RECEIVED, lambda **_: None)
        self.cloud.set_callback(LandroidEvent.API, lambda **_: None)
        if self._unsub_periodic_refresh is not None:
            self._unsub_periodic_refresh()
            self._unsub_periodic_refresh = None

    async def _async_periodic_device_refresh(self, _now: datetime) -> None:
        """Ask each mower for a fresh update on a fixed cadence.

        Some pyworxcloud data (e.g. work-time statistics used by the daily
        progress/area sensors) is only included in the mower's MQTT payload
        when it responds to an explicit update request, not on every routine
        push. Relying solely on push events or the sporadic LandroidEvent.API
        callback can leave those figures stale for hours during active
        mowing, so ask every known device to report in on this interval.
        """
        for serial_number in list((self.data or {}).keys()):
            try:
                await self.async_request_device_update(serial_number)
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Periodic refresh failed for device %s",
                    serial_number,
                    exc_info=True,
                )

    async def _handle_push_update(self, device: DeviceHandler) -> None:
        """Merge one pushed device update."""
        serial = getattr(device, "serial_number", None)
        if serial is None:
            return

        async with self._event_lock:
            self._preserve_enriched_attributes(str(serial), device)
            self._remember_rtk_position(str(serial), device)
            data = dict(self.data or {})
            data[str(serial)] = device
            self.async_set_updated_data(data)

    async def _refresh_from_cloud_cache(self) -> dict[str, DeviceHandler]:
        """Return current cloud cache."""
        devices = _device_map(self.cloud)
        results = await asyncio.gather(
            *(self._enrich_device(serial, device) for serial, device in devices.items()),
            return_exceptions=True,
        )
        for serial, result in zip(devices, results):
            if isinstance(result, Exception):
                _LOGGER.warning(
                    "Failed to enrich device %s with REST API data (area mowed, "
                    "firmware, RTK map may be stale): %s",
                    serial,
                    result,
                )
        return devices

    async def _async_update_data(self) -> dict[str, DeviceHandler]:
        """Return current cloud cache for DataUpdateCoordinator."""
        try:
            return await self._refresh_from_cloud_cache()
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

    async def async_request_device_update(self, serial_number: str) -> None:
        """Ask one mower for a fresh MQTT state update, then refresh coordinator data."""
        try:
            await self.cloud.update(serial_number)
        finally:
            await self.async_request_refresh()

    async def async_start_edge_cut(self, serial_number: str) -> None:
        """Start an on-demand edge cutting task."""
        mower = self.cloud.get_mower(serial_number)
        if not mower.get("online"):
            raise HomeAssistantError(
                "The device is currently offline, no action was sent"
            )

        mqtt = getattr(self.cloud, "mqtt", None)
        if mqtt is None:
            raise HomeAssistantError("Worx MQTT connection is not available")

        protocol = mower.get("protocol")
        command_topic = (mower.get("mqtt_topics") or {}).get("command_in")
        if command_topic is None:
            raise HomeAssistantError("Worx command topic is not available")

        # On Vision Cloud firmware 3.46.x cmd 101 is the reliable edge-only
        # command. Full one-time mowing uses cmd 10 instead.
        if protocol == 0:
            await mqtt.apublish(
                serial_number,
                command_topic,
                {"sc": {"ots": {"bc": 1, "wtm": 0}}},
                protocol,
            )
        elif protocol == 1:
            await self.async_start_one_time_mowing(serial_number, 0, True, [])
            return
        else:
            raise HomeAssistantError(
                "Edge cutting is not supported for this mower protocol"
            )

        await self.async_request_device_update(serial_number)

    async def async_start_one_time_mowing(
        self,
        serial_number: str,
        runtime_minutes: int,
        edge_cut: bool = False,
        zones: list[int] | None = None,
    ) -> None:
        """Start a one-time mowing task, optionally limited to RTK zones."""
        mower = self.cloud.get_mower(serial_number)
        if not mower.get("online"):
            raise HomeAssistantError(
                "The device is currently offline, no action was sent"
            )

        protocol = mower.get("protocol")
        runtime = int(runtime_minutes)
        zone_ids = _normalize_zone_ids(zones)
        if protocol == 0:
            mqtt = getattr(self.cloud, "mqtt", None)
            if mqtt is None:
                raise HomeAssistantError("Worx MQTT connection is not available")

            command_topic = (mower.get("mqtt_topics") or {}).get("command_in")
            if command_topic is None:
                raise HomeAssistantError("Worx command topic is not available")

            if len(zone_ids) > 1:
                raise HomeAssistantError(
                    "Legacy Worx protocol supports only one selected zone per one-time mowing command"
                )

            setzone = getattr(self.cloud, "setzone", None)
            if zone_ids and setzone is not None:
                await setzone(serial_number, zone_ids[0])

            await mqtt.apublish(
                serial_number,
                command_topic,
                {"sc": {"ots": {"bc": int(edge_cut), "wtm": runtime}}},
                protocol,
            )
        elif protocol == 1:
            mqtt = getattr(self.cloud, "mqtt", None)
            if mqtt is None:
                raise HomeAssistantError("Worx MQTT connection is not available")

            command_topic = (mower.get("mqtt_topics") or {}).get("command_in")
            if command_topic is None:
                raise HomeAssistantError("Worx command topic is not available")

            uuid = mower.get("uuid")
            if uuid is None:
                raise HomeAssistantError("Worx mower UUID is not available")

            if edge_cut and runtime == 0 and not zone_ids:
                await mqtt.apublish(uuid, command_topic, {"cmd": 101}, protocol)
            else:
                await mqtt.apublish(
                    uuid,
                    command_topic,
                    {
                        "cmd": 10,
                        "sc": {
                            "once": {
                                "time": runtime,
                                "cfg": {"cut": {"b": int(edge_cut), "z": zone_ids}},
                            }
                        },
                    },
                    protocol,
                )
        else:
            raise HomeAssistantError(
                "One-time mowing is not supported for this mower protocol"
            )

        await self.async_request_device_update(serial_number)

    def _one_time_options(self, serial_number: str) -> dict[str, Any]:
        """Return local one-time mowing options for a mower."""
        return self._one_time_mowing_options.setdefault(
            serial_number,
            {
                "runtime": DEFAULT_ONE_TIME_MOWING_RUNTIME,
                "edge_cut": DEFAULT_ONE_TIME_MOWING_EDGE_CUT,
                "zones": [],
            },
        )

    def one_time_mowing_runtime(self, serial_number: str) -> int:
        """Return configured one-time mowing runtime."""
        return int(
            self._one_time_options(serial_number).get(
                "runtime", DEFAULT_ONE_TIME_MOWING_RUNTIME
            )
        )

    def one_time_mowing_edge_cut(self, serial_number: str) -> bool:
        """Return whether configured one-time mowing starts with edge cutting."""
        return bool(
            self._one_time_options(serial_number).get(
                "edge_cut", DEFAULT_ONE_TIME_MOWING_EDGE_CUT
            )
        )

    def one_time_mowing_zones(self, serial_number: str) -> list[int]:
        """Return configured one-time mowing RTK zones."""
        return list(self._one_time_options(serial_number).get("zones", []))

    async def async_set_one_time_mowing_runtime(
        self, serial_number: str, runtime_minutes: int
    ) -> None:
        """Set local one-time mowing runtime."""
        runtime = max(10, min(120, int(runtime_minutes)))
        self._one_time_options(serial_number)["runtime"] = runtime
        self.async_set_updated_data(self.data or {})

    async def async_set_one_time_mowing_edge_cut(
        self, serial_number: str, enabled: bool
    ) -> None:
        """Set whether local one-time mowing starts with edge cutting."""
        self._one_time_options(serial_number)["edge_cut"] = bool(enabled)
        self.async_set_updated_data(self.data or {})

    async def async_set_one_time_mowing_zones(
        self, serial_number: str, zones: list[int]
    ) -> None:
        """Set local one-time mowing RTK zones."""
        self._one_time_options(serial_number)["zones"] = _normalize_zone_ids(zones)
        self.async_set_updated_data(self.data or {})

    async def async_start_configured_one_time_mowing(self, serial_number: str) -> None:
        """Start one-time mowing using local UI options."""
        await self.async_start_one_time_mowing(
            serial_number,
            self.one_time_mowing_runtime(serial_number),
            self.one_time_mowing_edge_cut(serial_number),
            self.one_time_mowing_zones(serial_number),
        )

    async def async_set_rain_delay(self, serial_number: str, minutes: int) -> None:
        """Set rain delay in minutes."""
        raindelay = getattr(self.cloud, "raindelay", None)
        if raindelay is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support rain delay updates"
            )

        await raindelay(serial_number, str(int(minutes)))
        self._update_cached_rain_delay(serial_number, int(minutes))
        await self.async_request_device_update(serial_number)

    async def async_set_time_extension(
        self, serial_number: str, time_extension: int
    ) -> None:
        """Set schedule time extension in percent."""
        set_time_extension = getattr(self.cloud, "set_time_extension", None)
        if set_time_extension is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support time extension updates"
            )

        await set_time_extension(serial_number, int(time_extension))
        await self.async_request_device_update(serial_number)

    async def async_set_lawn_size(self, serial_number: str, size_m2: int) -> None:
        """Set top-level lawn size in square meters."""
        set_lawn_size = getattr(self.cloud, "set_lawn_size", None)
        if set_lawn_size is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support lawn size updates"
            )

        await set_lawn_size(serial_number, int(size_m2))
        self._update_cached_product_item(serial_number, lawn_size=int(size_m2))
        await self.async_request_device_update(serial_number)

    async def async_set_lawn_perimeter(
        self, serial_number: str, perimeter_m: int
    ) -> None:
        """Set top-level lawn perimeter in meters."""
        set_lawn_perimeter = getattr(self.cloud, "set_lawn_perimeter", None)
        if set_lawn_perimeter is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support lawn perimeter updates"
            )

        await set_lawn_perimeter(serial_number, int(perimeter_m))
        self._update_cached_product_item(
            serial_number, lawn_perimeter=int(perimeter_m)
        )
        await self.async_request_device_update(serial_number)

    async def async_set_firmware_auto_upgrade(
        self, serial_number: str, enabled: bool
    ) -> None:
        """Toggle vendor firmware auto-upgrades."""
        set_firmware_auto_upgrade = getattr(
            self.cloud, "set_firmware_auto_upgrade", None
        )
        if set_firmware_auto_upgrade is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support firmware auto-upgrade"
            )

        await set_firmware_auto_upgrade(serial_number, enabled)
        self._update_cached_product_item(serial_number, firmware_auto_upgrade=enabled)
        await self.async_request_device_update(serial_number)

    async def async_set_lock(self, serial_number: str, enabled: bool) -> None:
        """Lock or unlock the mower."""
        set_lock = getattr(self.cloud, "set_lock", None)
        if set_lock is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support lock updates"
            )

        await set_lock(serial_number, state=enabled)
        self._update_cached_product_item(serial_number, locked=enabled)
        await self.async_request_device_update(serial_number)

    async def async_set_party_mode(self, serial_number: str, enabled: bool) -> None:
        """Turn party mode on or off."""
        set_party_mode = getattr(self.cloud, "set_party_mode", None)
        if set_party_mode is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support party mode"
            )

        try:
            await set_party_mode(serial_number, enabled)
        except NoPartymodeError as err:
            raise HomeAssistantError(
                "This mower does not support party mode"
            ) from err

        await self.async_request_device_update(serial_number)

    async def async_set_off_limits(self, serial_number: str, enabled: bool) -> None:
        """Turn the off-limits module on or off."""
        set_offlimits = getattr(self.cloud, "set_offlimits", None)
        if set_offlimits is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support off limits"
            )

        try:
            await set_offlimits(serial_number, enabled)
        except NoOfflimitsError as err:
            raise HomeAssistantError(
                "This mower does not support off limits"
            ) from err

        await self.async_request_device_update(serial_number)

    async def async_set_cutting_height(self, serial_number: str, height_mm: int) -> None:
        """Set the cutting height in millimeters."""
        set_cutting_height = getattr(self.cloud, "set_cutting_height", None)
        if set_cutting_height is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support cutting height"
            )

        try:
            await set_cutting_height(serial_number, int(height_mm))
        except NoCuttingHeightError as err:
            raise HomeAssistantError(
                "This mower does not support cutting height"
            ) from err

        await self.async_request_device_update(serial_number)

    async def async_set_acs(self, serial_number: str, enabled: bool) -> None:
        """Turn the ACS (Automatic Cutting System) module on or off."""
        set_acs = getattr(self.cloud, "set_acs", None)
        if set_acs is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support ACS"
            )

        try:
            await set_acs(serial_number, enabled)
        except NoACSModuleError as err:
            raise HomeAssistantError(
                "This mower does not have an ACS module installed"
            ) from err

        await self.async_request_device_update(serial_number)

    async def async_set_torque(self, serial_number: str, torque: int) -> None:
        """Set wheel torque percentage."""
        set_torque = getattr(self.cloud, "set_torque", None)
        if set_torque is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support torque updates"
            )

        await set_torque(serial_number, int(torque))
        await self.async_request_device_update(serial_number)

    async def async_toggle_schedule(self, serial_number: str, enabled: bool) -> None:
        """Enable or disable the mower's native schedule."""
        toggle_schedule = getattr(self.cloud, "toggle_schedule", None)
        if toggle_schedule is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support schedule toggling"
            )

        await toggle_schedule(serial_number, enable=enabled)
        await self.async_request_device_update(serial_number)

    async def async_start_firmware_upgrade(self, serial_number: str) -> None:
        """Queue the latest firmware update for a mower."""
        start_firmware_upgrade = getattr(self.cloud, "start_firmware_upgrade", None)
        if start_firmware_upgrade is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support firmware installs"
            )

        await start_firmware_upgrade(serial_number)
        await self.async_get_firmware_upgrade_info(serial_number, force=True)
        await self.async_request_device_update(serial_number)

    async def async_reset_charge_cycle_counter(self, serial_number: str) -> None:
        """Reset battery charge cycle counter after battery maintenance."""
        reset_charge_cycle_counter = getattr(
            self.cloud, "reset_charge_cycle_counter", None
        )
        if reset_charge_cycle_counter is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support battery cycle reset"
            )

        await reset_charge_cycle_counter(serial_number)
        await self.async_request_device_update(serial_number)

    async def async_set_cut_over_border(
        self, serial_number: str, enabled: bool
    ) -> None:
        """Persist whether Vision border cutting may cross the lawn border."""
        set_cut_over_border = getattr(self.cloud, "set_cut_over_border", None)
        if set_cut_over_border is not None:
            await set_cut_over_border(serial_number, enabled)
        else:
            await self._async_send_cut_over_border(serial_number, enabled)

        self._update_cached_cut_over_border(serial_number, enabled)
        await self.async_request_device_update(serial_number)

    async def _async_send_cut_over_border(
        self, serial_number: str, enabled: bool
    ) -> None:
        """Send the observed Vision Cloud border-cut payload for pyworxcloud 6.3.x."""
        mower = self.cloud.get_mower(serial_number)
        if mower.get("protocol") != 1:
            raise ValueError(
                "Intelligent edge cutting is only supported for protocol 1 devices"
            )

        payload = {
            "mz": {
                "s": [
                    {
                        "id": 1,
                        "c": 1,
                        "cfg": {"cut": {"ob": 1 if enabled else 0}},
                    }
                ],
                "p": [],
            }
        }
        await self.cloud.send(serial_number, json.dumps(payload))

    def _update_cached_cut_over_border(
        self, serial_number: str, enabled: bool
    ) -> None:
        """Update local cached raw config so the switch state changes immediately."""
        device = (self.data or {}).get(serial_number)
        if device is None:
            return

        raw_cfg = getattr(device, "raw_cfg", None)
        if isinstance(raw_cfg, dict):
            cut = raw_cfg.setdefault("cut", {})
            if isinstance(cut, dict):
                cut["ob"] = 1 if enabled else 0

    async def async_get_rtk_map(
        self, map_id: str | None, *, force: bool = False
    ) -> dict[str, Any] | None:
        """Fetch RTK map geometry from the private Worx maps endpoint."""
        if not map_id:
            return None

        now = datetime.now(UTC)
        cached = self._rtk_map_cache.get(map_id)
        if (
            cached is not None
            and not force
            and now - cached[0] < RTK_MAP_CACHE_TTL
        ):
            return cached[1]

        api = getattr(self.cloud, "_api", None)
        if api is None:
            _LOGGER.debug("Cannot fetch RTK map: pyworxcloud API object missing")
            return None

        try:
            await api.check_token()
            endpoint = getattr(getattr(api, "cloud", None), "ENDPOINT", None)
            if endpoint is None:
                endpoint = getattr(getattr(self.cloud, "_cloud", None), "ENDPOINT", None)
            if endpoint is None:
                _LOGGER.debug("Cannot fetch RTK map: Worx API endpoint missing")
                return None

            map_data = await AGET(
                f"https://{endpoint}/api/v2/maps/{map_id}",
                HEADERS(api.access_token),
                session=await api._ensure_session(),
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not fetch RTK map %s", map_id, exc_info=True)
            return cached[1] if cached is not None else None

        if isinstance(map_data, dict):
            self._rtk_map_cache[map_id] = (now, map_data)
            return map_data

        return None

    async def async_get_product_item(
        self, serial_number: str | None, *, force: bool = False
    ) -> dict[str, Any] | None:
        """Fetch product item details from the private Worx endpoint."""
        if not serial_number:
            return None

        now = datetime.now(UTC)
        cached = self._product_item_cache.get(serial_number)
        if (
            cached is not None
            and not force
            and now - cached[0] < PRODUCT_ITEM_CACHE_TTL
        ):
            return cached[1]

        product_item = await self._api_get(f"/api/v2/product-items/{serial_number}")
        if isinstance(product_item, dict):
            self._product_item_cache[serial_number] = (now, product_item)
            return product_item

        return cached[1] if cached is not None else None

    async def async_get_firmware_upgrade_info(
        self, serial_number: str | None, *, force: bool = False
    ) -> dict[str, Any] | None:
        """Fetch firmware upgrade metadata from pyworxcloud/private API."""
        if not serial_number:
            return None

        now = datetime.now(UTC)
        cached = self._firmware_upgrade_cache.get(serial_number)
        if (
            cached is not None
            and not force
            and now - cached[0] < FIRMWARE_UPGRADE_CACHE_TTL
        ):
            return cached[1]

        firmware_info: dict[str, Any] | None = None
        get_firmware_upgrade_info = getattr(
            self.cloud, "get_firmware_upgrade_info", None
        )
        if get_firmware_upgrade_info is not None:
            try:
                value = await get_firmware_upgrade_info(serial_number)
                if isinstance(value, dict):
                    firmware_info = value
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Could not fetch firmware upgrade info for %s",
                    serial_number,
                    exc_info=True,
                )

        if firmware_info is None:
            product_item = await self.async_get_product_item(serial_number)
            firmware_info = self._fallback_firmware_upgrade_info(product_item)

        if firmware_info is not None:
            self._firmware_upgrade_cache[serial_number] = (now, firmware_info)
            device = (self.data or {}).get(serial_number)
            if device is not None:
                setattr(device, "_worx_vision_firmware_upgrade", firmware_info)
            return firmware_info

        return cached[1] if cached is not None else None

    def product_item_data(self, serial_number: str) -> dict[str, Any] | None:
        """Return cached product item details."""
        cached = self._product_item_cache.get(serial_number)
        return None if cached is None else cached[1]

    def firmware_upgrade_data(self, serial_number: str) -> dict[str, Any] | None:
        """Return cached firmware upgrade details."""
        cached = self._firmware_upgrade_cache.get(serial_number)
        return None if cached is None else cached[1]

    def rtk_map_data(self, map_id: str | None) -> dict[str, Any] | None:
        """Return cached RTK map details."""
        if not map_id:
            return None
        cached = self._rtk_map_cache.get(map_id)
        return None if cached is None else cached[1]

    def rtk_position_trail(
        self, serial_number: str, max_points: int = 120
    ) -> list[tuple[float, float]]:
        """Return recent RTK positions for map rendering."""
        trail = self._rtk_position_trails.get(serial_number)
        if trail is None:
            return []
        return [(lat, lon) for _, lat, lon in list(trail)[-max_points:]]

    def rtk_position_timed_trail(
        self, serial_number: str, max_points: int = 120
    ) -> list[tuple[datetime, float, float]]:
        """Return recent RTK positions with timestamps for map rendering."""
        trail = self._rtk_position_trails.get(serial_number)
        if trail is None:
            return []
        return list(trail)[-max_points:]

    async def async_reverse_geocode_rtk_position(
        self, position: tuple[float, float] | None, *, force: bool = False
    ) -> dict[str, Any] | None:
        """Return a cached reverse-geocoded address for an RTK position."""
        if position is None:
            return None

        cache_key = self.rtk_address_cache_key(position)
        now = datetime.now(UTC)
        cached = self._rtk_address_cache.get(cache_key)
        if (
            cached is not None
            and not force
            and now - cached[0] < RTK_ADDRESS_CACHE_TTL
        ):
            return cached[1]

        async with self._rtk_address_lock:
            cached = self._rtk_address_cache.get(cache_key)
            if (
                cached is not None
                and not force
                and now - cached[0] < RTK_ADDRESS_CACHE_TTL
            ):
                return cached[1]

            lookup_latitude, lookup_longitude = self.rtk_address_lookup_position(position)
            await self._throttle_rtk_address_lookup()

            session = async_get_clientsession(self.hass)
            params = {
                "format": "jsonv2",
                "lat": f"{lookup_latitude:.{RTK_ADDRESS_COORD_PRECISION}f}",
                "lon": f"{lookup_longitude:.{RTK_ADDRESS_COORD_PRECISION}f}",
                "zoom": "18",
                "addressdetails": "1",
                "accept-language": self.hass.config.language or "en",
            }
            headers = {
                "User-Agent": RTK_ADDRESS_USER_AGENT,
                "Accept": "application/json",
            }

            try:
                async with session.get(
                    RTK_ADDRESS_ENDPOINT,
                    params=params,
                    headers=headers,
                    timeout=ClientTimeout(total=10),
                ) as response:
                    if response.status == 429 and cached is not None:
                        _LOGGER.debug(
                            "Nominatim rate-limited address lookup; using cache"
                        )
                        return cached[1]
                    response.raise_for_status()
                    address_data = await response.json()
            except (ClientError, TimeoutError, ValueError):
                _LOGGER.debug("Could not reverse-geocode RTK position", exc_info=True)
                return cached[1] if cached is not None else None

        if isinstance(address_data, dict):
            self._rtk_address_cache[cache_key] = (now, address_data)
            return address_data

        return cached[1] if cached is not None else None

    @staticmethod
    def rtk_address_cache_key(position: tuple[float, float]) -> str:
        """Return a privacy-friendlier cache key for RTK address lookups."""
        latitude, longitude = WorxVisionCoordinator.rtk_address_lookup_position(position)
        return (
            f"{latitude:.{RTK_ADDRESS_COORD_PRECISION}f},"
            f"{longitude:.{RTK_ADDRESS_COORD_PRECISION}f}"
        )

    @staticmethod
    def rtk_address_lookup_position(position: tuple[float, float]) -> tuple[float, float]:
        """Return rounded coordinates used for reverse-geocoding."""
        return (
            round(position[0], RTK_ADDRESS_COORD_PRECISION),
            round(position[1], RTK_ADDRESS_COORD_PRECISION),
        )

    async def _throttle_rtk_address_lookup(self) -> None:
        """Keep public reverse-geocoding requests below one request per second."""
        if self._last_rtk_address_lookup is not None:
            elapsed = datetime.now(UTC) - self._last_rtk_address_lookup
            remaining = 1 - elapsed.total_seconds()
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._last_rtk_address_lookup = datetime.now(UTC)

    def _schedule_push_update(self, device: DeviceHandler) -> None:
        """Schedule a pushed device update on HA loop."""
        try:
            self.hass.loop.call_soon_threadsafe(self._create_push_update_task, device)
        except RuntimeError:
            _LOGGER.debug("Ignoring push update after HA loop shutdown")

    def _schedule_api_refresh(self) -> None:
        """Schedule API cache refresh on HA loop."""
        try:
            self.hass.loop.call_soon_threadsafe(self._create_api_refresh_task)
        except RuntimeError:
            _LOGGER.debug("Ignoring API update after HA loop shutdown")

    def _create_push_update_task(self, device: DeviceHandler) -> None:
        """Create task for a pushed update."""
        self.hass.async_create_task(self._handle_push_update(device))

    def _create_api_refresh_task(self) -> None:
        """Create task for API cache refresh."""
        self.hass.async_create_task(self.async_request_refresh())

    async def _enrich_device(self, serial_number: str, device: DeviceHandler) -> None:
        """Attach private API details to the cached device object."""
        product_item = await self.async_get_product_item(serial_number)
        if product_item is not None:
            setattr(device, "_worx_vision_product_item", product_item)
            _LOGGER.debug(
                "Enriched device %s: area_mowed=%s",
                serial_number,
                product_item.get("area_mowed"),
            )
        else:
            _LOGGER.debug(
                "No product item data returned for device %s", serial_number
            )

        firmware_info = await self.async_get_firmware_upgrade_info(serial_number)
        if firmware_info is not None:
            setattr(device, "_worx_vision_firmware_upgrade", firmware_info)

        map_id = self._device_rtk_map_id(device)
        map_data = await self.async_get_rtk_map(map_id)
        if map_data is not None:
            setattr(device, "_worx_vision_rtk_map", map_data)

        self._remember_rtk_position(serial_number, device)

    async def _api_get(self, path: str) -> Any:
        """Fetch a private Worx API path using pyworxcloud's session/token."""
        api = getattr(self.cloud, "_api", None)
        if api is None:
            _LOGGER.debug("Cannot fetch Worx API path %s: API object missing", path)
            return None

        try:
            await api.check_token()
            endpoint = getattr(getattr(api, "cloud", None), "ENDPOINT", None)
            if endpoint is None:
                endpoint = getattr(getattr(self.cloud, "_cloud", None), "ENDPOINT", None)
            if endpoint is None:
                _LOGGER.debug("Cannot fetch Worx API path %s: endpoint missing", path)
                return None

            return await AGET(
                f"https://{endpoint}{path}",
                HEADERS(api.access_token),
                session=await api._ensure_session(),
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not fetch Worx API path %s", path, exc_info=True)
            return None

    @staticmethod
    def _device_rtk_map_id(device: DeviceHandler) -> str | None:
        """Return RTK map id directly from a pyworxcloud device payload."""
        cfg = getattr(device, "raw_cfg", {}) or {}
        if not isinstance(cfg, dict):
            return None
        rtk = cfg.get("rtk") or {}
        if not isinstance(rtk, dict):
            return None
        value = rtk.get("map")
        return None if value is None else str(value)

    @staticmethod
    def _fallback_firmware_upgrade_info(
        product_item: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Build basic firmware metadata when the OTA endpoint is unavailable."""
        if not isinstance(product_item, dict):
            return None

        current_version = product_item.get("firmware_version")
        capabilities = product_item.get("capabilities") or []
        ota_supported = (
            isinstance(capabilities, list | tuple) and "ota_upgrade" in capabilities
        )
        return {
            "current_version": current_version,
            "latest_version": current_version,
            "update_available": False,
            "ota_supported": ota_supported,
            "auto_upgrade": product_item.get("firmware_auto_upgrade"),
        }

    def _remember_rtk_position(
        self, serial_number: str, device: DeviceHandler
    ) -> None:
        """Keep an in-memory RTK trail for dashboards and the map camera."""
        position = rtk_position(device)
        if position is None:
            return

        latitude, longitude = position
        trail = self._rtk_position_trails.setdefault(
            serial_number, deque(maxlen=RTK_TRAIL_MAX_POINTS)
        )
        if trail:
            _, previous_latitude, previous_longitude = trail[-1]
            if (
                round(previous_latitude, 7) == round(latitude, 7)
                and round(previous_longitude, 7) == round(longitude, 7)
            ):
                return

        trail.append((datetime.now(UTC), latitude, longitude))
        setattr(device, "_worx_vision_rtk_trail", list(trail))

    def _preserve_enriched_attributes(
        self, serial_number: str, device: DeviceHandler
    ) -> None:
        """Keep cached API enrichment on MQTT-only push updates."""
        previous = (self.data or {}).get(serial_number)
        if previous is None:
            return

        for attr in (
            "_worx_vision_product_item",
            "_worx_vision_firmware_upgrade",
            "_worx_vision_rtk_map",
        ):
            if hasattr(device, attr) or not hasattr(previous, attr):
                continue
            setattr(device, attr, getattr(previous, attr))

    def _update_cached_product_item(self, serial_number: str, **fields: Any) -> None:
        """Patch cached product item fields after a successful write."""
        cached = self._product_item_cache.get(serial_number)
        if cached is not None:
            cached[1].update(fields)

        device = (self.data or {}).get(serial_number)
        if device is not None:
            product_item = getattr(device, "_worx_vision_product_item", None)
            if isinstance(product_item, dict):
                product_item.update(fields)

    def _update_cached_rain_delay(self, serial_number: str, minutes: int) -> None:
        """Patch cached rain delay after a successful write."""
        device = (self.data or {}).get(serial_number)
        if device is None:
            return

        rainsensor = getattr(device, "rainsensor", None)
        if isinstance(rainsensor, dict):
            rainsensor["delay"] = minutes
        elif rainsensor is not None and hasattr(rainsensor, "delay"):
            try:
                setattr(rainsensor, "delay", minutes)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not update cached rain delay", exc_info=True)
