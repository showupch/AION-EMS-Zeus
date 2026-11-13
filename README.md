<div align="center">

<img width="1207" height="781" alt="image" src="https://github.com/user-attachments/assets/3b7b15d5-8ff1-416c-991e-357597be5deb" />



# ⚡ AION EMS Zeus

**Energy Management & Intelligence for Home Assistant**

See your home's energy flows in one place, understand where energy goes, and explore smarter ways to use solar, battery storage, EV charging, heating and the grid.

[![Latest release](https://img.shields.io/github/v/release/showupch/AION-EMS-Zeus?label=stable%20release)](https://github.com/showupch/AION-EMS-Zeus/releases/latest)
[![GitHub stars](https://img.shields.io/github/stars/showupch/AION-EMS-Zeus?style=flat)](https://github.com/showupch/AION-EMS-Zeus/stargazers)
[![Issues](https://img.shields.io/github/issues/showupch/AION-EMS-Zeus)](https://github.com/showupch/AION-EMS-Zeus/issues)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**[Install with HACS](#installation)** · **[Features](#what-zeus-does)** · **[Latest release](https://github.com/showupch/AION-EMS-Zeus/releases/latest)** · **[Report an issue](https://github.com/showupch/AION-EMS-Zeus/issues)**

</div>

---

## What is Zeus?

**AION EMS Zeus** is a custom Home Assistant integration that brings energy monitoring, analysis and decision-support tools together in a dedicated dashboard.

It is designed for homes with some combination of **photovoltaics, a home battery, grid import/export, EV chargers and a heat pump**. Zeus works with entities exposed to Home Assistant; the available insights depend on the sensors and devices you configure.

Zeus is a community project and is **not an official Home Assistant Core integration**.

## What Zeus does

| Area | What you can explore |
| --- | --- |
| ☀️ **Solar & energy flow** | Live PV production, household consumption, grid import/export and battery flows |
| 🔋 **Battery** | State of charge, charging/discharging activity and energy history |
| 🚗 **EV charging** | Charger activity and available per-charger energy information |
| 🌡️ **Heat pump** | Electrical/thermal measurements and efficiency insights when suitable entities are mapped |
| 📈 **Statistics & history** | Day/week/month/year views, trends, comparisons and device analysis |
| 🌦️ **Weather & solar forecasts** | Weather-informed solar forecasts and supporting forecast diagnostics |
| 💰 **Energy finance** | Cost, tariff and energy-value views when the relevant inputs are configured |
| 🧠 **Insights & diagnostics** | Energy observations, data-quality checks, explanations and planning information |
| 🖥️ **Dashboard & kiosk** | A unified Home Assistant interface for monitoring the energy system |

**Data quality matters:** An accurate dashboard requires correct entity mappings, appropriate units, and consistent energy accounting. A reported value should always be checked against its underlying Home Assistant sensor when troubleshooting.

## Installation

### HACS (recommended)

1. Install **HACS** in Home Assistant if you have not already.
2. Open **HACS** and search for **AION EMS Zeus**.
3. Install the integration from its repository.
4. Restart Home Assistant.
5. Go to **Settings → Devices & services → Add integration** and search for **AION EMS Zeus**.
6. Complete the configuration flow and map the relevant Home Assistant entities.

If the integration does not appear in HACS, add this repository as a **custom repository** of category **Integration**:

```text
https://github.com/showupch/AION-EMS-Zeus
```

Then search for Zeus again. See [HACS setup notes](HACS_SETUP.md) for additional installation details.

### Manual installation

Download the `aion_ems_zeus.zip` asset from the [latest stable release](https://github.com/showupch/AION-EMS-Zeus/releases/latest), extract its contents into the correct Home Assistant custom-integration location so the result is:

```text
config/
└── custom_components/
    └── aion_ems_zeus/
        ├── __init__.py
        ├── manifest.json
        └── ...
```

Restart Home Assistant and add **AION EMS Zeus** under **Settings → Devices & services**.

**Use the uploaded release ZIP:** GitHub's automatically generated “Source code” archives are not the HACS installation asset. Avoid mixing files from different releases.

## Getting started

After installation:

1. **Map the source entities** for solar production, household consumption, grid import/export and any battery, charger or heat-pump devices you use.
2. **Check units:** live power is typically W or kW; accumulated energy is Wh or kWh.
3. **Compare totals** with your existing Home Assistant Energy dashboard for the *same time window*.
4. **Explore the Zeus dashboard** and review the diagnostics if a value looks inconsistent.
5. **Enable optional features deliberately** and confirm their required sensors and permissions before relying on automation or planning outputs.

> **Self-consumption and self-sufficiency are not the same metric.** Solar self-consumption measures how much generated solar energy stays on site; self-sufficiency measures how much household demand is supplied without grid imports. Battery flows and sensor selection can affect the accounting.

## Forecasts: stable versus experimental

**Current public stable release: [v16.0.505](https://github.com/showupch/AION-EMS-Zeus/releases/tag/v16.0.505).**

In the stable release, **Forecast V2** is the production forecast engine; **V2.1 and multi-array forecast comparisons are shadow-only**. Experimental forecast candidates are not automatically authorized to control devices.

Newer developer test builds, including research into local irradiance and radiation-based solar forecasting, are **not public stable releases**. Their predictions should not be treated as validated improvements or used to infer accuracy from a single day.

For what changed in the public release, read [v16.0.505 release notes](RELEASE_NOTES_v16.0.505.md).

## Safety and scope

- Zeus depends on **Home Assistant sensor data** and the quality of the entity mappings.
- Monitoring, forecasts and suggestions do **not** guarantee energy savings or forecast accuracy.
- Device-control capabilities, when configured, must respect existing Home Assistant permissions and safety constraints.
- Keep manual control available and verify critical loads, battery limits and electrical equipment settings independently.
- Back up Home Assistant before significant integration upgrades or configuration changes.

## Troubleshooting and support

If values differ from Home Assistant Energy, please compare **the same date and time range**, then check the mapped entities for:

- PV production and household consumption
- Grid import and export
- Battery charge and discharge, if applicable
- Power-versus-energy units and counter resets

When opening an issue, include the **Zeus version**, **Home Assistant version**, a clear description of the problem, and **relevant sensor entity IDs and readings**. Redact tokens, coordinates, personal details and private network information from diagnostics or screenshots.

**[Open an issue](https://github.com/showupch/AION-EMS-Zeus/issues/new)** · **[Browse issues](https://github.com/showupch/AION-EMS-Zeus/issues)** · **[View releases](https://github.com/showupch/AION-EMS-Zeus/releases)**

## Contributing

Bug reports, reproducible examples, translation corrections and thoughtful feature requests are welcome. Please keep reports focused on one issue and include enough evidence to distinguish incorrect sensor input from a Zeus calculation or display problem.

## License

Released under the [MIT License](LICENSE).

---

<div align="center">

**Built for the Home Assistant energy community. ⚡**

[Repository](https://github.com/showupch/AION-EMS-Zeus) · [Stable releases](https://github.com/showupch/AION-EMS-Zeus/releases) · [Issues & feedback](https://github.com/showupch/AION-EMS-Zeus/issues)

</div>
