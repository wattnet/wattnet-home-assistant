<div align="left">
  <picture>
    <source media="(prefers-color-scheme: dark)"
            srcset="https://github.com/wattnet/.github/raw/main/images/wattnet-logo-full-dark-transparent-cropped.png" />
    <source media="(prefers-color-scheme: light)"
            srcset="https://github.com/wattnet/.github/raw/main/images/wattnet-logo-full-light-transparent-cropped.png" />
    <img src="https://github.com/wattnet/.github/raw/main/images/wattnet-logo-full-light-transparent-cropped.png"
         alt="Wattnet Logo"
         width="300" />
  </picture>
</div>

# Home Assistant Integration

[![CI](https://github.com/wattnet/wattnet-home-assistant/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/wattnet/wattnet-home-assistant/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/wattnet/wattnet-home-assistant)](https://github.com/wattnet/wattnet-home-assistant/releases)
[![Release Please](https://github.com/wattnet/wattnet-home-assistant/actions/workflows/release-please.yml/badge.svg?branch=main)](https://github.com/wattnet/wattnet-home-assistant/actions/workflows/release-please.yml)
[![codecov](https://codecov.io/gh/wattnet/wattnet-home-assistant/graph/badge.svg)](https://codecov.io/gh/wattnet/wattnet-home-assistant)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.11%2B-41BDF5?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)

The Wattnet integration brings the carbon footprint, water footprint, water impact and environmental score of the electricity consumed in your zone into [Home Assistant](https://www.home-assistant.io/). It is built on the official [wattnet-client](https://github.com/wattnet/wattnet-client-python) library and the [wattnet API](https://github.com/wattnet/wattnet-api).

## Features

- **Live sensors** for the latest value of each metric in the zone you choose, with the consolidation state of the data point as attributes.
- **Long-term statistics** imported into the Home Assistant recorder, so the values show up in history graphs and statistics cards with the right timestamps.
- **Non-blocking setup**: the sensors are ready right away and the history is downloaded in the background, so a slow first import never delays Home Assistant.
- **Updates aligned with Wattnet**: they run on each quarter hour, at the second 0, so a state lands in the history as close as possible to the time of its data point instead of at an arbitrary offset from when Home Assistant started. The point of the new quarter is usually not published at that exact moment, so the integration asks again every minute for up to five minutes until it is. The exact time of the data is always in the `data_timestamp` attribute.
- **Tolerant to short outages**: if Wattnet stops responding the last data is kept for an hour. After that the sensors become unavailable and a repair issue appears, and everything recovers by itself when the API is back.
- **Automatic consolidation** of provisional data. Wattnet publishes recent data first as provisional (`valid: false`) and later consolidates it (`valid: true`, immutable). The integration keeps re-importing the provisional hours until Wattnet consolidates them, replacing the earlier values.
- **Config flow** with e-mail and password authentication (the client renews its short-lived token by itself, so the integration never stops working when a token expires), zone selection preseeded from your home location, re-authentication, reconfiguration (change zone without losing the entry) and options. Entries are named after the zone, for example `Wattnet - Spain (ES)`.
- **Fully async**, sharing Home Assistant's HTTP session through `AsyncWattnetRawClient`, with the metrics queried concurrently.
- **Diagnostics and an action** to re-import history: see [Actions](#actions) and the diagnostics download in the device page.

## Installation

### HACS (recommended)

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/wattnet/wattnet-home-assistant` with the category **Integration**.
3. Search for **Wattnet**, download it and restart Home Assistant.

### Manual

Copy the `custom_components/wattnet` folder of this repository into the `custom_components` folder of your Home Assistant configuration and restart Home Assistant.

## Configuration

Go to **Settings > Devices & services > Add integration** and search for **Wattnet**.

1. Sign in with the **e-mail and password** of your Wattnet account (the e-mail must be registered with Wattnet, see the [client documentation](https://github.com/wattnet/wattnet-client-python#authentication)). Static API tokens are not supported on purpose: they expire, while the e-mail and password let the integration request a fresh token whenever it needs one.
2. Pick the **zone** to monitor. Your home location is preselected when it falls inside a Wattnet zone. Pick the **footprint scope** (`life-cycle` or `operational`).

One entry is created per zone. Add the integration again to monitor more zones.

### Options

| Option                  | Default      | Description                                                                               |
| ----------------------- | ------------ | ----------------------------------------------------------------------------------------- |
| Footprint scope         | `life-cycle` | Scope used for the carbon and water footprints.                                           |
| Update interval         | 15 minutes   | Every 15 minutes, every 30 minutes or every hour. Updates run at fixed minutes (0, 15, 30 and 45 past the hour for 15 minutes), on each slot of Wattnet's data. |
| Initial history to import | 7 days     | History imported the first time a metric is fetched.                                      |

## Entities

Each zone creates one device with these sensors:

| Sensor                | Unit           | Source endpoint        |
| --------------------- | -------------- | ---------------------- |
| Carbon footprint      | `gCO2/kWh`     | `/footprints` (carbon) |
| Water footprint       | `L/kWh`        | `/footprints` (water)  |
| Water impact          | `stress-L/kWh` | `/impacts` (water)     |
| Environmental score   | none (0 to 100, higher is better) | `/environmental-score` |
| Statistics consolidated until | timestamp (diagnostic) | first hour still provisional |

The water impact and the environmental score are always operational. Every sensor carries these attributes:

- `valid`: `true` when the latest data point is consolidated and immutable.
- `zone_status`: `complete`, `preview` or `missing`, as reported by Wattnet.
- `data_timestamp`: the timestamp of the latest data point.

### Environmental score

The environmental score is a number from 0 to 100 where higher is better. It compares the zones with each other, so it tells you how your zone ranks against the others, not how it has changed over time.

The sensors have no `state_class` on purpose. Otherwise Home Assistant would also compile statistics from the polled states, stamped with the poll time and never corrected, next to the consolidated `wattnet:*` statistics described below. Use those for history graphs. The Environmental score has no unit either, so Home Assistant shows its history as a band of values instead of a line.

The device is named after the zone and each entity states `Data provided by Wattnet` as its attribution.

## Dashboard

The integration adds a **Wattnet** dashboard to the sidebar, with the Wattnet logo as its icon, as soon as the first zone is set up. It shows the footprint of each zone you have set up, made from the zones and entities of this Home Assistant. The values are the factors that Wattnet publishes for each zone, per kWh, and not data of your home. Only the **Your consumption** section crosses them with your own electricity, and only when your Energy dashboard has a grid source.

It is generated by the integration and is read-only, like a dashboard defined in YAML. It appears in Settings > Dashboards, it is updated when you add or remove a zone, and it disappears when you remove the last one. It does not touch your own dashboards. A zone can be left out with its **Show the Wattnet dashboard** option.

### What the dashboard shows

It is made only with Home Assistant's built-in cards, with every zone in it.

Every page has the same layout, so a single zone looks like the page that compares them all: the headline across the whole width, a column of gauges per metric, the four graphs of the last 7 days in one row, and then the home and the data.

With one zone it is a single page. With several zones it starts with an **ALL** tab that compares them, followed by one tab per zone named with its code (`ES`, `FR`, `PL`...), sorted by code:

- **ALL**: a one-line summary of the zones, a column of gauges for each metric with one gauge per zone, and the four graphs of the last 7 days (carbon, water footprint, water impact and score), each with every zone drawn in it.
- **Each zone**:
  - A headline with the footprint of a kWh right now, for example "Every kWh in Spain" followed by "90 g of CO₂ · 2.9 L of water · 0.5 stress-L of water stress · environmental score 63 out of 100" on one line, with the time of the data and whether it is provisional or consolidated. A stress-litre is a litre of water weighted by how scarce water is in the zone, so it is not a weight.
  - A column for each of the four metrics with its gauge, which has a needle and colour ranges: carbon footprint, water footprint, water impact and environmental score. For the first three, green is low and red is high. For the score it is the other way round, since higher is better.
  - The four graphs of the last 7 days in one row, drawn from the corrected statistics.
  - Below the graphs, when your Energy dashboard has a grid source, a **Your consumption** section across the whole width with the CO₂, water and water impact of the electricity you used today, and bars per day. See [Energy dashboard](#energy-dashboard).
  - The consolidation marker and, beside it, a tile of the same style that re-imports the history of that zone when you press it. Each takes two of the four columns. A short note without background goes under them.

Every page ends with a footer across the whole width, with no background: on the left the name Wattnet, linked to wattnet.eu, with the description of the service, and on the right the copyright notice. It always matches your zones, entities and Energy configuration, because it is generated when Home Assistant asks for it. To customise it, copy the cards you like into a dashboard of your own.

The logo is added to Home Assistant as a custom icon set by a small script that the integration serves (`/wattnet_static/wattnet-icons.js`), which is the documented way to have an icon of your own. It is a single path, so Home Assistant tints it with your theme like any other icon.

The gauge ranges come from real data, from the cleanest grids (20 to 100 gCO2/kWh) to coal-heavy ones (600 to 800).

## Energy dashboard

The Energy dashboard has no place for the carbon footprint of a third party, because it only reads the grid carbon signal from the Electricity Maps integration. What the integration can do is combine your own grid consumption with the Wattnet footprints, and the water part fits the dashboard natively.

When your Energy dashboard has a grid source, the integration reads its consumption meter or meters, and for each hour adds `kWh drawn from the grid x footprint of that kWh` to a running total. It creates these statistics for each zone:

| Statistic id                                   | Unit       | What it is                                           |
| ---------------------------------------------- | ---------- | ---------------------------------------------------- |
| `wattnet:<zone>_grid_water_consumption_<scope>` | `L`        | Litres of water behind the electricity you consumed. |
| `wattnet:<zone>_grid_carbon_emissions_<scope>`  | `g`        | Grams of CO2 emitted to produce it.                  |
| `wattnet:<zone>_grid_water_impact`              | `stress-L` | Water stress impact of that electricity.             |

For example, for Spain with the life cycle scope the water statistic is `wattnet:es_grid_water_consumption_life_cycle`.

### Show the water in the Energy dashboard

Go to **Settings > Dashboards > Energy**, then **Water consumption > Add water source**, and choose `Wattnet - Spain (ES) grid water consumption`. Nothing is changed in your Energy configuration by the integration itself. The carbon and impact totals do not appear in the dashboard, but you can plot them with a **Statistics graph** card using their ids.

Use the entry of the zone where your home is, since the footprint of a kWh depends on the zone that produced it.

### How the totals are calculated

- Each hour uses the mean of the 15-minute footprints of that hour and the kWh your meter recorded in it. The meters can be in any energy unit and several grid meters are added together.
- The hour in progress is left out until it finishes, because its consumption is still partial.
- An hour for which Wattnet has no data adds nothing, so the total stays continuous.
- The totals follow the footprint data: a provisional hour is recalculated, and every total after it with it, when Wattnet consolidates the hour.
- Some meters publish their consumption a day or two late. To pick that up, the last 3 days are recalculated on every update while there is a grid source.
- If the Energy dashboard had no grid when the integration started, only the data of the next updates is calculated. Run the `wattnet.reimport_statistics` action to calculate the previous days.

## Actions

### `wattnet.reimport_statistics`

Forgets what was imported and downloads the last days of data again, replacing the statistics. Use it after a gap, or to load more history than the initial import.

| Field          | Required | Description                                                          |
| -------------- | -------- | -------------------------------------------------------------------- |
| `config_entry` | no       | The zone to re-import. Every zone when omitted.                      |
| `days`         | no       | Days of history, 1 to 90. Defaults to the initial history option.    |

## How provisional data is handled

Each update does two things per metric:

1. It asks Wattnet for the latest point (15-minute resolution), which feeds the sensor.
2. It asks for the window starting at a stored watermark and imports it as hourly statistics under `wattnet:<zone>_<metric>[_<scope>]`.

Wattnet publishes one data point every 15 minutes, while Home Assistant external statistics are hourly, so the four points of each hour are averaged (the minimum and maximum are kept too). An hour counts as consolidated only when it has all four points and every one of them is `valid`, so the current, still incomplete hour is never frozen with a partial average.

The watermark is the first hour that is not consolidated yet, minus one hour of safety margin. Hours already consolidated are never requested again, while provisional hours are requested on every update and overwritten in the recorder until Wattnet flags them as consolidated. The watermark is stored in Home Assistant's `.storage`, so it survives restarts. When the same hour appears as provisional and consolidated in one response, the consolidated value wins.

## Development

```bash
git clone https://github.com/wattnet/wattnet-home-assistant.git
cd wattnet-home-assistant
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements_dev.txt
pre-commit install --hook-type pre-commit --hook-type commit-msg
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the code style, the test setup and how to submit changes.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for environment setup, code style, and how to run the tests.

## License

This repository is licensed under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).

See the [LICENSE](LICENSE) file for more details.

## Funding and Acknowledgments

This work was developed within the [GreenDIGIT](https://greendigit-project.eu/) project, funded by the European Union's Horizon Europe research and innovation programme under grant agreement No. [101131207](https://cordis.europa.eu/project/id/101131207), and by the Swiss State Secretariat for Education, Research and Innovation (SERI).

<img src="https://github.com/wattnet/.github/raw/main/images/GreenDIGIT logo color horizontal2.png" alt="GreenDIGIT Logo" width="230" align="right"/>
<img src="https://github.com/wattnet/.github/raw/main/images/EN_FundedbytheEU_RGB_POS.png" alt="EU Funded Logo" width="260" align="left"/>
<img src="https://github.com/wattnet/.github/raw/main/images/Flag_of_Switzerland.svg" alt="Swiss State Secretariat for Education, Research and Innovation (SERI)" height="50" align="left"/>
<br clear="all"/>
