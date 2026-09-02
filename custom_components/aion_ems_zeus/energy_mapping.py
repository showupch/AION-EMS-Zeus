"""Manual, registry-backed Home Assistant entity mapping for AION EMS Zeus."""
from __future__ import annotations
from typing import Any

AION_OUTPUT_PREFIXES = ("sensor.aion_ems_zeus_", "binary_sensor.aion_ems_zeus_", "switch.aion_ems_zeus_")
UNAVAILABLE = {"unknown", "unavailable", "none", ""}

class EnergyMappingEngine:
    """Use only mappings explicitly saved by the user; discovery is advisory.

    v14.0.0-alpha.22.8.0 introduces a source-first catalog. Solar remains the
    proven canonical production path; Wind and Generator are optional source
    slots that can be mapped without registering inverter/source devices as
    household loads. Downstream consumers can adopt the normalized catalog
    incrementally without changing existing Solar/Battery/Grid arithmetic.
    """

    SOURCE_DEFINITIONS = {
        "solar": {"label": "Solar", "kind": "generation", "power": "solar_power", "energy_today": "solar_energy_today", "energy_total": "solar_energy_total"},
        "wind": {"label": "Wind", "kind": "generation", "power": "wind_power", "energy_today": "wind_energy_today", "energy_total": "wind_energy_total"},
        "generator": {"label": "Generator", "kind": "generation", "power": "generator_power", "energy_today": "generator_energy_today", "energy_total": "generator_energy_total"},
        "grid": {"label": "Grid", "kind": "exchange", "power": "grid_power", "import_power": "grid_import_power", "export_power": "grid_export_power", "import_energy_today": "grid_import_energy_today", "export_energy_today": "grid_export_energy_today"},
        "battery": {"label": "Battery", "kind": "storage", "power": "battery_power", "charge_power": "battery_charge_power", "discharge_power": "battery_discharge_power", "charge_energy_today": "battery_charge_energy_today", "discharge_energy_today": "battery_discharge_energy_today"},
        "home": {"label": "Home", "kind": "demand", "power": "house_power", "energy_today": "house_energy_today", "energy_total": "house_energy_total"},
    }

    FIELD_RULES = {
        "solar_power": ({"power"}, {"W","kW"}),
        "solar_energy_today": ({"energy"}, {"Wh","kWh","MWh"}), "solar_energy_total": ({"energy"}, {"Wh","kWh","MWh"}),
        "wind_power": ({"power"}, {"W","kW"}),
        "wind_energy_today": ({"energy"}, {"Wh","kWh","MWh"}), "wind_energy_total": ({"energy"}, {"Wh","kWh","MWh"}),
        "generator_power": ({"power"}, {"W","kW"}),
        "generator_energy_today": ({"energy"}, {"Wh","kWh","MWh"}), "generator_energy_total": ({"energy"}, {"Wh","kWh","MWh"}),
        "house_power": ({"power"}, {"W","kW"}),
        "house_energy_today": ({"energy"}, {"Wh","kWh","MWh"}), "house_energy_total": ({"energy"}, {"Wh","kWh","MWh"}),
        "grid_import_power": ({"power"}, {"W","kW"}), "grid_export_power": ({"power"}, {"W","kW"}), "grid_power": ({"power"}, {"W","kW"}),
        "grid_import_energy_today": ({"energy"}, {"Wh","kWh","MWh"}), "grid_export_energy_today": ({"energy"}, {"Wh","kWh","MWh"}),
        "grid_import_energy_total": ({"energy"}, {"Wh","kWh","MWh"}), "grid_export_energy_total": ({"energy"}, {"Wh","kWh","MWh"}),
        "battery_power": ({"power"}, {"W","kW"}), "battery_soc": ({"battery"}, {"%"}),
        "battery_dc_current": ({"current"}, {"A"}), "battery_dc_voltage": ({"voltage"}, {"V"}),
        "battery_charge_power": ({"power"}, {"W","kW"}), "battery_discharge_power": ({"power"}, {"W","kW"}),
        "battery_energy_total": ({"energy"}, {"Wh","kWh","MWh"}),
        "battery_charge_energy_today": ({"energy"}, {"Wh","kWh","MWh"}), "battery_discharge_energy_today": ({"energy"}, {"Wh","kWh","MWh"}),
        "battery_charge_energy_total": ({"energy"}, {"Wh","kWh","MWh"}), "battery_discharge_energy_total": ({"energy"}, {"Wh","kWh","MWh"}),
        "battery_state": (set(), set()),
        "ev_power": ({"power"}, {"W","kW"}), "ev_energy_total": ({"energy"}, {"Wh","kWh","MWh"}),
        "ev_charging_state": (set(), set()), "ev_connected_state": (set(), set()),
        "ev_soc": ({"battery"}, {"%"}), "ev_target_soc": ({"battery"}, {"%"}),
        "heat_pump_power": ({"power"}, {"W","kW"}), "heat_pump_energy_total": ({"energy"}, {"Wh","kWh","MWh"}),
        "water_heater_power": ({"power"}, {"W","kW"}), "water_heater_energy_total": ({"energy"}, {"Wh","kWh","MWh"}),
    }

    def __init__(self, hass, event_bus, registry=None) -> None:
        self.hass, self.event_bus, self.registry = hass, event_bus, registry
        self.last = {"status":"Not mapped", "mapped_count":0, "missing_count":len(self.FIELD_RULES)}
        self.public_last = {"status":"Not mapped", "mapped_count":0, "missing_count":len(self.FIELD_RULES), "invalid_count":0, "mappings":{}}
        self.last_test: dict[str, Any] = {}

    @property
    def mappings(self) -> dict[str,str]:
        return dict((self.registry.data if self.registry else {}).get("entity_mappings", {}))

    @property
    def mapping_options(self) -> dict[str, Any]:
        return dict((self.registry.data if self.registry else {}).get("mapping_options", {}))

    def _numeric(self, value):
        try: return float(value)
        except (TypeError, ValueError): return None

    def validate(self, field: str, entity_id: str) -> dict[str, Any]:
        issues=[]
        if field not in self.FIELD_RULES: issues.append({"severity":"error","code":"UNKNOWN_MAPPING_FIELD","message":f"Unknown mapping field: {field}"})
        if not entity_id or "." not in entity_id: issues.append({"severity":"error","code":"INVALID_ENTITY_ID","message":"A valid Home Assistant entity ID is required."})
        if entity_id.startswith(AION_OUTPUT_PREFIXES): issues.append({"severity":"error","code":"CIRCULAR_AION_MAPPING","message":"AION output entities cannot be selected as AION mapping sources."})
        state=self.hass.states.get(entity_id) if entity_id else None
        if state is None: issues.append({"severity":"error","code":"ENTITY_NOT_FOUND","message":"Entity does not exist in Home Assistant."})
        attrs=state.attributes if state else {}
        available=bool(state and str(state.state).lower() not in UNAVAILABLE)
        if state and not available: issues.append({"severity":"error","code":"ENTITY_UNAVAILABLE","message":"Entity is unavailable or unknown."})
        if field in self.FIELD_RULES and state:
            classes, units=self.FIELD_RULES[field]; dc=attrs.get("device_class"); unit=attrs.get("unit_of_measurement")
            if classes and dc not in classes: issues.append({"severity":"error","code":"DEVICE_CLASS_MISMATCH","message":f"Expected device class {sorted(classes)}; got {dc or 'none'}."})
            if units and unit not in units: issues.append({"severity":"error","code":"UNIT_MISMATCH","message":f"Expected unit {sorted(units)}; got {unit or 'none'}."})
        return {"status":"valid" if not issues else "invalid", "field":field, "entity_id":entity_id, "exists":state is not None, "available":available, "state":state.state if state else None, "unit":attrs.get("unit_of_measurement"), "device_class":attrs.get("device_class"), "last_changed": state.last_changed.isoformat() if state else None, "last_updated": state.last_updated.isoformat() if state else None, "issues":issues}

    def suggestions(self, field: str, limit: int=8) -> list[dict[str,Any]]:
        if field not in self.FIELD_RULES: return []
        classes, units=self.FIELD_RULES[field]; words=field.replace("_total","").split("_")
        out=[]
        for state in self.hass.states.async_all():
            if state.entity_id.startswith(AION_OUTPUT_PREFIXES): continue
            attrs=state.attributes; blob=(state.entity_id+" "+str(attrs.get("friendly_name",""))).lower(); score=sum(20 for w in words if w in blob)
            if classes and attrs.get("device_class") in classes: score+=30
            if units and attrs.get("unit_of_measurement") in units: score+=20
            if score: out.append({"entity_id":state.entity_id,"name":attrs.get("friendly_name",state.entity_id),"score":score})
        return sorted(out,key=lambda x:(x["score"],x["entity_id"]),reverse=True)[:limit]

    def _source_catalog(self, mapped: dict[str, Any]) -> dict[str, Any]:
        catalog: dict[str, Any] = {}
        mappings = self.mappings
        for source_id, definition in self.SOURCE_DEFINITIONS.items():
            fields = {k: v for k, v in definition.items() if k not in {"label", "kind"}}
            entities = {role: mappings.get(field) for role, field in fields.items() if mappings.get(field)}
            values = {}
            available_roles = []
            for role, field in fields.items():
                item = mapped.get(field)
                if item and item.get("available", True):
                    available_roles.append(role)
                    values[role] = item.get("value")
            configured = bool(entities)
            live_roles = {"power", "import_power", "export_power", "charge_power", "discharge_power", "soc"}
            energy_roles = {"energy_today", "energy_total", "import_energy_today", "export_energy_today", "charge_energy_today", "discharge_energy_today"}
            live_configured = any(role in entities for role in live_roles)
            live_available = any(role in available_roles for role in live_roles)
            energy_configured = any(role in entities for role in energy_roles)
            energy_available = any(role in available_roles for role in energy_roles)
            issues = []
            if configured and live_configured and not live_available:
                issues.append("live_measurement_unavailable")
            if configured and energy_configured and not energy_available:
                issues.append("energy_measurement_unavailable")
            if not configured:
                status = "not_configured"
            elif issues:
                status = "degraded"
            elif live_available or energy_available:
                status = "healthy"
            else:
                status = "configured_waiting"
            readiness_parts = [live_available, energy_available] if source_id in {"solar", "wind", "generator", "home"} else [bool(available_roles)]
            readiness = round(100 * sum(bool(x) for x in readiness_parts) / max(len(readiness_parts), 1))
            catalog[source_id] = {
                "label": definition["label"],
                "kind": definition["kind"],
                "configured": configured,
                "available": bool(available_roles),
                "status": status,
                "readiness_percent": readiness,
                "live_ready": live_available,
                "accounting_ready": energy_available,
                "issues": issues,
                "entities": entities,
                "values": values,
                "available_roles": available_roles,
            }
        return catalog

    def refresh(self) -> dict[str,Any]:
        # Self-heal legacy device-role mappings left by devices that were removed
        # before lifecycle cleanup existed. Core installation mappings are never
        # auto-pruned; only optional load-role mappings are eligible.
        optional_prefixes = ("ev_", "heat_pump_", "water_heater_")
        device_entities = {str(d.get(k)) for d in (self.registry.data.get("devices", []) if self.registry else []) for k in ("power_entity", "energy_entity", "state_entity", "availability_entity") if d.get(k)}
        mappings_ref = self.registry.data.setdefault("entity_mappings", {}) if self.registry else {}
        pruned=[]
        for field, entity_id in list(mappings_ref.items()):
            if field.startswith(optional_prefixes) and entity_id not in device_entities and self.hass.states.get(entity_id) is None:
                mappings_ref.pop(field, None); pruned.append(field)
        if pruned and self.registry:
            self.registry.data.setdefault("audit", []).append({"action":"prune_orphan_mappings","fields":pruned})
            self.hass.async_create_task(self.registry.async_save())
        mapped={}; invalid={}; missing=[]
        for field in self.FIELD_RULES:
            entity_id=self.mappings.get(field)
            if not entity_id: missing.append(field); continue
            result=self.validate(field,entity_id)
            if result["status"]=="valid":
                value = self._numeric(result["state"])
                unit = result.get("unit")
                if value is not None:
                    if field.endswith("_power") or field == "grid_power":
                        if unit == "kW": value *= 1000
                        result["normalized_unit"] = "W"
                    elif "energy" in field:
                        if unit == "Wh": value /= 1000
                        elif unit == "MWh": value *= 1000
                        result["normalized_unit"] = "kWh"
                    else:
                        result["normalized_unit"] = unit
                result["value"] = value
                result["reason"]="manual registry mapping"; mapped[field]=result
            else: invalid[field]=result
        source_catalog = self._source_catalog(mapped)
        self.last={"status":"Ready" if not invalid else "Warning","mapped_count":len(mapped),"missing_count":len(missing),"invalid_count":len(invalid),"mapped":mapped,"invalid":invalid,"missing":missing,"mappings":self.mappings,"mapping_options":self.mapping_options,"fields":list(self.FIELD_RULES),"source_catalog":source_catalog,"source_model_version":"source_first_v1","suggestions":{f:self.suggestions(f,3) for f in self.FIELD_RULES},"last_test":self.last_test,"summary":f"Using {len(mapped)} manually saved mapping(s); discovery suggestions are never activated automatically.","safety":"Read-only mapping. Manual save only. No device control."}
        # Recorder-safe public payload. Full validation details and candidate lists remain
        # in runtime memory and are generated on demand by the Energy Sources page.
        self.public_last={
            "status": self.last["status"],
            "configured": bool(self.mappings),
            "mapped_count": len(mapped),
            "missing_count": len(missing),
            "invalid_count": len(invalid),
            "mappings": self.mappings,
            "missing_fields": missing,
            "invalid_fields": list(invalid),
            "field_count": len(self.FIELD_RULES),
            "source_catalog": source_catalog,
            "source_model_version": "source_first_v1",
            "last_test_status": self.last_test.get("status") if isinstance(self.last_test, dict) else None,
            "summary": f"{len(mapped)} mapped, {len(missing)} missing, {len(invalid)} invalid.",
        }
        self.event_bus.publish("EnergyMappingUpdated","EnergyMappingEngine",{"mapped_count":len(mapped),"invalid_count":len(invalid)})
        return self.last

    def summary(self): return self.last
    def public_summary(self): return self.public_last
