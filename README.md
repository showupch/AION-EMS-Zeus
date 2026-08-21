AION EMS Zeus
Energy Management & Intelligence for Home Assistant
AION EMS Zeus is an open-source custom Home Assistant integration designed to turn existing Home Assistant energy data into a unified Energy Management System (EMS).
Zeus combines live energy monitoring, historical statistics, registered-device intelligence, forecasting, planning, finance analysis, system diagnostics and energy recommendations in one interface.
The project is built around a simple principle:
> **If the data isn't known, Zeus should say it isn't known.**
Zeus is designed to use real Home Assistant entities, Energy configuration and Recorder history wherever possible rather than generating values simply to populate a dashboard.
---
Features
⚡ Live Energy Flow
Monitor the current state of the home's energy system, including:
Solar production
Grid import and export
House consumption
Battery charging and discharging
Battery state of charge
Registered loads
Unknown loads
Total household load
Dynamic energy-flow direction
Grid and battery direction are represented according to the actual energy flow.
---
🏠 Home Assistant Energy Integration
Homes already using the standard Home Assistant Energy Dashboard can use Zeus' quick setup functionality.
During the Zeus Setup Wizard:
Inputs → Energy Sources
select:
Use Home Assistant Energy Setup
and click:
Scan Home Assistant Energy
Zeus scans the existing Home Assistant Energy configuration and identifies compatible energy sources that can be imported.
This provides a fast starting point without requiring every energy source to be configured manually.
The import is opt-in, and Zeus does not replace the existing Home Assistant Energy configuration.
---
🔌 Device Intelligence
Zeus includes a Device Manager for registering important household loads.
Registered devices allow Zeus to distinguish between:
Registered consumption
Unknown consumption
Total household consumption
Device mappings can use real Home Assistant power and energy entities.
This creates a reusable device-information layer that can be used by Zeus dashboards and intelligence features.
---
🚿 DHW / Water Heating
Domestic Hot Water systems can be registered and monitored through Zeus.
Supported information can include:
DHW power
DHW energy consumption
Daily DHW consumption
Historical DHW consumption
Optional DHW temperature
DHW temperature must come from a real mapped Home Assistant temperature entity.
Zeus never estimates DHW temperature from power or energy consumption.
If no real temperature sensor is available, Zeus reports the temperature as unavailable.
---
📊 Energy Statistics
Zeus provides period-based energy statistics using Home Assistant historical data.
Supported periods include:
Today
This week
This month
This year
Statistics can include:
Solar production
Consumption
Grid import
Grid export
Battery energy
DHW consumption
---
🚿 DHW Energy Statistics
Registered DHW systems with suitable Home Assistant Recorder-backed energy data receive dedicated DHW statistics.
Zeus provides:
Today
This week
This month
This year
The values come from the real mapped DHW energy source and Home Assistant Recorder statistics.
Missing historical DHW data is not estimated.
---
📈 Historical Energy Explorer
Zeus includes a Historical Energy Explorer for examining recorded energy behaviour over time.
Historical information helps provide evidence for:
Energy analysis
System behaviour
Planning
Forecast validation
Historical learning
Consumption analysis
---
☀️ Solar Utilization
Zeus evaluates how effectively available solar energy is being used during the selected analysis period.
Period calculations are based on the relevant historical energy data rather than being inferred from an unrelated live value.
---
🔮 Forecast Intelligence
Zeus can integrate energy and solar forecast information.
Forecast intelligence distinguishes between:
Historical learning confidence
and:
Forward forecast validation
These are intentionally treated as different concepts.
A system having extensive historical data does not automatically mean that a future forecast is accurate.
---
🗓 Planning Intelligence
Zeus Planning combines available system information to support energy decisions.
Planning can consider information such as:
Historical behaviour
Forecast evidence
Solar availability
Battery state
Consumption patterns
Device information
Energy costs
Planning results maintain confidence and evidence information rather than presenting every recommendation with equal certainty.
---
💰 Finance Intelligence
Zeus can analyse the financial side of the energy system.
Depending on the configured data, this can include:
Grid import cost
Grid export compensation
Energy tariffs
Energy-cost attribution
Financial impact of energy behaviour
Financial calculations depend on correctly configured Home Assistant entities and tariff information.
---
🧠 System Intelligence
Zeus includes system-level intelligence and diagnostics.
This provides information about:
Available capabilities
Data-source health
Recorder availability
Source validation
System state
Historical evidence
Intelligence readiness
The Zeus health-check lifecycle distinguishes between:
Not checked
and:
Validated results
A capability is not considered validated simply because a configuration field exists.
---
🩺 Source-First Diagnostics
When Zeus detects missing information, diagnostics are designed to identify the underlying data source first.
This helps distinguish between problems such as:
Missing entity
Missing Recorder history
Unsupported statistics
Unconfigured capability
Unavailable source
Source that has not yet been validated
---
🤖 Zeus Copilot
Zeus Copilot provides human-readable interpretation of the energy system.
Copilot can use information from multiple Zeus domains, including:
Live energy flow
Statistics
Historical behaviour
Registered devices
Forecasting
Planning
Finance
System intelligence
Copilot is designed around evidence transparency.
Where sufficient evidence is unavailable, Zeus should communicate that limitation rather than present an unsupported conclusion as fact.
---
⚡ Zeus Briefing
Zeus Briefing provides an at-a-glance interpretation of the current energy situation.
Briefings distinguish between appropriate live-flow information and historical/day-to-date information.
---
🖥 Dedicated Kiosk
Zeus includes a dedicated full-screen monitoring interface.
The kiosk can display:
Live Solar / Grid / House / Battery flow
Dynamic grid import/export
Dynamic battery charge/discharge
Registered and unknown loads
Day totals
Solar production
Grid import
Grid export
Consumption
Battery energy
DHW day consumption
Energy Sources / Supply Distribution
Energy independence
Solar / Grid / Battery contribution
DHW temperature
System status
Performance gauges
Energy score
Learning confidence
Solar utilization
Battery reserve
Zeus Briefing
The kiosk uses the same underlying Zeus/Home Assistant data sources as the main application.
---
Installation
1. Download Zeus
Download the latest AION EMS Zeus release ZIP.
Creating a Home Assistant backup before installing or updating a custom integration is recommended.
---
2. Extract the Package
Extract the downloaded ZIP.
The important directories are:
`custom_components/aion_ems_zeus`
and:
`www/aion_ems_zeus`
---
3. Install the Custom Integration
Copy:
`custom_components/aion_ems_zeus`
to:
`/config/custom_components/aion_ems_zeus`
The resulting installation should include:
`/config/custom_components/aion_ems_zeus/manifest.json`
Do not rename the integration directory or its files.
---
4. Install the Frontend
Copy the contents of:
`www/aion_ems_zeus`
to:
`/config/www/aion_ems_zeus`
The resulting directory should be:
`/config/www/aion_ems_zeus/`
Do not rename the Zeus frontend directory or files.
---
5. Restart Home Assistant
Perform a full Home Assistant restart:
Settings → System → Restart Home Assistant
A browser refresh alone is not sufficient for the initial custom-integration installation.
---
6. Add AION EMS Zeus
After Home Assistant has restarted, go to:
Settings → Devices & services → Add Integration
Search for:
AION EMS Zeus
Select the integration and complete the initial setup.
---
Initial Configuration
7. Run the Setup Wizard
Open Zeus and go to:
Configuration
Click:
Setup wizard
The Setup Wizard is the recommended starting point for a new Zeus installation.
---
8. Scan the Home Assistant Energy Configuration
During the Setup Wizard, open:
Inputs → Energy Sources
For homes already using the Home Assistant Energy Dashboard, select:
Use Home Assistant Energy Setup
Then click:
Scan Home Assistant Energy
Zeus scans the existing Home Assistant Energy configuration and identifies compatible energy sources.
Depending on the installation, this can include:
Solar production
Grid import
Grid export
Battery energy
Other supported Home Assistant energy sources
Review all detected entities before accepting the configuration.
The scan is opt-in and does not replace the existing Home Assistant Energy configuration.
---
9. Configure Additional Inputs
Configure any additional entities required by the installation.
These can include:
Solar power
Grid power
House consumption
Battery power
Battery state of charge
Additional energy sensors
Electricity tariffs
Export compensation
Forecast sources
Registered devices
DHW energy
DHW temperature
Only configure entities that actually exist in Home Assistant.
---
10. Register Devices
Open:
Zeus → Devices → Device Manager
Register important household loads and map their real Home Assistant entities.
This enables Zeus to distinguish registered consumption from remaining unknown household consumption.
---
11. Configure DHW
For a DHW/water-heating device, map the real Home Assistant energy entity.
If a real temperature sensor exists, map it using:
Temperature entity
Zeus will use that real temperature measurement where supported.
No temperature sensor means no estimated temperature.
---
Home Assistant Recorder
Home Assistant Recorder is important for Zeus historical intelligence.
Suitable historical/statistical data is used for features including:
Today / Week / Month / Year statistics
Solar history
Grid import/export history
Consumption history
DHW statistics
Historical Energy Explorer
Historical learning
Forecast validation
Planning intelligence
A newly installed system may initially have less historical intelligence.
As Home Assistant accumulates suitable historical data, Zeus gains additional evidence.
---
Verification After Installation
After configuration, verify the Zeus values against Home Assistant.
Check:
Solar production
Grid import/export
Grid direction
House consumption
Battery charging/discharging
Battery state of charge
Registered loads
Unknown loads
DHW energy
DHW temperature, if configured
Today / Week / Month / Year statistics
Incorrect source mappings should be corrected before relying on Zeus intelligence or recommendations.
---
Updating Zeus
Before updating, create a Home Assistant backup.
Replace the existing Zeus integration and frontend files with the files supplied by the new release while preserving the same directory structure:
`/config/custom_components/aion_ems_zeus`
`/config/www/aion_ems_zeus`
Perform a full Home Assistant restart afterward.
Browsers and the Home Assistant Companion App can cache frontend resources. If an older Zeus interface remains visible after updating, reload the Home Assistant frontend/app.
Do not mix integration or frontend files from different Zeus releases.
---
Recommendation Only
AION EMS Zeus currently follows a Recommendation Only philosophy.
Zeus analyses the energy system and provides intelligence and recommendations.
It is not intended to silently take operational control of household equipment.
The user remains responsible for operational decisions and automation.
---
Data Integrity Philosophy
A core Zeus development principle is:
> **If Zeus doesn't know it, Zeus shouldn't invent it.**
Zeus is designed to distinguish between:
Measured data
Recorded historical data
Forecast data
Learned information
Validated information
Unavailable information
A missing measurement should not automatically become an estimated measurement simply because a dashboard has space for a value.
This is particularly important for historical statistics, forecast confidence, device information and DHW temperature.
---
Open Source
AION EMS Zeus is an open-source project.
The project is intended to encourage experimentation, community feedback, improvement and collaboration around advanced residential energy management in Home Assistant.
Contributions, testing, bug reports and constructive feedback from the Home Assistant community are welcome.
---
Copyright & License
AION EMS Zeus
Copyright © 2026 V.T., Switzerland
AION EMS Zeus is open-source software released under the MIT License.
Permission is granted to use, copy, modify, merge, publish, distribute, sublicense and/or sell copies of the software subject to the terms of the MIT License.
The copyright and license notice must be retained in copies or substantial portions of the software.
The complete MIT License should be included with distributed copies of AION EMS Zeus in the project's `LICENSE` file.
License: MIT  
Author: V.T.  
Country: Switzerland  
Project: AION EMS Zeus
---
Disclaimer
AION EMS Zeus is an independent community/open-source project.
It is not an official Home Assistant or Nabu Casa product, and the project is not endorsed, maintained or supported by Home Assistant or Nabu Casa.
Energy measurements, forecasts, recommendations, financial calculations and other Zeus intelligence depend on the accuracy and availability of the underlying Home Assistant entities and external data sources.
Users should verify important measurements, entity mappings, financial information and recommendations against their actual energy system before acting on them.
AION EMS Zeus should not be treated as a substitute for certified electrical protection, metering, battery-management or safety equipment.
---
Support & Feedback
AION EMS Zeus is under active development.
When reporting an issue, useful information includes:
Home Assistant version
Zeus version
Description of the problem
Relevant screenshots
Relevant Home Assistant log entries
Which Zeus page or feature is affected
Whether the problem appeared after an update
Please avoid publishing passwords, API keys, access tokens, private URLs or other sensitive information when reporting issues.
---
AION EMS Zeus — Energy Management & Intelligence for Home Assistant
By V.T. Switzerland
