"""Collect actual installed runtime/build-loader license files for binary releases."""
from importlib import metadata
import json
from pathlib import Path
import re
import shutil
import sys
import sysconfig
import urllib.request
from packaging.requirements import Requirement


def collect(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    queue = ['edge-tts', 'certifi', 'defusedxml', 'pyinstaller', 'pywebview']
    seen = set()
    inventory = []
    while queue:
        requested = queue.pop()
        dist = metadata.distribution(requested)
        name = dist.metadata['Name']
        normalized = name.lower().replace('_','-')
        if normalized in seen: continue
        seen.add(normalized)
        folder = destination / (re.sub(r'[^A-Za-z0-9_.-]','_',name) + '-' + dist.version)
        folder.mkdir(exist_ok=True)
        copied = []
        for entry in dist.files or []:
            basename = Path(str(entry)).name
            if not re.match(r'(?i)^(licen[sc]e|copying|notice)([._-].*)?$', basename): continue
            original = Path(dist.locate_file(entry))
            if original.is_file():
                target = folder / (str(len(copied)).zfill(2) + '-' + basename)
                shutil.copy2(original, target)
                copied.append(target.name)
        inventory.append({'name':name,'version':dist.version,
                          'license':dist.metadata.get('License-Expression') or dist.metadata.get('License',''),
                          'homepage':dist.metadata.get('Home-page',''),
                          'project_urls':dist.metadata.get_all('Project-URL') or [], 'files':copied})
        for raw in dist.requires or []:
            requirement=Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate({'extra':''}):queue.append(requirement.name)
    candidates = [Path(sys.base_prefix)/'LICENSE.txt', Path(sys.base_prefix)/'LICENSE',
                  Path(sysconfig.get_path('stdlib'))/'LICENSE.txt',
                  Path(sys.base_prefix)/'lib'/('python%d.%d' % sys.version_info[:2])/'LICENSE.txt',
                  Path(sys.executable).resolve().parent.parent/'LICENSE.txt']
    python_license = next((p for p in candidates if p.is_file()), None)
    if python_license is not None:
        shutil.copy2(python_license,destination/'Python-LICENSE.txt')
    else:
        # Some Framework/Homebrew Python installs omit their license file.
        # Retrieve the exact interpreter release's official license, never a generic substitute.
        version = '.'.join(str(n) for n in sys.version_info[:3])
        url = 'https://raw.githubusercontent.com/python/cpython/v'+version+'/LICENSE'
        with urllib.request.urlopen(url, timeout=20) as response:
            body = response.read(1024*1024+1)
        if len(body)>1024*1024 or b'PYTHON SOFTWARE FOUNDATION' not in body:
            raise RuntimeError('Official Python license could not be verified.')
        (destination/'Python-LICENSE.txt').write_bytes(body)
    inventory.append({'name':'Python','version':sys.version.split()[0],
                      'source':'https://github.com/python/cpython/tree/v'+'.'.join(str(n) for n in sys.version_info[:3]),
                      'files':['Python-LICENSE.txt']})
    (destination/'installed-dependencies.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
    # Avoid silently shipping a binary whose key dependency license was omitted.
    missing=[x['name'] for x in inventory if not x['files'] and x['name'].lower() in {'edge-tts','certifi','defusedxml','pyinstaller','aiohttp'}]
    if missing:raise RuntimeError('Required license text missing: '+', '.join(missing))
    return inventory
