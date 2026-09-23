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
        r"[\(\[\-–—:_/]?\s*(spot\s*#?\s*[\w\d-]+)\s*[\)\]]?",
        r"[\(\[\-–—:_/]?\s*(stall\s*#?\s*[\w\d-]+)\s*[\)\]]?",
        r"[\(\[\-–—:_/]?\s*(charger\s*#?\s*[\w\d-]+)\s*[\)\]]?",
        r"[\(\[\-–—:_/]?\s*(parking\s*#?\s*[\w\d-]+)\s*[\)\]]?",
        r"[\(\[]\s*([pP]\d+[-_]?\d+)\s*[\)\]]",
        r"[-–—:_/]\s*([pP]\d+[-_]?\d+)\s*$",
        r"[-–—:_/]\s*(\d+)\s*$",
    ]

    for pat in patterns:
        match = re.search(pat, name, re.IGNORECASE)
        if match:
            spot = match.group(1).strip()
            clean_name = re.sub(pat, "", name, flags=re.IGNORECASE).strip(" -–—:_/()[]")
            return clean_name or name, spot

    return name, None


def clean_spot_name(name: str, utility_name: str = "EV") -> str:
    """Clean and normalize spot name, removing redundant utility prefixes.

    Prevents doubled names such as 'EV - EV Spot 1' or 'ev_spot_1_ev_spot_1'.
    """
    if not name:
        return ""
    # Strip utility prefix if present (e.g. "EV - Spot 1" -> "Spot 1", "EV Spot 1" -> "Spot 1")
    u_pat = rf"^{re.escape(utility_name)}\s*[-–—:_/]?\s*"
    cleaned = re.sub(u_pat, "", name.strip(), flags=re.IGNORECASE).strip(" -–—:_#()[]")
    if not cleaned:
        cleaned = name.strip()
    cleaned = re.sub(r"_+", " ", cleaned).strip()
    if re.match(r"^[pP]\d+[-_]?\d+$", cleaned):
        cleaned = f"Spot {cleaned.upper()}"
    elif re.match(r"^\d+$", cleaned):
        cleaned = f"Spot {cleaned}"
    elif re.match(r"^(spot|stall|charger|parking)\s*#?\s*(\w+)$", cleaned, re.IGNORECASE):
        m = re.match(r"^(spot|stall|charger|parking)\s*#?\s*(\w+)$", cleaned, re.IGNORECASE)
        cleaned = f"{m.group(1).capitalize()} {m.group(2)}"
    return cleaned


def parse_timestamp_to_datetime(ts: Any) -> datetime | None:
    """Parse numeric epoch or ISO/ASP.NET string timestamp into local datetime."""
    if ts is None:
        return None

    # 1. Numeric epoch timestamp
    if isinstance(ts, (int, float)):
        try:
            sec = ts / 1000.0 if ts > 1e11 else (ts if ts > 1e8 else None)
            if sec is not None:
                if hasattr(dt_util, "as_local") and hasattr(dt_util, "utc_from_timestamp"):
                    return dt_util.as_local(dt_util.utc_from_timestamp(sec))
                return datetime.fromtimestamp(sec)
        except Exception:
            return None

    # 2. String timestamp
    if isinstance(ts, str):
        ts_clean = ts.strip()
        date_match = re.search(r"/Date\((\d+)\)/", ts_clean)
        if date_match:
            try:
                sec = int(date_match.group(1)) / 1000.0
                if hasattr(dt_util, "as_local") and hasattr(dt_util, "utc_from_timestamp"):
                    return dt_util.as_local(dt_util.utc_from_timestamp(sec))
                return datetime.fromtimestamp(sec)
            except Exception:
                return None
        if hasattr(dt_util, "parse_datetime"):
            parsed = dt_util.parse_datetime(ts_clean)
            if parsed:
                return dt_util.as_local(parsed) if hasattr(dt_util, "as_local") else parsed
        try:
            return datetime.fromisoformat(ts_clean)
        except Exception:
            pass

    return None


