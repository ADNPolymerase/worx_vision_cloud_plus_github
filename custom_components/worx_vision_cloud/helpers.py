"""Helper functions for Worx Vision Cloud Plus."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from enum import Enum
import json
from math import cos, hypot, radians
from typing import Any

from homeassistant.util import slugify

# Shared with lawn_mower.py, sensor.py and coordinator.py so all agree on what
# each mower status means (used e.g. to track today's actual mowing time
# independent of Worx's own, sometimes-stale work-time statistics).
MOWING_STATUS_IDS = {7, 8, 12, 32, 110, 111}
RETURNING_STATUS_IDS = {4, 5, 6, 30, 104}
STARTING_STATUS_IDS = {2, 3, 33, 103}
PAUSED_STATUS_IDS = {34}
DOCKED_STATUS_IDS = {1}
ERROR_STATUS_IDS = {9, 10, 13}

RAW_SOURCE_ATTRS = (
    "raw_dat",
    "raw_cfg",
    "module_status",
    "module_config",
    "battery",
    "blades",
    "rainsensor",
    "status",
    "error",
    "orientation",
    "zone",
    "schedules",
    "statistics",
    "firmware",
    "warranty",
    "lawn",
)

MAX_LIST_ITEMS = 80
MAX_STRING_STATE_LENGTH = 240

SENSITIVE_RAW_PATHS = {
    "cfg.rtk.ck",
}

NOISY_RAW_PATH_PREFIXES = (
    "cfg.log.",
    "cfg.dk.id.",
    "cfg.sc.slots[",
    "schedules.slots[",
)

NOISY_RAW_PATHS = {
    "cfg.sc.slots.count",
    "schedules.slots.count",
}

SCHEDULE_DEFAULT_LANGUAGE = "en"

# Schedule text is free-form sensor state that Home Assistant cannot translate
# through translations/*.json, so it is localized here from the UI language.
SCHEDULE_DAY_LABELS = {
    "en": {
        "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu",
        "friday": "Fri", "saturday": "Sat", "sunday": "Sun",
    },
    "de": {
        "monday": "Mo", "tuesday": "Di", "wednesday": "Mi", "thursday": "Do",
        "friday": "Fr", "saturday": "Sa", "sunday": "So",
    },
    "fr": {
        "monday": "lun", "tuesday": "mar", "wednesday": "mer", "thursday": "jeu",
        "friday": "ven", "saturday": "sam", "sunday": "dim",
    },
    "pl": {
        "monday": "pon", "tuesday": "wt", "wednesday": "śr", "thursday": "czw",
        "friday": "pt", "saturday": "sob", "sunday": "niedz",
    },
}

SCHEDULE_TEXT_LABELS = {
    "en": {"none": "no active slots", "count": "{count} active slots", "edge": "+ edge"},
    "de": {"none": "keine aktiven Zeitfenster", "count": "{count} aktive Zeitfenster", "edge": "+ Kante"},
    "fr": {"none": "aucun créneau actif", "count": "{count} créneaux actifs", "edge": "+ bordure"},
    "pl": {"none": "brak aktywnych slotów", "count": "{count} aktywnych slotów", "edge": "+ krawędź"},
}


def schedule_language(language: Any) -> str:
    """Return a supported schedule language code (falls back to English)."""
    code = str(language or "").lower().split("-")[0]
    return code if code in SCHEDULE_DAY_LABELS else SCHEDULE_DEFAULT_LANGUAGE

SCHEDULE_DAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def get_dict_value(obj: Any, key: str, default: Any = None) -> Any:
    """Read a key from dict-like or object-like values."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def get_nested_value(obj: Any, *keys: str, default: Any = None) -> Any:
    """Read a nested key path from dict-like or object-like values."""
    value = obj
    for key in keys:
        value = get_dict_value(value, key, None)
        if value is None:
            return default
    return value


def normalize_scalar(value: Any) -> Any | None:
    """Normalize a value so it is safe as a Home Assistant state."""
    if value is None:
        return None
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, str):
        if len(value) > MAX_STRING_STATE_LENGTH:
            return value[:MAX_STRING_STATE_LENGTH]
        return value
    return None


