"""Tests for Provident component setup and services."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests import conftest  # Load mocks

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.provident import async_setup, async_setup_entry, DOMAIN, SERVICE_GET_HOURLY_BREAKDOWN
from custom_components.provident.const import CONF_BASE_URL, DEFAULT_BASE_URL
from custom_components.provident.coordinator import ProvidentUtilityData


class TestProvidentInit(unittest.IsolatedAsyncioTestCase):
    """Test suite for component initialization and services."""

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
        )

    async def test_async_setup_registers_service(self):
        """Test service registration in async_setup."""
        res = await async_setup(self.hass, {})
        self.assertTrue(res)
        self.assertTrue(self.hass.services.has_service(DOMAIN, SERVICE_GET_HOURLY_BREAKDOWN))

    @patch("custom_components.provident.coordinator.ProvidentAPIClient")
    async def test_service_get_hourly_breakdown_call(self, mock_client_cls):
        """Test calling get_hourly_breakdown service."""
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_client.is_authenticated = True
        mock_client.check_login.return_value = True
        mock_client.get_meter_hierarchy.return_value = {"groups": {}, "meters": {}, "meter_list": []}
        mock_client.get_quickgraphs.return_value = []
        mock_client.get_utilities.return_value = ["Electricity"]
        mock_client.get_card_data.return_value = {"total": 400.0, "units": "kWh", "data": []}
        mock_client.get_chart_data.return_value = {"error": False, "units": "kWh", "data": [0.5, 0.5]}

        await async_setup(self.hass, {})
        await async_setup_entry(self.hass, self.entry)

        # Call service
        service_response = await self.hass.services.async_call(
            DOMAIN,
            SERVICE_GET_HOURLY_BREAKDOWN,
            {"utility": "Electricity", "days": 3, "update_entities": True},
            blocking=True,
            return_response=True,
        )

        self.assertIsNotNone(service_response)
        self.assertIn("Electricity", service_response)
        self.assertEqual(len(service_response["Electricity"]["readings"]), 3)


if __name__ == "__main__":
    unittest.main()
