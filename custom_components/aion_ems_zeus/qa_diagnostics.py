"""Zeus QA and diagnostics center.

Read-only validation of the AION EMS installation.  This engine never calls
Home Assistant control services and keeps its public summary Recorder-safe.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .const import DOMAIN, VERSION


class QADiagnosticsCenter:
    """Run compact health checks across Zeus core, data, plugins and UI."""

    RECORDER_WARNING_BYTES = 14000
    RECORDER_LIMIT_BYTES = 16384

    def __init__(self, hass, event_bus, registry, core) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.registry = registry
        self.core = core
        self.last: dict[str, Any] = {
            "status": "Not run",
            "score": 0,
            "grade": "—",
            "last_run": None,
            "checks": [],
            "summary": "Run the Zeus health check to validate the installation.",
            "safety": "Read-only diagnostics. No device control.",
        }

    @staticmethod
    def _json_size(value: Any) -> int:
        try:
            return len(json.dumps(value, default=str, separators=(",", ":")).encode("utf-8"))
        except Exception:
            return 0

    @staticmethod
    def _check(check_id: str, category: str, title: str, status: str, message: str, recommendation: str = "") -> dict[str, Any]:
        severity = "error" if status == "error" else "warning" if status == "warning" else "ok"
        return {
            "id": check_id,
            "category": category,
            "title": title,
            "status": status,
            "severity": severity,
            "message": message,
            "recommendation": recommendation,
        }

    def _frontend_checks(self) -> list[dict[str, Any]]:
        checks=[]
        base=Path(__file__).parent
        frontend=base/'frontend'/'device_manager.js'
        public=base.parent.parent.parent/'www'/'aion_ems_zeus'/'device_manager.js'
        required=("content()", "settingsPage()", "healthPage()", "wizard()", "integrationsPage()")
        if not frontend.exists():
            return [self._check("frontend_missing","Frontend","Dashboard bundle","error","The embedded dashboard JavaScript file is missing.","Reinstall the complete Zeus package.")]
        text=frontend.read_text(encoding='utf-8',errors='replace')
        missing=[name for name in required if name not in text]
        checks.append(self._check("frontend_methods","Frontend","Required dashboard methods","error" if missing else "ok", f"Missing: {', '.join(missing)}" if missing else "All required dashboard methods are present.","Reinstall the dashboard bundle." if missing else ""))
        checks.append(self._check("frontend_size","Frontend","Dashboard bundle","warning" if len(text)<50000 else "ok",f"Dashboard bundle size: {len(text):,} bytes.","Confirm the full frontend file was copied." if len(text)<50000 else ""))
        if public.exists():
            same=public.read_bytes()==frontend.read_bytes()
            checks.append(self._check("frontend_mirror","Frontend","Frontend file mirror","ok" if same else "warning","Embedded and /www dashboard files match." if same else "Embedded and /www dashboard files differ.","Copy the packaged files again and restart Home Assistant." if not same else ""))
        return checks

    def run(self) -> dict[str, Any]:
        checks: list[dict[str, Any]]=[]
        data=self.registry.data or {}
        devices=[d for d in data.get('devices',[]) if isinstance(d,dict)]
        mappings=data.get('entity_mappings',{}) if isinstance(data.get('entity_mappings',{}),dict) else {}

        # Core and service availability.
        checks.append(self._check("core_version","Core","Core version","ok" if self.core.version==VERSION else "error",f"Running Zeus {self.core.version}; package expects {VERSION}.","Perform a full Home Assistant restart after copying all files." if self.core.version!=VERSION else ""))
        required_services=("refresh_entity_discovery","save_entity_mapping","save_tariff_settings","test_plugin","run_qa_health_check")
        missing_services=[s for s in required_services if not self.hass.services.has_service(DOMAIN,s)]
        checks.append(self._check("services","Core","Required Zeus actions","error" if missing_services else "ok",f"Missing actions: {', '.join(missing_services)}" if missing_services else "All required Zeus actions are registered.","Restart Home Assistant and inspect the integration log." if missing_services else ""))

        # Registry consistency.
        ids=[str(d.get('device_id','')).strip() for d in devices if d.get('device_id')]
        dup_ids=sorted({x for x in ids if ids.count(x)>1})
        checks.append(self._check("registry_devices","Registry","Device IDs","error" if dup_ids else "ok",f"Duplicate IDs: {', '.join(dup_ids[:8])}" if dup_ids else f"{len(devices)} registered device(s), all IDs unique.","Rename or remove duplicate registry entries." if dup_ids else ""))
        unavailable=[]; missing_fields=[]
        source_usage={}
        for d in devices:
            name=d.get('name') or d.get('device_id') or 'Unnamed device'
            elwa_direct = (
                str(d.get('type') or '') == 'water_heater'
                and str(d.get('device_profile') or '') == 'my_pv_elwa'
                and bool(str(d.get('control_elwa_ip') or '').strip())
            )
            for key in ('power_entity','energy_entity'):
                eid=d.get(key)
                if not eid:
                    if not elwa_direct:
                        missing_fields.append(f"{name}: {key}")
                elif not self.hass.states.get(eid): unavailable.append(eid)
                if eid: source_usage.setdefault(eid,[]).append(str(name))
        duplicate_sources={k:v for k,v in source_usage.items() if len(v)>1}
        state='error' if missing_fields else 'warning' if unavailable else 'ok'
        checks.append(self._check("device_sources","Registry","Registered device sources",state,(f"Missing fields: {', '.join(missing_fields[:6])}. " if missing_fields else "")+(f"Unavailable entities: {', '.join(sorted(set(unavailable))[:6])}." if unavailable else "All required device source entities are available."),"Open Devices and correct missing or unavailable mappings." if state!='ok' else ""))
        checks.append(self._check("duplicate_sources","Registry","Duplicate device mappings","warning" if duplicate_sources else "ok",f"Shared entities: {', '.join(list(duplicate_sources)[:6])}" if duplicate_sources else "No duplicate device power/energy mappings detected.","Confirm whether shared entities are intentional." if duplicate_sources else ""))

        # Multi-inverter topology and aggregation consistency.
        topology=self.core.energy_topology.summary() or {}
        inverter_count=int(topology.get('inverter_count') or 0)
        inverter_rows=topology.get('inverters',[]) if isinstance(topology.get('inverters',[]),list) else []
        missing_inverter_power=[x.get('name') or x.get('id') for x in inverter_rows if not x.get('power_entity')]
        missing_inverter_energy=[x.get('name') or x.get('id') for x in inverter_rows if not x.get('energy_entity')]
        # Source-first topology: dedicated inverter registration is optional when the
        # installation already has a healthy canonical solar source mapping.  Do not
        # raise a false warning simply because generation is modeled by Sources rather
        # than by individual inverter device records.
        mapped_solar_entry = mappings.get('solar_power')
        mapped_solar_entity = mapped_solar_entry.get('entity_id') if isinstance(mapped_solar_entry, dict) else mapped_solar_entry
        mapped_solar_ok = bool(isinstance(mapped_solar_entity, str) and mapped_solar_entity and self.hass.states.get(mapped_solar_entity))
        if inverter_count:
            inv_status='error' if missing_inverter_power else 'warning' if missing_inverter_energy else 'ok'
            inv_message=(f"{inverter_count} inverter(s) registered. " + (f"Missing power mapping: {', '.join(missing_inverter_power[:5])}. " if missing_inverter_power else '') + (f"Missing energy mapping: {', '.join(missing_inverter_energy[:5])}." if missing_inverter_energy else 'Mappings are complete.'))
            inv_recommendation='Import and map every inverter in Integration Hub.' if inv_status!='ok' else ''
        elif mapped_solar_ok:
            inv_status='ok'
            inv_message=f"Canonical solar source is available via {mapped_solar_entity}; dedicated inverter registration is optional in source-first mode."
            inv_recommendation=''
        else:
            inv_status='warning'
            inv_message='No registered inverter devices and no available canonical solar source mapping were found.'
            inv_recommendation='Map the Solar source, or register individual inverters if per-inverter diagnostics are required.'
        checks.append(self._check('multi_inverter','Topology','Multi-inverter mappings',inv_status,inv_message,inv_recommendation))
        balance=topology.get('balance',{}) or {}
        balance_state='warning' if balance.get('status')=='Review' else 'ok'
        if not inverter_count and mapped_solar_ok:
            balance_message=f"Canonical solar source {mapped_solar_entity} is available at {balance.get('mapped_total_solar_w','—')} W; dedicated inverter aggregation is optional in source-first mode."
        else:
            balance_message=f"Inverter sum: {balance.get('inverter_sum_w',0)} W; mapped total: {balance.get('mapped_total_solar_w','—')} W; status: {balance.get('status','Not available')}."
        checks.append(self._check('solar_aggregation','Topology','Solar aggregation balance',balance_state,balance_message,'Review sensor update timing and mappings.' if balance_state=='warning' else ''))

        # Main energy mappings.
        map_entities=[]
        for key,value in mappings.items():
            eid=value.get('entity_id') if isinstance(value,dict) else value
            if isinstance(eid,str) and '.' in eid: map_entities.append((key,eid))
        bad_maps=[f"{k}: {e}" for k,e in map_entities if not self.hass.states.get(e)]
        checks.append(self._check("energy_mappings","Mappings","Energy source availability","warning" if bad_maps else "ok",f"Unavailable mappings: {', '.join(bad_maps[:8])}" if bad_maps else f"{len(map_entities)} mapped energy source(s) are available.","Review Sources and remap unavailable entities." if bad_maps else ""))

        # v14.0.0-alpha.22.10.0: portable system capability profile.
        # Optional capabilities are descriptive, not failures: Zeus must remain usable on
        # installations without a battery, export meter, DEA devices, or weather source.
        def mapped(*keys: str) -> tuple[bool, list[str]]:
            found=[]
            for key in keys:
                value=mappings.get(key)
                eid=value.get('entity_id') if isinstance(value,dict) else value
                if isinstance(eid,str) and eid.strip():
                    found.append(eid.strip())
            return bool(found), found

        # Canonical capability truth comes from EnergyMappingEngine's source-first
        # catalog. Fall back to raw registry mappings only for older/restored data.
        mapping_summary=self.core.energy_mapping.summary() or {}
        source_catalog=mapping_summary.get('source_catalog',{}) if isinstance(mapping_summary.get('source_catalog',{}),dict) else {}
        def canonical_source(source_id: str, *fallback_keys: str) -> tuple[bool, list[str]]:
            source=source_catalog.get(source_id,{}) if isinstance(source_catalog.get(source_id,{}),dict) else {}
            entities=source.get('entities',{}) if isinstance(source.get('entities',{}),dict) else {}
            entity_ids=[str(e).strip() for e in entities.values() if isinstance(e,str) and e.strip()]
            if source.get('configured') or entity_ids:
                return True, entity_ids
            return mapped(*fallback_keys)

        solar_present, solar_entities=canonical_source('solar','solar_power','solar_energy_today','solar_energy_total')
        home_present, home_entities=canonical_source('home','house_power','house_energy_today','house_energy_total','home_power','load_power')
        grid_present, grid_entities=canonical_source('grid','grid_power','grid_import_power','grid_export_power','grid_import_energy_today','grid_export_energy_today')
        export_present, export_entities=mapped('grid_export_power','grid_export_energy_today','grid_export_energy_total')
        if source_catalog.get('grid',{}).get('configured') and 'export_power' in (source_catalog.get('grid',{}).get('entities') or {}):
            export_present=True
            export_entities=list(dict.fromkeys(export_entities+[source_catalog['grid']['entities']['export_power']]))
        battery_present, battery_entities=canonical_source('battery','battery_power','battery_charge_power','battery_discharge_power','battery_soc','battery_charge_energy_today','battery_discharge_energy_today')
        dea_count=sum(1 for d in devices if str(d.get('role') or d.get('device_role') or '').lower() in ('load','consumer','consuming_load'))
        if not dea_count:
            dea_count=len(devices)
        tariff_cfg=(data.get('sources',{}) or {}).get('tariffs',{}) if isinstance(data.get('sources',{}),dict) else {}
        finance_ready=bool(tariff_cfg.get('enabled') and tariff_cfg.get('import_tariff') is not None)
        capability_profile={
            'profile': ' + '.join(x for x,yes in (('Solar',solar_present),('Battery',battery_present),('Grid',grid_present)) if yes) or 'Unconfigured',
            'overall': 'Ready' if home_present and (solar_present or grid_present) else 'Setup required',
            'solar': {'available':solar_present,'entities':solar_entities},
            'battery': {'available':battery_present,'entities':battery_entities},
            'grid': {'available':grid_present,'entities':grid_entities},
            'export': {'available':export_present,'entities':export_entities},
            'consumption': {'available':home_present,'entities':home_entities},
            'finance': {'available':finance_ready},
            'dea': {'available':dea_count>0,'registered_loads':dea_count},
            'history': {'available':False},
            'recommendation_only': True,
        }
        checks.append(self._check('capability_profile','Portability','System capability profile','ok',f"Detected {capability_profile['profile']}; optional missing capabilities are handled without blocking setup."))

        # Data quality, tariff, weather and history.
        quality=self.core.data_quality.summary() or {}
        confidence=float(quality.get('confidence_score') or 0)
        checks.append(self._check("data_quality","Data","Data confidence","ok" if confidence>=75 else "warning" if confidence>=40 else "error",f"Current data-confidence score: {confidence:.0f}%.","Review System Health issues and source freshness." if confidence<75 else ""))
        finance=self.core.finance.summary() or {}
        checks.append(self._check("tariffs","Finance","Tariff configuration","ok" if finance.get('configured') else "warning","Import and export tariffs are configured." if finance.get('configured') else "Tariffs are not configured.","Open Finance or Sources and enter import/export tariffs." if not finance.get('configured') else ""))
        weather=self.core.weather.summary() or {}
        weather_ok=bool(weather.get('entity_id') or weather.get('source_entity') or weather.get('configured')) and str(weather.get('status','')).lower() not in ('unavailable','error')
        checks.append(self._check("weather","Forecast","Weather source","ok" if weather_ok else "warning",f"Weather status: {weather.get('status','Not configured')}.","Select a weather entity in Sources for better forecasts." if not weather_ok else ""))
        lake=self.core.data_lake.summary() or {}
        sample_count=int(lake.get('snapshot_count') or lake.get('count') or lake.get('sample_count') or 0)
        capability_profile['history']={'available':sample_count>0,'samples':sample_count}
        checks.append(self._check("history","History","Historical data","ok" if sample_count>0 else "warning",f"Stored history samples: {sample_count}.","Allow Zeus to collect data or run a data-lake snapshot." if sample_count<=0 else ""))

        # v14.0.0-alpha.22.10.1: capability-aware readiness self-test.
        # This is deliberately read-only.  Optional capabilities that are not configured
        # are reported as Missing (optional), but do not reduce the installation score.
        checks_by_id={c.get('id'):c for c in checks}

        def entity_readiness(label: str, cap_key: str, required: bool = False) -> dict[str, Any]:
            cap=capability_profile.get(cap_key,{}) or {}
            entities=[str(x) for x in (cap.get('entities') or []) if x]
            configured=bool(cap.get('available'))
            if not configured:
                return {
                    'label':label, 'status':'Error' if required else 'Missing',
                    'configured':False, 'optional':not required,
                    'message':'Required mapping is not configured.' if required else 'Optional capability is not configured.',
                    'action':'Configure the required source mapping.' if required else '',
                }
            unavailable=[eid for eid in entities if not self.hass.states.get(eid) or str(self.hass.states.get(eid).state).lower() in ('unknown','unavailable')]
            if unavailable:
                return {
                    'label':label, 'status':'Error' if required else 'Limited',
                    'configured':True, 'optional':not required,
                    'message':f"{len(unavailable)} mapped entity/entities unavailable.",
                    'action':f"Review {label} mappings: {', '.join(unavailable[:3])}.",
                }
            return {'label':label,'status':'Ready','configured':True,'optional':not required,'message':'Configured sources are available.','action':''}

        readiness={
            'solar':entity_readiness('Solar','solar'),
            'battery':entity_readiness('Battery','battery'),
            'grid':entity_readiness('Grid','grid'),
            'export':entity_readiness('Export','export'),
            'consumption':entity_readiness('Consumption','consumption',required=True),
        }

        finance_check=checks_by_id.get('tariffs',{})
        if capability_profile.get('finance',{}).get('available') and finance_check.get('status')=='ok':
            readiness['finance']={'label':'Finance','status':'Ready','configured':True,'optional':True,'message':'Tariffs are configured.','action':''}
        elif capability_profile.get('finance',{}).get('available'):
            readiness['finance']={'label':'Finance','status':'Limited','configured':True,'optional':True,'message':'Finance is configured but tariff validation needs attention.','action':finance_check.get('recommendation') or 'Review tariff settings.'}
        else:
            readiness['finance']={'label':'Finance','status':'Missing','configured':False,'optional':True,'message':'Tariffs are not configured.','action':''}

        device_check=checks_by_id.get('device_sources',{})
        if dea_count<=0:
            readiness['dea']={'label':'DEA','status':'Missing','configured':False,'optional':True,'message':'No registered loads.','action':''}
        elif device_check.get('status')=='error':
            readiness['dea']={'label':'DEA','status':'Error','configured':True,'optional':True,'message':device_check.get('message') or 'Registered-load sources contain errors.','action':device_check.get('recommendation') or 'Review registered loads.'}
        elif device_check.get('status')=='warning':
            readiness['dea']={'label':'DEA','status':'Limited','configured':True,'optional':True,'message':device_check.get('message') or 'Some registered-load sources are limited.','action':device_check.get('recommendation') or 'Review registered loads.'}
        else:
            readiness['dea']={'label':'DEA','status':'Ready','configured':True,'optional':True,'message':f'{dea_count} registered load(s) available.','action':''}

        if sample_count<=0:
            readiness['history']={'label':'History','status':'Missing','configured':False,'optional':True,'message':'No Zeus history samples are available yet.','action':'Allow Zeus to collect history before relying on long-period analysis.'}
        elif sample_count<100:
            readiness['history']={'label':'History','status':'Limited','configured':True,'optional':True,'message':f'Only {sample_count} history samples are available.','action':'Allow more history to accumulate for stronger period analysis.'}
        else:
            readiness['history']={'label':'History','status':'Ready','configured':True,'optional':True,'message':f'{sample_count} history samples available.','action':''}

        readiness_items=list(readiness.values())
        active_items=[x for x in readiness_items if x.get('configured') or not x.get('optional')]
        readiness_weight={'Ready':1.0,'Limited':0.6,'Missing':0.0,'Error':0.0}
        readiness_percent=round(100*sum(readiness_weight.get(str(x.get('status')),0.0) for x in active_items)/max(1,len(active_items)))
        readiness_counts={name:sum(1 for x in readiness_items if x.get('status')==name) for name in ('Ready','Limited','Missing','Error')}
        readiness_status='Error' if readiness_counts['Error'] else 'Limited' if readiness_counts['Limited'] else 'Ready'
        readiness_actions=[{'capability':x.get('label'),'status':x.get('status'),'message':x.get('message'),'action':x.get('action')} for x in readiness_items if x.get('status') in ('Limited','Error') and x.get('action')]
        readiness_summary={
            'status':readiness_status,
            'percent':readiness_percent,
            'checked':len(readiness_items),
            'active_checked':len(active_items),
            'counts':readiness_counts,
            'capabilities':readiness,
            'actions':readiness_actions[:8],
            'meaning':'Readiness validates whether detected capabilities are healthy enough for Zeus to trust. Optional missing hardware does not reduce the score.',
            'safety':'Read-only self-test. Recommendation Only mode remains active.',
        }
        checks.append(self._check('system_readiness','Portability','Capability readiness self-test','error' if readiness_status=='Error' else 'warning' if readiness_status=='Limited' else 'ok',f"Overall readiness {readiness_percent}% · {readiness_counts['Ready']} ready · {readiness_counts['Limited']} limited · {readiness_counts['Error']} error.",readiness_actions[0]['action'] if readiness_actions else ''))

        # Plugins and NAS.
        hub=self.core.integration_hub.summary() or {}
        plugins=hub.get('plugins',[]) if isinstance(hub.get('plugins',[]),list) else []
        enabled=[p for p in plugins if p.get('enabled')]
        unhealthy=[p.get('name') or p.get('id') for p in enabled if str(p.get('health','')).lower() in ('error','unavailable','failed')]
        checks.append(self._check("plugins","Integrations","Enabled plugin health","error" if unhealthy else "ok",f"Unhealthy plugins: {', '.join(unhealthy[:8])}" if unhealthy else f"{len(enabled)} enabled plugin(s); no hard plugin failures.","Open Integration Hub and test the affected plugin." if unhealthy else ""))
        nas_cfg=(data.get('plugin_settings',{}) or {}).get('nas_backup',{})
        nas_enabled=bool(nas_cfg.get('enabled'))
        nas_ready=bool(str(nas_cfg.get('server','')).strip() and str(nas_cfg.get('path','')).strip())
        checks.append(self._check("nas","Backup","NAS backup configuration","ok" if (not nas_enabled or nas_ready) else "warning","NAS backup is disabled." if not nas_enabled else (f"NAS target configured: {nas_cfg.get('server')} → {nas_cfg.get('path')}." if nas_ready else "NAS backup is enabled but server or mount path is missing."),"Complete NAS server and local mount settings." if nas_enabled and not nas_ready else ""))
        notify=data.get('notification_settings',{}) or {}
        checks.append(self._check("notifications","Notifications","Notification configuration","ok" if not notify.get('enabled') or notify.get('persistent_enabled') or notify.get('mobile_enabled') else "warning","Notifications are disabled." if not notify.get('enabled') else "At least one notification channel is enabled." if notify.get('persistent_enabled') or notify.get('mobile_enabled') else "Notifications are enabled with no delivery channel.","Enable a notification delivery channel." if notify.get('enabled') and not (notify.get('persistent_enabled') or notify.get('mobile_enabled')) else ""))

        # Recorder-size estimates.  Full reports remain on their source sensors.
        # Estimate the attributes Recorder actually persists, not the full live
        # engine payload. Heavy live detail deliberately excluded by sensor.py
        # must not create a false Recorder warning in QA.
        advisor = self.core.ai_advisor.summary() or {}
        advisor_battery = advisor.get('battery_strategy') if isinstance(advisor.get('battery_strategy'), dict) else {}
        advisor_recorded = {
            'status': advisor.get('status'), 'headline': advisor.get('headline'),
            'explanation': advisor.get('explanation'), 'live_context': advisor.get('live_context', {}),
            'today_score': advisor.get('today_score'), 'forecast_today_kwh': advisor.get('forecast_today_kwh'),
            'forecast_tomorrow_kwh': advisor.get('forecast_tomorrow_kwh'),
            'battery_strategy': {k: advisor_battery.get(k) for k in ('strategy','recommended_action','recommended_reserve_percent','summary','safety')},
            'learning_confidence_percent': advisor.get('learning_confidence_percent'),
            'recommendations': (advisor.get('recommendations') or [])[:5],
            'questions': (advisor.get('questions') or [])[:3], 'summary': advisor.get('summary'),
            'safety': advisor.get('safety'), 'details_entity': 'sensor.aion_ems_zeus_predictive_battery',
            'recorder_safe': True,
        }
        predictive_recorded = dict(self.core.predictive_battery.summary() or {})
        predictive_recorded.pop('timeline', None)
        summaries={
            'AI Advisor': advisor_recorded,
            'Predictive Battery': predictive_recorded,
            'Integration Hub': self.core.integration_hub.recorder_summary(),
            'Learning': self.core.learning.summary(),
            'Historical Analytics': self.core.analytics.recorder_summary(),
            'Historical Chart Data': self.core.analytics.recorder_chart_data(),
            'Dashboard API': self.core.dashboard_api.recorder_summary(),
        }
        sizes={name:self._json_size(value) for name,value in summaries.items()}
        oversized=[f"{name} ({size} B)" for name,size in sizes.items() if size>=self.RECORDER_WARNING_BYTES]
        checks.append(self._check("recorder","Recorder","Attribute-size estimate","warning" if oversized else "ok",f"Large summaries: {', '.join(oversized)}" if oversized else f"All sampled summaries are below {self.RECORDER_WARNING_BYTES:,} bytes.","Open the affected module and move remaining detail arrays to dedicated unrecorded entities." if oversized else ""))

        checks.extend(self._frontend_checks())

        errors=sum(1 for c in checks if c['status']=='error')
        warnings=sum(1 for c in checks if c['status']=='warning')
        score=max(0,100-errors*14-warnings*4)
        grade='A+' if score>=97 else 'A' if score>=90 else 'B' if score>=80 else 'C' if score>=70 else 'D' if score>=60 else 'E'
        status='Critical' if errors else 'Attention' if warnings else 'Healthy'
        categories={}
        for c in checks:
            item=categories.setdefault(c['category'],{'ok':0,'warning':0,'error':0})
            item[c['status']]+=1
        self.last={
            'status':status,
            'score':score,
            'grade':grade,
            'last_run':datetime.now(timezone.utc).isoformat(),
            'check_count':len(checks),
            'error_count':errors,
            'warning_count':warnings,
            'passed_count':len(checks)-errors-warnings,
            'categories':categories,
            'capability_profile':capability_profile,
            'readiness':readiness_summary,
            'checks':checks[:40],
            'recorder_estimates_bytes':sizes,
            'summary':f"{len(checks)-errors-warnings} passed, {warnings} warning(s), {errors} error(s).",
            'next_action':next((c['recommendation'] for c in checks if c['status']=='error' and c['recommendation']),next((c['recommendation'] for c in checks if c['status']=='warning' and c['recommendation']),'No action required.')),
            'safety':'Read-only QA diagnostics. Recommendation Only mode remains active.',
            'recorder_safe':True,
        }
        self.event_bus.publish('QAHealthCheckCompleted','QADiagnosticsCenter',{'score':score,'errors':errors,'warnings':warnings,'mode':'recommendation_only'})
        return self.last

    def summary(self) -> dict[str, Any]:
        return dict(self.last)
