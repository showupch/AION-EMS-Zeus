#!/usr/bin/env python3
from pathlib import Path
import ast, json, re, sys, zipfile
root=Path(__file__).resolve().parent
errors=[]
cc=root/'custom_components/aion_ems_zeus'
js=cc/'frontend/device_manager.js'
www=root/'www/aion_ems_zeus/device_manager.js'
text=js.read_text(encoding='utf-8')
# Packaging cleanliness
for artifact in root.rglob('*'):
    if artifact.is_dir() and artifact.name == '__pycache__': errors.append(f'packaging cache directory: {artifact}')
    if artifact.is_file() and artifact.suffix in {'.pyc', '.pyo'}: errors.append(f'compiled Python artifact: {artifact}')

# Python syntax
for p in cc.rglob('*.py'):
    try: ast.parse(p.read_text(encoding='utf-8'), filename=str(p))
    except Exception as e: errors.append(f'Python syntax {p}: {e}')
# JSON
try: manifest=json.loads((cc/'manifest.json').read_text())
except Exception as e: errors.append(f'manifest: {e}'); manifest={}
# mirrored JS
if not www.exists() or js.read_bytes()!=www.read_bytes(): errors.append('frontend copies differ')
# renderer contract
method_names=set(re.findall(r'^\s{2}([A-Za-z_$][\w$]*)\([^\n]*\)\{',text,re.M))
registry_block=text[text.index('  pageRegistry(){'):text.index('  navigationSections(){')]
renderers=set(re.findall(r"renderer:'([^']+)'",registry_block))
aliases=re.findall(r"([A-Za-z_][\w]*):\{alias:'([^']+)'\}",registry_block)
keys=set(re.findall(r'(?<![A-Za-z0-9_$])([A-Za-z_][\w]*):\{',registry_block))
for renderer in sorted(renderers-method_names): errors.append(f'missing renderer method: {renderer}')
# required helper contracts
for helper_list in re.findall(r"requires:\[([^\]]*)\]",registry_block):
    for helper in re.findall(r"'([^']+)'",helper_list):
        if helper not in method_names: errors.append(f'missing required helper method: {helper}')
for renderer in sorted(renderers):
    count=len(re.findall(rf'^\s{{2}}{re.escape(renderer)}\(',text,re.M))
    if count!=1: errors.append(f'renderer method {renderer} occurs {count} times')
for source,target in aliases:
    if target not in keys: errors.append(f'alias {source} targets missing page {target}')
# literal route targets registered (buttons, shortcuts and fallback actions)
for target in sorted(set(re.findall(r'data-page=[\"\']([^\"\']+)[\"\']', text))):
    if '${' in target: continue
    if target not in keys: errors.append(f'literal data-page target not registered: {target}')
# navigation pages registered
nav_block=text[text.index('  navigationSections(){'):text.index('  resolvePageDefinition(',text.index('  navigationSections(){'))]
for page_list in re.findall(r"pages:\[([^\]]*)\]",nav_block):
    for page in re.findall(r"'([^']+)'",page_list):
        if page not in keys: errors.append(f'navigation page not registered: {page}')
# component identity
backend=(cc/'__init__.py').read_text()
front_tags=re.findall(r"customElements\.(?:get|define)\('([^']+)'",text)
backend_tag=(re.search(r'webcomponent_name="([^"]+)"',backend) or [None,None])[1]
if backend_tag!='aion-ems-zeus-dashboard': errors.append(f'backend tag is {backend_tag}')
if set(front_tags)!={'aion-ems-zeus-dashboard','aion-ems-zeus-command-center'}: errors.append(f'frontend tags are {set(front_tags)}')
const_text=(cc/'const.py').read_text(); expected=(re.search(r'^VERSION\s*=\s*[\"\']([^\"\']+)',const_text,re.M) or [None,None])[1]
if not expected or manifest.get('version')!=expected: errors.append(f"manifest version {manifest.get('version')} does not match const {expected}")
# dangerous uncaught direct route map should be gone
if 'const renderer=({' in text: errors.append('legacy hand-written renderer map still exists')
if errors:
    print('\n'.join('FAIL: '+e for e in errors));sys.exit(1)
print(f'PASS: {len(list(cc.rglob("*.py")))} Python files')
print(f'PASS: {len(keys)} registered routes, {len(renderers)} renderer contracts')
print('PASS: navigation registry, aliases, component identity and mirrored frontend')
