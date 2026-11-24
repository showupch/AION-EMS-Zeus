"""Fail-closed, resource-bounded read-only MariaDB Recorder audit."""
from __future__ import annotations
from datetime import timedelta, timezone, datetime
from types import SimpleNamespace
from time import monotonic
import json
from sqlalchemy import text

class AuditLimit(RuntimeError):
    pass

SQL = """SELECT s.state_id, s.last_updated_ts, s.state, a.shared_attrs
FROM states s LEFT JOIN state_attributes a ON a.attributes_id=s.attributes_id
WHERE s.metadata_id=:mid AND s.last_updated_ts>=:begin AND s.last_updated_ts<:end
AND (s.last_updated_ts>:after_ts OR (s.last_updated_ts=:after_ts AND s.state_id>:after_id))
ORDER BY s.last_updated_ts,s.state_id LIMIT :page_limit"""
META = text('SELECT metadata_id FROM states_meta WHERE entity_id=:entity')

def collect(hass, entity_ids, start, end, *, max_seconds=18, max_rows=30000, page_limit=250):
    from homeassistant.components.recorder.util import session_scope
    from .recorder_hourly_pairs import _indexed_hourly_results, _unit_scale
    if end-start > timedelta(days=30) or end <= start or len(set(entity_ids)) != 2:
        raise ValueError('invalid_audit_range')
    began=monotonic()
    result={key:[] for key in entity_ids}
    units={key:set() for key in entity_ids}
    count=0
    # Only MariaDB supports this per-statement server-side timeout. Fail closed.
    try:
        return _collect_inside_session(hass, entity_ids, start, end, max_seconds=max_seconds, max_rows=max_rows, page_limit=page_limit)
    except AuditLimit:
        raise

def _collect_inside_session(hass, entity_ids, start, end, *, max_seconds, max_rows, page_limit):
    from homeassistant.components.recorder.util import session_scope
    began=monotonic()
    result={key:[] for key in entity_ids}
    units={key:set() for key in entity_ids}
    count=0
    limit_reason=None
    with session_scope(hass=hass, read_only=True) as session:
        try:
            dialect=session.get_bind().dialect
            if not (dialect.name=='mariadb' or (dialect.name=='mysql' and getattr(dialect,'is_mariadb',False))):
                raise AuditLimit('mariadb_timeout_not_supported')
            ids={}
            for key in entity_ids:
                row=session.execute(text('SET STATEMENT max_statement_time=1 FOR '+META.text),{'entity':key}).first()
                if row is None: raise AuditLimit('entity_metadata_missing')
                ids[key]=row[0]
            statement=text('SET STATEMENT max_statement_time=1 FOR '+SQL)
            for entity in entity_ids:
                cursor=start.astimezone(timezone.utc)-timedelta(minutes=15)
                finish=end.astimezone(timezone.utc)
                while cursor<finish:
                    stop=min(cursor+timedelta(hours=6),finish)
                    after=(cursor.timestamp()-1,-1)
                    while True:
                        if monotonic()-began>max_seconds: raise AuditLimit('audit_time_budget_exceeded')
                        if count>=max_rows: raise AuditLimit('audit_row_budget_exceeded')
                        rows=session.execute(statement,{'mid':ids[entity], 'begin':cursor.timestamp(),
                             'end':stop.timestamp(),'after_ts':after[0],'after_id':after[1],
                             'page_limit':min(page_limit,max_rows-count)}).mappings().all()
                        if not rows: break
                        for r in rows:
                            stamp=float(r['last_updated_ts'])
                            key=(stamp,int(r['state_id']))
                            if key<=after: raise AuditLimit('non_monotonic_recorder_cursor')
                            after=key
                            raw=r['shared_attrs']
                            try: attrs=json.loads(raw) if raw else {}
                            except (TypeError,ValueError): attrs={}
                            if not isinstance(attrs,dict): attrs={}
                            unit=attrs.get('unit_of_measurement')
                            if unit: units[entity].add(str(unit))
                            result[entity].append(SimpleNamespace(last_updated=datetime.fromtimestamp(stamp,timezone.utc),
                                                                   state=r['state'],attributes=attrs))
                        count+=len(rows)
                        if len(rows)<min(page_limit,max_rows-(count-len(rows))): break
                    cursor=stop
        except AuditLimit as exc:
            limit_reason=str(exc)
    if limit_reason:
        raise AuditLimit(limit_reason)
    return result,units,count,monotonic()-began
