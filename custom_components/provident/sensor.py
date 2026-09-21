"""Sensor platform for the Provident Energy integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfEnergy,
    UnitOfPower,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    CONF_BASE_URL,
    DEFAULT_BASE_URL,
    DEFAULT_ICON,
    DOMAIN,
    SENSOR_TYPE_LAST_30_DAYS,
    SENSOR_TYPE_LATEST,
    SENSOR_TYPE_MONTH,
    SENSOR_TYPE_TODAY,
    SENSOR_TYPE_YEAR,
    SENSOR_TYPE_YESTERDAY,
    UTILITY_ICONS,
)
from .coordinator import (
    ProvidentDataUpdateCoordinator,
    ProvidentUtilityData,
    clean_spot_name,
)

_LOGGER = logging.getLogger(__name__)

SENSOR_TYPE_PRIMARY = "usage"


@dataclass(frozen=True, kw_only=True)
class ProvidentSensorEntityDescription(SensorEntityDescription):
    """Class describing Provident sensor entities."""

    value_fn: Callable[[ProvidentUtilityData], StateType]
    attributes_fn: Callable[[ProvidentUtilityData], dict[str, Any]] | None = None


def _get_device_class_and_unit(
    utility_name: str, reported_unit: str
) -> tuple[SensorDeviceClass | None, str | None]:
    """Determine the appropriate SensorDeviceClass and native unit of measurement."""
    name_lower = utility_name.lower().strip()
    unit_lower = reported_unit.lower().strip()

    # Electricity and EV / EV Charging
    if (
        "electr" in name_lower
        or name_lower == "ev"
        or name_lower.startswith("ev ")
        or "ev charging" in name_lower
        or "ev charger" in name_lower
    ):
        if unit_lower in ("kwh", "kw.h", "kw-h"):
            return SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR
        if unit_lower in ("wh",):
            return SensorDeviceClass.ENERGY, UnitOfEnergy.WATT_HOUR
        if unit_lower in ("mwh",):
            return SensorDeviceClass.ENERGY, UnitOfEnergy.MEGA_WATT_HOUR
        return SensorDeviceClass.ENERGY, reported_unit or UnitOfEnergy.KILO_WATT_HOUR

    # Gas
    if "gas" in name_lower:
        if unit_lower in ("m3", "m³"):
            return SensorDeviceClass.GAS, UnitOfVolume.CUBIC_METERS
        if unit_lower in ("ccf",):
            return SensorDeviceClass.GAS, UnitOfVolume.CENTUM_CUBIC_FEET
        if unit_lower in ("ft3", "ft³", "cu ft"):
            return SensorDeviceClass.GAS, UnitOfVolume.CUBIC_FEET
        return SensorDeviceClass.GAS, reported_unit or UnitOfVolume.CUBIC_METERS

    # Water (Hot Water / Cold Water)
    if "water" in name_lower:
        if unit_lower in ("kwh",):
            return SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR
        if unit_lower in ("m3", "m³"):
            return SensorDeviceClass.WATER, UnitOfVolume.CUBIC_METERS
        if unit_lower in ("l", "liter", "liters", "litre", "litres"):
            return SensorDeviceClass.WATER, UnitOfVolume.LITERS
        if unit_lower in ("gal", "gals", "gallon", "gallons"):
            return SensorDeviceClass.WATER, UnitOfVolume.GALLONS
        if unit_lower in ("ccf",):
            return SensorDeviceClass.WATER, UnitOfVolume.CENTUM_CUBIC_FEET
        return SensorDeviceClass.WATER, reported_unit or UnitOfVolume.CUBIC_METERS

    # Cooling & Heating
    if "cool" in name_lower or "heat" in name_lower:
        if unit_lower in ("kwh", "kw.h"):
            return SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR
        if unit_lower in ("btu", "kbtu"):
            return SensorDeviceClass.ENERGY, reported_unit
        if unit_lower in ("ton-hr", "ton-hour", "ton-hours"):
            return SensorDeviceClass.ENERGY, reported_unit
        if unit_lower in ("m3", "m³"):
            return SensorDeviceClass.WATER, UnitOfVolume.CUBIC_METERS
        return SensorDeviceClass.ENERGY, reported_unit or UnitOfEnergy.KILO_WATT_HOUR

    # Generic fallback based on reported unit
    if unit_lower in ("kwh", "wh", "mwh", "btu", "kbtu", "gj", "mj"):
        return SensorDeviceClass.ENERGY, reported_unit
    if unit_lower in ("m3", "m³", "l", "gal", "ccf", "ft3", "ft³"):
        return SensorDeviceClass.WATER, reported_unit
    if unit_lower in ("kw", "w", "mw"):
        return SensorDeviceClass.POWER, reported_unit

    return None, reported_unit or None


def _build_sensor_descriptions(
    utility_name: str, reported_unit: str
) -> list[ProvidentSensorEntityDescription]:
    """Build sensor descriptions for a given utility."""
    dev_class, native_unit = _get_device_class_and_unit(utility_name, reported_unit)
    icon = UTILITY_ICONS.get(utility_name, DEFAULT_ICON)

    return [
        # 1. Primary Portal Meter Sensor (Matches Portal Card value e.g. 402 kWh Electricity, 116 kWh EV)
        ProvidentSensorEntityDescription(
            key=SENSOR_TYPE_PRIMARY,
            name=None,
            device_class=dev_class,
            state_class=SensorStateClass.TOTAL,
            native_unit_of_measurement=native_unit,
            icon=icon,
            value_fn=lambda data: data.portal_total,
            attributes_fn=lambda data: {
                "portal_card_total": data.portal_total,
                "last_30_days_total": data.last_30_days_total,
                "yesterday_total": data.yesterday_total,
                "month_to_date": data.month_total,
                "year_to_date": data.year_total,
                "latest_hourly_reading": data.latest_reading,
                "daily_readings_30d": data.last_30_days_daily,
                "yesterday_hourly_readings": data.yesterday_hourly,
                "historical_hourly_by_date": data.daily_hourly_history,
                "historical_daily_totals": data.daily_totals_history,
                "hourly_breakdown_past_days": data.hourly_breakdown_past_days,
                "spot_name": data.spot_name,
                "spots": data.spots,
                "spot_names": list(data.spots.keys()),
                "spot_yesterday_totals": {k: v.get("yesterday_total", 0.0) for k, v in data.spots.items()},
                "spot_month_totals": {k: v.get("month_total", 0.0) for k, v in data.spots.items()},
                "meter_units": data.units,
                "last_updated": data.last_updated.isoformat() if data.last_updated else None,
            },
        ),
        # 2. Yesterday (Previous Day completed 24-hour reading)
        ProvidentSensorEntityDescription(
            key=SENSOR_TYPE_YESTERDAY,
            name="Yesterday",
            device_class=dev_class,
            state_class=SensorStateClass.TOTAL,
            native_unit_of_measurement=native_unit,
            icon=icon,
            value_fn=lambda data: data.yesterday_total,
            attributes_fn=lambda data: {
                "reading_date": data.yesterday_date,
                "hourly_readings": data.yesterday_hourly,
                "historical_hourly_by_date": data.daily_hourly_history,
                "historical_daily_totals": data.daily_totals_history,
                "latest_hourly_reading": data.latest_reading,
                "spot_name": data.spot_name,
                "spots": data.spots,
                "spot_names": list(data.spots.keys()),
                "spot_yesterday_totals": {k: v.get("yesterday_total", 0.0) for k, v in data.spots.items()},
                "spot_month_totals": {k: v.get("month_total", 0.0) for k, v in data.spots.items()},
                "meter_units": data.units,
                "last_updated": data.last_updated.isoformat() if data.last_updated else None,
            },
        ),
        # 3. Last 30 Days (Direct 30-Day Homepage Card)
        ProvidentSensorEntityDescription(
            key=SENSOR_TYPE_LAST_30_DAYS,
            name="Last 30 Days",
            device_class=dev_class,
            state_class=SensorStateClass.TOTAL,
            native_unit_of_measurement=native_unit,
            icon=icon,
            value_fn=lambda data: data.last_30_days_total,
            attributes_fn=lambda data: {
                "daily_readings": data.last_30_days_daily,
                "meter_units": data.units,
                "last_updated": data.last_updated.isoformat() if data.last_updated else None,
            },
        ),
        # 4. This Month (Month-to-Date)
        ProvidentSensorEntityDescription(
            key=SENSOR_TYPE_MONTH,
            name="This Month",
            device_class=dev_class,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=native_unit,
            icon=icon,
            value_fn=lambda data: data.month_total,
            attributes_fn=lambda data: {
                "daily_readings": data.month_daily,
                "spot_name": data.spot_name,
                "spots": data.spots,
                "spot_names": list(data.spots.keys()),
                "spot_yesterday_totals": {k: v.get("yesterday_total", 0.0) for k, v in data.spots.items()},
                "spot_month_totals": {k: v.get("month_total", 0.0) for k, v in data.spots.items()},
                "meter_units": data.units,
                "last_updated": data.last_updated.isoformat() if data.last_updated else None,
            },
        ),
        # 5. This Year (Year-to-Date)
        ProvidentSensorEntityDescription(
            key=SENSOR_TYPE_YEAR,
            name="This Year",
            device_class=dev_class,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=native_unit,
            icon=icon,
            value_fn=lambda data: data.year_total,
            attributes_fn=lambda data: {
                "monthly_readings": data.year_monthly,
                "meter_units": data.units,
                "last_updated": data.last_updated.isoformat() if data.last_updated else None,
            },
        ),
        # 6. Today (Current Day - populated as posted)
        ProvidentSensorEntityDescription(
            key=SENSOR_TYPE_TODAY,
            name="Today",
            device_class=dev_class,
            state_class=SensorStateClass.TOTAL,
            native_unit_of_measurement=native_unit,
            icon=icon,
            value_fn=lambda data: data.today_total,
            attributes_fn=lambda data: {
                "hourly_readings": data.today_hourly,
                "meter_units": data.units,
                "last_updated": data.last_updated.isoformat() if data.last_updated else None,
            },
        ),
        # 7. Latest Reading
        ProvidentSensorEntityDescription(
            key=SENSOR_TYPE_LATEST,
            name="Latest Reading",
            device_class=dev_class,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=native_unit,
            icon=icon,
            value_fn=lambda data: data.latest_reading,
            attributes_fn=lambda data: {
                "meter_units": data.units,
                "last_updated": data.last_updated.isoformat() if data.last_updated else None,
            },
        ),
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Provident Energy sensor platform."""
    coordinator: ProvidentDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    if not coordinator.data:
        _LOGGER.warning("No utility data available to create Provident sensors")
        return

    entities: list[ProvidentSensorEntity] = []

    for utility_name, utility_data in coordinator.data.items():
        descriptions = _build_sensor_descriptions(utility_name, utility_data.units)
        for desc in descriptions:
            entities.append(
                ProvidentSensorEntity(
                    coordinator=coordinator,
                    entry=entry,
                    utility_name=utility_name,
                    description=desc,
                )
            )

        # If multiple parking spots exist, register dedicated per-spot sensors (yesterday and month)
        for spot_name in utility_data.spots:
            if len(utility_data.spots) > 1:
                entities.append(
                    ProvidentSpotSensorEntity(
                        coordinator=coordinator,
                        entry=entry,
                        utility_name=utility_name,
                        spot_name=spot_name,
                        sensor_type="yesterday",
                    )
                )
                entities.append(
                    ProvidentSpotSensorEntity(
                        coordinator=coordinator,
                        entry=entry,
                        utility_name=utility_name,
                        spot_name=spot_name,
                        sensor_type="month",
                    )
                )

    async_add_entities(entities)