def parse_interval_points_to_daily_hourly(
    points: list[Any],
    target_dates: list[date] | None = None,
) -> dict[str, list[float]]:
    """Convert timestamped interval points into 24-hour hourly arrays mapped by YYYY-MM-DD."""
    daily_buckets: dict[str, list[float]] = {}
    target_date_strs = {d.isoformat() for d in target_dates} if target_dates else None
    has_timestamps = False

    for pt in points:
        ts = None
        val = 0.0

        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            ts = pt[0]
            try:
                val = float(pt[1]) if pt[1] is not None else 0.0
            except (ValueError, TypeError):
                val = 0.0
        elif isinstance(pt, dict):
            val_raw = pt.get("y", pt.get("value", pt.get("val", pt.get("reading", 0.0))))
            try:
                val = float(val_raw) if val_raw is not None else 0.0
            except (ValueError, TypeError):
                val = 0.0
            ts = pt.get("x", pt.get("date", pt.get("Date", pt.get("time", pt.get("Time", pt.get("timestamp"))))))

        if ts is not None:
            dt = parse_timestamp_to_datetime(ts)
            if dt is not None:
                has_timestamps = True
                d_str = dt.date().isoformat()
                if target_date_strs is None or d_str in target_date_strs:
                    if d_str not in daily_buckets:
                        daily_buckets[d_str] = [0.0] * 24
                    hr = dt.hour
                    if 0 <= hr < 24:
                        daily_buckets[d_str][hr] = round(daily_buckets[d_str][hr] + val, 4)

    # Fallback if points were non-timestamped flat list of 24 hourly floats for a single date
    if not has_timestamps and points:
        num_list = []
        for pt in points:
            if isinstance(pt, (int, float)):
                num_list.append(float(pt))
            elif isinstance(pt, (list, tuple)) and len(pt) >= 1 and isinstance(pt[0], (int, float)):
                num_list.append(float(pt[0]))
        if len(num_list) == 24 and target_dates and len(target_dates) == 1:
            d_str = target_dates[0].isoformat()
            daily_buckets[d_str] = [round(v, 4) for v in num_list]

    return daily_buckets


