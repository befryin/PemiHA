from datetime import date
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import DOMAIN
from .coordinator import ProvidentDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

SERVICE_GET_HOURLY_BREAKDOWN = "get_hourly_breakdown"

SCHEMA_GET_HOURLY_BREAKDOWN = vol.Schema(
    {
        vol.Optional("utility"): cv.string,
        vol.Optional("days", default=7): cv.positive_int,
        vol.Optional("start_date"): cv.string,
        vol.Optional("end_date"): cv.string,
        vol.Optional("update_entities", default=True): cv.boolean,
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Provident Energy component."""
    hass.data.setdefault(DOMAIN, {})

    async def handle_get_hourly_breakdown(call: ServiceCall) -> ServiceResponse:
        """Handle on-demand retrieval of historical hourly breakdown."""
        utility = call.data.get("utility")
        days = call.data.get("days", 7)
        start_date_str = call.data.get("start_date")
        end_date_str = call.data.get("end_date")
        update_entities = call.data.get("update_entities", True)

        start_date = None
        end_date = None
        if start_date_str:
            try:
                start_date = date.fromisoformat(str(start_date_str).strip())
            except ValueError:
                _LOGGER.error("Invalid start_date format (expected YYYY-MM-DD): %s", start_date_str)
        if end_date_str:
            try:
                end_date = date.fromisoformat(str(end_date_str).strip())
            except ValueError:
                _LOGGER.error("Invalid end_date format (expected YYYY-MM-DD): %s", end_date_str)

        all_results: dict[str, Any] = {}
        for coordinator in hass.data.get(DOMAIN, {}).values():
            if isinstance(coordinator, ProvidentDataUpdateCoordinator):
                res = await coordinator.async_fetch_historical_hourly(
                    utility_name=utility,
                    start_date=start_date,
                    end_date=end_date,
                    days=days,
                    update_entities=update_entities,
                )
                all_results.update(res)

        return all_results

    if not hass.services.has_service(DOMAIN, SERVICE_GET_HOURLY_BREAKDOWN):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_HOURLY_BREAKDOWN,
            handle_get_hourly_breakdown,
            schema=SCHEMA_GET_HOURLY_BREAKDOWN,
            supports_response=SupportsResponse.OPTIONAL,
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Provident Energy from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = ProvidentDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Register options update listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Set up platforms (Sensor)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: ProvidentDataUpdateCoordinator | None = hass.data[DOMAIN].pop(
            entry.entry_id, None
        )
        if coordinator is not None:
            await coordinator.async_close()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
