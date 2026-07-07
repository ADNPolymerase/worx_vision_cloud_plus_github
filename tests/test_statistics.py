"""Tests for cumulative-counter and local mowing-time tracking."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "worx_vision_cloud"
    / "statistics.py"
)
SPEC = importlib.util.spec_from_file_location("worx_statistics_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
STATISTICS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = STATISTICS
SPEC.loader.exec_module(STATISTICS)
DailyStatisticsTracker = STATISTICS.DailyStatisticsTracker


class DailyStatisticsTrackerTests(unittest.TestCase):
    """Exercise daily rollover and counter-reset behavior."""

    def test_daily_area_uses_cumulative_counter_delta(self) -> None:
        tracker = DailyStatisticsTracker()
        now = datetime(2026, 7, 6, 8, tzinfo=UTC)
        day = date(2026, 7, 6)

        tracker.update(
            "mower",
            area_total=6640,
            mowing_active=False,
            now_utc=now,
            local_day=day,
            local_midnight_utc=now.replace(hour=0),
        )
        self.assertEqual(tracker.area_mowed_today("mower", day), 0)

        tracker.update(
            "mower",
            area_total=6817.61,
            mowing_active=False,
            now_utc=now + timedelta(hours=2),
            local_day=day,
            local_midnight_utc=now.replace(hour=0),
        )
        self.assertEqual(tracker.area_mowed_today("mower", day), 177.61)

    def test_next_day_starts_from_last_observed_total(self) -> None:
        tracker = DailyStatisticsTracker()
        first_day = date(2026, 7, 6)
        next_day = date(2026, 7, 7)
        now = datetime(2026, 7, 6, 23, 55, tzinfo=UTC)

        tracker.update(
            "mower",
            area_total=1000,
            mowing_active=False,
            now_utc=now,
            local_day=first_day,
            local_midnight_utc=now.replace(hour=0, minute=0),
        )
        tracker.update(
            "mower",
            area_total=1025,
            mowing_active=False,
            now_utc=now + timedelta(minutes=10),
            local_day=next_day,
            local_midnight_utc=datetime(2026, 7, 7, tzinfo=UTC),
        )
        self.assertEqual(tracker.area_mowed_today("mower", next_day), 25)

    def test_long_gap_and_counter_reset_do_not_create_fake_area(self) -> None:
        tracker = DailyStatisticsTracker()
        start = datetime(2026, 7, 1, 8, tzinfo=UTC)
        tracker.update(
            "mower",
            area_total=5000,
            mowing_active=False,
            now_utc=start,
            local_day=start.date(),
            local_midnight_utc=start.replace(hour=0),
        )
        later = start + timedelta(days=3)
        tracker.update(
            "mower",
            area_total=5400,
            mowing_active=False,
            now_utc=later,
            local_day=later.date(),
            local_midnight_utc=later.replace(hour=0),
        )
        self.assertEqual(tracker.area_mowed_today("mower", later.date()), 0)

        tracker.update(
            "mower",
            area_total=10,
            mowing_active=False,
            now_utc=later + timedelta(hours=1),
            local_day=later.date(),
            local_midnight_utc=later.replace(hour=0),
        )
        self.assertEqual(tracker.area_mowed_today("mower", later.date()), 0)

    def test_mowing_time_tracks_active_period_across_midnight(self) -> None:
        tracker = DailyStatisticsTracker()
        start = datetime(2026, 7, 6, 23, 59, tzinfo=UTC)
        tracker.update(
            "mower",
            area_total=None,
            mowing_active=True,
            now_utc=start,
            local_day=start.date(),
            local_midnight_utc=start.replace(hour=0, minute=0),
        )

        after_midnight = start + timedelta(minutes=2)
        tracker.update(
            "mower",
            area_total=None,
            mowing_active=True,
            now_utc=after_midnight,
            local_day=after_midnight.date(),
            local_midnight_utc=datetime(2026, 7, 7, tzinfo=UTC),
        )
        tracker.update(
            "mower",
            area_total=None,
            mowing_active=False,
            now_utc=after_midnight + timedelta(minutes=1),
            local_day=after_midnight.date(),
            local_midnight_utc=datetime(2026, 7, 7, tzinfo=UTC),
        )
        self.assertEqual(
            tracker.mowing_minutes_today(
                "mower",
                after_midnight + timedelta(minutes=1),
                after_midnight.date(),
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