class ProvidentSpotSensorEntity(CoordinatorEntity[ProvidentDataUpdateCoordinator], SensorEntity):
    """Representation of an individual parking spot EV sub-meter."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_icon = "mdi:ev-station"

    def __init__(
        self,
        coordinator: ProvidentDataUpdateCoordinator,
        entry: ConfigEntry,
        utility_name: str,
        spot_name: str,
        sensor_type: str = "yesterday",
    ) -> None:
        """Initialize spot sub-meter."""
        super().__init__(coordinator)
        self.entry = entry
        self.utility_name = utility_name
        self.spot_name = spot_name
        self.sensor_type = sensor_type

        clean_spot = clean_spot_name(spot_name, utility_name)
        spot_slug = slugify(clean_spot)
        utility_slug = slugify(utility_name)

        # In Home Assistant with _attr_has_entity_name = True,
        # HA names the entity as "{device_name} {entity_name}"
        # Setting _attr_name to just "Yesterday" or "This Month" prevents
        # repeated naming like "Provident EV - Spot 1 Spot 1 Yesterday"
        self._attr_name = "This Month" if sensor_type == "month" else "Yesterday"
        self._attr_unique_id = f"{entry.entry_id}_{utility_slug}_{spot_slug}_{sensor_type}"
        self._attr_suggested_object_id = f"provident_{utility_slug}_{spot_slug}_{sensor_type}"
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_state_class = (
            SensorStateClass.TOTAL_INCREASING if sensor_type == "month" else SensorStateClass.TOTAL
        )

        base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{utility_slug}_{spot_slug}")},
            name=f"Provident {utility_name} - {clean_spot}",
            manufacturer="Provident Energy",
            model=f"{clean_spot} EV Sub-meter",
            configuration_url=base_url,
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def spot_data(self) -> dict[str, Any]:
        """Return spot details from coordinator."""
        if not self.coordinator.data:
            return {}
        u_data = self.coordinator.data.get(self.utility_name)
        if not u_data:
            return {}
        return u_data.spots.get(self.spot_name, {})

    @property
    def native_value(self) -> StateType:
        """Return total for this spot (yesterday or month-to-date)."""
        if self.sensor_type == "month":
            return self.spot_data.get("month_total", 0.0)
        return self.spot_data.get("yesterday_total", 0.0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return spot attributes."""
        sd = self.spot_data
        attrs: dict[str, Any] = {
            "spot_name": self.spot_name,
            "meter_id": sd.get("meter_id"),
            "meter_units": sd.get("units", "kWh"),
        }
        if self.sensor_type == "month":
            attrs["daily_readings"] = sd.get("month_readings", [])
            attrs["month_total"] = sd.get("month_total", 0.0)
        else:
            attrs["hourly_readings"] = sd.get("readings", [])
            attrs["yesterday_total"] = sd.get("yesterday_total", 0.0)
        return attrs


