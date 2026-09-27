# AION EMS Zeus

## Energy Management & Intelligence for Home Assistant

**AION EMS Zeus** is an open-source **Energy Management System for Home Assistant** built to unify monitoring, analysis, forecasting and energy optimization across a complete home energy system.

Zeus brings **Solar, Grid, House, Battery, EV charging, Heat Pump, DHW/ELWA and flexible loads** together as one connected energy system instead of treating them as isolated devices.

It works directly with real Home Assistant entities and Recorder history to understand where energy comes from, where it goes, how devices behave over time, how efficiently energy is being used, and where optimization opportunities exist.

**Observe → Learn → Predict → Recommend → Verify → Improve**

Zeus is built around a simple principle:

> **If Zeus doesn't know it, Zeus shouldn't invent it.**

Zeus therefore prioritizes measured, mapped and Recorder-backed evidence. When reliable data is missing, the result remains **Unavailable** instead of being presented as a guessed or synthetic value.

### ⚡ Current Release — v16.0.203

v16.0.203 improves Recorder graph compatibility for **Day Status → Heat Pump → Today Power** and **Statistics → Grid Flow → Today**, while preserving the confirmed frontend flicker fixes, Forecast sensors, Heat Pump accounting, Grid accounting totals and the locked go-e MQTT solar-surplus behaviour.

### What Zeus does

- ⚡ Live whole-home energy flow
- ☀️ Solar, Grid and Battery intelligence
- ♨️ Heat Pump & DHW analysis
- 🚗 EV and flexible-load monitoring
- 📊 Recorder-backed daily and historical analytics
- 🧩 Device Energy Attribution
- 💰 Energy costs, savings and export value
- 🔮 Calendar-day forecasting and planning
- 🔋 Battery planning and intelligence
- 🧠 Device and system intelligence
- ⚙️ Supervised Smart Control
- 🖥️ Dedicated Command Center / Kiosk
- 🤖 Zeus Briefing & Copilot
- 🩺 Diagnostics and evidence confidence
- 🏠 Home Assistant-native forecast sensors with long-term statistics support

**AION** stands for **Adaptive Intelligence & Optimization Network**.

AION EMS Zeus is developed and tested against **real Home Assistant energy systems**, where actual measurements, Recorder history and community feedback help uncover edge cases that simulations often miss.

AION EMS Zeus is an independent community project released under the **MIT License**. It is not an official Home Assistant or Nabu Casa product.

Feedback, testing and reproducible examples are very welcome.

---

## Quick Links

- **GitHub Repository:** [showupch/AION-EMS-Zeus](https://github.com/showupch/AION-EMS-Zeus)
- **Latest Releases:** [AION EMS Zeus Releases](https://github.com/showupch/AION-EMS-Zeus/releases)
- **Setup Guides / Help:** [aion-ems.ch/setup-guide](https://aion-ems.ch/setup-guide/)
- **Installation:** [Jump to Installation](#installation)
- **Home Assistant Community Forum:** [AION EMS Zeus Forum Thread](https://community.home-assistant.io/t/aion-ems-zeus-energy-management-intelligence-for-home-assistant/1021982)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?repository=AION-EMS-Zeus&category=Integration&owner=Showupch)
