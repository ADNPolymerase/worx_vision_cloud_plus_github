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

## Highlights

- Estimated area mowed today sensor, computed locally to work around REST API staleness
- Estimated daily mowing progress sensor
- Periodic device refresh for up to date stats
- Next schedule now uses pyworxcloud's own computed value
- Schedule day names and calendar events localized to the UI language
- Last update sensor throttled to once per 24h instead of on every push
- Primary mower entity follows the Home Assistant naming convention (no redundant name), improving compatibility with third-party cards such as landroid-card
- Non-deprecated device_tracker imports
- 8 additional languages: French, German, Dutch, Spanish, Italian, Swedish, Norwegian and Danish, on top of the original English and Polish
- Party mode switch (previously only exposed as a read-only sensor)
- ACS, off limits, cutting height and torque controls, gated on pyworxcloud's live per-device capability detection rather than a manual model list, so they only show up when your mower actually reports the matching hardware module
- Removed duplicate entities that exposed the same value twice as both a switch and a read-only sensor (lock, smart edge cutting, save the hedgehogs, party mode) and a duplicate rain delay sensor next to the existing rain delay number
- Mower home time and charging time sensors are now disabled by default: for many accounts the Worx API reports them as a permanent 0 (unlike mower work time, which does update), so they're kept as opt-in diagnostics rather than shown by default
- Daily area/progress and mowing-time tracking persisted in Home Assistant storage at the coordinator level: one shared baseline per mower that survives restarts and entity renames, with proper handling of cloud counter resets, multi-day gaps and mowing across midnight
- Mowing time today sensor (locally observed, independent of Worx's sporadic work-time statistics) and a Cloud statistics updated diagnostic timestamp
- Download diagnostics support with automatic redaction of coordinates, addresses and account/device identifiers, so issue reports are safe to attach
- Home Assistant Repairs integration: actionable alerts in Settings > Repairs when blade cutting time or battery charge cycles exceed the maintenance thresholds, cleared automatically after the matching reset button is pressed
- Border distance select (50/100/150/200 mm) for Vision mowers; the Worx API is write-only here, so the entity remembers the last value set through Home Assistant
- Restart mower button (reboots the mower baseboard remotely when it is stuck) and a live MQTT connected diagnostic sensor

## Features

- Native Home Assistant `lawn_mower` entity.
- Start, pause and dock commands.
- One-time mowing controls with runtime, edge cutting and optional RTK zone selection.
- On-demand edge cutting button.
- Native firmware `update` entity with OTA install support when Worx exposes it.
- Rain delay, schedule time-extension, lawn area and lawn perimeter number entities.
- Switches for firmware auto update, mower lock and native schedule.
- Battery, status, error and connectivity sensors.
- Useful maintenance, cloud/MQTT diagnostic and mowing-readiness sensors.
- Schedule sensor and Home Assistant calendar entity.
- RTK map camera rendered from the Worx private map API with a recent RTK trail overlay.
- RTK robot position as a `device_tracker`.
- Optional RTK address sensor using OpenStreetMap Nominatim reverse geocoding, disabled by default.
- Switches for Smart edge cutting, Save the hedgehogs and schedule edge procedure.
- Next mowing time sensor, daily and remaining progress, today and total mowed area, lawn area and mowing efficiency sensors when available from the API.
- Translations: Polish, English, French, German, Dutch, Spanish, Italian, Swedish, Norwegian and Danish, including localized entity states, schedule and calendar.
- Optional raw payload entities for debugging, disabled by default.

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

## RTK Map

For compatible Vision Cloud / RTK mowers the integration tries to read the private Worx map endpoint and renders a Home Assistant camera entity as SVG.

The map can include:

- mowing boundary
- excluded areas
- markers and station information when available
- current robot position from RTK payload
- recent RTK trail kept in memory by Home Assistant

The map is not a video stream. It updates when Home Assistant receives new data from Worx Cloud or when the integration refreshes cached API data.

## RTK Address

The integration includes a disabled-by-default `RTK address` sensor. When enabled, it reverse-geocodes the mower's rounded RTK coordinates with OpenStreetMap Nominatim and caches the result for 24 hours.

Enable this entity only if you accept sending approximate mower coordinates to the reverse-geocoding provider. This is intentionally opt-in because RTK coordinates can reveal a home or garden location. Lookups are rounded, cached and throttled to respect the public Nominatim service.

## Privacy

RTK maps and address lookups can contain precise garden geometry and coordinates. Do not publish debug dumps, Home Assistant storage files, access tokens, serial numbers, raw API responses or screenshots showing exact locations.

Before opening an issue, remove private data from logs and screenshots. See [SECURITY.md](SECURITY.md).

## Mowed area

The mower reports its mowing figures as covered area (the surface the blades pass over), not unique lawn area. Because a robot mows with overlapping passes, the Today mowed area and Total area mowed sensors can legitimately exceed your lawn size, and Daily progress reaches 100% once the covered area matches the lawn size. Today mowed area is derived from a local-midnight baseline kept in Home Assistant storage per mower, so it survives restarts and entity renames; cloud counter resets and multi-day offline gaps are detected instead of being misattributed to today.

## Entity naming

The `lawn_mower` entity is the device's primary entity and has no name of its own: its displayed name is exactly the device name (e.g. just "Vision Cloud" instead of "Vision Cloud Mower"). This is both for readability and for compatibility with third-party cards such as [landroid-card](https://github.com/Barma-lej/landroid-card), which strip the device name from every other entity's label using the primary entity's name as the prefix; a redundant word there (like "Mower") previously prevented the prefix from matching.

## Limitations

The Worx / Positec cloud API is not officially public. Some endpoints used here are reverse-engineered and can change without notice. This is a best-effort custom integration, not official Worx software.

- Off limits and ACS entities can show up as `unavailable` even on a mower model that supports the feature. Availability is based on pyworxcloud detecting the matching module (`DF` for off limits, `US` for ACS) in the mower's live data, and for off limits specifically that module only appears once at least one off-limit zone has been configured in the Worx app at least once. This is a limitation of the underlying API data (the same behavior exists in the community `landroid_cloud` integration), not a bug in this integration.
- Mower home time and charging time can permanently read `0` for some accounts even though mower work time updates normally, because the Worx API itself doesn't populate those two fields for every model. That's why both sensors are disabled by default; enable them if your account happens to report real values.

## Credits

- Uses [`pyworxcloud`](https://github.com/MTrab/pyworxcloud).
- Integration originally prepared by Smart Service.
