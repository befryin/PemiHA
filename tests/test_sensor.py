"""Tests for Provident Energy sensor entities."""
from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock

from tests import conftest  # Load mocks

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant

from custom_components.provident.const import (
    DOMAIN,
    SENSOR_TYPE_LAST_30_DAYS,
    SENSOR_TYPE_LATEST,
    SENSOR_TYPE_MONTH,
    SENSOR_TYPE_TODAY,
    SENSOR_TYPE_YEAR,
    SENSOR_TYPE_YESTERDAY,
)
from custom_components.provident.coordinator import (
    ProvidentDataUpdateCoordinator,
    ProvidentUtilityData,
)
from custom_components.provident.sensor import (
    ProvidentSensorEntity,
    SENSOR_TYPE_PRIMARY,
    _build_sensor_descriptions,
    _get_device_class_and_unit,
    async_setup_entry,
)


class TestProvidentSensor(unittest.IsolatedAsyncioTestCase):
    """Test suite for Provident Sensor platform."""

    def setUp(self):
        """Set up mock environment."""
        self.hass = HomeAssistant()
        self.entry = ConfigEntry(
            entry_id="test_entry_id",
            data={"username": "test_user", "password": "pw"},
        )
        self.coordinator = MagicMock()
        self.coordinator.data = {}
        self.coordinator.last_update_success = True

    def test_device_class_and_unit_mapping(self):
        """Verify proper mapping of device classes and units."""
        # Electricity
        dc, unit = _get_device_class_and_unit("Electricity", "kWh")
        self.assertEqual(dc, SensorDeviceClass.ENERGY)
        self.assertEqual(unit, UnitOfEnergy.KILO_WATT_HOUR)

        # EV and EV Charging
        dc, unit = _get_device_class_and_unit("EV", "kWh")
        self.assertEqual(dc, SensorDeviceClass.ENERGY)
        self.assertEqual(unit, UnitOfEnergy.KILO_WATT_HOUR)

        dc, unit = _get_device_class_and_unit("EV Charging", "kWh")
        self.assertEqual(dc, SensorDeviceClass.ENERGY)
        self.assertEqual(unit, UnitOfEnergy.KILO_WATT_HOUR)

        # Hot Water
        dc, unit = _get_device_class_and_unit("Hot Water", "m³")
        self.assertEqual(dc, SensorDeviceClass.WATER)
        self.assertEqual(unit, UnitOfVolume.CUBIC_METERS)

        dc, unit = _get_device_class_and_unit("Hot Water", "gal")
        self.assertEqual(dc, SensorDeviceClass.WATER)
        self.assertEqual(unit, UnitOfVolume.GALLONS)

        # Cooling
        dc, unit = _get_device_class_and_unit("Cooling", "ton-hr")
        self.assertEqual(dc, SensorDeviceClass.ENERGY)
        self.assertEqual(unit, "ton-hr")

        dc, unit = _get_device_class_and_unit("Cooling", "kWh")
        self.assertEqual(dc, SensorDeviceClass.ENERGY)
        self.assertEqual(unit, UnitOfEnergy.KILO_WATT_HOUR)

        # Heating
        dc, unit = _get_device_class_and_unit("Heating", "kWh")
        self.assertEqual(dc, SensorDeviceClass.ENERGY)
        self.assertEqual(unit, UnitOfEnergy.KILO_WATT_HOUR)

    def test_sensor_creation_and_values(self):
        """Verify sensor properties and state values."""
        util_data = ProvidentUtilityData(
            name="Electricity",
            units="kWh",
            portal_total=402.0,
            yesterday_total=12.8,
            yesterday_hourly=[0.4, 0.8, 1.6],
            yesterday_date="2026-09-19",
            today_total=0.0,
            today_hourly=[0.0],
            last_30_days_total=402.0,
            last_30_days_daily=[12.8, 14.2],
            month_total=298.58,
            month_daily=[10.0, 12.0],
            year_total=1254.45,
            year_monthly=[0.0, 0.0, 0.0, 0.0, 0.0, 6.26, 469.75, 479.85, 298.58],
            daily_hourly_history={"2026-09-19": [0.4, 0.8, 1.6], "2026-09-18": [1.0, 1.2, 0.8]},
            daily_totals_history={"2026-09-19": 2.8, "2026-09-18": 3.0},
            hourly_breakdown_past_days=[
                {"date": "2026-09-19", "total": 2.8, "hourly": [0.4, 0.8, 1.6]},
                {"date": "2026-09-18", "total": 3.0, "hourly": [1.0, 1.2, 0.8]},
            ],
            latest_reading=1.6,
            last_updated=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
        )
        self.coordinator.data = {"Electricity": util_data}

        descriptions = _build_sensor_descriptions("Electricity", "kWh")
        entities = [
            ProvidentSensorEntity(
                self.coordinator,
                self.entry,
                "Electricity",
                desc,
            )
            for desc in descriptions
        ]

        primary_sensor = next(e for e in entities if e.entity_description.key == SENSOR_TYPE_PRIMARY)
        yesterday_sensor = next(e for e in entities if e.entity_description.key == SENSOR_TYPE_YESTERDAY)
        today_sensor = next(e for e in entities if e.entity_description.key == SENSOR_TYPE_TODAY)
        last_30_sensor = next(e for e in entities if e.entity_description.key == SENSOR_TYPE_LAST_30_DAYS)
        month_sensor = next(e for e in entities if e.entity_description.key == SENSOR_TYPE_MONTH)
        year_sensor = next(e for e in entities if e.entity_description.key == SENSOR_TYPE_YEAR)
        latest_sensor = next(e for e in entities if e.entity_description.key == SENSOR_TYPE_LATEST)

        # Primary Portal Sensor (402 kWh Electricity)
        self.assertEqual(primary_sensor.native_value, 402.0)
        self.assertEqual(primary_sensor.entity_description.device_class, SensorDeviceClass.ENERGY)
        self.assertEqual(primary_sensor.entity_description.state_class, SensorStateClass.TOTAL)
        self.assertEqual(primary_sensor.entity_description.native_unit_of_measurement, UnitOfEnergy.KILO_WATT_HOUR)
        self.assertEqual(primary_sensor.extra_state_attributes["portal_card_total"], 402.0)
        self.assertEqual(primary_sensor.extra_state_attributes["month_to_date"], 298.58)
        self.assertEqual(primary_sensor.extra_state_attributes["year_to_date"], 1254.45)
        self.assertIn("2026-09-19", primary_sensor.extra_state_attributes["historical_hourly_by_date"])
        self.assertIn("2026-09-18", primary_sensor.extra_state_attributes["historical_hourly_by_date"])
        self.assertEqual(primary_sensor.unique_id, "test_entry_id_electricity_usage")

        # Yesterday sensor
        self.assertEqual(yesterday_sensor.native_value, 12.8)
        self.assertEqual(yesterday_sensor.extra_state_attributes["reading_date"], "2026-09-19")
        self.assertIn("2026-09-19", yesterday_sensor.extra_state_attributes["historical_hourly_by_date"])

        # Last 30 Days sensor
        self.assertEqual(last_30_sensor.native_value, 402.0)

        # Month and Year checks
        self.assertEqual(month_sensor.native_value, 298.58)
        self.assertEqual(year_sensor.native_value, 1254.45)

        # Latest reading check
        self.assertEqual(latest_sensor.native_value, 1.6)

    async def test_async_setup_entry(self):
        """Test platform setup adding entities callback."""
        self.coordinator.data = {
            "Electricity": ProvidentUtilityData("Electricity", "kWh", portal_total=402.0),
            "EV": ProvidentUtilityData("EV", "kWh", portal_total=116.0),
        }
        self.hass.data = {DOMAIN: {self.entry.entry_id: self.coordinator}}

        added_entities = []
        def add_entities(ents):
            added_entities.extend(ents)

        await async_setup_entry(self.hass, self.entry, add_entities)
        # 7 sensors per utility x 2 utilities = 14 sensors
        self.assertEqual(len(added_entities), 14)

    async def test_spot_sensors_created_for_multiple_spots(self):
        """Test individual per-spot sensors for yesterday and this month."""
        ev_data = ProvidentUtilityData("EV", "kWh", portal_total=116.0)
        ev_data.spots = {
            "Spot P2-14": {
                "meter_id": "MP:123",
                "yesterday_total": 4.5,
                "month_total": 55.2,
                "readings": [0.5, 1.0],
                "month_readings": [10.0, 15.0],
            },
            "Spot P2-15": {
                "meter_id": "MP:124",
                "yesterday_total": 2.0,
                "month_total": 28.1,
                "readings": [0.2, 0.5],
                "month_readings": [5.0, 8.0],
            },
        }
        self.coordinator.data = {"EV": ev_data}
        self.hass.data = {DOMAIN: {self.entry.entry_id: self.coordinator}}

        added_entities = []
        def add_entities(ents):
            added_entities.extend(ents)

        await async_setup_entry(self.hass, self.entry, add_entities)
        # 7 regular EV sensors + (2 spots * 2 sensors: yesterday + month) = 11 sensors
        self.assertEqual(len(added_entities), 11)

        spot_sensors = [e for e in added_entities if getattr(e, "spot_name", None)]
        self.assertEqual(len(spot_sensors), 4)

        # Verify Yesterday sensor
        y_sensor = next(e for e in spot_sensors if e.spot_name == "Spot P2-14" and e.sensor_type == "yesterday")
        self.assertEqual(y_sensor.native_value, 4.5)
        self.assertEqual(y_sensor.extra_state_attributes["yesterday_total"], 4.5)
        self.assertEqual(y_sensor.name, "Yesterday")
        self.assertEqual(y_sensor.device_info.name, "Provident EV - Spot P2-14")
        self.assertEqual(y_sensor.suggested_object_id, "provident_ev_spot_p2_14_yesterday")
        self.assertEqual(y_sensor.unique_id, "test_entry_id_ev_spot_p2_14_yesterday")

        # Verify Month sensor
        m_sensor = next(e for e in spot_sensors if e.spot_name == "Spot P2-14" and e.sensor_type == "month")
        self.assertEqual(m_sensor.native_value, 55.2)
        self.assertEqual(m_sensor.extra_state_attributes["month_total"], 55.2)
        self.assertEqual(m_sensor.state_class, SensorStateClass.TOTAL_INCREASING)
        self.assertEqual(m_sensor.name, "This Month")
        self.assertEqual(m_sensor.device_info.name, "Provident EV - Spot P2-14")
        self.assertEqual(m_sensor.suggested_object_id, "provident_ev_spot_p2_14_month")
        self.assertEqual(m_sensor.unique_id, "test_entry_id_ev_spot_p2_14_month")


if __name__ == "__main__":
    unittest.main()
