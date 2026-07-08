"""Camera platform for Worx Vision Cloud Plus."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from html import escape
from math import cos, hypot, radians
from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import WorxVisionEntity
from .helpers import get_dict_value, get_nested_value, rtk_map_id, rtk_position

SVG_WIDTH = 900
SVG_HEIGHT = 620
SVG_PADDING = 48
TRAIL_MAX_GAP = timedelta(minutes=5)
TRAIL_MAX_SEGMENT_DISTANCE_M = 35.0
TRAIL_MIN_POINT_DISTANCE_M = 0.25
TRAIL_MAP_MARGIN_M = 12.0
DEFAULT_CUTTING_WIDTH_M = 0.18
MOWED_SWATH_MIN_WIDTH_PX = 3.0
MOWED_SWATH_MAX_WIDTH_PX = 32.0
MOWED_MAX_OPACITY = 0.58
MOWED_MIN_OPACITY = 0.12
# Purely a visual fade duration for freshly-mowed swaths (older swaths look
# lighter), unrelated to how long trail data is kept (that's a full local
# day, reset at midnight - see coordinator.py/_remember_rtk_position).
MOWED_FADE_DURATION = timedelta(hours=6)
CUTTING_WIDTH_BY_MODEL_M = {
    "WR202E": 0.18,
    "WR206E": 0.18,
    "WR208E": 0.18,
    "WR303E": 0.18,
    "WR305E": 0.18,
    "WR308E": 0.18,
    "WR365E": 0.18,
    "WR365E1": 0.18,
    "WR213E": 0.22,
    "WR216E": 0.22,
    "WR312E": 0.22,
    "WR318E": 0.22,
    "WR330E": 0.22,
    "WR340E": 0.22,
    "WR341E": 0.22,
    "WR342E": 0.22,
    "WR344E": 0.22,
    "WR310": 0.229,
    "WR320": 0.229,
    "WR340": 0.229,
    "WR341": 0.229,
    "WR344": 0.229,
    "WR346": 0.229,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up RTK map cameras."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator

    async_add_entities(
        WorxVisionMapCamera(coordinator, entry, serial_number)
        for serial_number in coordinator.data
    )


class WorxVisionMapCamera(WorxVisionEntity, Camera):
    """RTK map rendered from Worx map geometry."""

    _attr_icon = "mdi:map"
    _attr_translation_key = "rtk_map_camera"

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize RTK map camera."""
        Camera.__init__(self)
        WorxVisionEntity.__init__(self, coordinator, entry, serial_number, "rtk_map_camera")
        self.content_type = "image/svg+xml"
        self._last_map_data: dict[str, Any] | None = None
        self._last_mowed_swath_width_px: float | None = None

    @property
    def available(self) -> bool:
        """Return entity availability."""
        return super().available and rtk_map_id(self.device) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return RTK map metadata."""
        map_data = self._last_map_data or {}
        zone = _first_zone(map_data)
        exclusion_count = len(get_nested_value(map_data, "layers", "exclusions", default=[]) or [])
        marker_count = len(get_nested_value(map_data, "layers", "markers", default=[]) or [])

        attrs: dict[str, Any] = {
            "map_id": rtk_map_id(self.device),
            "map_status": get_dict_value(map_data, "status"),
            "map_type": get_dict_value(map_data, "type"),
            "active": get_dict_value(map_data, "active"),
            "rtk_provider": get_dict_value(map_data, "rtk_provider"),
            "zone_name": get_dict_value(zone, "name"),
            "zone_area_m2": _scaled_area(get_dict_value(zone, "area")),
            "zone_perimeter_m": _scaled_length(get_dict_value(zone, "perimeter")),
            "exclusion_count": exclusion_count,
            "marker_count": marker_count,
            "cutting_width_cm": round(_cutting_width_m(self.device) * 100, 1),
            "mowed_swath_width_px": self._last_mowed_swath_width_px,
        }
        return {key: value for key, value in attrs.items() if value is not None}

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return an SVG map rendered from Worx map geometry."""
        del width, height

        map_id = rtk_map_id(self.device)
        map_data = (
            await self.coordinator.async_get_rtk_map(str(map_id))
            if map_id is not None
            else None
        )
        if map_data is not None:
            self._last_map_data = map_data
        else:
            # A momentary fetch failure (or a partial MQTT cfg push that
            # briefly omits the RTK map id) should keep showing the last
            # known map instead of rendering a blank/unknown one.
            map_data = self._last_map_data

        trail = self.coordinator.rtk_position_timed_trail(self._serial_number)
        svg, swath_width_px = _render_svg_map(
            map_data,
            rtk_position(self.device),
            trail,
            _cutting_width_m(self.device),
        )
        self._last_mowed_swath_width_px = swath_width_px
        self.async_write_ha_state()
        return svg.encode()


