"""Constants for the Provident Energy integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "provident"

# Configuration constants
CONF_BASE_URL: Final = "base_url"
DEFAULT_BASE_URL: Final = "https://provident.meterconnex.com"

CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 30  # minutes
MIN_SCAN_INTERVAL: Final = 15  # minutes

# Known Utility Names (from Provident API / MeterConnex)
UTILITY_ELECTRICITY: Final = "Electricity"
UTILITY_EV: Final = "EV"
UTILITY_EV_CHARGING: Final = "EV Charging"
UTILITY_HOT_WATER: Final = "Hot Water"
UTILITY_COLD_WATER: Final = "Cold Water"
UTILITY_COOLING: Final = "Cooling"
UTILITY_HEATING: Final = "Heating"
UTILITY_GAS: Final = "Gas"

# Meter Icons mapping
UTILITY_ICONS: Final[dict[str, str]] = {
    UTILITY_ELECTRICITY: "mdi:flash",
    UTILITY_EV: "mdi:ev-station",
    UTILITY_EV_CHARGING: "mdi:ev-station",
    "EV Charger": "mdi:ev-station",
    UTILITY_HOT_WATER: "mdi:water-boiler",
    UTILITY_COLD_WATER: "mdi:water",
    "Water": "mdi:water",
    UTILITY_COOLING: "mdi:snowflake",
    UTILITY_HEATING: "mdi:radiator",
    UTILITY_GAS: "mdi:gas-burner",
}
DEFAULT_ICON: Final = "mdi:gauge"

# Sensor type identifiers
SENSOR_TYPE_YESTERDAY: Final = "yesterday"
SENSOR_TYPE_TODAY: Final = "today"
SENSOR_TYPE_LAST_30_DAYS: Final = "last_30_days"
SENSOR_TYPE_MONTH: Final = "this_month"
SENSOR_TYPE_YEAR: Final = "this_year"
SENSOR_TYPE_LATEST: Final = "latest_reading"
