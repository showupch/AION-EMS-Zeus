"""Safe, bounded V3 weather rejection diagnostics; no changes to physics."""
class WeatherInputRejected(ValueError):
    def __init__(self, *, hour_end_utc, reason, ghi, dni, dhi):
        self.hour_end_utc=hour_end_utc
        self.reason=reason
        self.values={"ghi_w_m2":ghi,"dni_w_m2":dni,"dhi_w_m2":dhi}
        super().__init__(reason)

    def diagnostics(self):
        # Only primitive, bounded values enter Home Assistant attributes.
        def safe(v):
            if v is None or isinstance(v,(bool,int,float,str)):
                return v if not isinstance(v,str) else v[:120]
            return repr(v)[:120]
        return {"hour_end_utc":self.hour_end_utc,
                "reason":self.reason[:180],
                "provider_values":{k:safe(v) for k,v in self.values.items()},
                "action":"rejected_no_fallback_no_calibration"}
