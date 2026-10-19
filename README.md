# AION EMS Zeus

## Energy Management & Intelligence for Home Assistant

**AION EMS Zeus** is an open-source energy management integration for Home Assistant. It brings solar, grid, house consumption, battery, EV charging, heat pumps, DHW/ELWA and flexible loads into one interface.

Zeus works with existing Home Assistant entities and Recorder history to observe energy flows, analyze consumption, forecast solar production and support supervised energy optimization.

**Observe → Learn → Predict → Recommend → Verify → Improve**

> **If Zeus doesn't know it, Zeus shouldn't invent it.**

Where required measurements or historical evidence are missing, Zeus reports **Unavailable** instead of inventing data.

## Features

- Live solar, grid, house and battery energy flow
- Heat pump and DHW electrical/thermal energy, COP and runtime analysis
- EV charging, flexible loads and Switch Hub
- Recorder-backed energy statistics and device attribution
- Energy costs, tariffs and savings estimates
- Solar forecasting, planning and battery intelligence
- Supervised control, diagnostics and recommendations
- Command Center and kiosk interface

## Quick links

- [Releases](https://github.com/showupch/AION-EMS-Zeus/releases)
- [Installation](#installation)
- [First-time setup](#first-time-setup)
- [Additional setup guide](https://aion-ems.ch/setup-guide/)
- [Community discussion](https://community.home-assistant.io/t/aion-ems-zeus-energy-management-intelligence-for-home-assistant/1021982)
- [Issues](https://github.com/showupch/AION-EMS-Zeus/issues)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?repository=AION-EMS-Zeus&category=Integration&owner=Showupch)

## Installation

### Requirements

- Home Assistant with the Recorder integration enabled
- Existing Home Assistant entities for the energy equipment you want to monitor
- HACS for the recommended installation method (or access to Home Assistant's configuration directory for manual installation)

Zeus does not require a battery, heat pump or EV charger. Unconfigured features may show **Unavailable** until their input entities and sufficient history exist.

### Install using HACS (custom repository)

While Zeus is awaiting inclusion in the default HACS catalog:

1. Open **HACS** in Home Assistant.
2. Open the three-dot menu and choose **Custom repositories**.
3. Enter `https://github.com/showupch/AION-EMS-Zeus`.
4. Select **Integration** as the category and add the repository.
5. Find **AION EMS Zeus** in HACS, select **Download**, and install the desired release.
6. **Restart Home Assistant**.

For a pre-release, use HACS's version selection / redownload options and enable beta versions if offered.

### Manual installation

1. Download the integration release archive from [GitHub Releases](https://github.com/showupch/AION-EMS-Zeus/releases).
2. Extract it and place the integration directory at `/config/custom_components/aion_ems_zeus/`.
3. Check that `/config/custom_components/aion_ems_zeus/manifest.json` exists (avoid an extra nested directory).
4. Restart Home Assistant.

### Add the integration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **AION EMS Zeus** and select it.
3. The initial configuration form asks only for **Developer Mode** (optional, **off** by default). Leave it off for normal use.
4. Submit to create the Zeus configuration entry. Only one entry is supported.
5. Open the Zeus panel to configure energy inputs; the initial config flow does **not** ask for solar, grid, battery or device entity IDs.

## First-time setup

A practical route from a fresh installation to working monitoring:

1. Open the Zeus interface and navigate to **Settings → Inputs**.
2. **Solar:** map a power entity in **W** representing genuine photovoltaic production. With multiple inverters, use a combined PV-only power entity where appropriate; avoid hybrid inverter AC output that includes battery discharge.
3. **Grid:** map either a signed bidirectional grid power entity or separate grid import/export power entities. Verify the displayed sign/direction against your meter.
4. **House/Loads:** map the house consumption power entity in **W** and verify it matches your meter or existing dashboard.
5. **Battery (optional):** map state of charge (%) and charge/discharge power (W), or a supported bidirectional battery power sensor.
6. **Devices (optional):** use the Devices area to add a heat pump, EV charger, DHW device or flexible load. Choose the device type, name it and map available electrical power/energy entities. Extra thermal, temperature and COP inputs are optional for basic heat-pump registration.
7. **Tariffs (optional):** configure import prices and export compensation for cost calculations.
8. Check the live energy overview: solar output, grid direction, house consumption and battery charge/discharge should match the source entities. Check the statistics page once Recorder has sufficient history.

**Do not enable supervised device controls until the mapped entities, sign conventions and device behavior have been verified.** Features requiring unavailable data will not have reliable statistics.

## Panels and interface

Zeus registers a custom Home Assistant frontend served from its own static resource path. You do not need to copy frontend files into `/config/www`.

The interface contains areas for:

| Area | What it provides |
| --- | --- |
| Home / Energy Flow | Live solar, grid, house and battery monitoring |
| Forecast | Weather-informed solar forecasts and planning |
| Statistics | Daily and historical Recorder-backed energy analysis |
| Heat Pump | Electrical and thermal energy, COP, compressor runtime and history |
| Devices | Registered devices, entity mappings and device monitoring |
| Energy | Energy accounting and Device Energy Attribution |
| System / Settings | Inputs, configuration, diagnostics and system state |
| Intelligence | Insights, recommendations and supporting evidence |
| Command Center | Consolidated monitoring and kiosk-style display |

Page names and available content can vary by release and configured equipment.

## Entities created by Zeus

The integration registers Home Assistant sensor entities for integration state, mapping/configuration summaries, forecasts, analytics and supported equipment. The exact inventory depends on the release and enabled equipment. Examples include the Zeus settings and registry summary sensors and, where supported, ELWA-related sensors.

To see the authoritative list on **your** installation, open **Settings → Devices & Services → AION EMS Zeus → Entities**, or search for `aion_ems_zeus` in **Developer Tools → States**. Check entity attributes for diagnostic detail.

Zeus **reads existing** solar, grid, battery, heat pump and other Home Assistant entities; it does not replace the device integrations that provide those measurements. Historical features depend on Recorder data.

## Actions and services

Zeus exposes actions in **Developer Tools → Actions** under the `aion_ems_zeus` domain. Available actions include:

- Testing, saving and clearing entity mappings and weather-source settings
- Registering, importing and removing supported devices
- Configuring tariffs, home and battery settings
- Managing Switch Hub devices
- Refreshing energy discovery, diagnostics and reports
- Creating configuration backups and configuring backup destinations
- Selecting the solar forecast engine

Use **Developer Tools → Actions**, search `aion_ems_zeus`, select an action and inspect its field descriptions before calling it. Configuration-changing actions and backup operations are intended for administrators; read-only diagnostics are separate. Back up Home Assistant before restoring Zeus configuration.

## Solar forecasts

Forecasts use available weather data, mapped solar measurements and historical evidence. Accuracy depends on the quality of these inputs. Releases supporting **Forecast V2** offer it as an opt-in alternative to **Legacy**; Legacy remains available for rollback. Forecasts are estimates, not guaranteed production.

## Troubleshooting

- **Integration missing after installation:** check the directory layout and restart Home Assistant.
- **Unavailable readings:** check input mappings, source entity states/units and Recorder coverage.
- **Solar totals too high:** ensure a hybrid inverter's battery-supplied AC energy is not being counted as PV production.
- **Wrong grid direction:** verify the bidirectional meter sign convention or separate import/export mappings.
- **Incomplete heat pump statistics:** check electrical and optional thermal mappings, compressor-state entities and Recorder history.
- **HACS version not shown:** use **Update information** or **Redownload** and select an available version.

For more help, see the [setup guide](https://aion-ems.ch/setup-guide/) or [open an issue](https://github.com/showupch/AION-EMS-Zeus/issues).

## License

AION stands for **Adaptive Intelligence & Optimization Network**. AION EMS Zeus is an independent community project under the **MIT License**; it is not an official Home Assistant or Nabu Casa product.