def _normalize_model(value: Any) -> str:
    """Return a compact model key, for example WR365E1."""
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _cutting_width_m(device: Any) -> float:
    """Return mower cutting width in meters based on known WORX models."""
    product_item = getattr(device, "_worx_vision_product_item", {}) or {}
    candidates = [
        getattr(device, "model", None),
        get_dict_value(product_item, "model"),
        get_dict_value(product_item, "product_model"),
        get_dict_value(product_item, "code"),
        get_dict_value(product_item, "sku"),
    ]
    for candidate in candidates:
        normalized = _normalize_model(candidate)
        if not normalized:
            continue
        if normalized in CUTTING_WIDTH_BY_MODEL_M:
            return CUTTING_WIDTH_BY_MODEL_M[normalized]
        for model, width in CUTTING_WIDTH_BY_MODEL_M.items():
            if normalized.startswith(model):
                return width
    return DEFAULT_CUTTING_WIDTH_M


def _scaled_area(value: Any) -> float | None:
    """Return area in square meters from Worx square-millimeter values."""
    try:
        return round(float(value) / 1_000_000, 2)
    except (TypeError, ValueError):
        return None


def _scaled_length(value: Any) -> float | None:
    """Return length in meters from Worx millimeter values."""
    try:
        return round(float(value) / 1000, 2)
    except (TypeError, ValueError):
        return None


def _first_zone(map_data: dict[str, Any]) -> dict[str, Any]:
    """Return the first boundary zone from map data."""
    boundaries = get_nested_value(map_data, "layers", "boundaries", default=[]) or []
    for boundary in boundaries:
        zones = get_dict_value(boundary, "zones", []) or []
        for zone in zones:
            if isinstance(zone, dict):
                return zone
    return {}


def _point_pair(point: Any) -> tuple[float, float] | None:
    """Return latitude/longitude from a Worx point array."""
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        return None
    try:
        return float(point[0]), float(point[1])
    except (TypeError, ValueError):
        return None


def _contour_points(contour: dict[str, Any]) -> list[tuple[float, float]]:
    """Return normalized points from one contour."""
    return [
        pair
        for pair in (_point_pair(point) for point in get_dict_value(contour, "points", []) or [])
        if pair is not None
    ]


