"""Tests for Provident Energy coordinator."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch

from tests import conftest  # Load mocks if needed

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.provident.api import (
    ProvidentAuthError,
    ProvidentConnError,
)
from custom_components.provident.const import (
    CONF_BASE_URL,
    CONF_SCAN_INTERVAL,
    DEFAULT_BASE_URL,
    DOMAIN,
)
from custom_components.provident.coordinator import (
    ProvidentDataUpdateCoordinator,
    match_series_to_utility,
    normalize_unit,
    parse_interval_points_to_daily_hourly,
    parse_timestamp_to_datetime,
)


class TestProvidentCoordinator(unittest.IsolatedAsyncioTestCase):
    """Test suite for Provident DataUpdateCoordinator."""

    def setUp(self):
        """Set up test environment."""
        self.hass = HomeAssistant()
        self.entry = ConfigEntry(
            entry_id="test_entry_id",
            data={
                "username": "user123",
                "password": "secret_password",
                CONF_BASE_URL: DEFAULT_BASE_URL,
            },
            options={CONF_SCAN_INTERVAL: 30},
        )

    def test_unit_normalization(self):
        """Test unit string normalization."""
        self.assertEqual(normalize_unit("kWh", "Electricity"), "kWh")
        self.assertEqual(normalize_unit("kwh", "Electricity"), "kWh")
        self.assertEqual(normalize_unit("kW.h", "Electricity"), "kWh")
        self.assertEqual(normalize_unit("m3", "Hot Water"), "m³")
        self.assertEqual(normalize_unit("m³", "Hot Water"), "m³")
        self.assertEqual(normalize_unit("gal", "Hot Water"), "gal")
        self.assertEqual(normalize_unit("L", "Cold Water"), "L")
        self.assertEqual(normalize_unit("BTU", "Cooling"), "BTU")
        self.assertEqual(normalize_unit("ton-hr", "Cooling"), "ton-hr")
        self.assertEqual(normalize_unit("custom_unit", "Other"), "custom_unit")

        # Inferred from utility name when unit is None
        self.assertEqual(normalize_unit(None, "Electricity"), "kWh")
        self.assertEqual(normalize_unit(None, "EV"), "kWh")
        self.assertEqual(normalize_unit(None, "EV Charging"), "kWh")
        self.assertEqual(normalize_unit(None, "Hot Water"), "m³")
        self.assertEqual(normalize_unit(None, "Cooling"), "kWh")
        self.assertEqual(normalize_unit(None, "Heating"), "kWh")

    def test_extract_spot_info(self):
        """Test parking spot extraction from utility/meter names."""
        from custom_components.provident.coordinator import extract_spot_info

        clean, spot = extract_spot_info("EV - Spot P2-14")
        self.assertEqual(clean, "EV")
        self.assertEqual(spot, "Spot P2-14")

        clean, spot = extract_spot_info("EV Charging (Spot 42)")
        self.assertEqual(clean, "EV Charging")
        self.assertEqual(spot, "Spot 42")

        clean, spot = extract_spot_info("EV [P1-102]")
        self.assertEqual(clean, "EV")
        self.assertEqual(spot, "P1-102")

        clean, spot = extract_spot_info("Electricity")
        self.assertEqual(clean, "Electricity")
        self.assertIsNone(spot)

    def test_clean_spot_name(self):
        """Test clean_spot_name to ensure no repeating utility/spot prefixes."""
        from custom_components.provident.coordinator import clean_spot_name

        self.assertEqual(clean_spot_name("EV - Spot P2-14", "EV"), "Spot P2-14")
        self.assertEqual(clean_spot_name("EV Spot 1", "EV"), "Spot 1")
        self.assertEqual(clean_spot_name("EV_Spot_1", "EV"), "Spot 1")
        self.assertEqual(clean_spot_name("EV Charger 1", "EV"), "Charger 1")
        self.assertEqual(clean_spot_name("EV (P2-10)", "EV"), "Spot P2-10")
        self.assertEqual(clean_spot_name("P2-14", "EV"), "Spot P2-14")
        self.assertEqual(clean_spot_name("Spot P2-14", "EV"), "Spot P2-14")
        self.assertEqual(clean_spot_name("1", "EV"), "Spot 1")

    @patch("custom_components.provident.coordinator.ProvidentAPIClient")
    async def test_coordinator_data_fetch_success(self, mock_client_cls):
        """Test successful data update coordinator refresh."""
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client

        mock_client.is_authenticated = False
        mock_client.check_login.return_value = False
        mock_client.get_meter_hierarchy.return_value = {"groups": {}, "meters": {}, "meter_list": []}
        mock_client.get_quickgraphs.return_value = []
        mock_client.get_utilities.return_value = [
            "Electricity",
            "EV",
            "Hot Water",
            "Cooling",
            "Heating",
        ]

        # Return mock card data
        async def mock_get_card_data(utility, period=30):
            if utility == "Electricity":
                return {"total": 402.0, "units": "kWh", "data": [10.0, 12.0, 15.0]}
            if utility == "EV":
                return {"total": 116.0, "units": "kWh", "data": [3.5, 4.0]}
            if utility == "Hot Water":
                return {"total": 3.136, "units": "m3", "data": [0.05, 0.10]}
            if utility == "Cooling":
                return {"total": 124.0, "units": "kWh", "data": [1.0, 2.5]}
            if utility == "Heating":
                return {"total": 0.0, "units": "kWh", "data": [0.0]}
            return {"total": 0.0, "units": "", "data": []}

        # Return mock chart data
        async def mock_get_chart_data(utility, period, start):
            if utility == "Electricity":
                if period == "day":
                    return {"error": False, "units": "kWh", "data": [0.5, 0.7, 1.2, 0.0]}
                if period == "month":
                    return {"error": False, "units": "kWh", "data": [10.0, 12.0, 15.0]}
                if period == "year":
                    return {"error": False, "units": "kWh", "data": [0.0, 6.26, 469.75, 479.85, 298.57]}
            elif utility == "EV":
                if period == "day":
                    return {"error": False, "units": "kWh", "data": [3.5, 4.0, 0.0]}
                if period == "month":
                    return {"error": False, "units": "kWh", "data": [45.0, 50.0]}
                if period == "year":
                    return {"error": False, "units": "kWh", "data": [500.0]}
            elif utility == "Hot Water":
                if period == "day":
                    return {"error": False, "units": "m3", "data": [0.05, 0.10, 0.08]}
                if period == "month":
                    return {"error": False, "units": "m3", "data": [1.5, 2.0]}
                if period == "year":
                    return {"error": False, "units": "m3", "data": [20.0]}
            elif utility == "Cooling":
                if period == "day":
                    return {"error": False, "units": "kWh", "data": [1.0, 2.5]}
                if period == "month":
                    return {"error": False, "units": "kWh", "data": [30.0, 94.0]}
                if period == "year":
                    return {"error": False, "units": "kWh", "data": [150.0]}
            elif utility == "Heating":
                if period == "day":
                    return {"error": False, "units": "kWh", "data": [0.0]}
                if period == "month":
                    return {"error": False, "units": "kWh", "data": [0.0]}
                if period == "year":
                    return {"error": False, "units": "kWh", "data": [0.0]}
            return {"error": False, "units": "unknown", "data": []}

        mock_client.get_card_data.side_effect = mock_get_card_data
        mock_client.get_chart_data.side_effect = mock_get_chart_data

        coordinator = ProvidentDataUpdateCoordinator(self.hass, self.entry)
        await coordinator.async_config_entry_first_refresh()

        self.assertIn("Electricity", coordinator.data)
        self.assertIn("EV", coordinator.data)
        self.assertIn("Hot Water", coordinator.data)
        self.assertIn("Cooling", coordinator.data)
        self.assertIn("Heating", coordinator.data)

        elec = coordinator.data["Electricity"]
        self.assertEqual(elec.name, "Electricity")
        self.assertEqual(elec.units, "kWh")
        self.assertEqual(elec.last_30_days_total, 402.0)
        self.assertEqual(elec.yesterday_total, 2.4)
        self.assertEqual(elec.today_total, 2.4)
        self.assertEqual(elec.latest_reading, 1.2)
        self.assertEqual(elec.month_total, 37.0)
        self.assertEqual(len(elec.yesterday_hourly), 4)

        ev = coordinator.data["EV"]
        self.assertEqual(ev.name, "EV")
        self.assertEqual(ev.units, "kWh")
        self.assertEqual(ev.last_30_days_total, 116.0)
        self.assertEqual(ev.yesterday_total, 7.5)
        self.assertEqual(ev.latest_reading, 4.0)

        hw = coordinator.data["Hot Water"]
        self.assertEqual(hw.name, "Hot Water")
        self.assertEqual(hw.units, "m³")
        self.assertEqual(hw.last_30_days_total, 3.136)
        self.assertAlmostEqual(hw.yesterday_total, 0.23, places=2)

        cool = coordinator.data["Cooling"]
        self.assertEqual(cool.name, "Cooling")
        self.assertEqual(cool.units, "kWh")
        self.assertEqual(cool.last_30_days_total, 124.0)

        # Test on-demand historical hourly retrieval
        hist_res = await coordinator.async_fetch_historical_hourly(
            utility_name="Electricity",
            days=5,
            update_entities=True,
        )
        self.assertIn("Electricity", hist_res)
        self.assertEqual(len(hist_res["Electricity"]["readings"]), 5)
        self.assertTrue(len(coordinator.data["Electricity"].daily_hourly_history) >= 5)

    @patch("custom_components.provident.coordinator.ProvidentAPIClient")
    async def test_coordinator_auth_failure(self, mock_client_cls):
        """Test coordinator raising ConfigEntryAuthFailed when login fails."""
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client

        mock_client.is_authenticated = False
        mock_client.check_login.return_value = False
        mock_client.login.return_value = False

        coordinator = ProvidentDataUpdateCoordinator(self.hass, self.entry)

        with self.assertRaises(ConfigEntryAuthFailed):
            await coordinator.async_config_entry_first_refresh()

    @patch("custom_components.provident.coordinator.ProvidentAPIClient")
    async def test_coordinator_connection_failure(self, mock_client_cls):
        """Test coordinator raising UpdateFailed on connection failure."""
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client

        mock_client.is_authenticated = False
        mock_client.check_login.return_value = False
        mock_client.login.side_effect = ProvidentConnError("Timeout")

        coordinator = ProvidentDataUpdateCoordinator(self.hass, self.entry)

        with self.assertRaises(UpdateFailed):
            await coordinator.async_config_entry_first_refresh()

    def test_parse_timestamp_to_datetime(self):
        """Test timestamp parsing for ms epoch, s epoch, ISO, and ASP.NET strings."""
        # Milliseconds epoch
        dt_ms = parse_timestamp_to_datetime(1726790400000)
        self.assertIsNotNone(dt_ms)
        self.assertEqual(dt_ms.year, 2024)

        # Seconds epoch
        dt_s = parse_timestamp_to_datetime(1726790400)
        self.assertIsNotNone(dt_s)
        self.assertEqual(dt_s.year, 2024)

        # ISO string
        dt_iso = parse_timestamp_to_datetime("2026-09-22T14:30:00")
        self.assertIsNotNone(dt_iso)
        self.assertEqual(dt_iso.hour, 14)
        self.assertEqual(dt_iso.minute, 30)

        # ASP.NET /Date(1726790400000)/
        dt_asp = parse_timestamp_to_datetime("/Date(1726790400000)/")
        self.assertIsNotNone(dt_asp)
        self.assertEqual(dt_asp.year, 2024)

        # None / invalid
        self.assertIsNone(parse_timestamp_to_datetime(None))
        self.assertIsNone(parse_timestamp_to_datetime("invalid-string-date"))

    def test_parse_interval_points_to_daily_hourly(self):
        """Test bucketing interval timestamped points into 24-hour daily arrays."""
        # 2 readings in hour 10 on 2026-09-20 (e.g. 15-min intervals)
        dt1 = datetime(2026, 9, 20, 10, 15, 0, tzinfo=timezone.utc)
        dt2 = datetime(2026, 9, 20, 10, 45, 0, tzinfo=timezone.utc)
        dt3 = datetime(2026, 9, 21, 14, 0, 0, tzinfo=timezone.utc)

        pts = [
            [int(dt1.timestamp() * 1000), 0.25],
            [int(dt2.timestamp() * 1000), 0.75],
            {"x": int(dt3.timestamp() * 1000), "y": 1.5},
        ]

        buckets = parse_interval_points_to_daily_hourly(pts)
        self.assertIn("2026-09-20", buckets)
        self.assertIn("2026-09-21", buckets)
        self.assertEqual(len(buckets["2026-09-20"]), 24)
        self.assertEqual(len(buckets["2026-09-21"]), 24)
        # Hour 10 accumulated sum: 0.25 + 0.75 = 1.0
        self.assertEqual(buckets["2026-09-20"][10], 1.0)
        self.assertEqual(buckets["2026-09-21"][14], 1.5)

    def test_match_series_to_utility(self):
        """Test matching QuickGraphs series to known utility names."""
        utilities = ["Electricity", "EV", "Hot Water", "Cooling", "Heating"]
        hierarchy = {
            "meters": {
                "MP:100": {"name": "Heating", "type": "thermal", "parent_group": "G1"},
                "MP:200": {"name": "Fan Coil", "type": "cooling", "parent_group": "G2"},
                "MP:300": {"name": "DHW", "type": "water", "parent_group": None},
                "MP:400": {"name": "EV Spot 14", "type": "electric", "parent_group": "G3"},
            },
            "groups": {
                "G1": {"name": "Heating"},
                "G2": {"name": "Cooling"},
                "G3": {"name": "EV Parking"},
            },
        }

        self.assertEqual(match_series_to_utility("Heating", "MP:100", utilities, hierarchy), "Heating")
        self.assertEqual(match_series_to_utility("Cooling", "MP:200", utilities, hierarchy), "Cooling")
        self.assertEqual(match_series_to_utility("Hot Water", "MP:300", utilities, hierarchy), "Hot Water")
        self.assertEqual(match_series_to_utility("Domestic Hot Water", "MP:300", utilities, hierarchy), "Hot Water")
        self.assertEqual(match_series_to_utility("EV - Spot P2-14", "MP:400", utilities, hierarchy), "EV")
        self.assertEqual(match_series_to_utility("Electricity", "MP:999", utilities, hierarchy), "Electricity")

    @patch("custom_components.provident.coordinator.ProvidentAPIClient")
    async def test_coordinator_initial_7day_load_and_daily_refresh(self, mock_client_cls):
        """Verify initial load queries 7 days and subsequent refresh queries 1 day."""
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_client.is_authenticated = True
        mock_client.check_login.return_value = True

        mock_client.get_utilities.return_value = ["Electricity", "EV", "Hot Water", "Cooling", "Heating"]
        mock_client.get_meter_hierarchy.return_value = {
            "groups": {"G1": {"name": "EV"}},
            "meters": {
                "M1": {"id": "M1", "name": "Electricity", "parent_group": None},
                "M2": {"id": "M2", "name": "Heating", "parent_group": None},
                "M3": {"id": "M3", "name": "Cooling", "parent_group": None},
                "M4": {"id": "M4", "name": "Hot Water", "parent_group": None},
                "M5": {"id": "M5", "name": "EV - Spot P2-14", "parent_group": "G1"},
            },
            "meter_list": ["M1", "M2", "M3", "M4", "M5", "GROUP:G1"],
        }

        # Build 7 days of QuickGraphs test data
        now = datetime.now()
        today = now.date()
        qg_points_heating = []
        qg_points_cooling = []
        qg_points_hot_water = []
        for i in range(1, 8):
            d = today - timedelta(days=i)
            dt_pt = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
            ts = int(dt_pt.timestamp() * 1000)
            qg_points_heating.append([ts, 1.25])
            qg_points_cooling.append([ts, 2.50])
            qg_points_hot_water.append([ts, 0.08])

        qg_7day_series = [
            {"name": "Heating", "meterId": "M2", "unit": "kWh", "data": qg_points_heating},
            {"name": "Cooling", "meterId": "M3", "unit": "kWh", "data": qg_points_cooling},
            {"name": "Hot Water", "meterId": "M4", "unit": "m³", "data": qg_points_hot_water},
            {"name": "EV - Spot P2-14", "meterId": "M5", "unit": "kWh", "data": [[int(datetime(today.year, today.month, today.day, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000), 5.0]]},
        ]

        mock_client.get_quickgraphs.return_value = qg_7day_series
        mock_client.get_card_data.return_value = {"total": 100.0, "units": "kWh", "data": [5.0]*30}
        mock_client.get_chart_data.return_value = {"error": False, "units": "kWh", "data": [0.0]*24}

        coordinator = ProvidentDataUpdateCoordinator(self.hass, self.entry)

        # 1. INITIAL REFRESH (coordinator.data is empty) -> Should query 7 days!
        await coordinator.async_config_entry_first_refresh()

        # Check that get_quickgraphs was called with start_date = today - 7 days
        calls = mock_client.get_quickgraphs.call_args_list
        self.assertTrue(len(calls) >= 1)
        first_call_kwargs = calls[0].kwargs
        expected_7d_start = today - timedelta(days=7)
        self.assertEqual(first_call_kwargs["start_date"], expected_7d_start)
        self.assertEqual(first_call_kwargs["end_date"], today)

        # Verify Heating, Cooling, and Hot Water have 7 days in daily_hourly_history
        heating = coordinator.data["Heating"]
        self.assertEqual(len(heating.daily_hourly_history), 7)
        self.assertEqual(len(heating.hourly_breakdown_past_days), 7)
        yesterday_str = (today - timedelta(days=1)).isoformat()
        self.assertIn(yesterday_str, heating.daily_hourly_history)
        self.assertEqual(heating.yesterday_hourly[12], 1.25)
        self.assertEqual(heating.yesterday_total, 1.25)

        cooling = coordinator.data["Cooling"]
        self.assertEqual(len(cooling.daily_hourly_history), 7)
        self.assertEqual(len(cooling.hourly_breakdown_past_days), 7)
        self.assertEqual(cooling.yesterday_hourly[12], 2.50)
        self.assertEqual(cooling.yesterday_total, 2.50)

        hw = coordinator.data["Hot Water"]
        self.assertEqual(len(hw.daily_hourly_history), 7)
        self.assertEqual(len(hw.hourly_breakdown_past_days), 7)
        self.assertEqual(hw.yesterday_hourly[12], 0.08)
        self.assertEqual(hw.yesterday_total, 0.08)

        # 2. SUBSEQUENT DAILY REFRESH -> Should query only 1 day (yesterday)
        mock_client.get_quickgraphs.reset_mock()
        yesterday = today - timedelta(days=1)
        dt_yesterday = datetime(yesterday.year, yesterday.month, yesterday.day, 12, 0, 0, tzinfo=timezone.utc)
        ts_y = int(dt_yesterday.timestamp() * 1000)
        mock_client.get_quickgraphs.return_value = [
            {"name": "Heating", "meterId": "M2", "unit": "kWh", "data": [[ts_y, 1.80]]},
            {"name": "Cooling", "meterId": "M3", "unit": "kWh", "data": [[ts_y, 3.10]]},
            {"name": "Hot Water", "meterId": "M4", "unit": "m³", "data": [[ts_y, 0.12]]},
        ]

        await coordinator.async_refresh()

        # Verify get_quickgraphs was called with start_date = yesterday (1 day only!)
        calls_refresh = mock_client.get_quickgraphs.call_args_list
        self.assertTrue(len(calls_refresh) >= 1)
        refresh_call_kwargs = calls_refresh[0].kwargs
        self.assertEqual(refresh_call_kwargs["start_date"], yesterday)
        self.assertEqual(refresh_call_kwargs["end_date"], today)

        # Verify history is preserved and yesterday is updated
        self.assertEqual(coordinator.data["Heating"].yesterday_total, 1.80)
        self.assertEqual(coordinator.data["Cooling"].yesterday_total, 3.10)
        self.assertEqual(coordinator.data["Hot Water"].yesterday_total, 0.12)
        self.assertEqual(len(coordinator.data["Heating"].daily_hourly_history), 7)


if __name__ == "__main__":
    unittest.main()
