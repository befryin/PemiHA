"""Test helper and mocks for Home Assistant testing when HA core is not installed."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import sys
import types
from unittest.mock import MagicMock


# If homeassistant is not in sys.modules, create lightweight mocks for testing
if "homeassistant" not in sys.modules:
    # 1. Base homeassistant module
    ha = types.ModuleType("homeassistant")
    sys.modules["homeassistant"] = ha

    # 2. const module
    ha_const = types.ModuleType("homeassistant.const")
    ha_const.CONF_USERNAME = "username"
    ha_const.CONF_PASSWORD = "password"
    ha_const.CONF_BASE_URL = "base_url"

    class UnitOfEnergy(StrEnum):
        KILO_WATT_HOUR = "kWh"
        WATT_HOUR = "Wh"
        MEGA_WATT_HOUR = "MWh"
        GIGA_JOULE = "GJ"
        MEGA_JOULE = "MJ"

    class UnitOfPower(StrEnum):
        WATT = "W"
        KILO_WATT = "kW"
        MEGA_WATT = "MW"

    class UnitOfVolume(StrEnum):
        CUBIC_METERS = "m³"
        LITERS = "L"
        GALLONS = "gal"
        CENTUM_CUBIC_FEET = "CCF"
        CUBIC_FEET = "ft³"

    class Platform(StrEnum):
        SENSOR = "sensor"

    ha_const.UnitOfEnergy = UnitOfEnergy
    ha_const.UnitOfPower = UnitOfPower
    ha_const.UnitOfVolume = UnitOfVolume
    ha_const.Platform = Platform
    sys.modules["homeassistant.const"] = ha_const

    # 3. core module
    ha_core = types.ModuleType("homeassistant.core")
    class HomeAssistant:
        def __init__(self):
            self.data = {}
            self.config_entries = MagicMock()
    ha_core.HomeAssistant = HomeAssistant
    ha_core.callback = lambda fn: fn
    sys.modules["homeassistant.core"] = ha_core

    # 4. config_entries module
    ha_config_entries = types.ModuleType("homeassistant.config_entries")
    @dataclass
    class ConfigEntry:
        entry_id: str
        data: dict
        options: dict = field(default_factory=dict)
        unique_id: str | None = None
        def async_on_unload(self, fn): pass
        def add_update_listener(self, fn): pass

    class ConfigFlow:
        def __init_subclass__(cls, domain=None, **kwargs):
            super().__init_subclass__(**kwargs)
            cls.DOMAIN = domain

        def __init__(self):
            self.hass = HomeAssistant()
            self.context = {}
            self.unique_id = None

        async def async_set_unique_id(self, uid):
            self.unique_id = uid

        def _abort_if_unique_id_configured(self):
            pass

        def async_show_form(self, step_id, data_schema, errors=None, description_placeholders=None):
            return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors or {}, "description_placeholders": description_placeholders}

        def async_create_entry(self, title, data):
            return {"type": "create_entry", "title": title, "data": data}

        def async_abort(self, reason):
            return {"type": "abort", "reason": reason}

        def async_update_reload_and_abort(self, entry, data=None):
            return {"type": "abort", "reason": "reauth_successful", "data": data}

    class OptionsFlow:
        def __init__(self):
            self.config_entry = None
        def async_show_form(self, step_id, data_schema, errors=None):
            return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors or {}}
        def async_create_entry(self, title, data):
            return {"type": "create_entry", "title": title, "data": data}

    ha_config_entries.ConfigEntry = ConfigEntry
    ha_config_entries.ConfigFlow = ConfigFlow
    ha_config_entries.OptionsFlow = OptionsFlow
    sys.modules["homeassistant.config_entries"] = ha_config_entries

    # 5. exceptions module
    ha_exceptions = types.ModuleType("homeassistant.exceptions")
    class ConfigEntryAuthFailed(Exception): pass
    ha_exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    sys.modules["homeassistant.exceptions"] = ha_exceptions

    # 6. data_entry_flow module
    ha_data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    ha_data_entry_flow.FlowResult = dict
    sys.modules["homeassistant.data_entry_flow"] = ha_data_entry_flow

    # 7. helpers.update_coordinator module
    ha_helpers = types.ModuleType("homeassistant.helpers")
    sys.modules["homeassistant.helpers"] = ha_helpers

    ha_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
    class UpdateFailed(Exception): pass

    class DataUpdateCoordinator:
        __class_getitem__ = classmethod(lambda cls, item: cls)

        def __init__(self, hass, logger, name, update_interval):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = {}
            self.last_update_success = True

        async def async_config_entry_first_refresh(self):
            self.data = await self._async_update_data()

        async def _async_update_data(self):
            raise NotImplementedError

    class CoordinatorEntity:
        __class_getitem__ = classmethod(lambda cls, item: cls)

        def __init__(self, coordinator):
            self.coordinator = coordinator
        @property
        def available(self):
            return self.coordinator.last_update_success

    ha_coordinator.UpdateFailed = UpdateFailed
    ha_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    ha_coordinator.CoordinatorEntity = CoordinatorEntity
    sys.modules["homeassistant.helpers.update_coordinator"] = ha_coordinator

    # 8. helpers.device_registry module
    ha_dev_reg = types.ModuleType("homeassistant.helpers.device_registry")
    @dataclass(frozen=True)
    class DeviceInfo:
        identifiers: set
        name: str | None = None
        manufacturer: str | None = None
        model: str | None = None
        configuration_url: str | None = None
        via_device: tuple | None = None
    ha_dev_reg.DeviceInfo = DeviceInfo
    sys.modules["homeassistant.helpers.device_registry"] = ha_dev_reg

    # 9. helpers.entity_platform & typing
    ha_entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    ha_entity_platform.AddEntitiesCallback = MagicMock
    sys.modules["homeassistant.helpers.entity_platform"] = ha_entity_platform

    ha_typing = types.ModuleType("homeassistant.helpers.typing")
    ha_typing.StateType = str | int | float | None
    sys.modules["homeassistant.helpers.typing"] = ha_typing

    # 10. components.sensor module
    ha_components = types.ModuleType("homeassistant.components")
    sys.modules["homeassistant.components"] = ha_components

    ha_sensor = types.ModuleType("homeassistant.components.sensor")
    class SensorDeviceClass(StrEnum):
        ENERGY = "energy"
        POWER = "power"
        WATER = "water"
        GAS = "gas"

    class SensorStateClass(StrEnum):
        TOTAL = "total"
        TOTAL_INCREASING = "total_increasing"
        MEASUREMENT = "measurement"

    @dataclass(frozen=True, kw_only=True)
    class SensorEntityDescription:
        key: str
        name: str | None = None
        device_class: SensorDeviceClass | None = None
        state_class: SensorStateClass | None = None
        native_unit_of_measurement: str | None = None
        icon: str | None = None

    class SensorEntity:
        entity_description: SensorEntityDescription
        _attr_has_entity_name: bool = False
        _attr_unique_id: str | None = None
        _attr_device_info: DeviceInfo | None = None

        @property
        def unique_id(self) -> str | None:
            return self._attr_unique_id

        @property
        def device_info(self) -> DeviceInfo | None:
            return self._attr_device_info

    ha_sensor.SensorDeviceClass = SensorDeviceClass
    ha_sensor.SensorStateClass = SensorStateClass
    ha_sensor.SensorEntityDescription = SensorEntityDescription
    ha_sensor.SensorEntity = SensorEntity
    sys.modules["homeassistant.components.sensor"] = ha_sensor

    # 11. util module (dt, slugify)
    ha_util = types.ModuleType("homeassistant.util")
    from datetime import datetime, timezone
    ha_util_dt = types.ModuleType("homeassistant.util.dt")
    ha_util_dt.utcnow = lambda: datetime.now(timezone.utc)
    ha_util_dt.now = lambda: datetime.now()
    ha_util.dt = ha_util_dt

    import re
    def slugify(text: str) -> str:
        text = text.lower()
        return re.sub(r"[^\w]+", "_", text).strip("_")
    ha_util.slugify = slugify

    sys.modules["homeassistant.util"] = ha_util
    sys.modules["homeassistant.util.dt"] = ha_util_dt

# If voluptuous is not in sys.modules, create lightweight mock
if "voluptuous" not in sys.modules:
    vol = types.ModuleType("voluptuous")
    class Schema:
        def __init__(self, schema):
            self.schema = schema
        def __call__(self, val):
            return val
    class Marker:
        def __init__(self, schema, default=None):
            self.schema = schema
            self.default = default
    class Required(Marker): pass
    class Optional(Marker): pass
    class All:
        def __init__(self, *validators):
            self.validators = validators
    class Range:
        def __init__(self, min=None, max=None):
            self.min = min
            self.max = max
    def Coerce(t):
        return t

    vol.Schema = Schema
    vol.Required = Required
    vol.Optional = Optional
    vol.All = All
    vol.Range = Range
    vol.Coerce = Coerce
    sys.modules["voluptuous"] = vol

