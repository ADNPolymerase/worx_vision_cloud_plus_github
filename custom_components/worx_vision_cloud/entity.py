"""Entity base classes for Worx Vision Cloud Plus."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from pyworxcloud import DeviceHandler

from .const import DOMAIN
from .coordinator import WorxVisionCoordinator


def _firmware_version(device: DeviceHandler) -> str | None:
    """Return firmware version if exposed."""
    firmware = getattr(device, "firmware", None)
    if isinstance(firmware, dict):
        value = firmware.get("version")
        return None if value is None else str(value)
    value = getattr(firmware, "version", None)
    return None if value is None else str(value)


def _device_name(device: DeviceHandler) -> str:
    """Return a mower name without an account e-mail prefix."""
    value = str(getattr(device, "name", "") or "").strip()
    first_part, separator, mower_name = value.partition(" ")
    if separator and "@" in first_part and mower_name.strip():
        return mower_name.strip()
    return value or "Worx Landroid Vision"


class WorxVisionEntity(CoordinatorEntity[WorxVisionCoordinator]):
    """Base entity for one Worx mower."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WorxVisionCoordinator,
        entry: ConfigEntry,
        serial_number: str,
        entity_key: str,
    ) -> None:
        """Initialize entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._serial_number = serial_number
        self._entity_key = entity_key
        self._attr_unique_id = f"{serial_number}_{entity_key}"

    @property
    def device(self) -> DeviceHandler:
        """Return pyworxcloud device."""
        return self.coordinator.data[self._serial_number]

    @property
    def available(self) -> bool:
        """Return entity availability."""
        return super().available and self._serial_number in (self.coordinator.data or {})

    @property
    def device_info(self) -> DeviceInfo:
        """Return HA device registry info."""
        device = self.device
        manufacturer = "Worx"
        model = str(getattr(device, "model", "Landroid Vision Cloud"))
        serial_number = str(getattr(device, "serial_number", self._serial_number))
        name = _device_name(device)

        info = {
            "identifiers": {(DOMAIN, serial_number)},
            "manufacturer": manufacturer,
            "model": model,
            "name": name,
            "serial_number": serial_number,
            "sw_version": _firmware_version(device),
        }

        mac_address = getattr(device, "mac_address", None)
        if mac_address and mac_address != "__UUID__":
            info["connections"] = {(CONNECTION_NETWORK_MAC, str(mac_address))}

        return DeviceInfo(**info)
