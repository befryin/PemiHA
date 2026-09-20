"""Native asynchronous API client for Provident Energy (MeterConnex)."""
from __future__ import annotations

from datetime import date
import json
import logging
import re
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://provident.meterconnex.com",
    "Referer": "https://provident.meterconnex.com/secure/Dashboard/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
}


def _unwrap_response(response: httpx.Response) -> Any:
    """Unwrap ASP.NET AJAX PageMethod response format with recursive parsing."""
    if not response.text or not response.text.strip():
        return None
    try:
        data = response.json()
    except Exception:
        return response.text

    if isinstance(data, dict) and "d" in data:
        inner = data["d"]
        if isinstance(inner, str):
            if not inner.strip():
                return {}
            try:
                parsed = json.loads(inner)
                return parsed
            except Exception:
                return inner
        return inner
    return data


def extract_numeric_list(data: Any) -> list[float]:
    """Extract list of numbers from any structure (graphData, data, points, series, or list)."""
    if isinstance(data, list):
        out: list[float] = []
        for item in data:
            if isinstance(item, (int, float)):
                out.append(float(item))
            elif isinstance(item, dict):
                found = False
                for k in ("y", "value", "val", "v", "usage", "consumption", "Total", "total"):
                    if k in item and item[k] is not None:
                        try:
                            out.append(float(item[k]))
                            found = True
                            break
                        except (ValueError, TypeError):
                            pass
                if not found:
                    out.append(0.0)
            elif isinstance(item, str):
                try:
                    out.append(float(item.replace(",", "").strip()))
                except ValueError:
                    out.append(0.0)
        return out
    if isinstance(data, dict):
        for k in (
            "graphData", "GraphData", "data", "Data", "values", "Values",
            "points", "Points", "series", "Series", "readings", "Readings"
        ):
            if k in data and isinstance(data[k], list):
                return extract_numeric_list(data[k])
    return []


def extract_total_and_units(data: Any) -> tuple[float, str]:
    """Extract total usage and units from any dict structure with case-insensitive fallback."""
    if not isinstance(data, dict):
        if isinstance(data, (int, float)):
            return float(data), ""
        return 0.0, ""

    total = 0.0
    units = ""

    # 1. Search for units
    for k in ("units", "Units", "unit", "Unit", "uom", "UOM", "meterUnits"):
        if k in data and data[k]:
            units = str(data[k]).strip()
            break

    # 2. Search for explicit total / usage fields
    found_total = False
    for k in (
        "total", "Total", "usage", "Usage", "consumption", "Consumption",
        "value", "Value", "current", "Current", "amount", "Amount", "reading", "Reading"
    ):
        if k in data and data[k] is not None:
            try:
                val_str = str(data[k]).replace(",", "").strip()
                # Split any combined strings like "402 kWh" or "3.136 m3*"
                val_clean = re.sub(r"[^\d.-]", " ", val_str).split()
                if val_clean:
                    total = float(val_clean[0])
                    found_total = True
                    # Check if unit was in the string e.g. "402 kWh"
                    parts = val_str.replace("*", "").split()
                    if len(parts) > 1 and not units:
                        units = parts[1]
                    break
            except (ValueError, TypeError, IndexError):
                pass

    # 3. If no explicit total found, sum up the numeric array
    num_list = extract_numeric_list(data)
    if not found_total and num_list:
        total = round(sum(num_list), 4)

    return total, units


class ProvidentAPIError(Exception):
    """Base exception for Provident API errors."""


class ProvidentAuthError(ProvidentAPIError):
    """Authentication failed."""


class ProvidentConnError(ProvidentAPIError):
    """Connection or network error."""


class ProvidentAPIClient:
    """Asynchronous client interacting with Provident MeterConnex API."""

    def __init__(self, base_url: str = "https://provident.meterconnex.com") -> None:
        """Initialize API client."""
        self.base_url = base_url.rstrip("/")
        headers = dict(DEFAULT_HEADERS)
        headers["Origin"] = self.base_url
        headers["Referer"] = f"{self.base_url}/secure/Dashboard/"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        )

    @property
    def is_authenticated(self) -> bool:
        """Check if auth cookie is present."""
        return ".ASPXAUTH" in self._client.cookies

    async def close(self) -> None:
        """Close HTTP client session."""
        await self._client.aclose()

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        """Perform POST request and unwrap response."""
        try:
            resp = await self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise ProvidentConnError(f"HTTP connection error: {exc}") from exc

        if resp.status_code == 401:
            raise ProvidentAuthError("Authentication expired or invalid.")
        if resp.status_code >= 400:
            raise ProvidentAPIError(f"HTTP {resp.status_code}: {resp.text}")

        return _unwrap_response(resp)

    async def login(self, username: str, password: str, remember_me: bool = False) -> bool:
        """Authenticate with Provident portal and initialize ASP.NET Session state."""
        data = await self._post(
            "/login/LoginService.aspx/ProcessLogin",
            {"username": username, "password": password, "rememberMe": remember_me},
        )
        if isinstance(data, dict) and data.get("success"):
            # CRUCIAL: Must visit /secure/Dashboard/ so ASP.NET executes Page_Load
            # and populates Session["CurrentAccount"], Session["MeterList"], etc.
            try:
                resp = await self._client.get("/secure/Dashboard/")
                _LOGGER.debug("Initialized dashboard session state, status: %s", resp.status_code)
            except Exception as err:
                _LOGGER.warning("Failed to initialize dashboard session: %s", err)
            return True

        msg = data.get("msg") if isinstance(data, dict) else "Invalid credentials"
        _LOGGER.warning("Provident login failed for %s: %s", username, msg)
        return False

    async def check_login(self) -> bool:
        """Verify if current session is active."""
        try:
            resp = await self._client.get("/login/LoginService.aspx/CheckLogin")
            data = _unwrap_response(resp)
            return bool(data)
        except Exception:
            return False

    async def get_utilities(self) -> list[str]:
        """Fetch list of available utilities (e.g. Electricity, EV, Hot Water, Cooling, Heating)."""
        data = await self._post("/secure/Dashboard/Default.aspx/GetUtilities", {})
        if isinstance(data, list):
            return [str(u) for u in data if u]
        return []

    async def get_card_data(self, utility: str, period: int = 30) -> dict[str, Any]:
        """Fetch summary card data (30-day breakdown from homepage)."""
        data = await self._post(
            "/secure/Dashboard/Default.aspx/UpdateCard",
            {"utility": utility, "period": period},
        )
        total, units = extract_total_and_units(data)
        data_list = extract_numeric_list(data)

        last_updated = None
        if isinstance(data, dict):
            last_updated = (
                data.get("lastUpdated")
                or data.get("LastUpdated")
                or data.get("lastUpdate")
                or data.get("date")
            )

        return {
            "total": total,
            "units": units,
            "data": data_list,
            "last_updated": last_updated,
            "raw": data if isinstance(data, dict) else {},
        }

    async def get_chart_data(self, utility: str, period: str, start: date) -> dict[str, Any]:
        """Fetch chart breakdown data for day/month/year."""
        start_str = start.strftime("%Y-%m-%d")
        data = await self._post(
            "/secure/Dashboard/Default.aspx/GetChartData",
            {"utility": utility, "period": period, "start": start_str},
        )
        total, units = extract_total_and_units(data)
        data_list = extract_numeric_list(data)
        error = False
        if isinstance(data, dict):
            error = bool(data.get("error", False))

        return {
            "error": error,
            "total": total,
            "units": units,
            "data": data_list,
        }
