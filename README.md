# Provident Energy Home Assistant Custom Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/default)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A Home Assistant custom integration for **Provident Energy (MeterConnex)**, built upon the official [`provident`](https://pypi.org/project/provident/0.1.4/) (0.1.4) Python client library.

This integration automatically discovers and retrieves consumption data for all meters on your Provident account—including **Electricity**, **EV / EV Charging**, **Hot Water**, **Cooling**, **Heating**, **Cold Water**, and **Gas**—and integrates directly with **Home Assistant's Energy Management Dashboard**.

---

## 🕒 1-Day Reporting Delay & Periods

Provident (MeterConnex) meters operate on a **1-day reporting delay** (utility meter readings are finalized and published at the close of each full day).
- **Yesterday / Previous Day (`<Utility> Yesterday`)**: Default and most accurate metric for daily consumption (24 full hourly intervals).
- **Today (`<Utility> Today`)**: Shows real-time / intra-day readings when published by your utility.
- **Last 30 Days (`<Utility> Last 30 Days`)**: 30-day cumulative breakdown matching the Provident portal dashboard cards.
- **This Month (`<Utility> This Month`)**: Month-to-date cumulative consumption.
- **This Year (`<Utility> This Year`)**: Year-to-date cumulative consumption.
- **Latest Reading (`<Utility> Latest Reading`)**: Most recent recorded interval reading.

---

## Features

- ⚡ **Full Energy Dashboard Integration**:
  - **Electricity Usage** mapped directly into the *Grid Consumption* section.
  - **EV / EV Charging** mapped directly into the *Individual Devices* section.
  - **Hot Water** / **Cold Water** mapped directly into the *Water Consumption* section.
- ❄️ **Cooling & Heating Support**: Automatic sensor creation and unit normalization (`kWh`, `ton-hr`, `BTU`, `m³`) for central fan-coil/chiller sub-meters.
- 🔄 **Multi-Period Metrics**: Exposes **Yesterday**, **Today**, **Last 30 Days**, **This Month**, **This Year**, and **Latest Reading** for every discovered utility.
- 📊 **Detailed Historical Attributes**: Stores hourly breakdowns for yesterday/today and daily breakdowns for the last 30 days in sensor attributes (ideal for custom ApexCharts / Lovelace cards).
- ⚙️ **Config Flow Setup & Options**: Easy setup via the Home Assistant UI with automatic credential validation, session re-authentication, and configurable polling intervals.

---

## Monitored Entities

For each utility discovered on your account (e.g., `Electricity`, `EV`, `Hot Water`, `Cooling`, `Heating`), the integration creates the following sensors:

| Entity Name | Sensor Key | State Class | Device Class | Default Units | Description |
|---|---|---|---|---|---|
| `<Utility> Yesterday` | `sensor.provident_<utility>_yesterday` | `total` | `energy` / `water` | `kWh` / `m³` | Cumulative consumption for previous day (**Primary daily metric**). |
| `<Utility> Today` | `sensor.provident_<utility>_today` | `total` | `energy` / `water` | `kWh` / `m³` | Cumulative consumption for current day (populated as utility posts). |
| `<Utility> Last 30 Days` | `sensor.provident_<utility>_last_30_days` | `total` | `energy` / `water` | `kWh` / `m³` | Cumulative consumption over last 30 days (matches homepage). |
| `<Utility> This Month` | `sensor.provident_<utility>_this_month` | `total` | `energy` / `water` | `kWh` / `m³` | Month-to-date cumulative consumption. |
| `<Utility> This Year` | `sensor.provident_<utility>_this_year` | `total` | `energy` / `water` | `kWh` / `m³` | Year-to-date cumulative consumption. |
| `<Utility> Latest Reading` | `sensor.provident_<utility>_latest_reading` | `measurement` | `energy` / `water` / `power` | `kWh` / `m³` / `ton-hr` | Most recent interval reading from the meter. |

---

## Installation

### Method 1: Via HACS (Recommended)

1. Open **HACS** in your Home Assistant instance.
2. Click the three dots in the top right corner and select **Custom repositories**.
3. Enter repository URL: `https://github.com/befryin/PemiHA`.
4. Select category: **Integration**.
5. Click **Add**, find **Provident Energy**, and click **Download**.
6. Restart Home Assistant.

### Method 2: Manual Installation

