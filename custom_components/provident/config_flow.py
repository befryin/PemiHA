"""Config flow for Provident Energy integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import ProvidentAPIClient, ProvidentAuthError, ProvidentConnError
from .const import (
    CONF_BASE_URL,
    CONF_SCAN_INTERVAL,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
    }
)


async def validate_credentials(data: dict[str, Any]) -> dict[str, str]:
    """Validate credentials with the Provident API.

    Returns a dict of errors (empty if successful).
    """
    errors: dict[str, str] = {}
    base_url = data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
    client = ProvidentAPIClient(base_url=base_url)

    try:
        success = await client.login(data[CONF_USERNAME], data[CONF_PASSWORD])
        if not success:
            errors["base"] = "invalid_auth"
    except ProvidentAuthError:
        errors["base"] = "invalid_auth"
    except ProvidentConnError:
        errors["base"] = "cannot_connect"
    except Exception as err:  # pylint: disable=broad-except
        _LOGGER.exception("Unexpected error during Provident authentication: %s", err)
        errors["base"] = "unknown"
    finally:
        await client.close()

    return errors


class ProvidentConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Provident Energy."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            user_input[CONF_USERNAME] = username

            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()

            errors = await validate_credentials(user_input)

            if not errors:
                return self.async_create_entry(
                    title=f"Provident Energy ({username})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """Handle re-authentication upon auth failure."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Dialog to confirm and input updated password."""
        errors: dict[str, str] = {}

        if self._reauth_entry is None:
            return self.async_abort(reason="unknown")

        username = self._reauth_entry.data[CONF_USERNAME]
        base_url = self._reauth_entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)

        if user_input is not None:
            validate_data = {
                CONF_USERNAME: username,
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_BASE_URL: base_url,
            }
            errors = await validate_credentials(validate_data)

            if not errors:
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": username},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return ProvidentOptionsFlowHandler()


class ProvidentOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for Provident Energy integration."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=current_interval,
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)),
                }
            ),
        )
