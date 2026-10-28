"""Build the exact locked React workbench shipped by both native hosts."""
from pathlib import Path
import json
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def build_frontend(root=ROOT):
    root = Path(root)
    npm = shutil.which('npm.cmd' if sys.platform == 'win32' else 'npm')
    if not npm:
        raise RuntimeError('Node 22 / npm are required to build the workbench. Run npm ci first.')
    subprocess.run([npm, 'run', 'build', '--workspace', 'frontend'], cwd=root, check=True)
    output = root / 'frontend' / 'dist'
    if not (output / 'index.html').is_file() or not any((output / 'assets').glob('*.js')):
        raise RuntimeError('The production workbench assets were not produced.')
    return output


def collect_frontend_licenses(destination, root=ROOT):
    root, destination = Path(root).resolve(), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    inventory = []
    dependencies = json.loads((root / 'frontend' / 'package.json').read_text(encoding='utf-8'))['dependencies']
    queue = [(name, root / 'frontend') for name in dependencies]
    seen = set()
    while queue:
        name, origin = queue.pop()
        package = next((directory / 'node_modules' / name for directory in (origin, *origin.parents)
                        if (directory == root or root in directory.parents)
                        if (directory / 'node_modules' / name / 'package.json').is_file()), None)
        if package is None:
            raise RuntimeError('Missing locked frontend dependency: ' + name)
        metadata = json.loads((package / 'package.json').read_text(encoding='utf-8'))
        key = metadata['name'] + '@' + metadata['version']
        if key in seen:
            continue
        seen.add(key)
        folder_name = re.sub(r'[^A-Za-z0-9_.-]', '-', metadata['name']).strip('-') + '-' + metadata['version']
        folder = destination / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        files = []
        for license_path in sorted(package.iterdir()):
            if license_path.is_file() and re.match(r'(?i)^(licen[sc]e|copying|notice)([._-].*)?$', license_path.name):
                shutil.copyfile(license_path, folder / license_path.name)
                files.append(folder_name + '/' + license_path.name)
        if not files:
            fallback = root / 'third_party' / (metadata['name'] + '-' + metadata['version'] + '-LICENSE.txt')
            provenance = fallback.with_name(fallback.name.replace('-LICENSE.txt', '-SOURCE.json'))
            if key != 'react-remove-scroll-bar@2.3.8' or metadata.get('license') != 'MIT' or not fallback.is_file() or not provenance.is_file():
                raise RuntimeError('Missing frontend runtime license text: ' + key)
            shutil.copyfile(fallback, folder / 'LICENSE.txt')
            shutil.copyfile(provenance, folder / 'SOURCE.json')
            files = [folder_name + '/LICENSE.txt', folder_name + '/SOURCE.json']
        inventory.append({'name': metadata['name'], 'version': metadata['version'],
                          'license': metadata.get('license'), 'files': files})
        queue.extend((dependency, package) for dependency in metadata.get('dependencies', {}))
    inventory.sort(key=lambda item: (item['name'], item['version']))
    (destination / 'frontend-dependencies.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    return inventory
