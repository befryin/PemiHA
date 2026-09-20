"""Tests for Provident Energy coordinator."""
from __future__ import annotations

from datetime import date
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
    normalize_unit,
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

    @patch("custom_components.provident.coordinator.ProvidentAPIClient")
    async def test_coordinator_data_fetch_success(self, mock_client_cls):
        """Test successful data update coordinator refresh."""
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client

        mock_client.is_authenticated = False
        mock_client.check_login.return_value = False
        mock_client.login.return_value = True
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


if __name__ == "__main__":
    unittest.main()
