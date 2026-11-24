"""Conservative, local, read-only Zeus installation security checks.

No credentials, configuration contents, or file hashes leave Home Assistant.
"""
from __future__ import annotations
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def scan_zeus_installation():
    findings=[]
    def add(code,severity,title,detail):
        findings.append({"code":code,"severity":severity,"title":title,"detail":detail})
    manifest_path=ROOT / "manifest.json"
    try:
        manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        version=manifest.get("version")
        if not isinstance(version,str) or not version.strip():
            add("manifest_version","warning","Version not declared","The Zeus manifest has no valid version string.")
        if manifest.get("domain")!="aion_ems_zeus":
            add("manifest_domain","warning","Unexpected integration domain","The manifest domain does not match Zeus.")
    except (OSError,ValueError,UnicodeError):
        version=None
        add("manifest_read","warning","Manifest unavailable","Could not read or parse the installed Zeus manifest.")
    for name in ("__init__.py","manifest.json","frontend/device_manager.js"):
        path=ROOT/name
        try:
            info=path.lstat()
            if stat.S_ISLNK(info.st_mode):
                add("symlink_"+name.replace("/","_"),"warning","Symlink in installation",name+" is a symbolic link; review its destination.")
            elif not stat.S_ISREG(info.st_mode):
                add("not_file_"+name.replace("/","_"),"warning","Unexpected installation entry",name+" is not a regular file.")
            if info.st_mode & stat.S_IWOTH:
                add("world_write_"+name.replace("/","_"),"warning","World-writable installation file",name+" can be modified by other local users.")
        except OSError:
            add("missing_"+name.replace("/","_"),"warning","Missing installation file",name+" could not be inspected.")
    try:
        info=ROOT.stat()
        if info.st_mode & stat.S_IWOTH:
            add("world_write_dir","warning","World-writable Zeus directory","The integration directory is writable by other local users.")
    except OSError:
        add("dir_unreadable","unknown","Installation directory unavailable","Cannot inspect integration directory permissions.")
    # Explicitly do not claim checks we cannot perform locally without trusted baselines or feeds.
    checks=[
        {"name":"Manifest and critical installation files","status":"checked"},
        {"name":"Basic local file permissions","status":"checked"},
        {"name":"Trusted signed-file integrity baseline","status":"not_checked","reason":"No trusted signed baseline is installed."},
        {"name":"Dependency CVE vulnerability feed","status":"not_checked","reason":"No authenticated or up-to-date vulnerability feed was queried."},
        {"name":"Secrets and diagnostic redaction","status":"not_checked","reason":"Configuration contents were not scanned to avoid exposing private data."},
        {"name":"Network and Home Assistant host","status":"out_of_scope","reason":"Only Zeus itself is in scope."},
    ]
    return {"mode":"read_only","scope":"zeus_integration_only","scanned_at_utc":datetime.now(timezone.utc).isoformat(),"installed_version":version,"findings":findings,"checks":checks,"status":"findings" if findings else "limited_checks_passed","note":"Limited local audit only. No findings does not prove Zeus is secure; no files or settings were changed."}
