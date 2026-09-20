"""Tests for Provident Energy config flow."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests import conftest  # Load mocks

from provident.errors import (
    ProvidentAuthenticationError,
    ProvidentConnectionError,
    ProvidentRateLimitError,
)
from provident.models import LoginResult

from custom_components.provident.config_flow import (
    ProvidentConfigFlow,
    validate_credentials,
)
from custom_components.provident.const import CONF_BASE_URL, CONF_SCAN_INTERVAL


class TestProvidentConfigFlow(unittest.IsolatedAsyncioTestCase):
    """Test suite for Provident config flow."""

    @patch("custom_components.provident.config_flow.AsyncProvidentClient")
    async def test_validate_credentials_success(self, mock_client_cls):
        """Test successful credential validation."""
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_client.login.return_value = LoginResult(success=True, msg=None)

        errors = await validate_credentials(
            {"username": "user1", "password": "pass", CONF_BASE_URL: "https://provident.meterconnex.com"}
        )
        self.assertEqual(errors, {})
        mock_client.close.assert_awaited_once()

    @patch("custom_components.provident.config_flow.AsyncProvidentClient")
    async def test_validate_credentials_invalid_auth(self, mock_client_cls):
        """Test credential validation with wrong password."""
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_client.login.return_value = LoginResult(success=False, msg="Bad credentials")

        errors = await validate_credentials(
            {"username": "user1", "password": "wrong_password"}
        )
        self.assertEqual(errors, {"base": "invalid_auth"})

    @patch("custom_components.provident.config_flow.AsyncProvidentClient")
    async def test_validate_credentials_connection_error(self, mock_client_cls):
        """Test connection failure during credential validation."""
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_client.login.side_effect = ProvidentConnectionError("Timeout")

        errors = await validate_credentials(
            {"username": "user1", "password": "pass"}
        )
        self.assertEqual(errors, {"base": "cannot_connect"})

    @patch("custom_components.provident.config_flow.AsyncProvidentClient")
    async def test_validate_credentials_rate_limit(self, mock_client_cls):
        """Test rate limit during credential validation."""
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_client.login.side_effect = ProvidentRateLimitError(429, "Rate limited")

        errors = await validate_credentials(
            {"username": "user1", "password": "pass"}
        )
        self.assertEqual(errors, {"base": "rate_limit"})

    @patch("custom_components.provident.config_flow.validate_credentials")
    async def test_flow_user_step_success(self, mock_validate):
        """Test user step creating entry on success."""
        mock_validate.return_value = {}

        flow = ProvidentConfigFlow()
        result = await flow.async_step_user(
            {"username": "testuser", "password": "password123"}
        )

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["title"], "Provident Energy (testuser)")
        self.assertEqual(result["data"]["username"], "testuser")

    @patch("custom_components.provident.config_flow.validate_credentials")
    async def test_flow_user_step_failure(self, mock_validate):
        """Test user step displaying form with error on failure."""
        mock_validate.return_value = {"base": "invalid_auth"}

        flow = ProvidentConfigFlow()
        result = await flow.async_step_user(
            {"username": "testuser", "password": "wrongpassword"}
        )

        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"base": "invalid_auth"})

    async def test_options_flow(self):
        """Test options flow handling scan interval configuration."""
        from custom_components.provident.config_flow import ProvidentOptionsFlowHandler
        from homeassistant.config_entries import ConfigEntry

        entry = ConfigEntry(
            entry_id="test",
            data={"username": "test", "password": "pw"},
            options={CONF_SCAN_INTERVAL: 30},
        )
        handler = ProvidentOptionsFlowHandler()
        handler.config_entry = entry

        # Initial view
        res = await handler.async_step_init()
        self.assertEqual(res["type"], "form")

        # Save option
        res_save = await handler.async_step_init({CONF_SCAN_INTERVAL: 60})
        self.assertEqual(res_save["type"], "create_entry")
        self.assertEqual(res_save["data"][CONF_SCAN_INTERVAL], 60)


if __name__ == "__main__":
    unittest.main()
