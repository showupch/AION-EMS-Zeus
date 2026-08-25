<img width="1904" height="907" alt="image" src="https://github.com/user-attachments/assets/c8355c20-772f-4e09-ab23-450a778a2fa3" />


[README.md](https://github.com/user-attachments/files/31312982/README.md)
# AION EMS Zeus

## Energy Management & Intelligence for Home Assistant

**AION EMS Zeus** is an open-source custom Home Assistant integration designed to turn existing Home Assistant energy data into a unified **Energy Management System (EMS)**.

Zeus combines live energy monitoring, historical statistics, registered-device intelligence, forecasting, planning, finance analysis, system diagnostics and energy recommendations in one interface.

The project is built around a simple principle:

> **If the data isn't known, Zeus should say it isn't known.**

Zeus is designed to use real Home Assistant entities, Energy configuration and Recorder history wherever possible rather than generating values simply to populate a dashboard.

---
💬 **Home Assistant Community:**  
https://community.home-assistant.io/t/aion-ems-zeus-energy-management-intelligence-for-home-assistant/1021982
---

# Features

## ⚡ Live Energy Flow

Monitor the current state of the home's energy system, including:

- Solar production
- Grid import and export
- House consumption
- Battery charging and discharging
- Battery state of charge
- Registered loads
- Unknown loads
- Total household load
- Dynamic energy-flow direction

Grid and battery direction are represented according to the actual energy flow.

---

## 🏠 Home Assistant Energy Integration

Homes already using the standard Home Assistant Energy Dashboard can use Zeus' quick setup functionality.

During the Zeus Setup Wizard:

**Inputs → Energy Sources**

Select:

**Use Home Assistant Energy Setup**

Then click:

**Scan Home Assistant Energy**

Zeus scans the existing Home Assistant Energy configuration and identifies compatible energy sources that can be imported.

This provides a fast starting point without requiring every energy source to be configured manually.

The import is **opt-in**, and Zeus does not replace the existing Home Assistant Energy configuration.

---

## 🔌 Device Intelligence

Zeus includes a Device Manager for registering important household loads.

Registered devices allow Zeus to distinguish between:

- Registered consumption
- Unknown consumption
- Total household consumption

Device mappings can use real Home Assistant power and energy entities.

This creates a reusable device-information layer that can be used by Zeus dashboards and intelligence features.

---

## ♨️ Heat Pump Intelligence & Statistics

Heat pumps registered through the Zeus Device Manager receive dedicated energy and operating statistics when suitable Home Assistant measurements are available.

Zeus uses the real entities mapped to the registered Heat Pump together with Home Assistant Recorder history.

### Heat Pump Energy History

The Heat Pump statistics view can provide:

- Today
- This week
- This month
- This year

These values represent measured Heat Pump electrical energy consumption from the configured Home Assistant energy entity and available Recorder history.

### Operating Evidence

Where suitable measurements are available, Zeus can also report:

- Current operating status
- Current electrical power
- Runtime today
- Peak electrical power today

These values are based on the registered Heat Pump's available Home Assistant measurements.

### Cost & Comparison

When an electricity import tariff is configured, Zeus can analyse Heat Pump electricity consumption and provide information such as:

- Today's Heat Pump electricity cost
- This week's Heat Pump electricity cost
- Comparison with recent completed-day Heat Pump consumption

Cost calculations depend on the configured tariff and measured Heat Pump electrical consumption.

### Evidence-First Heat Pump Analysis

Zeus does not infer Heat Pump thermal output or COP when the required measurements are unavailable.

Electrical consumption alone is not treated as evidence of thermal output.

If Zeus does not have the required measurement, the corresponding information remains unavailable rather than being invented.

This follows the core Zeus principle:

> **If the data isn't known, Zeus should say it isn't known.**

---

## 🚿 DHW / Water Heating

Domestic Hot Water systems can be registered and monitored through Zeus.

Supported information can include:

- DHW power
- DHW energy consumption
- Daily DHW consumption
- Historical DHW consumption
- Optional DHW temperature

DHW temperature must come from a real mapped Home Assistant temperature entity.

**Zeus never estimates DHW temperature from power or energy consumption.**

If no real temperature sensor is available, Zeus reports the temperature as unavailable.

---

## 📊 Energy Statistics

Zeus provides period-based energy statistics using Home Assistant historical data.

Supported periods include:

- Today
- This week
- This month
- This year

Statistics can include:

- Solar production
- Consumption
- Grid import
- Grid export
- Battery energy
- DHW consumption
- Heat Pump consumption

---

## 🚿 DHW Energy Statistics

Registered DHW systems with suitable Home Assistant Recorder-backed energy data receive dedicated DHW statistics.

Zeus provides:

- Today
- This week
- This month
- This year

The values come from the real mapped DHW energy source and Home Assistant Recorder statistics.

Missing historical DHW data is not estimated.

---

## 📈 Historical Energy Explorer

Zeus includes a Historical Energy Explorer for examining recorded energy behaviour over time.

Historical information helps provide evidence for:

- Energy analysis
- System behaviour
- Planning
- Forecast validation
- Historical learning
- Consumption analysis

---

## ☀️ Solar Utilization

Zeus evaluates how effectively available solar energy is being used during the selected analysis period.

Period calculations are based on the relevant historical energy data rather than being inferred from an unrelated live value.

---

## 🔮 Forecast Intelligence

Zeus can integrate energy and solar forecast information.

Forecast intelligence distinguishes between:

**Historical learning confidence**

and:

**Forward forecast validation**

These are intentionally treated as different concepts.

A system having extensive historical data does not automatically mean that a future forecast is accurate.

---

## 🗓 Planning Intelligence

Zeus Planning combines available system information to support energy decisions.

Planning can consider information such as:

- Historical behaviour
- Forecast evidence
- Solar availability
- Battery state
- Consumption patterns
- Device information
- Energy costs

Planning results maintain confidence and evidence information rather than presenting every recommendation with equal certainty.

---

## 💰 Finance Intelligence

Zeus can analyse the financial side of the energy system.

Depending on the configured data, this can include:

- Grid import cost
- Grid export compensation
- Energy tariffs
- Energy-cost attribution
- Financial impact of energy behaviour

Financial calculations depend on correctly configured Home Assistant entities and tariff information.

---

## 🧠 System Intelligence

Zeus includes system-level intelligence and diagnostics.

This provides information about:

- Available capabilities
- Data-source health
- Recorder availability
- Source validation
- System state
- Historical evidence
- Intelligence readiness

The Zeus health-check lifecycle distinguishes between:

**Not checked**

and:

**Validated results**

A capability is not considered validated simply because a configuration field exists.

---

## 🩺 Source-First Diagnostics

When Zeus detects missing information, diagnostics are designed to identify the underlying data source first.

This helps distinguish between problems such as:

- Missing entity
- Missing Recorder history
- Unsupported statistics
- Unconfigured capability
- Unavailable source
- Source that has not yet been validated

---

## 🤖 Zeus Copilot

Zeus Copilot provides human-readable interpretation of the energy system.

Copilot can use information from multiple Zeus domains, including:

- Live energy flow
- Statistics
- Historical behaviour
- Registered devices
- Forecasting
- Planning
- Finance
- System intelligence

Copilot is designed around **evidence transparency**.

Where sufficient evidence is unavailable, Zeus should communicate that limitation rather than present an unsupported conclusion as fact.

---

## ⚡ Zeus Briefing

Zeus Briefing provides an at-a-glance interpretation of the current energy situation.

Briefings distinguish between appropriate live-flow information and historical/day-to-date information.

---

## 🖥 Dedicated Kiosk

Zeus includes a dedicated full-screen monitoring interface.

The kiosk can display:

- Live Solar / Grid / House / Battery flow
- Dynamic grid import/export
- Dynamic battery charge/discharge
- Registered and unknown loads
- Day totals
- Solar production
- Grid import
- Grid export
- Consumption
- Battery energy
- DHW day consumption
- Energy Sources / Supply Distribution
- Energy independence
- Solar / Grid / Battery contribution
- DHW temperature
- System status
- Performance gauges
- Energy score
- Learning confidence
- Solar utilization
- Battery reserve
- Zeus Briefing

The kiosk uses the same underlying Zeus/Home Assistant data sources as the main application.

---

# Installation

## Recommended Installation — HACS

The recommended way to install and update **AION EMS Zeus** is through the **Home Assistant Community Store (HACS)**.

AION EMS Zeus is currently distributed as a **custom HACS repository** while the project remains in alpha development.

Creating a Home Assistant backup before installing or updating a custom integration is recommended.

### 1. Add the Zeus Repository to HACS

Open:

**HACS → Integrations**

Open the HACS menu and select:

**Custom repositories**

Add this repository:

```text
https://github.com/showupch/AION-EMS-Zeus
```

Select:

**Type → Integration**

Then click:

**Add**

### 2. Find AION EMS Zeus

Return to the HACS integrations page and search for:

**AION EMS Zeus**

Open the AION EMS Zeus repository page.

### 3. Download Zeus

Click:

**Download**

HACS displays the Zeus version that will be installed.

Confirm the expected version and click:

**Download**

HACS installs the integration into:

```text
/config/custom_components/aion_ems_zeus
```

Do not rename the integration directory or its files.

### 4. Restart Home Assistant

After the HACS download completes, restart Home Assistant when prompted.

You can also restart manually from:

**Settings → System → Restart Home Assistant**

A browser refresh alone is not sufficient after the initial custom-integration installation.

Wait for Home Assistant to finish starting before continuing.

### 5. Add AION EMS Zeus

After Home Assistant has restarted, go to:

**Settings → Devices & services → Add Integration**

Search for:

**AION EMS Zeus**

Select the integration and complete the initial setup.

After Zeus has been added successfully, open the Zeus interface.

---

## Manual Installation

HACS is the recommended installation method.

Manual installation remains available for users who do not use HACS.

Download the prepared Zeus installation package from the **latest GitHub Release**:

```text
https://github.com/showupch/AION-EMS-Zeus/releases
```

Under **Assets**, download the prepared AION EMS Zeus package supplied for that release.

> **Do not use GitHub's automatically generated `Source code (zip)` or `Source code (tar.gz)` archives as Home Assistant installation packages.**

Extract the package.

Copy:

```text
custom_components/aion_ems_zeus
```

to:

```text
/config/custom_components/aion_ems_zeus
```

For release packages that also provide the legacy/manual frontend tree, copy:

```text
www/aion_ems_zeus
```

to:

```text
/config/www/aion_ems_zeus
```

Preserve the supplied directory structure and do not mix files from different Zeus releases.

Then perform a full Home Assistant restart:

**Settings → System → Restart Home Assistant**

After Home Assistant restarts, go to:

**Settings → Devices & services → Add Integration**

Search for:

**AION EMS Zeus**

and complete the initial setup.

---

# Initial Configuration

## 6. Run the Setup Wizard

Open Zeus and go to:

**Configuration**

Click:

**Setup wizard**

The Setup Wizard is the recommended starting point for a new Zeus installation.

It guides you through the important data sources and configuration required by Zeus.

---

## 6. Scan the Home Assistant Energy Configuration

During the Setup Wizard, open:

**Inputs → Energy Sources**

For homes already using the standard Home Assistant Energy Dashboard, Zeus provides:

**QUICK SETUP · OPT-IN**

### Use Home Assistant Energy Setup

Click:

**Scan Home Assistant Energy**

Zeus scans the existing Home Assistant Energy configuration and identifies compatible energy sources that can be used by Zeus.

This provides a fast way to bring an existing Home Assistant energy installation into Zeus without manually configuring every available energy source from scratch.

Depending on the Home Assistant configuration, Zeus can identify compatible sources such as:

- Solar production
- Grid import
- Grid export
- Battery energy
- Other supported Home Assistant energy sources

The scan is **opt-in**.

Zeus does not replace the existing Home Assistant Energy configuration.

After the scan completes, review the detected sources and confirm that the proposed mappings correspond to the correct physical energy sources in your home.

---

## 6. Configure Additional Zeus Inputs

The Home Assistant Energy scan provides a starting point.

Additional entities required for live monitoring and advanced Zeus functionality can then be configured through the Setup Wizard or Zeus Configuration.

Depending on the installation, these can include:

- Solar power
- Grid power
- Grid import energy
- Grid export energy
- House consumption
- Battery power
- Battery energy
- Battery state of charge
- Electricity tariffs
- Import and export prices
- Forecast sources
- Other supported energy sensors

Only configure entities that actually exist in your Home Assistant installation.

Correct entity mapping is important because Zeus treats Home Assistant measurements and Recorder history as authoritative data.

---

## 6. Register Devices and Important Loads

Open:

**Zeus → Devices → Device Manager**

Device Manager allows individual household loads to be registered with Zeus.

Registered devices help Zeus distinguish between:

- **Registered consumption**
- **Unknown consumption**
- **Total household consumption**

Examples of devices that can be registered include:

- Water heaters / DHW
- EV charging equipment
- Heating systems
- Heat pumps
- Appliances
- Other significant electrical loads

Use the real Home Assistant entities associated with the physical device whenever possible.

---

## 6. Configure DHW / Water Heating

Domestic Hot Water equipment can be registered as a device in Zeus.

For a DHW or water-heating device, configure the real Home Assistant energy entity associated with that device.

If the device also has a real temperature sensor, open the device in Device Manager and map:

**Temperature entity**

to the appropriate Home Assistant temperature sensor.

Zeus can then reuse that real temperature measurement throughout supported parts of the system.

### Important

Zeus does **not** estimate DHW temperature from electrical power or energy consumption.

If no real temperature sensor is configured or available, Zeus reports the temperature as unavailable rather than inventing a value.

---

## 6. DHW Energy Statistics

When a registered DHW device has suitable Home Assistant Recorder-backed energy data, Zeus provides DHW historical statistics under:

**Zeus → Statistics**

The **DHW Energy History** section provides:

- Today
- This week
- This month
- This year

These values are based on real Home Assistant Recorder statistics for the mapped DHW energy source.

If the required historical data is unavailable, Zeus reports that condition rather than estimating missing DHW energy.

---

# Home Assistant Recorder & Historical Data

Home Assistant **Recorder** is important for Zeus historical analysis.

Suitable entities should have historical/statistical data available in Home Assistant for features that depend on history.

Recorder-backed information is used for functionality such as:

- Energy statistics
- Today / Week / Month / Year period analysis
- Solar history
- Grid import history
- Grid export history
- Consumption history
- DHW energy history
- Heat Pump energy history
- Historical Energy Explorer
- Historical learning
- Forecast validation
- Planning intelligence
- Energy-performance analysis

A newly installed Zeus system may therefore have limited historical intelligence initially.

As Home Assistant accumulates suitable historical data, more evidence becomes available to Zeus.

Zeus is designed to distinguish between information that is actually available and information that is not yet known.

---

# Verify the Live Energy Flow

After configuring the main energy sources, open the Zeus energy-flow interface.

Verify that the displayed values correspond to Home Assistant.

Check:

- Solar production
- Grid import/export
- House consumption
- Battery charging/discharging
- Battery state of charge
- Registered loads
- Unknown loads

Pay particular attention to grid and battery direction.

The displayed direction should represent the actual physical energy flow.

If a value or direction is incorrect, review the mapped Home Assistant entity before relying on Zeus analysis.

---

# Forecast & Planning

If your installation provides compatible forecast information, configure the appropriate forecast sources in Zeus.

Forecasting and historical learning are treated separately.

Zeus can use historical evidence to evaluate its knowledge of the energy system while separately evaluating the confidence and validation of forward-looking forecasts.

The quality of these features depends on the quality and availability of the underlying data.

Planning can consider information such as:

- Historical behaviour
- Forecast evidence
- Solar availability
- Battery state
- Consumption patterns
- Registered devices
- Energy costs

---

# Finance Configuration

Where supported by your installation, configure electricity pricing information such as:

- Grid import price
- Grid export compensation
- Tariffs
- Other relevant energy-cost information

This allows Zeus Finance Intelligence to analyse the financial impact of energy import, export, consumption and system behaviour.

Always verify financial configuration before relying on calculated costs or savings.

---

# System Intelligence & Health Checks

After completing the main configuration, review Zeus System Intelligence and health information.

Zeus provides diagnostics intended to help identify whether required capabilities and data sources are available.

Where a health check is available, run it after completing configuration.

Diagnostics can help distinguish between:

- Configured sources
- Available sources
- Recorder-backed sources
- Missing capabilities
- Sources that have not yet been validated

A capability is not considered validated simply because a configuration field exists.

---

# Dedicated Zeus Kiosk

Zeus includes a dedicated full-screen monitoring interface.

The kiosk can provide an at-a-glance view of:

- Live Solar / Grid / House / Battery flow
- Dynamic grid import/export
- Dynamic battery charging/discharging
- Registered and unknown loads
- Day energy totals
- Solar production
- Grid import
- Grid export
- Consumption
- Battery energy
- DHW day consumption
- Energy Sources / Supply Distribution
- Energy independence
- Solar / Grid / Battery contribution
- DHW temperature when a real sensor is mapped
- System status
- Performance gauges
- Energy score
- Learning confidence
- Solar utilization
- Battery reserve
- Zeus Briefing

The kiosk uses the same configured Zeus/Home Assistant data sources.

Complete and verify the main Zeus configuration before relying on the kiosk display.

---

# Zeus Copilot & Briefing

Zeus includes intelligence and briefing features designed to interpret the available energy information.

These features can use information from multiple Zeus domains, including:

- Live energy flow
- Historical statistics
- Registered devices
- Forecasts
- Planning
- Finance
- System intelligence

Zeus follows an evidence-focused approach.

If sufficient evidence is unavailable, Zeus should communicate that limitation rather than present an unsupported conclusion as fact.

---

# Recommendation Only

AION EMS Zeus currently follows a **Recommendation Only** philosophy.

Zeus analyses the energy system and provides intelligence and recommendations.

It is not intended to silently take operational control of household equipment.

The user remains responsible for operational decisions and automation.

---

# Data Integrity Philosophy

A core Zeus development principle is:

> **If Zeus doesn't know it, Zeus shouldn't invent it.**

Zeus is designed to distinguish between:

- Measured data
- Recorded historical data
- Forecast data
- Learned information
- Validated information
- Unavailable information

A missing measurement should not automatically become an estimated measurement simply because a dashboard has space for a value.

This is particularly important for:

- Historical statistics
- Forecast confidence
- Device information
- DHW temperature
- Financial calculations
- Planning recommendations

---

# Verification After Installation

Before considering the installation complete, verify:

- [ ] AION EMS Zeus appears under Home Assistant Integrations.
- [ ] Zeus opens without frontend errors.
- [ ] The **Setup wizard** has been completed.
- [ ] **Scan Home Assistant Energy** has been used where applicable.
- [ ] Imported energy sources have been reviewed.
- [ ] Solar values are correct.
- [ ] Grid import/export values are correct.
- [ ] Grid direction is correct.
- [ ] House consumption is correct.
- [ ] Battery charging/discharging is correct.
- [ ] Battery state of charge is correct.
- [ ] Important devices have been registered.
- [ ] Registered and unknown load accounting is correct.
- [ ] DHW energy is mapped if applicable.
- [ ] DHW temperature uses a real Home Assistant sensor if available.
- [ ] Registered Heat Pumps display the correct live power.
- [ ] Heat Pump Today / Week / Month / Year energy statistics match the available Home Assistant data.
- [ ] Heat Pump runtime and peak power statistics are plausible where supported.
- [ ] Heat Pump electricity-cost calculations use the configured tariff.
- [ ] Heat Pump COP or thermal output is not shown as measured data unless suitable measurements actually exist.
- [ ] Recorder-backed statistics are available where required.
- [ ] Today / Week / Month / Year statistics match Home Assistant data.
- [ ] Forecast sources are configured if used.
- [ ] Finance/tariff information is configured if used.
- [ ] System health/diagnostics have been reviewed.
- [ ] The kiosk displays the expected live information.

---

# Updating AION EMS Zeus

## Updating Through HACS

If AION EMS Zeus was installed through HACS, HACS is the recommended way to install future updates.

Before updating Zeus, creating a Home Assistant backup is recommended.

When a new Zeus release is available:

1. Open **HACS**.
2. Open **AION EMS Zeus**.
3. Review the available version.
4. Select **Download** or the available update action.
5. Confirm the version to install.
6. Allow HACS to complete the download.
7. Restart Home Assistant when requested.

After Home Assistant restarts, open Zeus and verify that the integration and interface load normally.

Existing Zeus configuration should remain in place during a normal HACS update.

Browsers and the Home Assistant Companion App can cache frontend resources. If an older Zeus interface is still displayed after an update, perform a full refresh or reload of the Home Assistant frontend/app.

---

## Manual Updates

For installations maintained manually, download the prepared installation package from the corresponding GitHub Release.

Replace the existing Zeus files with the files supplied by that release while preserving the required directory structure.

For legacy/manual packages this can include:

```text
/config/custom_components/aion_ems_zeus
```

and:

```text
/config/www/aion_ems_zeus
```

Then perform a **full Home Assistant restart**:

**Settings → System → Restart Home Assistant**

Do not mix integration or frontend files from different Zeus releases.

Using HACS is recommended because it provides a simpler and more consistent installation and update process.

---

# Open Source

AION EMS Zeus is an **open-source project**.

The project is intended to encourage experimentation, community feedback, improvement and collaboration around advanced residential energy management in Home Assistant.

Contributions, testing, bug reports and constructive feedback from the Home Assistant community are welcome.

---

# Copyright & License

**AION EMS Zeus**

Copyright © 2026 V.T., Switzerland

AION EMS Zeus is open-source software released under the **MIT License**.

Permission is granted to use, copy, modify, merge, publish, distribute, sublicense and/or sell copies of the software subject to the terms of the MIT License.

The copyright and license notice must be retained in copies or substantial portions of the software.

The complete MIT License is provided in the project's `LICENSE` file.

**License:** MIT  
**Author:** V.T.  
**Country:** Switzerland  
**Project:** AION EMS Zeus

---

# Disclaimer

AION EMS Zeus is an independent community/open-source project.

It is **not an official Home Assistant or Nabu Casa product**, and the project is not endorsed, maintained or supported by Home Assistant or Nabu Casa.

Energy measurements, forecasts, recommendations, financial calculations and other Zeus intelligence depend on the accuracy and availability of the underlying Home Assistant entities and external data sources.

Users should verify important measurements, entity mappings, financial information and recommendations against their actual energy system before acting on them.

AION EMS Zeus should not be treated as a substitute for certified electrical protection, metering, battery-management or safety equipment.

---

# Support & Feedback

AION EMS Zeus is under active development.

When reporting an issue, useful information includes:

- Home Assistant version
- Zeus version
- Description of the problem
- Relevant screenshots
- Relevant Home Assistant log entries
- Which Zeus page or feature is affected
- Whether the problem appeared after an update

Please **do not publish**:

- Passwords
- API keys
- Access tokens
- Private URLs
- Personal network information
- Other sensitive information

when reporting an issue.

---

# Project Information

**Project:** AION EMS Zeus  
**Purpose:** Energy Management & Intelligence for Home Assistant  
**License:** MIT  
**Author:** V.T.  
**Country:** Switzerland  
**Status:** Active development

---

## AION EMS Zeus — Energy Management & Intelligence for Home Assistant

**By V.T. Switzerland**
