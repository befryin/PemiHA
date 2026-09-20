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

from provident import AsyncProvidentClient, Period, ProvidentConfig
from provident.errors import (
    ProvidentAuthenticationError,
    ProvidentConnectionError,
    ProvidentError,
    ProvidentRateLimitError,
    ProvidentServerError,
)

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
        # Infer standard default based on utility name
        name_lower = utility_name.lower()
        if "electr" in name_lower or "ev" in name_lower:
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

        provident_config = ProvidentConfig(base_url=self.base_url)
        self.client = AsyncProvidentClient(provident_config)

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
                result = await self.client.login(self.username, self.password)
                if not result.success:
                    msg = result.msg or "Invalid username or password"
                    raise ConfigEntryAuthFailed(msg)
        except ProvidentAuthenticationError as err:
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except (ProvidentConnectionError, ProvidentServerError, ProvidentRateLimitError) as err:
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
        except ProvidentAuthenticationError as err:
            raise ConfigEntryAuthFailed(f"Authentication failed fetching utilities: {err}") from err
        except ProvidentError as err:
            raise UpdateFailed(f"Error fetching utility list: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error fetching utility list: {err}") from err

        if not utilities:
            _LOGGER.warning("No utilities found for Provident account %s", self.username)
            return {}

        now = dt_util.now()
        today = now.date()
        yesterday = today - timedelta(days=1)
        thirty_days_ago = today - timedelta(days=30)
        first_of_month = date(today.year, today.month, 1)
        first_of_year = date(today.year, 1, 1)

        data_by_utility: dict[str, ProvidentUtilityData] = {}

        for utility in utilities:
            try:
                # 1. Fetch Yesterday (Previous Day - primary due to 1-day reporting delay)
                yesterday_res = await self.client.get_chart_data(
                    utility, Period.DAY, yesterday
                )
                yesterday_hourly = [float(v) for v in (yesterday_res.data or [])]
                yesterday_total = round(sum(yesterday_hourly), 4)

                # 2. Fetch Today (Hourly breakdown - may be 0/empty due to 1-day delay)
                day_res = await self.client.get_chart_data(utility, Period.DAY, today)
                today_hourly = [float(v) for v in (day_res.data or [])]
                today_total = round(sum(today_hourly), 4)

                # 3. Fetch Last 30 Days (Breakdown matching Provident homepage cards)
                last_30_res = await self.client.get_chart_data(
                    utility, Period.MONTH, thirty_days_ago
                )
                last_30_daily = [float(v) for v in (last_30_res.data or [])]
                last_30_total = round(sum(last_30_daily), 4)

                # 4. Fetch Month-to-Date (Daily breakdown)
                month_res = await self.client.get_chart_data(
                    utility, Period.MONTH, first_of_month
                )
                daily_data = [float(v) for v in (month_res.data or [])]
                month_total = round(sum(daily_data), 4)

                # 5. Fetch Year (Monthly breakdown)
                year_res = await self.client.get_chart_data(
                    utility, Period.YEAR, first_of_year
                )
                monthly_data = [float(v) for v in (year_res.data or [])]
                year_total = round(sum(monthly_data), 4)

                # Determine latest reading (from today if available, otherwise yesterday)
                latest_reading = 0.0
                reading_source = [v for v in today_hourly if v > 0]
                if reading_source:
                    latest_reading = round(reading_source[-1], 4)
                else:
                    yesterday_non_zero = [v for v in yesterday_hourly if v > 0]
                    if yesterday_non_zero:
                        latest_reading = round(yesterday_non_zero[-1], 4)
                    elif yesterday_hourly:
                        latest_reading = round(yesterday_hourly[-1], 4)

                # Normalize units from any available response
                raw_units = (
                    yesterday_res.units
                    or day_res.units
                    or last_30_res.units
                    or month_res.units
                    or year_res.units
                )
                units = normalize_unit(raw_units, utility)

                data_by_utility[utility] = ProvidentUtilityData(
                    name=utility,
                    units=units,
                    yesterday_total=yesterday_total,
                    yesterday_hourly=yesterday_hourly,
                    yesterday_date=yesterday.isoformat(),
                    today_total=today_total,
                    today_hourly=today_hourly,
                    last_30_days_total=last_30_total,
                    last_30_days_daily=last_30_daily,
                    month_total=month_total,
                    month_daily=daily_data,
                    year_total=year_total,
                    year_monthly=monthly_data,
                    latest_reading=latest_reading,
                    last_updated=now,
                )

                _LOGGER.debug(
                    "Fetched %s: Yesterday=%.3f %s, Last30Days=%.3f %s, Month=%.3f %s, Year=%.3f %s, Today=%.3f %s",
                    utility,
                    yesterday_total,
                    units,
                    last_30_total,
                    units,
                    month_total,
                    units,
                    year_total,
                    units,
                    today_total,
                    units,
                )

            except ProvidentAuthenticationError as err:
                raise ConfigEntryAuthFailed(f"Authentication expired fetching {utility}: {err}") from err
            except (ProvidentConnectionError, ProvidentRateLimitError, ProvidentServerError) as err:
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
