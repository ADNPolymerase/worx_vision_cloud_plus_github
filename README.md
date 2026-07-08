<p align="center">
  <img src="https://raw.githubusercontent.com/ADNPolymerase/ha-landroid-vision/main/logo.png" alt="Worx Landroid Vision PLUS" width="380">
</p>

# Worx Landroid Vision PLUS

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/ADNPolymerase/ha-landroid-vision)
[![GitHub Release](https://badgen.net/github/release/ADNPolymerase/ha-landroid-vision)](https://github.com/ADNPolymerase/ha-landroid-vision/releases)
[![Validate](https://github.com/ADNPolymerase/ha-landroid-vision/actions/workflows/validate.yml/badge.svg)](https://github.com/ADNPolymerase/ha-landroid-vision/actions/workflows/validate.yml)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/ADNPolymerase/ha-landroid-vision/blob/main/LICENSE)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow.svg?logo=buy-me-a-coffee)](https://buymeacoffee.com/adnpolymerase)

<a href="https://buymeacoffee.com/adnpolymerase" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-orange.png" alt="Buy Me A Coffee" height="60"></a>
<a href="https://adnpolymerase.github.io/HA/" target="_blank"><img src="https://raw.githubusercontent.com/ADNPolymerase/HA/main/assets/site-button.svg" alt="Link to my github.io for my other projects" height="60"></a>

Custom Home Assistant integration for Worx Landroid Vision / Vision Cloud / RTK mowers.

This integration is built on top of the community `pyworxcloud` library and adds a cleaner Home Assistant entity layer for Vision mowers: mower controls, useful sensors, diagnostics, schedule calendar, RTK map rendering and live-ish robot position tracking.

## Features

- Native `lawn_mower` entity: start, pause, dock, one-time mowing (runtime, edge cutting, RTK zones) and on-demand edge cutting.
- Mower controls: firmware auto-update, lock, native schedule, smart edge cutting, save the hedgehogs, party mode, and (when your mower reports the matching hardware module) ACS, off limits, cutting height, torque and border distance.
- Daily area/progress tracking persisted per mower in Home Assistant storage, immune to cloud counter resets and multi-day gaps, plus a locally computed estimate that keeps moving even when Worx's own stats go stale.
- Schedule sensor and calendar, next mowing time, RTK map camera with mowed-area trail, RTK robot position and reverse-geocoded address (opt-in).
- Battery, status, error, connectivity, maintenance and mowing-readiness sensors, with Home Assistant Repairs alerts for blade/battery service and a restart button.
- Download diagnostics with automatic redaction of coordinates, addresses and identifiers.
- Translated into 10 languages (English, Polish, French, German, Dutch, Spanish, Italian, Swedish, Norwegian, Danish), including entity states, schedule and calendar.

## Installation With HACS

1. Open HACS.
2. Add this repository as a custom repository.
3. Select category `Integration`.
4. Install Worx Landroid Vision PLUS.
5. Restart Home Assistant.
6. Go to `Settings > Devices & services > Add integration`.
7. Search for `Worx Landroid Vision PLUS`.

At setup, sign in with the same e-mail and password as in your mower app and pick your brand cloud: `worx`, `kress` or `landxcape`.

## Manual Installation

Copy this directory:

```text
custom_components/worx_vision_cloud
```

to your Home Assistant config directory:

```text
/config/custom_components/worx_vision_cloud
```

Then restart Home Assistant and add the integration from `Settings > Devices & services`.

## Entities

The exact entity list depends on what your mower reports. Typical entities include:

- `lawn_mower` mower control
- `button` refresh, reset blade runtime, reset battery cycles and start edge cutting
- `calendar` mowing schedule
- `camera` RTK map
- `device_tracker` RTK robot position
- `sensor` battery, status, error, readiness, cloud connection, RSSI, schedule, next schedule, RTK map, RTK trail, daily progress, remaining progress, today and total mowed area, estimated daily area and progress, mowing time today, lawn area, runtime, efficiency, cloud statistics freshness and maintenance values (home time and charging time are included but disabled by default, see below)
- `binary_sensor` online, IoT/MQTT registration, rain, robot lifted and pause mode
- `switch` firmware auto update, mower lock, native schedule, smart edge cutting, save the hedgehogs, party mode, off limits and ACS (the last two only when your mower reports the matching module)
- `number` rain delay, schedule time extension, lawn area, lawn perimeter, cutting height and torque (the last two only when your mower reports the matching module; torque is disabled by default)
- `update` firmware version, release notes and OTA install when supported

See [docs/entities.md](docs/entities.md) for a more detailed list.

## RTK Map & Address

For compatible Vision Cloud / RTK mowers, a camera entity renders the mowing boundary, excluded areas, station and the current day's mowing trail as SVG from the private Worx map endpoint — not a video stream, it updates when new data arrives. The trail covers the full local day (like the Worx app) rather than a fixed time window: it resets at local midnight, is persisted so a Home Assistant restart mid-day doesn't lose it, and keeps showing the last known map if a fetch briefly fails.

An `RTK address` sensor (disabled by default) can reverse-geocode the mower's rounded position with OpenStreetMap Nominatim, cached 24h. It's opt-in because RTK coordinates can reveal a home or garden location.

RTK maps and address lookups can contain precise garden geometry and coordinates — don't publish debug dumps, storage files, tokens or screenshots showing exact locations. See [SECURITY.md](SECURITY.md).

## Mowed area

Mowing figures are covered area (surface the blades pass over), not unique lawn area — overlapping passes mean Today/Total mowed area can legitimately exceed your lawn size, and Daily progress reaches 100% once covered area matches it. The daily baseline is kept in Home Assistant storage per mower, so it survives restarts and entity renames, and correctly handles cloud counter resets and multi-day gaps.

## Entity naming

The `lawn_mower` entity has no name of its own — it displays exactly the device name (e.g. "Vision Cloud" rather than "Vision Cloud Mower"), for readability and for compatibility with third-party cards such as [landroid-card](https://github.com/Barma-lej/landroid-card) that strip the device name from every other entity's label using this one as the prefix.

As the device's primary entity, its availability doesn't depend on the mower's own online status: a wifi/cloud connectivity blip keeps showing the last known status and attributes instead of going unavailable (which would otherwise blank cards like landroid-card that hide their body when their main entity is unavailable). Only commands (start/pause/dock) are blocked while genuinely offline, with a clear error.

## Limitations

The Worx / Positec cloud API is not officially public. Some endpoints used here are reverse-engineered and can change without notice. This is a best-effort custom integration, not official Worx software.

- Off limits and ACS entities can show up as `unavailable` even on a mower model that supports the feature. Availability is based on pyworxcloud detecting the matching module (`DF` for off limits, `US` for ACS) in the mower's live data, and for off limits specifically that module only appears once at least one off-limit zone has been configured in the Worx app at least once. This is a limitation of the underlying API data (the same behavior exists in the community `landroid_cloud` integration), not a bug in this integration.
- Mower home time and charging time can permanently read `0` for some accounts even though mower work time updates normally, because the Worx API itself doesn't populate those two fields for every model. That's why both sensors are disabled by default; enable them if your account happens to report real values.

## Credits

- Uses [`pyworxcloud`](https://github.com/MTrab/pyworxcloud).
- Integration originally prepared by Smart Service.
