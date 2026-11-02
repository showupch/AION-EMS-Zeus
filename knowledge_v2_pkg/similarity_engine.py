"""Historical similar-day matching using recorder-safe daily summaries."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any


class SimilarityEngine:
    METRICS = ("solar_energy_kwh", "house_energy_kwh", "grid_import_energy_kwh", "grid_export_energy_kwh")

    @staticmethod
    def _num(value: Any) -> float:
        try: return float(value or 0)
        except (TypeError, ValueError): return 0.0

    def build(self, context: dict[str, Any], core: Any | None = None) -> dict[str, Any]:
        raw = getattr(getattr(core, "data_lake", None), "data", {}).get("daily_summaries", {}) if core else {}
        rows = []
        for key, source in sorted(raw.items()):
            try: dt = datetime.fromisoformat(str(key))
            except (TypeError, ValueError): continue
            rows.append((dt, {m: self._num(source.get(m)) for m in self.METRICS}))
        if len(rows) < 3:
            return {"status": "Learning", "history_days": len(rows), "similar_day": None, "similarity_percent": None,
                    "message": "At least three measured days are required for an initial match."}
        today_key = datetime.now(timezone.utc).date().isoformat()
        current_pair = next(((d, r) for d, r in rows if d.date().isoformat() == today_key), rows[-1])
        current_dt, current = current_pair
        candidates = [(d, r) for d, r in rows if d.date() != current_dt.date()]
        if not candidates:
            return {"status": "Learning", "history_days": len(rows), "similar_day": None, "similarity_percent": None}
        def score(row: dict[str, float]) -> float:
            parts=[]
            for metric in self.METRICS:
                a,b=current[metric],row[metric]; scale=max(abs(a),abs(b),1.0)
                parts.append(max(0.0, 1.0-abs(a-b)/scale))
            return sum(parts)/len(parts)*100
        best_dt,best_row=max(candidates,key=lambda item:score(item[1])); best=round(score(best_row),1)
        return {"status":"Ready","history_days":len(rows),"similar_day":best_dt.date().isoformat(),
                "similarity_percent":best,"weekday":best_dt.strftime("%A"),
                "message":f"Today is {best}% similar to {best_dt.strftime('%A %d %B')}.",
                "comparison":{"solar_kwh":best_row["solar_energy_kwh"],"home_kwh":best_row["house_energy_kwh"],
                              "import_kwh":best_row["grid_import_energy_kwh"],"export_kwh":best_row["grid_export_energy_kwh"]}}
