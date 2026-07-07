"""Tests for next-schedule calculation without a Home Assistant install."""

from __future__ import annotations

from datetime import datetime
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest
from zoneinfo import ZoneInfo


HOMEASSISTANT = ModuleType("homeassistant")
HOMEASSISTANT_UTIL = ModuleType("homeassistant.util")
HOMEASSISTANT_UTIL.slugify = lambda value: str(value).lower().replace(" ", "_")
HOMEASSISTANT.util = HOMEASSISTANT_UTIL
sys.modules.setdefault("homeassistant", HOMEASSISTANT)
sys.modules.setdefault("homeassistant.util", HOMEASSISTANT_UTIL)

MODULE_PATH = (
    Path(__file__).parents[1] / "custom_components" / "worx_vision_cloud" / "helpers.py"
)
SPEC = importlib.util.spec_from_file_location("worx_helpers_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
HELPERS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPERS)


class NextScheduleTests(unittest.TestCase):
    """Exercise authoritative and fallback schedule timestamps."""

    def setUp(self) -> None:
        self.timezone = ZoneInfo("Europe/Warsaw")
        self.now = datetime(2026, 7, 6, 8, 0, tzinfo=self.timezone)

    def test_uses_future_library_value(self) -> None:
        device = SimpleNamespace(
            schedules={
                "active": True,
                "next_schedule_start": "2026-07-06 10:00:00",
                "slots": [],
            }
        )
        result = HELPERS.next_schedule_start(device, self.now)
        self.assertEqual(result, datetime(2026, 7, 6, 10, 0, tzinfo=self.timezone))

    def test_stale_library_value_falls_back_to_weekly_slots(self) -> None:
        device = SimpleNamespace(
            schedules={
                "active": True,
                "next_schedule_start": "2026-07-06 07:00:00",
                "slots": [
                    {
                        "day": "tuesday",
                        "start": "09:30",
                    }
                ],
            }
        )
        result = HELPERS.next_schedule_start(device, self.now)
        self.assertEqual(result, datetime(2026, 7, 7, 9, 30, tzinfo=self.timezone))

    def test_disabled_or_paused_schedule_has_no_next_start(self) -> None:
        for schedules in (
            # active=False only blocks when the library offers no future
            # start; here the value is stale, so nothing is derived either.
            {
                "active": False,
                "next_schedule_start": "2026-07-06 07:00:00",
                "slots": [{"day": "tuesday", "start": "09:30"}],
            },
            {
                "active": True,
                "party_mode_enabled": True,
                "next_schedule_start": "2026-07-06 10:00:00",
            },
        ):
            with self.subTest(schedules=schedules):
                self.assertIsNone(
                    HELPERS.next_schedule_start(
                        SimpleNamespace(schedules=schedules),
                        self.now,
                    )
                )

    def test_inactive_flag_does_not_override_future_library_value(self) -> None:
        # Observed on a Vision protocol 1 mower: schedules["active"] is False
        # even though the weekly schedule genuinely runs and pyworxcloud
        # still computes next_schedule_start (offset-aware string format).
        device = SimpleNamespace(
            schedules={
                "active": False,
                "next_schedule_start": "2026-07-08 08:00:00+02:00",
                "slots": [],
            }
        )
        result = HELPERS.next_schedule_start(device, self.now)
        self.assertEqual(
            result,
            datetime(2026, 7, 8, 8, 0, tzinfo=ZoneInfo("Europe/Warsaw")),
        )

    def test_aware_datetime_library_value_is_accepted(self) -> None:
        device = SimpleNamespace(
            schedules={
                "active": True,
                "next_schedule_start": datetime(
                    2026, 7, 6, 10, 0, tzinfo=ZoneInfo("UTC")
                ),
                "slots": [],
            }
        )
        result = HELPERS.next_schedule_start(device, self.now)
        self.assertEqual(
            result, datetime(2026, 7, 6, 12, 0, tzinfo=self.timezone)
        )


if __name__ == "__main__":
    unittest.main()
