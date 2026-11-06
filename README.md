# ⚡ AION EMS Zeus
### Energy Management & Intelligence for Home Assistant

![AION EMS Zeus](https://github.com/user-attachments/assets/64132a34-3e96-4343-bed7-c1633f4adab2)

**AION EMS Zeus** is an open-source energy management integration for Home Assistant, combining solar, grid, battery, home consumption, EV charging, heat pumps, DHW and flexible loads in one intelligent interface.

**Observe → Learn → Predict → Recommend → Verify → Improve**

> **If Zeus doesn't know it, Zeus shouldn't invent it.**

Zeus uses real Home Assistant measurements and Recorder history. Missing evidence is reported as **Unavailable**, never estimated without justification.

## ✨ Features

- ⚡ **Live Energy Flow** — Solar, grid, home, battery and connected devices.
- 📊 **Statistics V2** — Energy Analytics Center, historical graphs and period comparisons.
- ☀️ **Solar Forecasting** — Weather-informed forecasts, accuracy tracking and V2.1 shadow validation.
- 🔥 **Heat Pump & DHW** — Electrical and thermal energy, COP, compressor efficiency and performance graphs.
- 🔋 **Battery Intelligence** — Energy monitoring, performance and planning.
- 🚗 **EV Charging & Loads** — Charger monitoring, flexible loads and Switch Hub.
- 💰 **Energy Costs** — Tariffs, consumption costs and savings estimates.
- 🧠 **Zeus Intelligence** — Diagnostics, recommendations, learning and supervised optimization.
- 🖥️ **Command Center & Kiosk** — Unified energy monitoring interface.

## 📦 Installation

**AION EMS Zeus is approved for the HACS default catalog.**

[![Open HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?repository=AION-EMS-Zeus&category=Integration&owner=Showupch)

1. Open **HACS / Home Assistant Marketplace**.
2. Find **AION EMS Zeus** and install it.
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration**.
5. Select **AION EMS Zeus**.
6. Open the Zeus panel and configure your energy inputs.

**Manual installation:** Download the latest release and extract the integration to `/config/custom_components/aion_ems_zeus/`.

## 📈 Statistics & Forecast Intelligence

**Statistics V2** includes solar and home consumption comparisons, grid and battery measurements, Heating/DHW thermal energy graphs and forecast accuracy analysis.

Forecast V2.1 uses frozen predictions and verified Recorder measurements to evaluate accuracy without changing production forecasts or smart controls.

## 🔧 Requirements

- Home Assistant with Recorder enabled.
- Existing Home Assistant energy sensors.
- Optional battery, EV charger, heat pump or DHW equipment.

Zeus works with your existing integrations and does not require specific hardware.

## 🔗 Links

- [Latest Releases](https://github.com/showupch/AION-EMS-Zeus/releases)
- [Setup Guide](https://aion-ems.ch/setup-guide/)
- [Home Assistant Community](https://community.home-assistant.io/t/aion-ems-zeus-energy-management-intelligence-for-home-assistant/1021982)
- [Report an Issue](https://github.com/showupch/AION-EMS-Zeus/issues)

## 📜 License

**AION** stands for *Adaptive Intelligence & Optimization Network*.

Licensed under **MIT**. Independent community project; not officially affiliated with Home Assistant or Nabu Casa.

---

**⚡ AION EMS Zeus — Observe · Learn · Predict · Optimize**