class ProvidentSensorEntity(CoordinatorEntity[ProvidentDataUpdateCoordinator], SensorEntity):
    """Representation of a Provident Energy consumption sensor."""

    entity_description: ProvidentSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ProvidentDataUpdateCoordinator,
        entry: ConfigEntry,
        utility_name: str,
        description: ProvidentSensorEntityDescription,
    ) -> None:
        """Initialize the Provident sensor."""
        super().__init__(coordinator)
        self.entry = entry
        self.utility_name = utility_name
        self.entity_description = description

        utility_slug = slugify(utility_name)
        # Unique ID combining entry_id, utility slug, and sensor type key
        self._attr_unique_id = f"{entry.entry_id}_{utility_slug}_{description.key}"

        # Per-utility device info linked to the main Provident account hub
        base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{utility_slug}")},
            name=f"Provident {utility_name}",
            manufacturer="Provident Energy",
            model=f"{utility_name} Sub-meter",
            configuration_url=base_url,
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def utility_data(self) -> ProvidentUtilityData | None:
        """Return the current data for this utility."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self.utility_name)

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        data = self.utility_data
        if data is None:
            return None
        return self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.utility_data
        if data is None or self.entity_description.attributes_fn is None:
            return {}
        return self.entity_description.attributes_fn(data)

    @property
    def available(self) -> bool:
        """Return True if coordinator is available and utility is in data."""
        return super().available and self.utility_data is not None
