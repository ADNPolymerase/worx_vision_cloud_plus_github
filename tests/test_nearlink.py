"""Tests for NearLink telemetry parsing without a Home Assistant install."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "worx_vision_cloud"
    / "nearlink.py"
)
SPEC = importlib.util.spec_from_file_location("worx_nearlink_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
NEARLINK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NEARLINK)


class NearLinkTests(unittest.TestCase):
    """Exercise defensive parsing and peer state mapping."""

    def test_dock_connection(self) -> None:
        device = SimpleNamespace(
            raw_dat={
                "conn": "wifi",
                "wifi": {"st": "connected", "er": 0, "rsi": -56},
                "modules": {
                    "NL": {
                        "stat": "ok",
                        "error": 0,
                        "conn": [
                            {
                                "type": 1,
                                "mac": "001122334455",
                                "rssi": -38,
                                "vers": "1.5.1+2",
                                "wifi": {"stat": "connected", "rssi": -56},
                            }
                        ],
                    }
                },
            }
        )

        self.assertEqual(NEARLINK.nearlink_connection_state(device), "dock")
        attrs = NEARLINK.nearlink_attributes(device)
        self.assertEqual(attrs["connection_count"], 1)
        self.assertEqual(attrs["connection_1_type"], 1)
        self.assertEqual(attrs["connection_1_rssi"], -38)
        self.assertEqual(attrs["connection_1_wifi_rssi"], -56)
        self.assertEqual(attrs["robot_wifi_rssi"], -56)

    def test_wa0900_connection(self) -> None:
        device = SimpleNamespace(
            raw_dat={
                "modules": {
                    "NL": {
                        "stat": "ok",
                        "error": 0,
                        "conn": [
                            {
                                "type": 2,
                                "mac": "A1B2C3D4E5F6",
                                "rssi": -69,
                                "vers": "1.5.1+2",
                                "wifi": {"stat": "connected", "rssi": -66},
                            }
                        ],
                    }
                }
            }
        )

        self.assertEqual(NEARLINK.nearlink_connection_state(device), "radiolink_adapter")

    def test_disconnected_when_module_has_no_peers(self) -> None:
        device = SimpleNamespace(raw_dat={"modules": {"NL": {"stat": "ok", "conn": []}}})
        self.assertEqual(NEARLINK.nearlink_connection_state(device), "disconnected")

    def test_unknown_peer_type_is_preserved_as_attribute(self) -> None:
        device = SimpleNamespace(
            raw_dat={"modules": {"NL": {"conn": [{"type": 99, "rssi": -72}]}}}
        )
        self.assertEqual(NEARLINK.nearlink_connection_state(device), "unknown")
        self.assertEqual(NEARLINK.nearlink_attributes(device)["connection_1_type"], 99)

    def test_module_presence_detection(self) -> None:
        self.assertTrue(
            NEARLINK.has_nearlink_module(SimpleNamespace(raw_dat={"modules": {"NL": {}}}))
        )
        for raw_dat in (None, {}, {"modules": []}, {"modules": {"NL": "bad"}}):
            with self.subTest(raw_dat=raw_dat):
                self.assertFalse(
                    NEARLINK.has_nearlink_module(SimpleNamespace(raw_dat=raw_dat))
                )

    def test_missing_or_malformed_module_is_unavailable(self) -> None:
        for raw_dat in (None, {}, {"modules": []}, {"modules": {"NL": "bad"}}):
            with self.subTest(raw_dat=raw_dat):
                device = SimpleNamespace(raw_dat=raw_dat)
                self.assertIsNone(NEARLINK.nearlink_connection_state(device))
                self.assertEqual(NEARLINK.nearlink_connections(device), [])


if __name__ == "__main__":
    unittest.main()
