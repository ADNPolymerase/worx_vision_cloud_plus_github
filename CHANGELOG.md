# Changelog

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
