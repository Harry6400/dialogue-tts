"""Build a self-contained Windows x64 app, then package only allowlisted files."""
from pathlib import Path
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from frontend_build import build_frontend, collect_frontend_licenses

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def pyinstaller_command(source, out, work, version_resource):
    return [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
                    '--name', 'DialogueTTS', '--version-file', str(version_resource), '--icon', str(source / 'assets' / 'dialogue.ico'), '--distpath', str(out), '--workpath', str(work),
                    '--specpath', str(work.parent), '--hidden-import', 'web_dialogue.worker',
                    '--hidden-import', 'web_dialogue.mp3_frames',
                    '--hidden-import', 'web_dialogue.updater_cli', '--hidden-import', 'web_dialogue.updater_bridge',
                    '--hidden-import', 'web_dialogue.safe_updater', '--hidden-import', 'web_dialogue.gitee_provider', '--hidden-import', 'web_dialogue.update_sources', '--hidden-import', 'web_dialogue.update_helper',
                    '--hidden-import', 'web_dialogue.install_lock', '--hidden-import', 'web_dialogue.update_cancel', '--hidden-import', 'native_windows_updater', '--collect-all', 'webview', '--collect-all', 'pythonnet',
                    '--hidden-import', 'clr_loader', '--exclude-module', 'PyQt5', '--exclude-module', 'PyQt6',
                    '--exclude-module', 'PySide2', '--exclude-module', 'PySide6', '--exclude-module', 'cefpython3', '--collect-data', 'certifi',
                    '--collect-submodules', 'edge_tts', '--add-data', f'{source / "web_dialogue" / "index.html"};web_dialogue',
                    '--add-data', f'{source / "web_dialogue" / "silence.mp3"};web_dialogue',
                    '--add-data', f'{source / "frontend" / "dist"};frontend/dist',
                    str(source / 'windows_launcher.py')]

def main():
    if sys.platform != 'win32':
        raise SystemExit('Build the Windows binary on a Windows x64 runner.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    out = args.output.resolve()
    build_frontend()
    from windows_version_resource import write_resource
    version_resource = ROOT / 'build' / 'windows-version.txt'
    write_resource(version_resource)
    subprocess.run(pyinstaller_command(ROOT, out, ROOT / 'build' / 'windows', version_resource), cwd=ROOT, check=True)
    exe = out / 'DialogueTTS.exe'
    if not exe.is_file() or exe.stat().st_size < 1000000:
        raise RuntimeError('The executable was not produced.')
    from web_dialogue.safe_updater import validate_payload
    from web_dialogue.updates import CURRENT_VERSION
    validate_payload(exe, CURRENT_VERSION, 'win32')
    # Frozen localhost HTTP+worker and native GUI smoke tests follow.
    subprocess.run([str(exe), '--self-test', str(out / 'windows-smoke.json'), '--live'], cwd=ROOT, check=True, timeout=240)
    # CI native-window verification is explicit; a failure is not an Android/Windows user-device pass.
    subprocess.run([str(exe), '--gui-test', str(out / 'windows-gui-smoke.json')], cwd=ROOT, check=True, timeout=80)
    subprocess.run([sys.executable, str(ROOT / 'tools' / 'windows_upgrade_smoke.py'), str(exe), str(out / 'windows-upgrade-smoke.json')], cwd=ROOT, check=True, timeout=600)
    stage = out / 'DialogueTTS-Windows-x64'
    stage.mkdir(exist_ok=True)
    shutil.copy2(exe, stage / exe.name)
    shutil.copy2(ROOT / 'WINDOWS_开始使用.txt', stage / '开始使用.txt')
    for name in ('LICENSE', 'NOTICE'):
        if (ROOT / name).is_file(): shutil.copy2(ROOT / name, stage / name)
    if (ROOT / 'third_party').is_dir():shutil.copytree(ROOT / 'third_party', stage / 'third_party', dirs_exist_ok=True)
    from collect_licenses import collect
    collect(stage / 'third_party' / 'bundled-runtime')
    collect_frontend_licenses(stage / 'third_party' / 'bundled-runtime')
    from package_sources import export_sources
    export_sources(ROOT, stage / 'DialogueTTS-Source.zip')
    archive = out / 'DialogueTTS-Windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for file in sorted(stage.rglob('*')):
            if file.is_file(): z.write(file, file.relative_to(out))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out / (archive.name + '.sha256')).write_text(digest + '  ' + archive.name + '\n')
    print(f'Built {archive.name}: {archive.stat().st_size} bytes; SHA-256 {digest}')

if __name__ == '__main__':main()
