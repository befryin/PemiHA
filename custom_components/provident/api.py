"""Native asynchronous API client for Provident Energy (MeterConnex)."""
from __future__ import annotations

from datetime import date
import json
import logging
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
}


def _unwrap_response(response: httpx.Response) -> Any:
    """Unwrap ASP.NET AJAX PageMethod response format."""
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
                return json.loads(inner)
            except Exception:
                return inner
        return inner
    return data


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
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=DEFAULT_HEADERS,
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
        """Authenticate with Provident portal."""
        data = await self._post(
            "/login/LoginService.aspx/ProcessLogin",
            {"username": username, "password": password, "rememberMe": remember_me},
        )
        if isinstance(data, dict) and data.get("success"):
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
        result = {
            "total": 0.0,
            "units": "",
            "data": [],
            "last_updated": None,
            "raw": data if isinstance(data, dict) else {},
        }
        if isinstance(data, dict):
            # Parse graphData / data array
            raw_list = data.get("graphData") or data.get("data") or []
            data_list = []
            for val in raw_list:
                try:
                    data_list.append(float(val))
                except (ValueError, TypeError):
                    data_list.append(0.0)
            result["data"] = data_list

            # Parse total consumption
            if "total" in data and data["total"] is not None:
                try:
                    result["total"] = float(data["total"])
                except (ValueError, TypeError):
                    result["total"] = round(sum(data_list), 4)
            elif "usage" in data and data["usage"] is not None:
                try:
                    result["total"] = float(data["usage"])
                except (ValueError, TypeError):
                    result["total"] = round(sum(data_list), 4)
            else:
                result["total"] = round(sum(data_list), 4)

            # Units & metadata
            result["units"] = str(data.get("units") or "")
            result["last_updated"] = data.get("lastUpdated") or data.get("date")

        return result

    async def get_chart_data(self, utility: str, period: str, start: date) -> dict[str, Any]:
        """Fetch chart breakdown data for day/month/year."""
        start_str = start.strftime("%Y-%m-%d")
        data = await self._post(
            "/secure/Dashboard/Default.aspx/GetChartData",
            {"utility": utility, "period": period, "start": start_str},
        )
        result = {
            "error": False,
            "units": "",
            "data": [],
        }
        if isinstance(data, dict):
            result["error"] = bool(data.get("error", False))
            result["units"] = str(data.get("units") or "")

            raw_list = data.get("graphData") or data.get("data") or []
            data_list = []
            for val in raw_list:
                try:
                    data_list.append(float(val))
                except (ValueError, TypeError):
                    data_list.append(0.0)
            result["data"] = data_list

        return result
