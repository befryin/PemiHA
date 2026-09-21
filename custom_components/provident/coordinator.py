"""DataUpdateCoordinator for the Provident Energy integration."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .api import (
    ProvidentAPIClient,
    ProvidentAuthError,
    ProvidentConnError,
    ProvidentAPIError,
    parse_quickgraphs_response,
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
    spot_name: str | None = None
    spots: dict[str, dict[str, Any]] = field(default_factory=dict)
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
    daily_hourly_history: dict[str, list[float]] = field(default_factory=dict)  # "YYYY-MM-DD" -> 24 hourly floats
    daily_totals_history: dict[str, float] = field(default_factory=dict)  # "YYYY-MM-DD" -> day total
    hourly_breakdown_past_days: list[dict[str, Any]] = field(default_factory=list)  # list of day dicts
    last_updated: datetime = field(default_factory=dt_util.utcnow)


def extract_spot_info(name: str) -> tuple[str, str | None]:
    """Extract clean base utility name and parking spot identifier if present.

    Examples:
        'EV - Spot P2-14' -> ('EV', 'Spot P2-14')
        'EV Charging (Spot 42)' -> ('EV Charging', 'Spot 42')
        'EV [P1-102]' -> ('EV', 'P1-102')
        'EV Spot #15' -> ('EV', 'Spot #15')
        'Electricity' -> ('Electricity', None)
    """
    if not name:
        return name, None

    patterns = [
        r"[\(\[\-–—:]?\s*(spot\s*#?\s*[\w\d-]+)\s*[\)\]]?",
        r"[\(\[\-–—:]?\s*(stall\s*#?\s*[\w\d-]+)\s*[\)\]]?",
        r"[\(\[\-–—:]?\s*(parking\s*#?\s*[\w\d-]+)\s*[\)\]]?",
        r"[\(\[]\s*([pP]\d+[-_]?\d+)\s*[\)\]]",
        r"[-–—:]\s*([pP]\d+[-_]?\d+)\s*$",
    ]

    for pat in patterns:
        match = re.search(pat, name, re.IGNORECASE)
        if match:
            spot = match.group(1).strip()
            clean_name = re.sub(pat, "", name, flags=re.IGNORECASE).strip(" -–—:()[]")
            return clean_name or name, spot

    return name, None


def extract_meters_from_tree(tree_data: Any) -> list[dict[str, str]]:
    """Recursively extract all meter and group descriptors from meter tree."""
    meters: list[dict[str, str]] = []
    if isinstance(tree_data, list):
        for item in tree_data:
            meters.extend(extract_meters_from_tree(item))
    elif isinstance(tree_data, dict):
        node_id = str(tree_data.get("id") or tree_data.get("Id") or "")
        node_text = str(
            tree_data.get("text")
            or tree_data.get("Text")
            or tree_data.get("name")
            or tree_data.get("Name")
            or ""
        )
        node_type = str(tree_data.get("type") or tree_data.get("Type") or "")

        if node_text and node_id and node_id.lower() != "root":
            meters.append(
                {
                    "id": node_id,
                    "name": node_text.strip(),
                    "type": node_type,
                }
            )

        children = (
            tree_data.get("children")
            or tree_data.get("Children")
            or tree_data.get("childNodes")
            or []
        )
        if isinstance(children, list):
            for child in children:
                meters.extend(extract_meters_from_tree(child))
    return meters


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

        hierarchy: dict[str, Any] = {}
        try:
            res = await self.client.get_meter_hierarchy()
            if isinstance(res, dict):
                hierarchy = res
            discovered_meters = hierarchy.get("meters", {})
            if isinstance(discovered_meters, dict):
                for m_id, m_info in discovered_meters.items():
                    if isinstance(m_info, dict):
                        name = m_info.get("name")
                        if name and name not in utilities and name.lower() not in ("root", "meters", "all"):
                            clean, spot = extract_spot_info(name)
                            if not spot and name not in utilities:
                                utilities.append(name)
        except Exception as err:
            _LOGGER.debug("Meter tree hierarchy discovery skipped or returned: %s", err)

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

                # Preserve any previously fetched on-demand history for this utility
                daily_hourly_history: dict[str, list[float]] = {}
                daily_totals_history: dict[str, float] = {}
                if self.data and utility in self.data:
                    daily_hourly_history = dict(self.data[utility].daily_hourly_history)
                    daily_totals_history = dict(self.data[utility].daily_totals_history)

                # Always maintain yesterday in historical breakdown
                yesterday_date_str = yesterday.isoformat()
                daily_hourly_history[yesterday_date_str] = yesterday_hourly
                daily_totals_history[yesterday_date_str] = yesterday_total

                # Ensure past 7 days are cached in daily_hourly_history (only queries missing dates)
                missing_dates = [
                    (today - timedelta(days=i))
                    for i in range(1, 8)
                    if (today - timedelta(days=i)).isoformat() not in daily_hourly_history
                ]
                if missing_dates:
                    try:
                        hist_res = await asyncio.gather(
                            *(self.client.get_chart_data(utility, "day", d) for d in missing_dates),
                            return_exceptions=True,
                        )
                        for d, res in zip(missing_dates, hist_res):
                            d_str = d.isoformat()
                            if isinstance(res, dict) and "data" in res and res["data"]:
                                daily_hourly_history[d_str] = res["data"]
                                daily_totals_history[d_str] = round(sum(res["data"]), 4)
                    except Exception as err:
                        _LOGGER.debug("Past days history prefetch error for %s: %s", utility, err)

                hourly_breakdown_past_days = [
                    {
                        "date": d_str,
                        "total": daily_totals_history[d_str],
                        "hourly": daily_hourly_history[d_str],
                    }
                    for d_str in sorted(daily_hourly_history.keys(), reverse=True)
                ]

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
                    else:
                        for d_str in sorted(daily_hourly_history.keys(), reverse=True):
                            nz = [v for v in daily_hourly_history[d_str] if v > 0]
                            if nz:
                                latest_reading = round(nz[-1], 4)
                                break
                        if latest_reading == 0.0 and last_30_days_daily:
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

                # Extract parking spot identifier if present in meter name
                clean_name, spot_name = extract_spot_info(utility)

                # Portal total: the prominent number from the portal card (e.g. 402 kWh)
                portal_total = last_30_days_total if last_30_days_total > 0 else (month_total or year_total or yesterday_total)

                data_by_utility[utility] = ProvidentUtilityData(
                    name=utility,
                    units=units,
                    spot_name=spot_name,
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
                    daily_hourly_history=daily_hourly_history,
                    daily_totals_history=daily_totals_history,
                    hourly_breakdown_past_days=hourly_breakdown_past_days,
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
        # 6. Fetch spot-level breakdown via QuickGraphs (as observed in HAR)
        ev_key = next((u for u in data_by_utility.keys() if "ev" in u.lower()), None)
        if isinstance(hierarchy, dict) and isinstance(hierarchy.get("meter_list"), list) and hierarchy["meter_list"]:
            try:
                # Query quickgraphs with aggregateGroups=false to get individual meter/spot series for yesterday
                qg_raw = await self.client.get_quickgraphs(
                    meter_list=hierarchy["meter_list"],
                    start_date=yesterday,
                    end_date=today,
                    aggregate_groups=False,
                )
                series_list = parse_quickgraphs_response(qg_raw)

                # Also query quickgraphs for month-to-date breakdown
                month_totals_by_id: dict[str, float] = {}
                month_readings_by_id: dict[str, list[Any]] = {}
                month_totals_by_name: dict[str, float] = {}
                try:
                    qg_month_raw = await self.client.get_quickgraphs(
                        meter_list=hierarchy["meter_list"],
                        start_date=first_of_month,
                        end_date=today,
                        aggregate_groups=False,
                    )
                    series_month_list = parse_quickgraphs_response(qg_month_raw)
                    for sm in series_month_list:
                        sm_id = sm.get("meter_id")
                        sm_name = sm.get("name")
                        if sm_id:
                            month_totals_by_id[sm_id] = sm.get("total", 0.0)
                            month_readings_by_id[sm_id] = sm.get("data", [])
                        if sm_name:
                            month_totals_by_name[sm_name] = sm.get("total", 0.0)
                except Exception as err_m:
                    _LOGGER.debug("Failed fetching month spot breakdown from quickgraphs: %s", err_m)

                spot_data: dict[str, dict[str, Any]] = {}
                for s in series_list:
                    s_name = s.get("name") or ""
                    s_id = s.get("meter_id") or ""
                    clean, spot = extract_spot_info(s_name)
                    spot_label = spot or s_name

                    m_details = hierarchy.get("meters", {}).get(s_id, {})
                    parent_gid = m_details.get("parent_group")
                    parent_gname = (
                        hierarchy.get("groups", {}).get(parent_gid, {}).get("name", "").lower()
                        if parent_gid
                        else ""
                    )

                    is_ev_or_spot = bool(
                        spot
                        or "ev" in s_name.lower()
                        or "spot" in s_name.lower()
                        or "stall" in s_name.lower()
                        or "charger" in s_name.lower()
                        or "ev" in parent_gname
                        or "parking" in parent_gname
                    )

                    if is_ev_or_spot and spot_label:
                        m_total = month_totals_by_id.get(s_id, month_totals_by_name.get(s_name, 0.0))
                        m_readings = month_readings_by_id.get(s_id, [])
                        spot_data[spot_label] = {
                            "meter_id": s_id,
                            "name": s_name,
                            "spot_name": spot_label,
                            "yesterday_total": s.get("total", 0.0),
                            "month_total": m_total,
                            "units": s.get("units", "kWh"),
                            "readings": s.get("data", []),
                            "month_readings": m_readings,
                        }

                if spot_data and ev_key and ev_key in data_by_utility:
                    data_by_utility[ev_key].spots = spot_data
                    if len(spot_data) == 1:
                        data_by_utility[ev_key].spot_name = list(spot_data.keys())[0]
                    _LOGGER.debug("Discovered %d spot(s) for %s: %s", len(spot_data), ev_key, list(spot_data.keys()))

            except Exception as err:
                _LOGGER.debug("QuickGraphs spot-level query skipped: %s", err)

        return data_by_utility

    async def async_fetch_historical_hourly(
        self,
        utility_name: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        days: int = 7,
        update_entities: bool = True,
    ) -> dict[str, Any]:
        """Fetch historical hourly breakdown on demand for specific date range or past N days."""
        await self._async_ensure_login()
        now = dt_util.now()
        today = now.date()

        # Determine utilities to query
        if utility_name and utility_name.strip().lower() not in ("all", "*", ""):
            u_clean = utility_name.strip()
            target_utilities = [u for u in self.data.keys() if u.lower() == u_clean.lower()]
            if not target_utilities:
                target_utilities = [u_clean]
        else:
            target_utilities = list(self.data.keys()) or await self.client.get_utilities()

        # Determine dates to query
        if start_date and end_date:
            dates = []
            curr = start_date
            while curr <= end_date:
                dates.append(curr)
                curr += timedelta(days=1)
        else:
            if not end_date:
                end_date = today - timedelta(days=1)
            num_days = max(1, min(int(days), 90))
            dates = [end_date - timedelta(days=i) for i in range(num_days)]

        results_by_utility: dict[str, Any] = {}

        for utility in target_utilities:
            day_results = await asyncio.gather(
                *(self.client.get_chart_data(utility, "day", d) for d in dates),
                return_exceptions=True,
            )

            hourly_map: dict[str, list[float]] = {}
            totals_map: dict[str, float] = {}

            for d, res in zip(dates, day_results):
                d_str = d.isoformat()
                if isinstance(res, dict) and "data" in res:
                    h_data = res["data"]
                    hourly_map[d_str] = h_data
                    totals_map[d_str] = round(sum(h_data), 4)
                else:
                    hourly_map[d_str] = []
                    totals_map[d_str] = 0.0

            results_by_utility[utility] = {
                "utility": utility,
                "readings": hourly_map,
                "totals": totals_map,
                "breakdown": [
                    {"date": d_str, "total": totals_map[d_str], "hourly": hourly_map[d_str]}
                    for d_str in sorted(hourly_map.keys(), reverse=True)
                ],
            }

            # Update coordinator cache & entities if requested
            if update_entities and self.data and utility in self.data:
                util_data = self.data[utility]
                util_data.daily_hourly_history.update(hourly_map)
                util_data.daily_totals_history.update(totals_map)
                util_data.hourly_breakdown_past_days = [
                    {
                        "date": d_str,
                        "total": util_data.daily_totals_history[d_str],
                        "hourly": util_data.daily_hourly_history[d_str],
                    }
                    for d_str in sorted(util_data.daily_hourly_history.keys(), reverse=True)
                ]

        if update_entities and self.data:
            self.async_set_updated_data(dict(self.data))

        return results_by_utility