1. Download or clone this repository.
2. Copy the `custom_components/provident` folder into your Home Assistant `<config>/custom_components/` directory:
   ```
   <config_dir>/
   └── custom_components/
       └── provident/
           ├── __init__.py
           ├── config_flow.py
           ├── const.py
           ├── coordinator.py
           ├── manifest.json
           ├── sensor.py
           ├── strings.json
           └── translations/
               └── en.json
   ```
3. Restart Home Assistant.

---

## Configuration

1. In Home Assistant, navigate to **Settings** -> **Devices & Services**.
2. Click **+ Add Integration** and search for **Provident Energy**.
3. Fill in your account credentials:
   - **Username / Account ID**: Your Provident portal login.
   - **Password**: Your Provident portal password.
   - **Portal URL (Optional)**: Defaults to `https://provident.meterconnex.com`.
4. Click **Submit**. Your utilities and meters will be automatically discovered and configured!

### Integration Options
To adjust the data refresh rate:
1. Go to **Settings** -> **Devices & Services** -> **Provident Energy**.
2. Click **Configure**.
3. Choose your desired polling interval in minutes (default is 30 minutes, minimum 15 minutes).

---

## Home Assistant Energy Dashboard Setup

### 1. Grid Consumption (Electricity)
1. Go to **Settings** -> **Dashboards** -> **Energy**.
2. Under **Electricity grid** -> **Add consumption**:
   - Select `sensor.provident_electricity_yesterday` (or `sensor.provident_electricity_today`).
3. Save changes.

### 2. EV Charging (Individual Devices)
1. In **Energy Dashboard settings**, scroll to **Individual devices**.
2. Click **Add Device**.
3. Select `sensor.provident_ev_yesterday` (or `sensor.provident_ev_charging_yesterday`).
4. Save changes.

### 3. Water Consumption (Hot Water & Cold Water)
1. In **Energy Dashboard settings**, scroll to **Water consumption**.
2. Click **Add water source**.
3. Select `sensor.provident_hot_water_yesterday` (and `sensor.provident_cold_water_yesterday` if available).
4. Save changes.

---

## Lovelace Dashboard Card Examples

### Overview Grid Card

```yaml
type: entities
title: Provident Utility Consumption
show_header_toggle: false
entities:
  - entity: sensor.provident_electricity_yesterday
    name: Electricity Yesterday
  - entity: sensor.provident_electricity_last_30_days
    name: Electricity Last 30 Days
  - entity: sensor.provident_ev_yesterday
    name: EV Yesterday
  - entity: sensor.provident_hot_water_yesterday
    name: Hot Water Yesterday
  - entity: sensor.provident_cooling_yesterday
    name: Cooling Yesterday
  - entity: sensor.provident_heating_yesterday
    name: Heating Yesterday
```

### ApexCharts Hourly Graph (Yesterday's 24-Hour Profile)

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Yesterday's Hourly Electricity & EV Consumption
series:
  - entity: sensor.provident_electricity_yesterday
    name: Electricity
    type: column
    data_generator: |
      return entity.attributes.hourly_readings.map((val, idx) => {
        const d = new Date();
        d.setDate(d.getDate() - 1);
        d.setHours(idx, 0, 0, 0);
        return [d.getTime(), val];
      });
  - entity: sensor.provident_ev_yesterday
    name: EV
    type: column
    data_generator: |
      return entity.attributes.hourly_readings.map((val, idx) => {
        const d = new Date();
        d.setDate(d.getDate() - 1);
        d.setHours(idx, 0, 0, 0);
        return [d.getTime(), val];
      });
```

---

## ⚡ On-Demand Hourly Breakdown Service

To avoid excessive polling while still providing complete historical access, hourly breakdowns for any historical days can be retrieved **on demand** using the `provident.get_hourly_breakdown` service action.

### Service Action: `provident.get_hourly_breakdown`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `utility` | string | `""` (all) | Specific utility (e.g. `Electricity`, `EV`, `Hot Water`) or leave empty for all meters. |
| `days` | integer | `7` | Number of past days prior to today to retrieve (1 to 90 days). |
| `start_date` | string (date) | *optional* | Custom start date (`YYYY-MM-DD`). |
| `end_date` | string (date) | *optional* | Custom end date (`YYYY-MM-DD`). |
| `update_entities` | boolean | `true` | When `true`, updates sensor entity attributes with the fetched historical series. |

#### Example Automation / Script:
```yaml
action: provident.get_hourly_breakdown
data:
  utility: "Electricity"
  days: 14
  update_entities: true
```

---

## Troubleshooting & Debug Logging

To enable verbose debug logs for the integration, add the following to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.provident: debug
    provident: debug
```

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
