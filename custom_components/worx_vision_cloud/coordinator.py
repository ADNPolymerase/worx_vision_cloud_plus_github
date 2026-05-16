"""Coordinator for Worx Vision Cloud Plus."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any

from aiohttp import ClientError, ClientTimeout

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from pyworxcloud import DeviceHandler, LandroidEvent, WorxCloud
from pyworxcloud.utils.requests import AGET, HEADERS

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

RTK_MAP_CACHE_TTL = timedelta(minutes=30)
RTK_ADDRESS_CACHE_TTL = timedelta(hours=24)
RTK_ADDRESS_COORD_PRECISION = 3
RTK_ADDRESS_ENDPOINT = "https://nominatim.openstreetmap.org/reverse"
RTK_ADDRESS_USER_AGENT = (
    "Worx Vision Cloud PLUS Home Assistant custom integration "
    "(https://github.com/SmartServicePL/Worx-Vision-Cloud-PLUS)"
)
PRODUCT_ITEM_CACHE_TTL = timedelta(minutes=5)


def _device_map(cloud: WorxCloud) -> dict[str, DeviceHandler]:
    """Build a serial-number-indexed map of devices from pyworxcloud."""
    devices: dict[str, DeviceHandler] = {}
    for device in cloud.devices.values():
        serial = getattr(device, "serial_number", None)
        if serial is not None:
            devices[str(serial)] = device
    return devices


class WorxVisionCoordinator(DataUpdateCoordinator[dict[str, DeviceHandler]]):
    """Coordinate push and manual updates."""

    def __init__(self, hass: HomeAssistant, cloud: WorxCloud) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
            always_update=False,
        )
        self.cloud = cloud
        self._event_lock = asyncio.Lock()
        self._rtk_address_lock = asyncio.Lock()
        self._last_rtk_address_lookup: datetime | None = None
        self._rtk_map_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._rtk_address_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._product_item_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}

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

    async def async_shutdown(self) -> None:
        """Detach callbacks."""
        self.cloud.set_callback(LandroidEvent.DATA_RECEIVED, lambda **_: None)
        self.cloud.set_callback(LandroidEvent.API, lambda **_: None)

    async def _handle_push_update(self, device: DeviceHandler) -> None:
        """Merge one pushed device update."""
        serial = getattr(device, "serial_number", None)
        if serial is None:
            return

        async with self._event_lock:
            data = dict(self.data or {})
            data[str(serial)] = device
            self.async_set_updated_data(data)

    async def _refresh_from_cloud_cache(self) -> dict[str, DeviceHandler]:
        """Return current cloud cache."""
        devices = _device_map(self.cloud)
        await asyncio.gather(
            *(self._enrich_device(serial, device) for serial, device in devices.items()),
            return_exceptions=True,
        )
        return devices

    async def _async_update_data(self) -> dict[str, DeviceHandler]:
        """Return current cloud cache for DataUpdateCoordinator."""
        try:
            return await self._refresh_from_cloud_cache()
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

    async def async_request_device_update(
        self, serial_number: str, timeout: float | None = 12.0
    ) -> None:
        """Ask one mower for a fresh MQTT state update, then refresh coordinator data."""
        try:
            await self.cloud.update(serial_number, timeout=timeout)
        finally:
            await self.async_request_refresh()

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

    def product_item_data(self, serial_number: str) -> dict[str, Any] | None:
        """Return cached product item details."""
        cached = self._product_item_cache.get(serial_number)
        return None if cached is None else cached[1]

    def rtk_map_data(self, map_id: str | None) -> dict[str, Any] | None:
        """Return cached RTK map details."""
        if not map_id:
            return None
        cached = self._rtk_map_cache.get(map_id)
        return None if cached is None else cached[1]

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

        map_id = self._device_rtk_map_id(device)
        map_data = await self.async_get_rtk_map(map_id)
        if map_data is not None:
            setattr(device, "_worx_vision_rtk_map", map_data)

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
