"""DataUpdateCoordinator for the Provident Energy integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .api import ProvidentAPIClient, ProvidentAuthError, ProvidentConnError, ProvidentAPIError
from .const import (
    CONF_BASE_URL,
    CONF_SCAN_INTERVAL,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ProvidentUtilityData:
    """Class to hold consumption metrics for a single utility meter."""

    name: str
    units: str
    portal_total: float = 0.0  # 30-day card total matching the web portal homepage
    yesterday_total: float = 0.0
    yesterday_hourly: list[float] = field(default_factory=list)
    yesterday_date: str = ""
    today_total: float = 0.0
    today_hourly: list[float] = field(default_factory=list)
    last_30_days_total: float = 0.0
    last_30_days_daily: list[float] = field(default_factory=list)
    month_total: float = 0.0
    month_daily: list[float] = field(default_factory=list)
    year_total: float = 0.0
    year_monthly: list[float] = field(default_factory=list)
    latest_reading: float = 0.0
    last_updated: datetime = field(default_factory=dt_util.utcnow)


def normalize_unit(unit_str: str | None, utility_name: str) -> str:
    """Normalize reported unit string into standard Home Assistant units."""
    if not unit_str:
        name_lower = utility_name.lower().strip()
        if "electr" in name_lower or name_lower == "ev" or "ev " in name_lower:
            return "kWh"
        if "water" in name_lower:
            return "m³"
        if "gas" in name_lower:
            return "m³"
        if "cool" in name_lower or "heat" in name_lower:
            return "kWh"
        return ""

    unit_clean = unit_str.strip()
    u_lower = unit_clean.lower()

    if u_lower in ("kwh", "kw.h", "kw-h", "kilowatt-hour", "kilowatt-hours"):
        return "kWh"
    if u_lower in ("wh", "watt-hour", "watt-hours"):
        return "Wh"
    if u_lower in ("mwh", "megawatt-hour"):
        return "MWh"
    if u_lower in ("m3", "m³", "cu m", "cubic meter", "cubic meters"):
        return "m³"
    if u_lower in ("l", "liter", "liters", "litre", "litres"):
        return "L"
    if u_lower in ("gal", "gals", "gallon", "gallons"):
        return "gal"
    if u_lower in ("ccf", "hundred cubic feet"):
        return "CCF"
    if u_lower in ("btu", "btus"):
        return "BTU"
    if u_lower in ("kbtu",):
        return "kBtu"
    if u_lower in ("ton-hr", "ton-hrs", "ton-hour", "ton-hours"):
        return "ton-hr"

    return unit_clean


class ProvidentDataUpdateCoordinator(DataUpdateCoordinator[dict[str, ProvidentUtilityData]]):
    """Class to manage fetching Provident meter data."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.entry = entry
        self.username = entry.data[CONF_USERNAME]
        self.password = entry.data[CONF_PASSWORD]
        self.base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)

        scan_interval_min = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        update_interval = timedelta(minutes=scan_interval_min)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.username}",
            update_interval=update_interval,
        )

        self.client = ProvidentAPIClient(base_url=self.base_url)

    async def async_close(self) -> None:
        """Close the underlying HTTP client session."""
        try:
            await self.client.close()
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("Error while closing Provident client session: %s", err)

    async def _async_ensure_login(self) -> None:
        """Ensure the client has an active authenticated session."""
        try:
            if not self.client.is_authenticated or not await self.client.check_login():
                _LOGGER.debug("Authenticating with Provident API for user %s", self.username)
                success = await self.client.login(self.username, self.password)
                if not success:
                    raise ConfigEntryAuthFailed("Invalid username or password")
        except ProvidentAuthError as err:
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except ProvidentConnError as err:
            raise UpdateFailed(f"Communication error during login: {err}") from err
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Unexpected error during login: {err}") from err

    async def _async_update_data(self) -> dict[str, ProvidentUtilityData]:
        """Fetch all utility data from the Provident API."""
        await self._async_ensure_login()

        try:
            utilities = await self.client.get_utilities()
        except ProvidentAuthError as err:
            raise ConfigEntryAuthFailed(f"Authentication failed fetching utilities: {err}") from err
        except ProvidentAPIError as err:
            raise UpdateFailed(f"Error fetching utility list: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error fetching utility list: {err}") from err

        if not utilities:
            _LOGGER.warning("No utilities found for Provident account %s", self.username)
            return {}

        now = dt_util.now()
        today = now.date()
        yesterday = today - timedelta(days=1)
        first_of_month = date(today.year, today.month, 1)
        first_of_year = date(today.year, 1, 1)

        data_by_utility: dict[str, ProvidentUtilityData] = {}

        for utility in utilities:
            try:
                # 1. Fetch Summary Card Data (Matches 30-day card on Provident dashboard)
                card_data = await self.client.get_card_data(utility, period=30)
                last_30_days_daily = card_data["data"]
                last_30_days_total = card_data["total"]

                # 2. Fetch Yesterday (Previous Day - 24 hourly readings)
                yesterday_res = await self.client.get_chart_data(utility, "day", yesterday)
                yesterday_hourly = yesterday_res["data"]
                yesterday_total = round(sum(yesterday_hourly), 4)

                # If yesterday hourly sum is 0 but 30-day card has daily points, fallback to latest non-zero daily reading
                if yesterday_total == 0.0 and last_30_days_daily:
                    non_zero_days = [v for v in last_30_days_daily if v > 0]
                    if non_zero_days:
                        yesterday_total = round(non_zero_days[-1], 4)

                # 3. Fetch Today (Hourly breakdown - may be 0/empty due to 1-day delay)
                today_res = await self.client.get_chart_data(utility, "day", today)
                today_hourly = today_res["data"]
                today_total = round(sum(today_hourly), 4)

                # 4. Fetch Year-to-Date (12 monthly numbers)
                year_res = await self.client.get_chart_data(utility, "year", first_of_year)
                year_monthly = year_res["data"]
                year_total = round(sum(year_monthly), 4)
                if year_total == 0.0 and last_30_days_total > 0:
                    year_total = last_30_days_total

                # 5. Fetch Month-to-Date (Daily breakdown)
                month_res = await self.client.get_chart_data(utility, "month", first_of_month)
                month_daily = month_res["data"]
                month_total = round(sum(month_daily), 4)

                # If month_total is 0 but year_monthly has current month's entry, use it
                current_month_idx = today.month - 1
                if month_total == 0.0 and year_monthly and len(year_monthly) > current_month_idx:
                    current_month_val = year_monthly[current_month_idx]
                    if current_month_val > 0:
                        month_total = round(current_month_val, 4)

                # Determine latest reading
                latest_reading = 0.0
                reading_source = [v for v in today_hourly if v > 0]
                if reading_source:
                    latest_reading = round(reading_source[-1], 4)
                else:
                    yesterday_non_zero = [v for v in yesterday_hourly if v > 0]
                    if yesterday_non_zero:
                        latest_reading = round(yesterday_non_zero[-1], 4)
                    elif last_30_days_daily:
                        non_zero_30d = [v for v in last_30_days_daily if v > 0]
                        if non_zero_30d:
                            latest_reading = round(non_zero_30d[-1], 4)

                # Normalize units from any available response
                raw_units = (
                    card_data["units"]
                    or yesterday_res["units"]
                    or month_res["units"]
                    or year_res["units"]
                    or today_res["units"]
                )
                units = normalize_unit(raw_units, utility)

                # Portal total: the prominent number from the portal card (e.g. 402 kWh)
                portal_total = last_30_days_total if last_30_days_total > 0 else (month_total or year_total or yesterday_total)

                data_by_utility[utility] = ProvidentUtilityData(
                    name=utility,
                    units=units,
                    portal_total=portal_total,
                    yesterday_total=yesterday_total,
                    yesterday_hourly=yesterday_hourly,
                    yesterday_date=yesterday.isoformat(),
                    today_total=today_total,
                    today_hourly=today_hourly,
                    last_30_days_total=last_30_days_total,
                    last_30_days_daily=last_30_days_daily,
                    month_total=month_total,
                    month_daily=month_daily,
                    year_total=year_total,
                    year_monthly=year_monthly,
                    latest_reading=latest_reading,
                    last_updated=now,
                )

                _LOGGER.debug(
                    "Fetched %s: PortalCard=%.3f %s, Yesterday=%.3f %s, Month=%.3f %s, Year=%.3f %s",
                    utility,
                    portal_total,
                    units,
                    yesterday_total,
                    units,
                    month_total,
                    units,
                    year_total,
                    units,
                )

            except ProvidentAuthError as err:
                raise ConfigEntryAuthFailed(f"Authentication expired fetching {utility}: {err}") from err
            except (ProvidentConnError, ProvidentAPIError) as err:
                _LOGGER.warning("Transient error fetching meter data for %s: %s", utility, err)
                if self.data and utility in self.data:
                    data_by_utility[utility] = self.data[utility]
                else:
                    raise UpdateFailed(f"Failed to fetch meter data for {utility}: {err}") from err
            except Exception as err:
                _LOGGER.error("Unexpected error fetching meter data for %s: %s", utility, err)
                if self.data and utility in self.data:
                    data_by_utility[utility] = self.data[utility]
                else:
                    raise UpdateFailed(f"Unexpected error for {utility}: {err}") from err

        return data_by_utility
