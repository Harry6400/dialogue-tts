"""Build and statically verify the native Apple Silicon app; never launch it."""
from pathlib import Path
import hashlib
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

from collect_licenses import collect
from package_sources import export_sources
from package_macos import package_macos

ROOT = Path(__file__).resolve().parent.parent


def main():
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise SystemExit('Build on an Apple Silicon Mac with Swift command line tools.')
    out = ROOT / 'dist'
    work = ROOT / 'build' / 'macos'
    work.mkdir(parents=True, exist_ok=True)
    runtime_licenses = work / 'runtime-licenses'
    collect(runtime_licenses)
    icon = work / 'dialogue.icns'
    subprocess.run(['/usr/bin/sips', '-s', 'format', 'icns', str(ROOT / 'assets' / 'dialogue.png'),
                    '--out', str(icon)], check=True, stdout=subprocess.DEVNULL)
    datas = [(str(ROOT / 'web_dialogue' / name), 'web_dialogue') for name in ('index.html', 'silence.mp3')]
    datas += [(str(ROOT / 'third_party'), 'third_party'), (str(runtime_licenses), 'third_party/bundled-runtime')]
    datas += [(str(ROOT / name), '.') for name in ('LICENSE', 'NOTICE')]
    spec = work / 'dialogue-macos.spec'
    spec.write_text(f'''from PyInstaller.utils.hooks import collect_submodules
a = Analysis([{str(ROOT / 'mac_launcher.py')!r}], pathex=[{str(ROOT)!r}], binaries=[], datas={datas!r},
    hiddenimports=collect_submodules('edge_tts') + ['web_dialogue.worker', 'web_dialogue.mp3_frames', 'web_dialogue.xml_pair', 'web_dialogue.updates', 'web_dialogue.fingerprints', 'web_dialogue.safe_updater', 'web_dialogue.gitee_provider', 'web_dialogue.update_sources', 'web_dialogue.update_helper', 'web_dialogue.update_cancel', 'web_dialogue.updater_bridge', 'web_dialogue.updater_cli', 'web_dialogue.install_lock'],
    hookspath=[], hooksconfig={{}}, runtime_hooks=[], excludes=['tkinter', 'unittest', 'pytest', 'numpy', 'pydub', 'imageio_ffmpeg'],
    noarchive=False, optimize=1)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='DialogueTTSService', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=True,
    disable_windowed_traceback=False, argv_emulation=False, target_arch='arm64', codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='DialogueTTSService')
app = BUNDLE(coll, name='对白.app', icon={str(icon)!r}, bundle_identifier='io.github.Harry6400.dialoguetts',
    info_plist={{'CFBundleDisplayName':'对白', 'CFBundleShortVersionString':'0.3.2', 'CFBundleVersion':'5',
    'LSMinimumSystemVersion':'12.0', 'NSHighResolutionCapable':True, 'LSApplicationCategoryType':'public.app-category.utilities'}})
''', encoding='utf-8')
    env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(work / 'cache'))
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', str(out),
                    '--workpath', str(work / 'pyinstaller'), str(spec)], cwd=ROOT, env=env, check=True)
    app = out / '对白.app'
    executable = app / 'Contents' / 'MacOS' / 'DialogueTTS'
    subprocess.run(['/usr/bin/swiftc', '-swift-version', '5', '-O', '-target', 'arm64-apple-macosx12.0',
                    '-module-cache-path', str(work / 'swift-cache'), str(ROOT / 'native_macos' / 'App.swift'),
                    '-o', str(executable)], check=True)
    plist_path = app / 'Contents' / 'Info.plist'
    with plist_path.open('rb') as stream:
        info = plistlib.load(stream)
    info['CFBundleExecutable'] = 'DialogueTTS'
    info['CFBundleName'] = '对白'
    info.pop('LSBackgroundOnly', None)
    info['NSAppTransportSecurity'] = {'NSAllowsLocalNetworking': True}
    with plist_path.open('wb') as stream:
        plistlib.dump(info, stream)
    # The former bundle entry point's signature included the old Info.plist.
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--identifier',
                    'io.github.Harry6400.dialoguetts.service',
                    str(app / 'Contents' / 'MacOS' / 'DialogueTTSService')], check=True)
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)], check=True)
    subprocess.run(['/usr/bin/plutil', '-lint', str(plist_path)], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    architecture = subprocess.check_output(['/usr/bin/file', '-b', str(executable)], text=True).strip()
    if 'arm64' not in architecture:
        raise RuntimeError('Unexpected app architecture: ' + architecture)
    stage = out / 'DialogueTTS-macOS-arm64'
    stage.mkdir(exist_ok=True)
    target = stage / app.name
    if target.exists():
        raise RuntimeError('Distribution already contains an app; use a fresh output directory.')
    shutil.copytree(app, target, symlinks=True)
    for name in ('LICENSE', 'NOTICE'):
        shutil.copyfile(ROOT / name, stage / name)
    (stage / '开始使用.txt').write_text(
        '对白 · Mac Apple Silicon 版（macOS 12 或更新）\n\n双击“对白.app”，会打开应用自己的窗口。\n'
        '选择 XML 来源文件夹与结果文件夹，解析预览后选择声音并生成 MP3。\n'
        '生成语音需要联网；无需安装 Python、FFmpeg、模型或配置后端。原文件保持不变。\n'
        '关窗或点击“退出应用”会结束后台并清理临时音频，请先保存需要的结果。\n'
        '“检查更新”查找本项目正式版本；按应用提示确认后安装并重启。\n\n'
        '界面使用系统 WKWebView，服务仅在本机临时端口运行，不是外部后端。\n'
        '本包为本机构建，包含临时签名，未经过 Apple 开发者公证。\n'
        '源码与重建说明见同目录 DialogueTTS-Source.zip。\n', encoding='utf-8')
    export_sources(ROOT, stage / 'DialogueTTS-Source.zip')
    archive = out / 'DialogueTTS-macOS-arm64.zip'
    package_macos(stage, archive)
    from web_dialogue.safe_updater import extract_safe, validate_payload
    with tempfile.TemporaryDirectory(prefix='dialogue-built-archive-') as check:
        extracted = extract_safe(archive, Path(check) / 'payload', 'darwin')
        validate_payload(extracted, info['CFBundleShortVersionString'], 'darwin')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out / (archive.name + '.sha256')).write_text(digest + '  ' + archive.name + '\n', encoding='utf-8')
    app_bytes = sum(file.stat().st_size for file in app.rglob('*') if file.is_file() and not file.is_symlink())
    print(f'App: {app_bytes} bytes; zip: {archive.stat().st_size} bytes; SHA256: {digest}')


if __name__ == '__main__':
    main()