def _iter_contours(map_data: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield map contours with semantic layer names."""
    boundaries = get_nested_value(map_data, "layers", "boundaries", default=[]) or []
    for boundary in boundaries:
        for zone in get_dict_value(boundary, "zones", []) or []:
            for contour in get_dict_value(zone, "contours", []) or []:
                if isinstance(contour, dict):
                    yield "zone", contour

    exclusions = get_nested_value(map_data, "layers", "exclusions", default=[]) or []
    for exclusion in exclusions:
        for contour in get_dict_value(exclusion, "contours", []) or []:
            if isinstance(contour, dict):
                yield "exclusion", contour


def _iter_bounds_points(
    map_data: dict[str, Any],
    robot_position: tuple[float, float] | None,
    trail: list[tuple[float, float]] | None = None,
) -> list[tuple[float, float]]:
    """Return all points that should influence map bounds."""
    points: list[tuple[float, float]] = []
    for _, contour in _iter_contours(map_data):
        points.extend(_contour_points(contour))
        for child in get_dict_value(contour, "children", []) or []:
            if isinstance(child, dict):
                points.extend(_contour_points(child))

    markers = get_nested_value(map_data, "layers", "markers", default=[]) or []
    for marker in markers:
        pair = _point_pair([
            get_nested_value(marker, "record", "latitude"),
            get_nested_value(marker, "record", "longitude"),
        ])
        if pair is not None:
            points.append(pair)

    if robot_position is not None:
        points.append(robot_position)

    if trail:
        points.extend(trail)

    return points


def _projector(points: list[tuple[float, float]]):
    """Build a lat/lon to SVG coordinate projector."""
    lats = [point[0] for point in points]
    lons = [point[1] for point in points]
    min_lat = min(lats)
    max_lat = max(lats)
    min_lon = min(lons)
    max_lon = max(lons)
    mean_lat = (min_lat + max_lat) / 2
    lon_scale = max(cos(radians(mean_lat)), 0.1)

    width_m = max((max_lon - min_lon) * 111_320 * lon_scale, 1)
    height_m = max((max_lat - min_lat) * 110_540, 1)
    scale = min(
        (SVG_WIDTH - SVG_PADDING * 2) / width_m,
        (SVG_HEIGHT - SVG_PADDING * 2) / height_m,
    )
    drawn_width = width_m * scale
    drawn_height = height_m * scale
    offset_x = (SVG_WIDTH - drawn_width) / 2
    offset_y = (SVG_HEIGHT - drawn_height) / 2

    def project(point: tuple[float, float]) -> tuple[float, float]:
        lat, lon = point
        x_m = (lon - min_lon) * 111_320 * lon_scale
        y_m = (max_lat - lat) * 110_540
        return offset_x + x_m * scale, offset_y + y_m * scale

    return project, scale


def _path(points: list[tuple[float, float]], project) -> str:
    """Return SVG path data for one polygon/line."""
    if not points:
        return ""
    projected = [project(point) for point in points]
    first_x, first_y = projected[0]
    parts = [f"M {first_x:.2f} {first_y:.2f}"]
    parts.extend(f"L {x:.2f} {y:.2f}" for x, y in projected[1:])
    parts.append("Z")
    return " ".join(parts)


def _open_path(points: list[tuple[float, float]], project) -> str:
    """Return SVG path data for an open line."""
    if not points:
        return ""
    projected = [project(point) for point in points]
    first_x, first_y = projected[0]
    parts = [f"M {first_x:.2f} {first_y:.2f}"]
    parts.extend(f"L {x:.2f} {y:.2f}" for x, y in projected[1:])
    return " ".join(parts)


def _polyline(points: list[tuple[float, float]], project) -> str:
    """Return SVG polyline points."""
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in (project(point) for point in points))


def _compound_zone_path(contour: dict[str, Any], project) -> str:
    """Return one SVG path for a zone contour and its holes."""
    parts: list[str] = []
    outer = _contour_points(contour)
    if outer:
        parts.append(_path(outer, project))
    for child in get_dict_value(contour, "children", []) or []:
        if not isinstance(child, dict):
            continue
        child_points = _contour_points(child)
        if child_points:
            parts.append(_path(child_points, project))
    return " ".join(part for part in parts if part)


def _mowed_clip_def(map_data: dict[str, Any], project) -> str:
    """Return an SVG clip path that keeps mowed swaths inside lawn zones."""
    clip_paths: list[str] = []
    for layer, contour in _iter_contours(map_data):
        if layer != "zone":
            continue
        path = _compound_zone_path(contour, project)
        if path:
            clip_paths.append(
                f'<path d="{path}" fill-rule="evenodd" clip-rule="evenodd" />'
            )
    if not clip_paths:
        return ""
    return (
        '<clipPath id="mowed-clip" clipPathUnits="userSpaceOnUse">'
        f'{"".join(clip_paths)}'
        "</clipPath>"
    )


def _distance_m(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    """Return approximate distance between two latitude/longitude points."""
    mean_lat = (first[0] + second[0]) / 2
    lon_scale = max(cos(radians(mean_lat)), 0.1)
    x_m = (second[1] - first[1]) * 111_320 * lon_scale
    y_m = (second[0] - first[0]) * 110_540
    return hypot(x_m, y_m)


def _coordinate_bounds(
    points: list[tuple[float, float]],
) -> tuple[float, float, float, float] | None:
    """Return min/max latitude and longitude for map geometry."""
    if not points:
        return None
    lats = [point[0] for point in points]
    lons = [point[1] for point in points]
    return min(lats), max(lats), min(lons), max(lons)


def _point_in_bounds(
    point: tuple[float, float],
    bounds: tuple[float, float, float, float],
    margin_m: float,
) -> bool:
    """Return whether a point is near the mapped garden geometry."""
    min_lat, max_lat, min_lon, max_lon = bounds
    mean_lat = (min_lat + max_lat) / 2
    lon_scale = max(cos(radians(mean_lat)), 0.1)
    lat_margin = margin_m / 110_540
    lon_margin = margin_m / (111_320 * lon_scale)
    lat, lon = point
    return (
        min_lat - lat_margin <= lat <= max_lat + lat_margin
        and min_lon - lon_margin <= lon <= max_lon + lon_margin
    )


def _trail_segments(
    map_data: dict[str, Any],
    trail: list[tuple[datetime, float, float]] | None,
) -> list[list[tuple[datetime, float, float]]]:
    """Return drawable RTK trail segments without long gaps or position jumps.

    Kept for the current local day (matching the Worx app, which shows the
    full day's trail rather than a fixed rolling window), not a fixed age
    cutoff — the coordinator itself drops yesterday's points at local
    midnight, so this only needs to guard against a stale point landing here
    right at the day boundary.
    """
    if not trail:
        return []

    today = dt_util.now().date()
    bounds = _coordinate_bounds(_iter_bounds_points(map_data, None))
    segments: list[list[tuple[datetime, float, float]]] = []
    current: list[tuple[datetime, float, float]] = []
    previous_time: datetime | None = None
    previous_point: tuple[float, float] | None = None

    def flush() -> None:
        if len(current) > 1:
            segments.append(list(current))
        current.clear()

    for timestamp, latitude, longitude in trail:
        point = (latitude, longitude)

        if dt_util.as_local(timestamp).date() != today:
            continue

        if bounds is not None and not _point_in_bounds(point, bounds, TRAIL_MAP_MARGIN_M):
            flush()
            previous_time = None
            previous_point = None
            continue

        if previous_point is not None:
            distance = _distance_m(previous_point, point)
            if distance < TRAIL_MIN_POINT_DISTANCE_M:
                continue
            if (
                previous_time is not None
                and timestamp - previous_time > TRAIL_MAX_GAP
            ) or distance > TRAIL_MAX_SEGMENT_DISTANCE_M:
                flush()

        current.append((timestamp, latitude, longitude))
        previous_time = timestamp
        previous_point = point

    flush()
    return segments


def _mowed_opacity(timestamp: datetime, now: datetime) -> float:
    """Return fading opacity for a recently mowed RTK segment."""
    age = max(now - timestamp, timedelta())
    fade = min(age / MOWED_FADE_DURATION, 1)
    return MOWED_MAX_OPACITY - (MOWED_MAX_OPACITY - MOWED_MIN_OPACITY) * fade


def _mowed_segments_svg(
    segments: list[list[tuple[datetime, float, float]]],
    project,
    swath_width_px: float,
    clip_ref: str,
) -> list[str]:
    """Return SVG paths for mowed swaths that fade with age."""
    now = datetime.now(UTC)
    paths: list[str] = []
    for segment in segments:
        for start, end in zip(segment, segment[1:]):
            points = [(start[1], start[2]), (end[1], end[2])]
            path = _open_path(points, project)
            opacity = _mowed_opacity(end[0], now)
            paths.append(
                f'<path class="mowed" d="{path}" opacity="{opacity:.2f}" '
                f'stroke-width="{swath_width_px:.2f}" />'
            )
    if paths and clip_ref:
        return [f'<g class="mowed-area" clip-path="{clip_ref}">', *paths, "</g>"]
    return paths


def _mowed_swath_width_px(cutting_width_m: float, meters_to_pixels: float) -> float:
    """Return SVG stroke width for a real mower cutting width."""
    raw_width = cutting_width_m * meters_to_pixels
    return max(
        MOWED_SWATH_MIN_WIDTH_PX,
        min(MOWED_SWATH_MAX_WIDTH_PX, raw_width),
    )


def _render_svg_map(
    map_data: dict[str, Any] | None,
    robot_position: tuple[float, float] | None,
    trail: list[tuple[datetime, float, float]] | None = None,
    cutting_width_m: float = DEFAULT_CUTTING_WIDTH_M,
) -> tuple[str, float | None]:
    """Render map data to SVG."""
    if not isinstance(map_data, dict):
        return _placeholder_svg("Brak mapy RTK z API"), None

    trail_segments = _trail_segments(map_data, trail)
    trail_points = [
        (latitude, longitude)
        for segment in trail_segments
        for _, latitude, longitude in segment
    ]
    points = _iter_bounds_points(map_data, robot_position, trail_points)
    if not points:
        return _placeholder_svg("Mapa RTK nie zawiera punktow"), None

    project, meters_to_pixels = _projector(points)
    swath_width_px = _mowed_swath_width_px(cutting_width_m, meters_to_pixels)
    clip_def = _mowed_clip_def(map_data, project)
    clip_ref = "url(#mowed-clip)" if clip_def else ""
    body: list[str] = []

    for layer, contour in _iter_contours(map_data):
        outer = _contour_points(contour)
        if not outer:
            continue

        if layer == "zone":
            zone_path = _path(outer, project)
            body.append(
                f'<path class="zone-shadow" d="{zone_path}" />'
                f'<path class="zone" d="{zone_path}" />'
                f'<path class="zone-edge" d="{_open_path(outer, project)}" />'
            )
            for child in get_dict_value(contour, "children", []) or []:
                if not isinstance(child, dict):
                    continue
                child_points = _contour_points(child)
                if child_points:
                    child_path = _path(child_points, project)
                    body.append(
                        f'<path class="hole-shadow" d="{child_path}" />'
                        f'<path class="hole" d="{child_path}" />'
                        f'<path class="hole-edge" d="{_open_path(child_points, project)}" />'
                    )
        else:
            exclusion_path = _path(outer, project)
            body.append(
                f'<path class="exclusion-shadow" d="{exclusion_path}" />'
                f'<path class="exclusion" d="{exclusion_path}" />'
            )

    body.extend(_mowed_segments_svg(trail_segments, project, swath_width_px, clip_ref))

    markers = get_nested_value(map_data, "layers", "markers", default=[]) or []
    for marker in markers:
        pair = _point_pair([
            get_nested_value(marker, "record", "latitude"),
            get_nested_value(marker, "record", "longitude"),
        ])
        if pair is None:
            continue
        x, y = project(pair)
        body.append(
            f'<g class="station" transform="translate({x:.2f} {y:.2f})">'
            '<circle class="station-shadow" r="24" cx="3" cy="5" />'
            '<circle r="22" />'
            '<path d="M 4 -16 L -9 3 H 0 L -5 17 L 12 -5 H 3 Z" />'
            '</g>'
        )

    if robot_position is not None:
        x, y = project(robot_position)
        body.append(
            f'<g class="robot" transform="translate({x:.2f} {y:.2f}) scale(0.68)">'
            '<ellipse class="robot-shadow" cx="2" cy="21" rx="27" ry="10" />'
            '<path class="track" d="M -27 -11 L -18 -18 L -16 20 L -25 17 Z" />'
            '<path class="track" d="M 27 -11 L 18 -18 L 16 20 L 25 17 Z" />'
            '<path class="body" d="M -20 -18 L -8 -24 H 12 L 22 -15 L 20 16 L 10 25 H -13 L -22 15 Z" />'
            '<path class="wing left" d="M -20 -17 L -7 -23 H -2 L -8 -4 H -18 Z" />'
            '<path class="wing right" d="M 10 -23 L 22 -14 L 17 0 L 7 -5 Z" />'
            '<circle class="rtk" cx="-6" cy="7" r="8" />'
            '<rect class="panel" x="3" y="2" width="13" height="10" rx="3" />'
            '<rect class="stop" x="9" y="7" width="9" height="13" rx="4" />'
            '<circle class="knob" cx="10" cy="-7" r="5" />'
            '<rect class="camera" x="-16" y="8" width="6" height="7" rx="2" />'
            '<path class="groove" d="M -14 -7 H -6 M 1 -11 H 10 M -2 18 H 6" />'
            '</g>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_WIDTH}" '
        f'height="{SVG_HEIGHT}" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}" role="img">'
        "<style>"
        "svg{background:#050607;font-family:Inter,Segoe UI,Arial,sans-serif}"
        ".grid{stroke:#202624;stroke-width:1;opacity:.45}"
        ".zone-shadow{fill:#000;opacity:.08;transform:translate(3px,5px)}"
        ".zone{fill:#008b3a;stroke:#ff6045;stroke-width:8;stroke-linejoin:round;stroke-linecap:round}"
        ".zone-edge{fill:none;stroke:#1fb95b;stroke-width:3;stroke-linejoin:round;stroke-linecap:round;opacity:.9}"
        ".hole-shadow{fill:#000;opacity:.06;transform:translate(3px,5px)}"
        ".hole{fill:#e4e7ed;stroke:#ff6045;stroke-width:6;stroke-linejoin:round;stroke-linecap:round}"
        ".hole-edge{fill:none;stroke:#cfd4dc;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}"
        ".exclusion-shadow{fill:#000;opacity:.1;transform:translate(2px,4px)}"
        ".exclusion{fill:#b66b36;stroke:#b66b36;stroke-width:4;opacity:.96}"
        ".mowed{fill:none;stroke:#005726;stroke-linecap:round;stroke-linejoin:round}"
        ".station-shadow{fill:#000;opacity:.14}.station circle{fill:#70380f}.station path{fill:#fff}"
        ".robot-shadow{fill:#000;opacity:.35}.robot .track{fill:#161b1f;stroke:#4b5563;stroke-width:2;stroke-linejoin:round}.robot .body{fill:#33383d;stroke:#111820;stroke-width:3;stroke-linejoin:round}.robot .wing{fill:#f47b20;stroke:#ffae55;stroke-width:1.5;stroke-linejoin:round}.robot .rtk{fill:#f8fafc;stroke:#e5e7eb;stroke-width:2}.robot .panel{fill:#22272e;stroke:#111820;stroke-width:1.5}.robot .stop{fill:#f43f4f;stroke:#991b1b;stroke-width:1.5}.robot .knob{fill:#f47b20;stroke:#fff7ed;stroke-width:1.5}.robot .camera{fill:#1f2429;stroke:#89929d;stroke-width:1}.robot .groove{fill:none;stroke:#171b20;stroke-width:2;stroke-linecap:round;opacity:.7}"
        "</style>"
        '<defs><pattern id="grid" width="48" height="48" patternUnits="userSpaceOnUse">'
        '<path class="grid" d="M 48 0 L 0 0 0 48" /></pattern></defs>'
        f'<defs>{clip_def}</defs>'
        f'<rect width="{SVG_WIDTH}" height="{SVG_HEIGHT}" fill="url(#grid)" />'
        f'{"".join(body)}'
        "</svg>"
    ), round(swath_width_px, 2)


def _placeholder_svg(message: str) -> str:
    """Return SVG placeholder."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_WIDTH}" '
        f'height="{SVG_HEIGHT}" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}">'
        "<rect width='100%' height='100%' fill='#101412'/>"
        f"<text x='50%' y='50%' fill='#f8faf4' font-size='28' "
        f"font-family='Inter,Segoe UI,Arial,sans-serif' text-anchor='middle'>{escape(message)}</text>"
        "</svg>"
    )
