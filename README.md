<p align="center">
  <img src="logo.png" alt="Worx Vision Cloud PLUS" width="360">
</p>

# Worx Vision Cloud PLUS

Custom Home Assistant integration for Worx Landroid Vision / Vision Cloud / RTK mowers.

This integration is built on top of the community `pyworxcloud` library and adds a cleaner Home Assistant entity layer for Vision mowers: mower controls, useful sensors, diagnostics, schedule calendar, RTK map rendering and live-ish robot position tracking.

Integration prepared by **Smart Service**.

## Support

If this integration helps you, you can support Smart Service:

[Donate via Revolut](https://revolut.me/smartserwis)

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
- Separate smart mowing automation blueprint repository.
- Translations: Polish, English, French, German, Dutch, Spanish, Italian, Swedish, Norwegian and Danish, including localized entity states, schedule and calendar.
- Optional raw payload entities for debugging, disabled by default.

## Installation With HACS

1. Open HACS.
2. Add this repository as a custom repository.
3. Select category `Integration`.
4. Install **Worx Vision Cloud PLUS**.
5. Restart Home Assistant.
6. Go to `Settings > Devices & services > Add integration`.
7. Search for `Worx Vision Cloud PLUS`.

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

## Configuration

Use the same e-mail and password as in the Worx Landroid app.

Supported cloud selector values:

- `worx`
- `kress`
- `landxcape`

Most users should keep SSL verification enabled.

## Entities

The exact entity list depends on what your mower reports. Typical entities include:

- `lawn_mower` mower control
- `button` refresh, reset blade runtime, reset battery cycles and start edge cutting
- `calendar` mowing schedule
- `camera` RTK map
- `device_tracker` RTK robot position
- `sensor` battery, status, error, readiness, cloud connection, RSSI, schedule, next schedule, rain delay, RTK map, RTK trail, daily progress, remaining progress, today and total mowed area, lawn area, runtime, efficiency and maintenance values
- `binary_sensor` online, IoT/MQTT registration, locked, rain, party mode and pause mode
- `switch` firmware auto update, mower lock, native schedule, Smart edge cutting, Save the hedgehogs and schedule edge procedure
- `number` rain delay, schedule time extension, lawn area and lawn perimeter
- `update` firmware version, release notes and OTA install when supported

See [docs/entities.md](docs/entities.md) for a more detailed list.

## Automations

Home Assistant blueprints and automations are maintained in a separate repository:

[SmartServicePL/worx_vision_cloud_plus_automation](https://github.com/SmartServicePL/worx_vision_cloud_plus_automation)

The smart mowing schedule blueprint lives there, together with its setup guide and the **My Home Assistant** import button.

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

The mower reports its mowing figures as **covered area** (the surface the blades pass over), not unique lawn area. Because a robot mows with overlapping passes, the **Today mowed area** and **Total area mowed** sensors can legitimately exceed your lawn size, and **Daily progress** reaches 100% once the covered area matches the lawn size. **Today mowed area** is derived from a local-midnight baseline and is rebuilt after a restart or a counter reset.

## Entity naming

The `lawn_mower` entity is the device's primary entity and has no name of its own: its displayed name is exactly the device name (e.g. just "Vision Cloud" instead of "Vision Cloud Mower"). This is both for readability and for compatibility with third-party cards such as [landroid-card](https://github.com/Barma-lej/landroid-card), which strip the device name from every other entity's label using the primary entity's name as the prefix; a redundant word there (like "Mower") previously prevented the prefix from matching.

## Limitations

The Worx / Positec cloud API is not officially public. Some endpoints used here are reverse-engineered and can change without notice. This is a best-effort custom integration, not official Worx software.

## Credits

- Uses [`pyworxcloud`](https://github.com/MTrab/pyworxcloud).
- Integration prepared by **Smart Service**.
