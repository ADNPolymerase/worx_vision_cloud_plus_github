# Changelog

## Unreleased

- Changed the Vision edge-cut button to send a zero-minute one-time schedule with edge cutting enabled instead of `cmd:101`, because firmware 3.46.x can continue into full mowing after `cmd:101`.
- Added one-time mowing controls and service with runtime, edge-cut and optional RTK zone selection.
- Added a robot-lifted binary sensor based on Worx Cloud `lifted` and `upside down` error states.
- Removed the unavailable schedule edge procedure entities.
- Removed the radio link validation pending binary sensor.
- Removed the duplicate read-only lawn perimeter sensor.
- Added Polish state labels for the status and mowing-readiness sensors.

## 1.0.4

- Fixed the edge cutting button for Vision mowers whose Worx Cloud schedule payload does not expose the derived edge-cut capability.
- The integration now sends the border-cut MQTT command directly instead of relying on `pyworxcloud.edgecut()`, which could silently do nothing.

## 1.0.3

- Removed the `auto_schedule` switch completely.
- Added entity-registry cleanup for the removed automatic schedule switch.

## 1.0.2

- Removed the unreliable battery charging binary sensor.
- Removed the unreliable distance covered sensor.
- Added entity-registry cleanup for both removed entities.

## 1.0.1

- Fixed mower command refresh for `pyworxcloud==6.3.6` by removing an unsupported `timeout` argument from device update requests.
- Restored button and mower commands that previously failed with `WorxCloud.update() got an unexpected keyword argument 'timeout'`.

## 1.0.0

- Promoted the integration to the first stable `1.0.0` release.
- Added a native Home Assistant firmware update entity with release notes and OTA install support when exposed by Worx Cloud.
- Added configurable rain delay, schedule time-extension, lawn area and lawn perimeter number entities.
- Added switches for firmware auto update, mower lock, native schedule and Worx auto schedule.
- Added cloud/MQTT diagnostics, mowing-readiness status, API capabilities and push notification state sensors.
- Added extended mowing statistics: lawn area/perimeter, distance covered, efficiency and mower time at home, charging and in error.
- Added maintenance tracking for blade runtime and battery cycles, including reset timestamps and a battery cycle reset button.
- Added recent RTK trail storage, a diagnostic trail sensor and a trail overlay on the RTK map camera.

## 0.3.5

- Added an on-demand edge cutting button that starts the mower in border-only cutting mode.

## 0.3.4

- Added root-level `icon.png` and `logo.png` compatibility files so HACS can resolve the repository image in places that do not read `brand/icon.png`.
- Updated the release workflow to publish icon-only fixes.

## 0.3.3

- Added Home Assistant switches for Smart edge cutting, Save the hedgehogs and schedule edge procedure.
- Renamed the Polish rain binary sensor label to `Czujnik opadów deszczu`.
- Removed the standard total driven distance sensor because the Worx payload does not update it reliably.
- Added entity-registry cleanup for the removed total driven distance sensor.
- Added integration-root icon and logo files so Home Assistant and HACS update cards can resolve the brand image more reliably.

## 0.3.2

- Moved Smart mowing schedule blueprint to a separate automation repository.
- Updated documentation to link to the separated automation repository.

## 0.3.1

- Added Smart mowing schedule Home Assistant blueprint.
- Added My Home Assistant import button for the blueprint.
- Added blueprint setup documentation and optional helper package example.

## 0.3.0

- Added diagnostic entities for Smart edge cutting, Save the hedgehogs and schedule edge procedure API fields.
- Added button to reset blade runtime after blade replacement.

## 0.2.2

- Added root-level HACS brand assets so the repository icon appears in HACS.

## 0.2.1

- Updated integration brand icon and logo assets.

## 0.2.0

- Added disabled-by-default RTK address sensor using OpenStreetMap Nominatim reverse geocoding.
- Added 24-hour address lookup cache, rounded-coordinate lookups and a one-request-per-second geocoding throttle.

## 0.1.0

- Initial public release.
- Added Home Assistant `lawn_mower` support.
- Added useful sensors and binary sensors.
- Added mowing schedule sensor and calendar entity.
- Added RTK map camera rendered from Worx map API data.
- Added RTK position `device_tracker`.
- Added daily progress, remaining progress and mowed area sensors when available.
- Added Polish and English translations.
- Added integration icon and Smart Service attribution.
