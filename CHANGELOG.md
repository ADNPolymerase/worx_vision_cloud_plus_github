# Changelog

## Unreleased

- Fixed the RTK map camera and sensor going unavailable/unknown again after
  several hours, even without any restart. The 1.6.2 fix compared the
  coordinator's "previous" device object against the newly pushed one to
  restore a missing rtk block, but pyworxcloud reuses and mutates a single
  DeviceHandler instance per mower in place, so those two references were
  actually the same object: there was never a real "before" snapshot to
  restore from once pyworxcloud itself had already overwritten the rtk
  block with a partial cfg push. Replaced with an independent last-known-id
  cache on the coordinator, decoupled entirely from pyworxcloud's own
  object graph, used by both the camera and the RTK map sensor.

## 1.6.2 - 2026-07-08

- Fixed WorxMowingTimeTodaySensor being registered twice in async_setup_entry, which logged "Platform worx_vision_cloud does not generate unique IDs" and silently dropped the duplicate at startup.
- The RTK trail shown on the map camera now covers the full local day like the Worx app, instead of a fixed 6-hour window capped at 300 in-memory points: it resets at local midnight instead, is persisted so a Home Assistant restart mid-day doesn't lose the morning's trail, and a generous per-day point cap replaces the old rolling window so a long mowing day no longer silently evicts its own earlier segments.
- Fixed the primary lawn_mower entity (and third-party cards built on it, such as landroid-card) going fully unavailable/blank whenever the mower lost wifi. Availability no longer depends on the mower's own online flag, so the last known status/activity keeps showing during a connectivity blip instead of the whole card collapsing to a bare "not available" placeholder. `online` stays available as a state attribute, and start/pause/dock commands sent while offline now fail with a clear error instead of being silently blocked by Home Assistant.
- Fixed the RTK map camera going unavailable, and the lawn area, daily
  progress, remaining progress and estimated daily progress sensors going
  unknown, whenever Worx sent a partial MQTT config update that momentarily
  omitted the RTK block. The coordinator now preserves the last known RTK
  map id and zones across such partial updates instead of losing them, the
  same way it already preserved REST-derived data.
- Fixed the map camera rendering a blank image on a fetch failure instead
  of keeping the last successfully rendered map.
- Fixed a related bug where a missing RTK map id was converted to the
  literal string "None" before being sent to the coordinator, which passed
  validation and fired a needless request against the private Worx map API
  (visible in logs as repeated 404s).

## 1.6.1 - 2026-07-07

- Rebranding release, no code changes: the official Landroid Vision logo is
  now used everywhere (README, HACS, and the Home Assistant UI through the
  bundled brand/ images served locally since HA 2026.3), with icons at the
  canonical 256/512 sizes.
- The integration title shown in the Home Assistant UI is now Worx Landroid
  Vision PLUS in all 10 languages, new pairings are titled Worx Landroid
  Vision (account e-mail), and existing entry titles are migrated
  automatically at startup.

## 1.6.0 - 2026-07-07

- Renamed the repository to `ADNPolymerase/ha-landroid-vision` and the
  integration display name to Worx Landroid Vision PLUS. GitHub redirects
  the old repository name and HACS tracks installations by repository ID,
  so existing installs are unaffected; the `worx_vision_cloud` domain and
  all entity IDs are unchanged.
- Added Home Assistant Repairs integration: when the blade cutting time or
  battery charge cycles exceed the maintenance thresholds (12 h / 500
  cycles, the same ones the maintenance sensor uses), an actionable issue
  appears in Settings > Repairs and clears automatically after the matching
  reset button is pressed. Mowers disabled in the device registry never
  raise repairs.
- Added a Border distance select (50/100/150/200 mm) for Vision mowers.
  The Worx API accepts writing this setting but never reports it back, so
  the entity is optimistic: it shows the last value set through Home
  Assistant (persisted across restarts) and stays unknown until used once.
- Fixed the Next schedule sensor going unknown on Vision protocol 1 mowers:
  pyworxcloud reports `schedules["active"]` as False on these models even
  while the weekly schedule genuinely runs, so the inactive flag now only
  suppresses the sensor when the library offers no future start either. The
  library timestamp parser also accepts offset-aware values
  (e.g. `2026-07-08 08:00:00+02:00`) and datetime objects, both observed on
  real devices.
- Added a Restart mower button (diagnostic) to reboot the mower baseboard
  remotely when it is stuck.
- Added an MQTT connected diagnostic binary sensor exposing the live push
  connection state, complementing the registration-only MQTT registered
  sensor.
- Moved the daily area/progress baselines and the local mowing-time counter
  from per-entity restored state into a coordinator-level tracker persisted in
  Home Assistant storage (synced back from upstream SmartServicePL 1.2.0):
  every daily sensor now shares one baseline per mower, survives entity
  renames, and handles cloud counter resets and multi-day gaps without
  attributing several days of mowing to today.
