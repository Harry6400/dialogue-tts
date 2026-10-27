"""Build a self-contained Windows x64 app, then package only allowlisted files."""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent

def main():
    if sys.platform != 'win32':
        raise SystemExit('Build the Windows binary on a Windows x64 runner.')
    out = ROOT / 'dist'
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
                    '--name', 'DialogueTTS', '--icon', str(ROOT / 'assets' / 'dialogue.ico'), '--distpath', str(out), '--workpath', str(ROOT / 'build' / 'windows'),
                    '--specpath', str(ROOT / 'build'), '--hidden-import', 'web_dialogue.worker',
                    '--hidden-import', 'web_dialogue.mp3_frames', '--collect-all', 'webview', '--collect-all', 'pythonnet',
                    '--hidden-import', 'clr_loader', '--exclude-module', 'PyQt5', '--exclude-module', 'PyQt6',
                    '--exclude-module', 'PySide2', '--exclude-module', 'PySide6', '--exclude-module', 'cefpython3', '--collect-data', 'certifi',
                    '--collect-submodules', 'edge_tts', '--add-data', f'{ROOT / "web_dialogue" / "index.html"};web_dialogue',
                    '--add-data', f'{ROOT / "web_dialogue" / "silence.mp3"};web_dialogue',
                    str(ROOT / 'windows_launcher.py')], cwd=ROOT, check=True)
    exe = out / 'DialogueTTS.exe'
    if not exe.is_file() or exe.stat().st_size < 1000000:
        raise RuntimeError('The executable was not produced.')
    # Desktop UI opening isn't possible here; a frozen localhost HTTP+worker test is.
    subprocess.run([str(exe), '--self-test', str(out / 'windows-smoke.json'), '--live'], cwd=ROOT, check=True, timeout=240)
    # CI native-window verification is explicit; a failure is not an Android/Windows user-device pass.
    subprocess.run([str(exe), '--gui-test', str(out / 'windows-gui-smoke.json')], cwd=ROOT, check=True, timeout=80)
    stage = out / 'DialogueTTS-Windows-x64'
    stage.mkdir(exist_ok=True)
    shutil.copy2(exe, stage / exe.name)
    shutil.copy2(ROOT / 'WINDOWS_开始使用.txt', stage / '开始使用.txt')
    for name in ('LICENSE', 'NOTICE'):
        if (ROOT / name).is_file(): shutil.copy2(ROOT / name, stage / name)
    if (ROOT / 'third_party').is_dir():shutil.copytree(ROOT / 'third_party', stage / 'third_party', dirs_exist_ok=True)
    from collect_licenses import collect
    collect(stage / 'third_party' / 'bundled-runtime')
    archive = out / 'DialogueTTS-Windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for file in sorted(stage.rglob('*')):
            if file.is_file(): z.write(file, file.relative_to(out))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out / (archive.name + '.sha256')).write_text(digest + '  ' + archive.name + '\n')
    print(f'Built {archive.name}: {archive.stat().st_size} bytes; SHA-256 {digest}')

if __name__ == '__main__':main()