def stable_json(value: Any) -> str:
    """Return a deterministic compact JSON string."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def raw_path_is_sensitive(path: str) -> bool:
    """Return true when a raw path should never be exposed as an entity."""
    return path in SENSITIVE_RAW_PATHS


def raw_path_enabled_default(path: str) -> bool:
    """Return default enabled state for raw diagnostic entities."""
    if raw_path_is_sensitive(path) or path in NOISY_RAW_PATHS:
        return False
    return not any(path.startswith(prefix) for prefix in NOISY_RAW_PATH_PREFIXES)


def safe_key(path: str) -> str:
    """Return a stable slug key for entity unique IDs."""
    cleaned = (
        path.replace("[", "_")
        .replace("]", "")
        .replace(".", "_")
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
    )
    return slugify(cleaned)


def iter_flatten(value: Any, prefix: str) -> Iterable[tuple[str, Any]]:
    """Flatten nested dict/list structures into scalar leaves."""
    if value is None:
        return

    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            next_prefix = f"{prefix}.{key_text}" if prefix else key_text
            yield from iter_flatten(item, next_prefix)
        return

    if isinstance(value, list | tuple):
        yield f"{prefix}.count", len(value)
        for index, item in enumerate(value[:MAX_LIST_ITEMS]):
            yield from iter_flatten(item, f"{prefix}[{index}]")
        return

    scalar = normalize_scalar(value)
    if scalar is not None:
        yield prefix, scalar


def raw_entity_values(device: Any) -> dict[str, Any]:
    """Return all scalar raw/dynamic values for a mower."""
    values: dict[str, Any] = {}

    for attr in RAW_SOURCE_ATTRS:
        source_value = getattr(device, attr, None)
        if source_value is None:
            continue
        source_name = attr.removeprefix("raw_")
        for path, value in iter_flatten(source_value, source_name):
            if raw_path_is_sensitive(path):
                continue
            key = safe_key(path)
            if key:
                values[key] = value

    # A few useful top-level object attributes that pyworxcloud maps from the API.
    for attr in (
        "online",
        "locked",
        "mac_address",
        "model",
        "name",
        "protocol",
        "rssi",
        "time_zone",
        "updated",
        "updated_origin",
        "uuid",
    ):
        if hasattr(device, attr):
            scalar = normalize_scalar(getattr(device, attr))
            if scalar is not None:
                values[safe_key(attr)] = scalar

    return values


def raw_entity_path_map(device: Any) -> dict[str, str]:
    """Return entity key -> readable raw path map."""
    result: dict[str, str] = {}

    for attr in RAW_SOURCE_ATTRS:
        source_value = getattr(device, attr, None)
        if source_value is None:
            continue
        source_name = attr.removeprefix("raw_")
        for path, value in iter_flatten(source_value, source_name):
            if raw_path_is_sensitive(path):
                continue
            key = safe_key(path)
            if key:
                result[key] = path

    for attr in (
        "online",
        "locked",
        "mac_address",
        "model",
        "name",
        "protocol",
        "rssi",
        "time_zone",
        "updated",
        "updated_origin",
        "uuid",
    ):
        if hasattr(device, attr):
            key = safe_key(attr)
            result[key] = attr

    return result


def _raw_cfg(device: Any) -> Any:
    """Return raw cfg payload from pyworxcloud."""
    return getattr(device, "raw_cfg", {}) or {}


def _raw_dat(device: Any) -> Any:
    """Return raw dat payload from pyworxcloud."""
    return getattr(device, "raw_dat", {}) or {}


def rtk_map_id(device: Any) -> Any:
    """Return RTK map identifier when the mower reports one."""
    return get_nested_value(_raw_cfg(device), "rtk", "map")


def rtk_map_attributes(device: Any) -> dict[str, Any]:
    """Return RTK map metadata that is available without map geometry."""
    rtk = get_dict_value(_raw_cfg(device), "rtk", {}) or {}
    zones = get_dict_value(rtk, "zs", []) or []
    if not isinstance(zones, list | tuple):
        zones = []

    return {
        "map_id": get_dict_value(rtk, "map"),
        "status": get_dict_value(rtk, "st"),
        "zones": [
            {
                "id": get_dict_value(zone, "id"),
                "cutting": get_nested_value(zone, "cfg", "cut", default={}),
                "schedule": get_nested_value(zone, "cfg", "sc", default={}),
            }
            for zone in zones
            if isinstance(zone, dict)
        ],
    }


def rtk_position(device: Any) -> tuple[float, float] | None:
    """Return current RTK latitude/longitude position."""
    position = get_nested_value(_raw_dat(device), "rtk", "pos", default=[])
    if not isinstance(position, list | tuple) or len(position) < 2:
        return None

    try:
        latitude = float(position[0])
        longitude = float(position[1])
    except (TypeError, ValueError):
        return None

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def rtk_station_position(device: Any) -> tuple[float, float] | None:
    """Return the RTK station marker position from cached map geometry."""
    map_data = getattr(device, "_worx_vision_rtk_map", None)
    if not isinstance(map_data, dict):
        return None

    markers = get_nested_value(map_data, "layers", "markers", default=[]) or []
    if not isinstance(markers, list | tuple):
        return None

    for marker in markers:
        if not isinstance(marker, dict):
            continue
        pair = (
            get_nested_value(marker, "record", "latitude"),
            get_nested_value(marker, "record", "longitude"),
        )
        try:
            latitude = float(pair[0])
            longitude = float(pair[1])
        except (TypeError, ValueError):
            continue
        if -90 <= latitude <= 90 and -180 <= longitude <= 180:
            return latitude, longitude

    return None


def distance_meters(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    """Return an approximate distance between two latitude/longitude pairs."""
    mean_latitude = radians((first[0] + second[0]) / 2)
    latitude_m = (first[0] - second[0]) * 110_540
    longitude_m = (first[1] - second[1]) * 111_320 * cos(mean_latitude)
    return hypot(latitude_m, longitude_m)


def rtk_distance_to_station_m(device: Any) -> float | None:
    """Return distance from current RTK position to the station marker."""
    position = rtk_position(device)
    station = rtk_station_position(device)
    if position is None or station is None:
        return None
    return distance_meters(position, station)


def rtk_at_station(device: Any, threshold_m: float = 2.5) -> bool:
    """Return true when RTK position is close enough to the station marker."""
    distance = rtk_distance_to_station_m(device)
    return distance is not None and distance <= threshold_m


def rtk_location_attributes(device: Any) -> dict[str, Any]:
    """Return RTK location diagnostic attributes."""
    dat_rtk = get_nested_value(_raw_dat(device), "rtk", default={}) or {}
    return {
        "map_id": rtk_map_id(device),
        "provider": get_dict_value(dat_rtk, "provider"),
        "gps": get_dict_value(dat_rtk, "gps"),
        "imu": get_dict_value(dat_rtk, "imu"),
        "network": get_dict_value(dat_rtk, "network"),
    }


def schedule_slots(device: Any) -> list[Any]:
    """Return normalized schedule slot objects from pyworxcloud."""
    schedules = getattr(device, "schedules", {}) or {}
    slots = get_dict_value(schedules, "slots", []) or []
    if not isinstance(slots, list | tuple):
        return []
    return [slot for slot in slots if get_dict_value(slot, "day") is not None]


def schedule_day_index(day: Any) -> int | None:
    """Return Python weekday index for a pyworxcloud schedule day."""
    if day is None:
        return None
    return SCHEDULE_DAY_INDEX.get(str(day).lower())


def parse_schedule_time(value: Any) -> time | None:
    """Parse an HH:MM schedule time from pyworxcloud data."""
    if not isinstance(value, str) or ":" not in value:
        return None
    hour, minute, *_ = value.split(":")
    try:
        return time(hour=int(hour), minute=int(minute))
    except ValueError:
        return None


def _library_next_schedule_start(device: Any, now: datetime) -> datetime | None:
    """Return the next start computed by pyworxcloud, if available.

    pyworxcloud exposes ``schedules["next_schedule_start"]`` either as a
    datetime or as a wall-clock string; observed formats include both naive
    ("2026-07-06 10:00:00") and offset-aware ("2026-07-08 08:00:00+02:00")
    values. Naive values are the local schedule time, so ``now``'s timezone is
    attached to make them comparable.
    """
    schedules = getattr(device, "schedules", {}) or {}
    raw = get_dict_value(schedules, "next_schedule_start")
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=now.tzinfo)
    return parsed.astimezone(now.tzinfo)


def next_schedule_start(device: Any, now: datetime) -> datetime | None:
    """Return the next scheduled mowing start at or after ``now``.

    Prefers the value already computed by pyworxcloud
    (``schedules["next_schedule_start"]``) when it is still in the future, and
    falls back to deriving it from the weekly slots ourselves. Returns None
    when the native schedule is disabled or party mode suspends it. Returns a
    timezone-aware datetime (matching ``now``'s tzinfo) otherwise.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    schedules = getattr(device, "schedules", {}) or {}
    if get_dict_value(schedules, "party_mode_enabled") is True:
        return None

    from_library = _library_next_schedule_start(device, now)
    if from_library is not None and from_library >= now:
        return from_library

    # `schedules["active"]` is unreliable on Vision protocol 1 mowers:
    # observed False while the weekly schedule was genuinely running and
    # pyworxcloud itself still computed next_schedule_start. Only treat it
    # as "schedule disabled" when the library offers no future start either.
    if get_dict_value(schedules, "active") is False:
        return None

    slots = schedule_slots(device)
    if not slots:
        return None

    candidates: list[datetime] = []
    for offset in range(0, 14):
        day = (now + timedelta(days=offset)).date()
        for slot in slots:
            if schedule_day_index(get_dict_value(slot, "day")) != day.weekday():
                continue
            start_time = parse_schedule_time(get_dict_value(slot, "start"))
            if start_time is None:
                continue
            start = datetime.combine(day, start_time, tzinfo=now.tzinfo)
            if start >= now:
                candidates.append(start)

    return min(candidates) if candidates else None


def schedule_day_label(day: Any, language: str = SCHEDULE_DEFAULT_LANGUAGE) -> str:
    """Return a short, localized human label for a schedule day."""
    if day is None:
        return ""
    labels = SCHEDULE_DAY_LABELS[schedule_language(language)]
    return labels.get(str(day).lower(), str(day))


def schedule_slot_summary(slot: Any, language: str = SCHEDULE_DEFAULT_LANGUAGE) -> str:
    """Return one compact, localized schedule slot line."""
    lang = schedule_language(language)
    day = schedule_day_label(get_dict_value(slot, "day"), lang)
    start = get_dict_value(slot, "start")
    end = get_dict_value(slot, "end")
    duration = get_dict_value(slot, "duration_extended")
    if duration is None:
        duration = get_dict_value(slot, "duration")

    if start and end:
        text = f"{day} {start}-{end}"
    elif start and duration is not None:
        text = f"{day} {start} ({duration} min)"
    else:
        text = day or "slot"

    if get_dict_value(slot, "boundary"):
        text = f"{text} {SCHEDULE_TEXT_LABELS[lang]['edge']}"
    return text


def schedule_summary(device: Any, language: str = SCHEDULE_DEFAULT_LANGUAGE) -> str | None:
    """Return a compact, localized schedule summary for Home Assistant state."""
    lang = schedule_language(language)
    slots = schedule_slots(device)
    if not slots:
        return SCHEDULE_TEXT_LABELS[lang]["none"]

    summary = ", ".join(schedule_slot_summary(slot, lang) for slot in slots)
    if len(summary) <= MAX_STRING_STATE_LENGTH:
        return summary
    return SCHEDULE_TEXT_LABELS[lang]["count"].format(count=len(slots))


def schedule_attributes(
    device: Any, language: str = SCHEDULE_DEFAULT_LANGUAGE
) -> dict[str, Any]:
    """Return structured schedule data for cards and templates."""
    schedules = getattr(device, "schedules", {}) or {}
    slots = schedule_slots(device)
    auto_schedule = get_dict_value(schedules, "auto_schedule", {}) or {}

    return {
        "active_slots": len(slots),
        "slots": [
            {
                "day": get_dict_value(slot, "day"),
                "day_label": schedule_day_label(get_dict_value(slot, "day"), language),
                "start": get_dict_value(slot, "start"),
                "end": get_dict_value(slot, "end"),
                "duration": get_dict_value(slot, "duration"),
                "duration_extended": get_dict_value(slot, "duration_extended"),
                "boundary": get_dict_value(slot, "boundary"),
                "source": get_dict_value(slot, "source"),
            }
            for slot in slots
        ],
        "auto_schedule_enabled": get_dict_value(auto_schedule, "enabled"),
        "one_time_schedule": get_dict_value(schedules, "one_time_schedule"),
        "party_mode_enabled": get_dict_value(schedules, "party_mode_enabled"),
        "time_extension": get_dict_value(schedules, "time_extension"),
        "next_schedule_start": get_dict_value(schedules, "next_schedule_start"),
    }
