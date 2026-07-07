"""Persistent daily statistics derived from cumulative Worx counters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


def _as_float(value: Any) -> float | None:
    """Return a finite non-negative float."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result < 0 or result != result:
        return None
    return result


@dataclass
class DailyAreaRecord:
    """State needed to turn a cumulative area counter into a daily value."""

    day: str
    baseline_total: float
    last_total: float


@dataclass
class DailyMowingRecord:
    """Locally observed blade-active time for one day."""

    day: str
    seconds: float = 0.0


class DailyStatisticsTracker:
    """Track daily area and mowing time without trusting counter labels.

    Keyed by mower serial number and persisted through Home Assistant
    storage, so the daily baselines survive restarts, entity renames and
    entity re-creation, and every daily sensor shares the same baseline.
    """

    def __init__(self, stored: dict[str, Any] | None = None) -> None:
        """Restore valid records from storage."""
        self._area: dict[str, DailyAreaRecord] = {}
        self._mowing: dict[str, DailyMowingRecord] = {}
        self._active_since: dict[str, datetime] = {}

        stored = stored if isinstance(stored, dict) else {}
        for serial, raw in (stored.get("area") or {}).items():
            if not isinstance(raw, dict):
                continue
            baseline = _as_float(raw.get("baseline_total"))
            last_total = _as_float(raw.get("last_total"))
            day = raw.get("day")
            if isinstance(day, str) and baseline is not None and last_total is not None:
                self._area[str(serial)] = DailyAreaRecord(
                    day=day,
                    baseline_total=baseline,
                    last_total=last_total,
                )

        for serial, raw in (stored.get("mowing") or {}).items():
            if not isinstance(raw, dict):
                continue
            seconds = _as_float(raw.get("seconds"))
            day = raw.get("day")
            if isinstance(day, str) and seconds is not None:
                self._mowing[str(serial)] = DailyMowingRecord(
                    day=day,
                    seconds=seconds,
                )

    def as_dict(self) -> dict[str, Any]:
        """Return the serializable state used by Home Assistant storage."""
        return {
            "area": {
                serial: {
                    "day": record.day,
                    "baseline_total": record.baseline_total,
                    "last_total": record.last_total,
                }
                for serial, record in self._area.items()
            },
            "mowing": {
                serial: {
                    "day": record.day,
                    "seconds": record.seconds,
                }
                for serial, record in self._mowing.items()
            },
        }

    def update(
        self,
        serial: str,
        *,
        area_total: Any,
        mowing_active: bool,
        now_utc: datetime,
        local_day: date,
        local_midnight_utc: datetime,
    ) -> bool:
        """Update one mower and return whether persistent data changed."""
        return self._update_area(serial, area_total, local_day) | self._update_mowing(
            serial,
            mowing_active,
            now_utc,
            local_day,
            local_midnight_utc,
        )

    def _update_area(self, serial: str, value: Any, local_day: date) -> bool:
        total = _as_float(value)
        if total is None:
            return False

        day_text = local_day.isoformat()
        record = self._area.get(serial)
        if record is None:
            self._area[serial] = DailyAreaRecord(day_text, total, total)
            return True

        changed = False
        if record.day != day_text:
            try:
                previous_day = date.fromisoformat(record.day)
            except ValueError:
                previous_day = None

            # When only one day elapsed, retain the last counter observed before
            # midnight. For longer gaps, do not attribute several days to today.
            if (
                previous_day is not None
                and (local_day - previous_day).days == 1
                and total >= record.last_total
            ):
                record.baseline_total = record.last_total
            else:
                record.baseline_total = total
            record.day = day_text
            changed = True

        # A lower value means the cloud counter was reset or replaced.
        if total < record.baseline_total or total < record.last_total:
            record.baseline_total = total
            changed = True

        if total != record.last_total:
            record.last_total = total
            changed = True
        return changed

    def _update_mowing(
        self,
        serial: str,
        active: bool,
        now_utc: datetime,
        local_day: date,
        local_midnight_utc: datetime,
    ) -> bool:
        day_text = local_day.isoformat()
        record = self._mowing.get(serial)
        active_since = self._active_since.get(serial)
        changed = False

        if record is None or record.day != day_text:
            seconds = 0.0
            if active and active_since is not None:
                # A streak crossing midnight only credits today with the part
                # mowed after local midnight.
                start = max(active_since, local_midnight_utc)
                seconds = max(0.0, (now_utc - start).total_seconds())
            self._mowing[serial] = DailyMowingRecord(day_text, seconds)
            record = self._mowing[serial]
            self._active_since.pop(serial, None)
            active_since = None
            changed = True

        if active:
            if active_since is not None:
                elapsed = max(0.0, (now_utc - active_since).total_seconds())
                if elapsed:
                    record.seconds += elapsed
                    changed = True
            self._active_since[serial] = now_utc
        elif active_since is not None:
            elapsed = max(0.0, (now_utc - active_since).total_seconds())
            if elapsed:
                record.seconds += elapsed
                changed = True
            self._active_since.pop(serial, None)

        return changed

    def area_mowed_today(self, serial: str, local_day: date) -> float | None:
        """Return the cumulative-counter delta for the requested local day."""
        record = self._area.get(serial)
        if record is None:
            return None
        if record.day != local_day.isoformat():
            return 0.0
        return round(max(0.0, record.last_total - record.baseline_total), 2)

    def area_details(self, serial: str) -> dict[str, Any]:
        """Return diagnostic details for a daily-area calculation."""
        record = self._area.get(serial)
        if record is None:
            return {}
        return {
            "baseline_date": record.day,
            "baseline_total": record.baseline_total,
            "area_mowed_total": record.last_total,
        }

    def mowing_minutes_today(
        self, serial: str, now_utc: datetime, local_day: date
    ) -> float:
        """Return locally observed blade-active minutes for one day."""
        record = self._mowing.get(serial)
        if record is None or record.day != local_day.isoformat():
            return 0.0

        seconds = record.seconds
        active_since = self._active_since.get(serial)
        if active_since is not None:
            seconds += max(0.0, (now_utc - active_since).total_seconds())
        return round(seconds / 60, 2)