def match_series_to_utility(
    series_name: str,
    meter_id: str,
    utilities: list[str] | dict[str, Any],
    hierarchy: dict[str, Any] | None = None,
) -> str | None:
    """Match a meter series from QuickGraphs to a known utility name."""
    s_clean = (series_name or "").strip()
    s_lower = s_clean.lower()
    m_id = (meter_id or "").strip()

    util_list = list(utilities.keys()) if isinstance(utilities, dict) else list(utilities)

    # 1. Exact case-insensitive match on series name
    for u in util_list:
        if s_lower == u.strip().lower():
            return u

    # 2. Check meter hierarchy info
    m_info = {}
    if hierarchy and isinstance(hierarchy.get("meters"), dict):
        m_info = hierarchy["meters"].get(m_id, {})
    m_name = (m_info.get("name") or "").strip().lower()
    parent_gid = m_info.get("parent_group")
    parent_gname = ""
    if hierarchy and isinstance(hierarchy.get("groups"), dict) and parent_gid:
        parent_gname = (hierarchy["groups"].get(parent_gid, {}).get("name") or "").strip().lower()

    for u in util_list:
        u_clean = u.strip().lower()
        if m_name and m_name == u_clean:
            return u
        if parent_gname and parent_gname == u_clean:
            return u

    # 3. Keyword / semantic matching
    combined = f"{s_lower} {m_name} {parent_gname}"

    # Hot Water vs Cold Water vs Water
    if "hot water" in combined or "dhw" in combined or ("water" in combined and "hot" in combined):
        hw = next((u for u in util_list if "hot" in u.lower() and "water" in u.lower()), None)
        if hw:
            return hw
    if "cold water" in combined or ("water" in combined and "cold" in combined):
        cw = next((u for u in util_list if "cold" in u.lower() and "water" in u.lower()), None)
        if cw:
            return cw
    if "water" in combined:
        w = next((u for u in util_list if "water" in u.lower()), None)
        if w:
            return w

    # Heating
    if "heating" in combined or "heat" in combined:
        ht = next((u for u in util_list if "heat" in u.lower()), None)
        if ht:
            return ht

    # Cooling / AC
    if any(k in combined for k in ("cooling", "cool", "chilled", "a/c", "ac")):
        cl = next((u for u in util_list if "cool" in u.lower() or "ac" in u.lower()), None)
        if cl:
            return cl

    # EV
    if any(k in combined for k in ("ev", "spot", "stall", "charger", "parking")):
        ev = next((u for u in util_list if "ev" in u.lower() or "charg" in u.lower()), None)
        if ev:
            return ev

    # Electricity
    if "electric" in combined or "power" in combined:
        el = next((u for u in util_list if "electr" in u.lower()), None)
        if el:
            return el

    # 4. Partial substring in utility name
    for u in util_list:
        u_clean = u.strip().lower()
        if u_clean in combined or any(word in combined for word in u_clean.split() if len(word) > 2):
            return u

    return None


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

        # Determine whether initial 7-day fetch is needed (startup/missing history) or 1-day daily refresh
        past_7_dates = [(today - timedelta(days=i)) for i in range(1, 8)]
        past_7_date_strs = {d.isoformat() for d in past_7_dates}

        is_initial_load = False
        if not self.data:
            is_initial_load = True
        else:
            for u in utilities:
                if u not in self.data:
                    is_initial_load = True
                    break
                u_history = self.data[u].daily_hourly_history
                if any(d_str not in u_history for d_str in past_7_date_strs):
                    is_initial_load = True
                    break

        if is_initial_load:
            qg_start_date = today - timedelta(days=7)
            _LOGGER.debug("Initial data load or missing days detected: querying 7 days from QuickGraphs")
        else:
            qg_start_date = yesterday
            _LOGGER.debug("Daily refresh: querying 1 day (yesterday) from QuickGraphs")
        qg_end_date = today

        # Fetch multi-meter interval readings via QuickGraphs (covers Heating, Cooling, Hot Water, EV spots, Electricity)
        qg_hourly_by_utility: dict[str, dict[str, list[float]]] = {}
        spot_data: dict[str, dict[str, Any]] = {}
        ev_key = next((u for u in utilities if "ev" in u.lower()), "EV")

        if isinstance(hierarchy, dict) and isinstance(hierarchy.get("meter_list"), list) and hierarchy["meter_list"]:
            try:
                # Query quickgraphs with aggregateGroups=false to get individual sub-meter/spot series
                qg_raw = await self.client.get_quickgraphs(
                    meter_list=hierarchy["meter_list"],
                    start_date=qg_start_date,
                    end_date=qg_end_date,
                    aggregate_groups=False,
                )
                series_list = parse_quickgraphs_response(qg_raw)

                # Also query quickgraphs for month-to-date breakdown for parking spots
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

                for s in series_list:
                    s_name = s.get("name") or ""
                    s_id = s.get("meter_id") or ""
                    s_pts = s.get("data") or []

                    # 1. Spot identification for EV charging stalls
                    clean, spot = extract_spot_info(s_name)
                    raw_spot = spot or s_name
                    spot_label = clean_spot_name(raw_spot, ev_key or "EV")

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

                    if is_ev_or_spot and spot_label and spot_label.lower() != (ev_key or "ev").lower():
                        m_total = month_totals_by_id.get(s_id, month_totals_by_name.get(s_name, 0.0))
                        m_readings = month_readings_by_id.get(s_id, [])
                        spot_data[spot_label] = {
                            "meter_id": s_id,
                            "name": s_name,
                            "spot_name": spot_label,
                            "yesterday_total": s.get("total", 0.0),
                            "month_total": m_total,
                            "units": s.get("units", "kWh"),
                            "readings": s_pts,
                            "month_readings": m_readings,
                        }

                    # 2. Match series to utility for hourly data
                    matched_u = match_series_to_utility(s_name, s_id, utilities, hierarchy)
                    if matched_u:
                        if matched_u not in qg_hourly_by_utility:
                            qg_hourly_by_utility[matched_u] = {}
                        daily_map = parse_interval_points_to_daily_hourly(s_pts)
                        for d_str, h_vals in daily_map.items():
                            if d_str not in qg_hourly_by_utility[matched_u]:
                                qg_hourly_by_utility[matched_u][d_str] = [0.0] * 24
                            for hr in range(24):
                                qg_hourly_by_utility[matched_u][d_str][hr] = round(
                                    qg_hourly_by_utility[matched_u][d_str][hr] + h_vals[hr], 4
                                )
            except Exception as err:
                _LOGGER.debug("QuickGraphs interval query error: %s", err)

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

                # Preserve any previously cached history for this utility
                daily_hourly_history: dict[str, list[float]] = {}
                daily_totals_history: dict[str, float] = {}
                if self.data and utility in self.data:
                    daily_hourly_history = dict(self.data[utility].daily_hourly_history)
                    daily_totals_history = dict(self.data[utility].daily_totals_history)

                # Merge QuickGraphs interval data for this utility
                if utility in qg_hourly_by_utility:
                    for d_str, h_vals in qg_hourly_by_utility[utility].items():
                        if d_str not in daily_hourly_history or not any(daily_hourly_history[d_str]):
                            daily_hourly_history[d_str] = h_vals
                            daily_totals_history[d_str] = round(sum(h_vals), 4)
                        elif any(h_vals) and sum(h_vals) > sum(daily_hourly_history[d_str]):
                            daily_hourly_history[d_str] = h_vals
                            daily_totals_history[d_str] = round(sum(h_vals), 4)

                # If yesterday from GetChartData was empty/zero, use QuickGraphs yesterday data
                yesterday_date_str = yesterday.isoformat()
                if yesterday_date_str in daily_hourly_history and any(daily_hourly_history[yesterday_date_str]):
                    if not any(yesterday_hourly) or sum(daily_hourly_history[yesterday_date_str]) > sum(yesterday_hourly):
                        yesterday_hourly = daily_hourly_history[yesterday_date_str]
                        yesterday_total = daily_totals_history[yesterday_date_str]
                else:
                    daily_hourly_history[yesterday_date_str] = yesterday_hourly
                    daily_totals_history[yesterday_date_str] = yesterday_total

                # If yesterday hourly sum is 0 but 30-day card has daily points, fallback to latest non-zero daily reading
                if yesterday_total == 0.0 and last_30_days_daily:
                    non_zero_days = [v for v in last_30_days_daily if v > 0]
                    if non_zero_days:
                        yesterday_total = round(non_zero_days[-1], 4)

                # Ensure past 7 days are in daily_hourly_history (only queries missing dates via GetChartData)
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

                # Keep rolling 7-day window (prune dates older than 8 days)
                allowed_dates = {(today - timedelta(days=i)).isoformat() for i in range(0, 9)}
                daily_hourly_history = {d: h for d, h in daily_hourly_history.items() if d in allowed_dates}
                daily_totals_history = {d: t for d, t in daily_totals_history.items() if d in allowed_dates}

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

                # Extract parking spot identifier if present in utility name
                clean_name, spot_name = extract_spot_info(utility)

                # Attach EV parking spots if this utility is EV
                util_spots = {}
                if spot_data and "ev" in utility.lower():
                    util_spots = spot_data
                    if len(spot_data) == 1 and not spot_name:
                        spot_name = list(spot_data.keys())[0]

                # Portal total: the prominent number from the portal card (e.g. 402 kWh)
                portal_total = last_30_days_total if last_30_days_total > 0 else (month_total or year_total or yesterday_total)

                data_by_utility[utility] = ProvidentUtilityData(
                    name=utility,
                    units=units,
                    spot_name=spot_name,
                    spots=util_spots,
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
                    "Fetched %s: PortalCard=%.3f %s, Yesterday=%.3f %s, Month=%.3f %s, Year=%.3f %s, HistoryDays=%d",
                    utility,
                    portal_total,
                    units,
                    yesterday_total,
                    units,
                    month_total,
                    units,
                    year_total,
                    units,
                    len(daily_hourly_history),
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

        # Pre-fetch QuickGraphs for requested date range to support sub-meters (Heating, Cooling, Hot Water)
        qg_hourly_by_u: dict[str, dict[str, list[float]]] = {}
        try:
            hierarchy = await self.client.get_meter_hierarchy()
            if isinstance(hierarchy, dict) and hierarchy.get("meter_list") and dates:
                qg_raw = await self.client.get_quickgraphs(
                    meter_list=hierarchy["meter_list"],
                    start_date=min(dates),
                    end_date=max(dates) + timedelta(days=1),
                    aggregate_groups=False,
                )
                qg_series = parse_quickgraphs_response(qg_raw)
                for s in qg_series:
                    s_name = s.get("name") or ""
                    s_id = s.get("meter_id") or ""
                    matched = match_series_to_utility(s_name, s_id, target_utilities, hierarchy)
                    if matched:
                        if matched not in qg_hourly_by_u:
                            qg_hourly_by_u[matched] = {}
                        daily_map = parse_interval_points_to_daily_hourly(s.get("data", []), target_dates=dates)
                        for d_str, h_vals in daily_map.items():
                            if d_str not in qg_hourly_by_u[matched]:
                                qg_hourly_by_u[matched][d_str] = [0.0] * 24
                            for hr in range(24):
                                qg_hourly_by_u[matched][d_str][hr] = round(
                                    qg_hourly_by_u[matched][d_str][hr] + h_vals[hr], 4
                                )
        except Exception as err:
            _LOGGER.debug("QuickGraphs fetch during historical hourly failed: %s", err)

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
                if isinstance(res, dict) and "data" in res and res["data"]:
                    h_data = res["data"]
                    hourly_map[d_str] = h_data
                    totals_map[d_str] = round(sum(h_data), 4)
                else:
                    hourly_map[d_str] = []
                    totals_map[d_str] = 0.0

                # If GetChartData gave empty/zero but QuickGraphs has data, use QuickGraphs
                if (not any(hourly_map[d_str])) and utility in qg_hourly_by_u and d_str in qg_hourly_by_u[utility]:
                    hourly_map[d_str] = qg_hourly_by_u[utility][d_str]
                    totals_map[d_str] = round(sum(qg_hourly_by_u[utility][d_str]), 4)

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
