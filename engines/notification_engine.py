"""Smart notification policy and multi-channel delivery for AION EMS Zeus."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any

DEFAULT_SETTINGS = {
    "enabled": False, "persistent_enabled": True, "mobile_enabled": True,
    "mobile_targets": [], "quiet_hours_enabled": False, "quiet_start": "22:00",
    "quiet_end": "07:00", "confidence_threshold": 40, "cooldown_minutes": 30,
    "categories": {"recommendation": True, "battery": True, "scheduler": True,
      "high_grid_import": True, "solar_surplus": True, "tariff": True,
      "daily_report": True, "system_health": True},
}

class NotificationEngine:
    """Build, filter, deduplicate and deliver recommendation-only notifications."""
    def __init__(self, hass, event_bus, energy_flow, intelligence, diagnostics, registry) -> None:
        self.hass=hass; self.event_bus=event_bus; self.energy_flow=energy_flow
        self.intelligence=intelligence; self.diagnostics=diagnostics; self.registry=registry
        self.last={"status":"Waiting","notifications":[]}; self._delivered={}

    def settings(self):
        saved=self.registry.data.setdefault("notification_settings", {})
        cfg={**DEFAULT_SETTINGS, **saved}
        cfg["categories"]={**DEFAULT_SETTINGS["categories"], **saved.get("categories",{})}
        return cfg

    def mobile_services(self):
        services=self.hass.services.async_services().get("notify", {})
        return [{"service":name,"name":name.removeprefix("mobile_app_").replace("_"," ").title(),"enabled":name in self.settings().get("mobile_targets",[])} for name in sorted(services) if name.startswith("mobile_app_")]

    @staticmethod
    def _power(value):
        if isinstance(value,dict): value=value.get("w")
        try:return float(value or 0)
        except (TypeError,ValueError):return 0.0

    @staticmethod
    def _id(item):
        payload=f"{item.get('type')}|{item.get('message')}"
        return "aion_ems_zeus_"+hashlib.sha1(payload.encode()).hexdigest()[:12]

    def _quiet(self,cfg,now):
        if not cfg.get("quiet_hours_enabled"): return False
        try:
            start=datetime.strptime(cfg.get("quiet_start","22:00"),"%H:%M").time(); end=datetime.strptime(cfg.get("quiet_end","07:00"),"%H:%M").time(); t=now.astimezone().time()
            return (t>=start or t<end) if start>end else start<=t<end
        except ValueError:return False

    def refresh(self):
        cfg=self.settings(); flow=self.energy_flow.summary().get("flows",{}); diag=self.diagnostics.summary(); recs=self.intelligence.summary().get("recommendations",[])
        items=[]; export_w=self._power(flow.get("grid_export_power")); import_w=self._power(flow.get("grid_import_power")); soc=flow.get("battery_soc_percent")
        try:soc=float(soc) if soc is not None else None
        except (TypeError,ValueError):soc=None
        def add(category,severity,title,message,confidence=80):
            if cfg["categories"].get(category,True) and confidence>=int(cfg.get("confidence_threshold",40)): items.append({"type":category,"severity":severity,"title":title,"message":message,"confidence":confidence})
        if export_w>=1500:add("solar_surplus","info","Solar surplus available",f"Solar surplus is {export_w:.0f} W.",90)
        if import_w>=5000:add("high_grid_import","warning","High grid import",f"Grid import is high at {import_w:.0f} W.",95)
        if soc is not None and soc<=15:add("battery","warning","Low battery",f"Battery state of charge is {soc:.1f}%.",95)
        if diag.get("status") not in (None,"Ready","Healthy"):add("system_health","warning","Zeus diagnostics","Zeus diagnostics requires attention.",90)
        if isinstance(recs,list) and recs:
            top=recs[0] if isinstance(recs[0],dict) else {}; add("recommendation","info",top.get("title") or "Zeus recommendation",top.get("why_now") or top.get("reason") or "A new energy recommendation is available.",int(top.get("confidence",70) or 70))
        for x in items:x["notification_id"]=self._id(x)
        phones=self.mobile_services(); now=datetime.now(timezone.utc)
        self.last={"status":"Enabled" if cfg.get("enabled") else "Disabled","generated_at":now.isoformat(),"notification_count":len(items),"notifications":items[:8],"delivery_enabled":bool(cfg.get("enabled")),"persistent_enabled":cfg.get("persistent_enabled"),"mobile_enabled":cfg.get("mobile_enabled"),"mobile_devices":phones,"selected_mobile_count":len(cfg.get("mobile_targets",[])),"settings":cfg,"quiet_now":self._quiet(cfg,now),"delivery_method":"Home Assistant + selected mobile apps","summary":f"{len(items)} active notification(s); {len(cfg.get('mobile_targets',[]))} phone(s) selected.","safety":"Notifications and recommendations only. Zeus does not control devices."}
        return self.last

    async def async_deliver(self,hass):
        cfg=self.settings(); now=datetime.now(timezone.utc)
        if not cfg.get("enabled") or self._quiet(cfg,now): self.last["delivered_this_refresh"]=0; return 0
        cooldown=timedelta(minutes=max(1,int(cfg.get("cooldown_minutes",30)))); delivered=0; active=set()
        for item in self.last.get("notifications",[]):
            nid=item.get("notification_id") or self._id(item); active.add(nid); prev=self._delivered.get(nid)
            if prev and now-prev<cooldown: continue
            title=f"AION EMS Zeus · {item.get('title','Notification')}"; message=item.get("message","A Zeus recommendation is available.")
            if cfg.get("persistent_enabled",True): await hass.services.async_call("persistent_notification","create",{"title":title,"message":message,"notification_id":nid},blocking=False)
            if cfg.get("mobile_enabled",True):
                available={x["service"] for x in self.mobile_services()}
                for target in cfg.get("mobile_targets",[]):
                    if target in available: await hass.services.async_call("notify",target,{"title":title,"message":message,"data":{"tag":nid}},blocking=False)
            self._delivered[nid]=now; delivered+=1
        self._delivered={k:v for k,v in self._delivered.items() if k in active or now-v<timedelta(hours=24)}
        self.last["delivered_this_refresh"]=delivered
        if delivered:self.last["last_delivery_at"]=now.isoformat()
        return delivered

    async def async_test(self,hass):
        cfg=self.settings(); title="AION EMS Zeus · Test notification"; message="Notifications are configured correctly. Zeus remains recommendation-only."; sent=[]
        if cfg.get("persistent_enabled",True): await hass.services.async_call("persistent_notification","create",{"title":title,"message":message,"notification_id":"aion_ems_zeus_test"},blocking=False); sent.append("Home Assistant")
        available={x["service"] for x in self.mobile_services()}
        for target in cfg.get("mobile_targets",[]):
            if target in available: await hass.services.async_call("notify",target,{"title":title,"message":message,"data":{"tag":"aion_ems_zeus_test"}},blocking=False); sent.append(target)
        self.last["test_result"]={"sent_to":sent,"at":datetime.now(timezone.utc).isoformat()}
        return sent
    def summary(self):return self.last
__all__=["NotificationEngine"]
