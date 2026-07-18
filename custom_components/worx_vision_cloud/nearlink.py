"""Helpers for NearLink telemetry exposed by Vision Cloud mowers."""
from __future__ import annotations

from typing import Any

NEARLINK_CONNECTION_OPTIONS = ["dock", "radiolink_adapter", "disconnected", "unknown"]

_NEARLINK_PEER_STATE_BY_TYPE = {
    1: "dock",
    2: "radiolink_adapter",
}


def nearlink_module(device: Any) -> dict[str, Any]:
    """Return the raw NearLink module payload when available."""
    raw_dat = getattr(device, "raw_dat", None)
    if not isinstance(raw_dat, dict):
        return {}

    modules = raw_dat.get("modules")
    if not isinstance(modules, dict):
        return {}

    nearlink = modules.get("NL")
    return nearlink if isinstance(nearlink, dict) else {}


def has_nearlink_module(device: Any) -> bool:
    """Return whether the mower payload exposes a NearLink module."""
    raw_dat = getattr(device, "raw_dat", None)
    if not isinstance(raw_dat, dict):
        return False

    modules = raw_dat.get("modules")
    return isinstance(modules, dict) and isinstance(modules.get("NL"), dict)


def nearlink_connections(device: Any) -> list[dict[str, Any]]:
    """Return the currently reported NearLink peer connections."""
    connections = nearlink_module(device).get("conn", [])
    if not isinstance(connections, list):
        return []
    return [connection for connection in connections if isinstance(connection, dict)]


def nearlink_connection_state(device: Any) -> str | None:
    """Return the canonical state key for the active NearLink peer."""
    module = nearlink_module(device)
    if not module:
        return None

    connections = nearlink_connections(device)
    if not connections:
        return "disconnected"

    return _NEARLINK_PEER_STATE_BY_TYPE.get(connections[0].get("type"), "unknown")


def nearlink_attributes(device: Any) -> dict[str, Any]:
    """Return compact diagnostics for NearLink and its current peers."""
    raw_dat = getattr(device, "raw_dat", None)
    nearlink = nearlink_module(device)
    connections = nearlink_connections(device)

    attributes: dict[str, Any] = {
        "module_status": nearlink.get("stat"),
        "module_error": nearlink.get("error"),
        "connection_count": len(connections),
    }

    if isinstance(raw_dat, dict):
        attributes["active_connection"] = raw_dat.get("conn")

        robot_wifi = raw_dat.get("wifi")
        if isinstance(robot_wifi, dict):
            attributes["robot_wifi_status"] = robot_wifi.get("st")
            attributes["robot_wifi_error"] = robot_wifi.get("er")
            attributes["robot_wifi_rssi"] = robot_wifi.get("rsi")

    for index, connection in enumerate(connections, start=1):
        prefix = f"connection_{index}"
        attributes[f"{prefix}_type"] = connection.get("type")
        attributes[f"{prefix}_mac"] = connection.get("mac")
        attributes[f"{prefix}_rssi"] = connection.get("rssi")
        attributes[f"{prefix}_firmware"] = connection.get("vers")

        peer_wifi = connection.get("wifi")
        if isinstance(peer_wifi, dict):
            attributes[f"{prefix}_wifi_status"] = peer_wifi.get("stat")
            attributes[f"{prefix}_wifi_rssi"] = peer_wifi.get("rssi")

    return {key: value for key, value in attributes.items() if value is not None}