- Added a Mowing time today sensor exposing the locally observed mowing
  minutes the estimated sensors are computed from.
- Added a Cloud statistics updated diagnostic timestamp showing when the
  cumulative Worx REST statistics were last fetched.
- Mowing efficiency now prefers blade-active time over total mower runtime
  (which includes driving and idling), improving the estimated daily figures.
- Next schedule now returns nothing while the native schedule is disabled or
  party mode is active, ignores stale library values, and looks up to 14 days
  ahead (synced from upstream 1.2.0).
- Added Download diagnostics support with automatic redaction of coordinates,
  addresses and account/device identifiers, for safe GitHub issue reports.
- Device names no longer repeat the account e-mail, the account e-mail is no
  longer suggested as a Home Assistant area (existing e-mail areas are
  detached automatically), and entity IDs that inherited the e-mail prefix
  are migrated (synced from upstream 1.2.0).
- Entities removed by the 1.5.0 consolidation are now cleaned from the entity
  registry automatically instead of lingering as restored orphans.
- Removed the deprecated device-tracker battery_level property override while
  keeping the value as a state attribute, preventing a Home Assistant 2027.7
  break (synced from upstream 1.2.0).
- Passed the config entry explicitly to the coordinator for Home Assistant
  2026.8 compatibility (synced from upstream 1.2.0).
- Added unit tests for the daily statistics tracker and next-schedule
  calculation, now run by the validation workflow.
- The release workflow is manual-only so code pushes can never silently
  re-tag the current stable release.

## 1.5.0 - 2026-07-06

- Added a party mode switch (previously only a read-only sensor).
- Added ACS, off limits, cutting height and torque controls, gated on pyworxcloud's live per-device capability detection instead of a manual model list, so they only appear when your mower reports the matching hardware module.
- Removed entities that duplicated the same value as both a switch and a read-only binary sensor: lock, smart edge cutting, save the hedgehogs and party mode. Also removed a duplicate rain delay sensor that repeated the existing rain delay number.
- Fixed rain delay, cutting height and torque numbers showing a spurious decimal (e.g. `180.0` instead of `180`).
- Disabled the torque number and the mower home time / charging time sensors by default: torque is an advanced setting most users won't touch, and home/charging time can permanently read `0` on accounts where the Worx API doesn't populate those two fields (mower work time is unaffected and still updates normally).
- Off limits and ACS entities can legitimately show as `unavailable` on a supported mower until the corresponding module shows up in the mower's live data; for off limits this can require configuring at least one off-limit zone once in the Worx app. This is a data limitation of the underlying API, not a bug (the community `landroid_cloud` integration has the same behavior).

## 1.0.11 - 2026-06-18

- Allowed the one-time mowing service to accept `runtime: 0`, so automations can explicitly start an edge-only pass after normal mowing.
- Updated the Home Assistant service description to show `runtime: 0` as the supported edge-only mode.
- Mapped Vision Cloud `runtime: 0` with `edge_cut: true` to the dedicated edge-cut command (`cmd: 101`), while keeping normal one-time mowing on the app-like `cmd: 10` payload.

## 1.0.10 - 2026-06-17

- Changed Vision one-time mowing with edge cutting back to the app-like one-time mowing payload (`cmd: 10` with `cfg.cut.b: 1`) so the mower performs normal mowing first and edge cutting at the end instead of doing an edge-only run.

## 1.0.9 - 2026-06-13

- Changed Vision one-time mowing with edge cutting and no selected zones to use the firmware command that starts edge cutting followed by the normal mowing cycle.
- Kept the standalone edge-cut button edge-only by continuing to use the zero-minute one-time mowing command for that button.

## 1.0.8 - 2026-06-12

- Added the official HACS validation workflow required for default HACS repository submissions.
- Updated HACS repository metadata so the integration passes the current HACS Action checks.

## 1.0.7 - 2026-06-12

- Removed RTK-based status overriding so mower state always follows the raw Worx Cloud status.
- Kept RTK station proximity as diagnostic attributes for automations without changing the displayed mower status.
- Allowed the one-time mowing service to run for 1 minute so automations can send a short status-refresh command when Worx Cloud gets stuck.
- Increased RTK address reverse-geocoding precision to 7 decimal places and kept the address based only on RTK coordinates.

## 1.0.6 - 2026-06-11

- Improved the RTK map trail so recent mower movement is rendered as a darker mowed grass swath instead of a thin GPS line.
- Calculated the mowed swath width from the mower model cutting width and the current map scale; WR308E/WR303E-class mowers use 18 cm.
- Clipped the mowed swath to the lawn contour so it stays inside the mapped grass area.

## 1.0.5 - 2026-06-11

- Added RTK station-based status correction so Home Assistant can show the mower as docked when Worx Cloud is stuck on stale mowing/returning/searching-home states.
- Preserved cached RTK map and product details across MQTT-only push updates so status correction keeps access to the base station marker.
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
