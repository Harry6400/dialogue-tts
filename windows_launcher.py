"""Double-click Windows entry point. Embedded runtime; no external server install."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import sys
import threading
import time
import urllib.request
import webbrowser
from contextlib import ExitStack


from native_windows_folders import NativeFolderMixin
from native_windows_updater import NativeUpdaterMixin


class NativeDownloads(NativeFolderMixin, NativeUpdaterMixin):
    """Only app-owned successful saves may be revealed; JS never supplies a path."""
    def __init__(self, base, storage=None, native_token=None, workspace_access=None):
        self._base = base
        self._window = None
        self._closing = False
        self._saved = {}
        self._lock = threading.Lock()
        self._native_token = native_token
        self._workspace_access = workspace_access
        self._owns_workspace_access = False
        self._setup_folders(storage)

    def request_close(self):
        if self._window is None: return {'ok': False}
        threading.Timer(.05, self._window.destroy).start()
        return {'ok': True}

    def _close_snapshot(self, page):
        from native_close import merge_close_safety, workspace_safety
        server = self._update_server
        state = merge_close_safety(page, workspace_safety(server))
        if state is not None:
            with server.jobs.lock, server.batches.lock:
                state['running'] = state['running'] or any(item['process'].poll() is None for item in server.jobs.items.values()) or bool(server.batches.active)
        return state

    def save_download(self, route):
        if self._closing: return {'ok': False, 'error': '正在退出'}
        if getattr(getattr(self,"_update_server",None),"update_in_progress",False): return {"ok":False,"error":"正在准备更新"}
        import re
        import tempfile
        import uuid
        if not isinstance(route, str) or not re.fullmatch(r'/api/(?:jobs/[a-f0-9]{32}/audio|batches/[a-f0-9]{32}/(?:zip|audio/[0-9]{3}))', route):
            return {'ok': False, 'error': '下载地址无效'}
        if not self._lock.acquire(blocking=False):
            return {'ok': False, 'error': '请先完成当前保存'}
        temporary = None
        try:
            import webview
            is_zip = route.endswith('/zip')
            if not is_zip:
                chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
                if not chosen: return {'ok':False,'cancelled':True}
                capability = self._authorize(Path(chosen[0]), 'output')
                try: return self.save_to_output(route,capability['folder_id'])
                finally: self._caps.pop(capability['folder_id'],None)
            name = 'dialogues.zip'
            chosen = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name,
                file_types=('ZIP (*.zip)',) if is_zip else ('MP3 (*.mp3)',))
            if not chosen:
                return {'ok': False, 'cancelled': True}
            target = Path(chosen[0]).absolute()
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs): return None
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            limit = 256 * 1024 * 1024
            with opener.open(self._base + route, timeout=30) as response:
                if response.headers.get_content_type() != ('application/zip' if is_zip else 'audio/mpeg'):
                    raise ValueError('Invalid audio response')
                expected = int(response.headers.get('Content-Length', '-1'))
                if not 0 < expected <= limit: raise ValueError('Invalid download size')
                fd, temporary = tempfile.mkstemp(prefix='.dialogue-save-', dir=str(target.parent))
                size = 0
                with os.fdopen(fd, 'wb') as output:
                    while True:
                        block = response.read(65536)
                        if not block: break
                        size += len(block)
                        if size > limit or size > expected: raise ValueError('Oversized download')
                        output.write(block)
                    output.flush(); os.fsync(output.fileno())
                if size != expected: raise ValueError('Incomplete download')
            if os.name == 'nt': os.rename(temporary,target)
            else: os.link(temporary,target); Path(temporary).unlink()
            temporary = None
            ident = uuid.uuid4().hex
            self._saved[ident] = target
            record = {'save_id': ident, 'name': target.name, 'path': str(target), 'resource_path': route}
            try:
                self._window.evaluate_js('window.dialogueNativeSaved && window.dialogueNativeSaved(' + json.dumps(record, ensure_ascii=True) + ')')
            except Exception:
                pass  # Saved on disk even if the window has just closed.
            return {'ok': True, **record}
        except Exception:
            return {'ok': False, 'error': '保存失败，请重试'}
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
            self._lock.release()

    def save_to_output(self, resource_path, folder_id):
        if self._closing: return {"ok":False,"error":"正在退出"}
        if getattr(getattr(self,"_update_server",None),"update_in_progress",False): return {"ok":False,"error":"正在准备更新"}
        return super().save_to_output(resource_path, folder_id)

    def reveal_saved(self, save_id):
        if not isinstance(save_id, str): return {'ok': False}
        target = self._saved.get(save_id)
        if target is None or not target.is_file(): return {'ok': False, 'error': '保存记录不存在'}
        try:
            os.startfile(str(target.parent), 'explore')
            return {'ok': True}
        except OSError:
            return {'ok': False, 'error': '无法打开文件夹'}


def self_test(report_path, live=False):
    """CI packaging smoke test. Does not open a browser or use private input."""
    from web_dialogue.app import make_server
    server = make_server(0)
    server.can_shutdown = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    report = {"frozen": bool(getattr(sys, "frozen", False)), "platform": sys.platform}
    try:
        with urllib.request.urlopen(base + "/api/config", timeout=10) as response:
            config = json.load(response)
        report["config"] = bool(config["voices"] and config["token"])
        with urllib.request.urlopen(base, timeout=10) as response:
            html = response.read()
        report["ui_asset"] = b"<html" in html and len(html) > 5000
        data = {"format":"xml_pair", "xml_files":config["xml_example"], "xml_time_unit":"ms", "rate":1, "pause_ms":250,
                "voices":{"AGENT":"zh-CN-XiaoxiaoNeural", "USER":"zh-CN-XiaoyiNeural"}, "consent":True}
        def post(path, payload):
            request = urllib.request.Request(base + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "X-App-Token": config["token"]})
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.load(response)
        preview = post("/api/preview", data)
        report["preview"] = len(preview["rows"]) == 5 and preview["muted_count"] == 1
        if live:
            cancelled_job = post("/api/jobs", data)
            time.sleep(0.3)
            cancelled = post("/api/jobs/" + cancelled_job["id"] + "/cancel", {})
            cancelled_process = server.jobs.items[cancelled_job["id"]]["process"]
            report["cancelled_worker"] = cancelled["status"] == "cancelled" and cancelled_process.poll() is not None
            if not report["cancelled_worker"]:
                raise RuntimeError("Worker process tree cancellation was not confirmed")
            job = post("/api/jobs", data)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                with urllib.request.urlopen(base + "/api/jobs/" + job["id"], timeout=10) as response:
                    status = json.load(response)
                if status["status"] == "done":
                    with urllib.request.urlopen(base + status["download_url"], timeout=10) as response:
                        audio = response.read()
                    from web_dialogue.mp3_frames import parse_frames
                    report["live_audio_frames"] = len(parse_frames(audio))
                    report["live_synthesis"] = report["live_audio_frames"] > 20
                    Path(report_path).with_suffix(".mp3").write_bytes(audio)
                    import tempfile
                    from types import SimpleNamespace
                    from unittest.mock import patch
                    with tempfile.TemporaryDirectory() as folder, ExitStack() as cleanup:
                        target = Path(folder) / status['filename']
                        callbacks = []
                        bridge = NativeDownloads(base,Path(folder)/'private-preferences.json')
                        cleanup.callback(bridge._close_folders)
                        bridge._window = SimpleNamespace(create_file_dialog=lambda *a, **k: (folder,), evaluate_js=callbacks.append)
                        with patch.dict(sys.modules, {'webview': SimpleNamespace(FileDialog=SimpleNamespace(FOLDER=20,SAVE=30))}):
                            saved = bridge.save_download(status['download_url'])
                        with patch('os.startfile', create=True) as reveal:
                            opened = bridge.reveal_saved(saved.get('save_id'))
                            report['native_save_reveal'] = bool(saved.get('ok') and target.read_bytes() == audio and callbacks and opened.get('ok') and reveal.call_args.args[0] == str(target.parent) and not bridge.reveal_saved('unknown').get('ok'))
                        if not report['native_save_reveal']: raise RuntimeError('Native save/reveal test failed')
                        bridge._setup_folders(Path(folder) / 'private-preferences.json')
                        folder_cap = bridge._authorize(Path(folder), 'output')['folder_id']
                        folder_saved = bridge.save_to_output(status['download_url'], folder_cap)
                        duplicate = bridge.save_to_output(status['download_url'], folder_cap)
                        completed = bridge.check_completed(folder_cap, [preview['fingerprint']], {preview['fingerprint']:preview['filename']})
                        report['native_folder_export'] = bool(folder_saved.get('ok') and duplicate.get('path') == folder_saved.get('path') and len(completed.get('completed', [])) == 1 and Path(folder_saved['path']).read_bytes() == audio)
                        if not report['native_folder_export']: raise RuntimeError('Native folder export verification failed')

                    break
                if status["status"] in {"error", "cancelled"}:
                    raise RuntimeError(status.get("error", status["status"]))
                time.sleep(.5)
            else:
                raise TimeoutError("Frozen synthesis smoke test timed out")
        report["passed"] = all(report[k] for k in ("config", "ui_asset", "preview")) and (not live or (report.get("live_synthesis", False) and report.get("cancelled_worker", False)))
    except Exception as exc:
        report["passed"] = False
        report["error"] = str(exc)
    finally:
        server.shutdown()
        server.server_close()
        server.jobs.close()
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


def has_webview2():
    """Require the modern system renderer; never silently fall back to Internet Explorer."""
    if sys.platform != 'win32': return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full') as key:
            if int(winreg.QueryValueEx(key, 'Release')[0]) < 394802: return False
    except OSError: return False
    client = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for prefix in ('SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\', 'SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\'):
            try:
                with winreg.OpenKey(root, prefix + client) as key:
                    version = str(winreg.QueryValueEx(key, 'pv')[0])
                if int(version.split('.')[0]) >= 86: return True
            except (OSError, ValueError): pass
    return False


def missing_runtime_message():
    import ctypes
    answer = ctypes.windll.user32.MessageBoxW(None,
        '此电脑缺少 Microsoft Edge WebView2 或受支持的 .NET 运行环境。\n应用不会自动安装软件。是否打开微软官方 WebView2 下载页？',
        '对白 · 缺少系统组件', 0x24)
    if answer == 6:
        webbrowser.open('https://developer.microsoft.com/microsoft-edge/webview2/#download-section')


def run_window(gui_report=None):
    if not has_webview2():
        if gui_report:
            Path(gui_report).write_text(json.dumps({'passed':False,'error':'System WebView2/.NET runtime missing'}),encoding='utf-8')
        else: missing_runtime_message()
        return 1
    import webview
    from web_dialogue.install_lock import InstallationLease
    installation_lease=InstallationLease(sys.executable).acquire() if getattr(sys,'frozen',False) else None
    from web_dialogue.app import make_server
    from web_dialogue.verification_profile import verification_root
    import secrets
    gui_temp = tempfile.TemporaryDirectory(prefix='dialogue-gui-ci-') if gui_report else None
    state_dir = Path(gui_temp.name) if gui_temp else verification_root() or Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'DialogueTTS'
    native_token = secrets.token_hex(32)
    server = make_server(0, state_dir=state_dir, native_token=native_token)
    # The native window close control owns the shutdown lifecycle.
    server.can_shutdown = False
    service = threading.Thread(target=server.serve_forever, daemon=True)
    service.start()
    webview.settings['ALLOW_DOWNLOADS'] = False
    webview.settings['ALLOW_FILE_URLS'] = False
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
    webview.settings['IGNORE_SSL_ERRORS'] = False
    url = f"http://127.0.0.1:{server.server_address[1]}"
    downloads = NativeDownloads(url, state_dir / 'preferences.json', native_token=native_token, workspace_access=server.native_access)
    if gui_report:
        from web_dialogue.xml_example import XML_EXAMPLE
        fixture = state_dir / 'synthetic-input'; fixture.mkdir()
        for i, item in enumerate(XML_EXAMPLE):
            (fixture / f'20261028001{i}.xml').write_text(item['content'], encoding='utf-8')
        grant = server.native_access.register(fixture, 'input')
        server.workspaces.import_folder(grant['folder_id'])
    window = webview.create_window('对白', url, js_api=downloads, width=1180, height=840, min_size=(760,620), background_color='#F5F7F8')
    downloads._window = window
    downloads._setup_updater(server)
    def acknowledge_update():
        if len(sys.argv)==4 and sys.argv[1]=="--update-receipt":
            from web_dialogue.update_helper import acknowledge_receipt
            acknowledge_receipt(sys.argv[2],sys.argv[3])
    window.events.loaded += acknowledge_update
    report = {'frozen':bool(getattr(sys,'frozen',False)), 'platform':sys.platform, 'passed':False}
    checking_close = threading.Event()
    def review_close():
        save_guard = False; folder_guard = False
        try:
            with server.update_lock:
                save_guard = downloads._lock.acquire(False)
                folder_guard = downloads._folder_lock.acquire(False)
                import ctypes
                from native_close import close_decision, workspace_safety
                try: page = window.evaluate_js("window.dialogueCloseSafety && window.dialogueCloseSafety()")
                except Exception: page = None
                state = downloads._close_snapshot(page)
                def prompt(kind):
                    if kind == 'unsaved':
                        answer = ctypes.windll.user32.MessageBoxW(None, '还有未保存的临时音频。退出会清理临时结果，并停止正在运行的任务。\n是：返回保存；否：明确放弃并退出；取消：取消退出。', '对白', 0x23)
                        return {6:'save',7:'discard'}.get(answer,'cancel')
                    if kind == 'persistent':
                        message = '还有未保存的完整音频，已保存在本机任务缓存中。退出会中断后台任务，但会保留进度和缓存；下次打开核验目录后可重试保存或继续。是否保存进度并退出？'
                        return 'preserve' if ctypes.windll.user32.MessageBoxW(None, message, '对白', 0x24) == 6 else 'cancel'
                    if kind == 'running':
                        message = '退出将中断后台任务；进度已保存在本机，下次打开核验目录后可继续。' if isinstance(state, dict) and state.get('persistent_workspace') else '正在合成，确定停止并退出？'
                        return 'discard' if ctypes.windll.user32.MessageBoxW(None, message, '对白', 0x24) == 6 else 'cancel'
                    ctypes.windll.user32.MessageBoxW(None, '正在保存或暂时无法确认全部任务状态，请完成操作后再退出。', '对白', 0x40)
                    return 'cancel'
                decision = close_decision(state, saving=not save_guard or not folder_guard, prompt=prompt)
                if decision == 'save':
                    window.evaluate_js("window.dialogueShowUnsaved && window.dialogueShowUnsaved()")
                if decision != 'exit': return
                # A running workspace can enter publication while its confirmation
                # is displayed. Recheck immediately before destroying the window.
                latest = workspace_safety(server)
                if latest is None or latest['saving'] or latest['pending']:
                    prompt('unknown' if latest is None else 'saving'); return
                downloads._closing = True
                window.destroy()
        finally:
            if folder_guard: downloads._folder_lock.release()
            if save_guard: downloads._lock.release()
            checking_close.clear()

    def closing():
        if getattr(server,"update_in_progress",False) and downloads._updater.state.get("state") != "waiting_for_exit": return False
        if gui_report or downloads._closing: return True
        if not checking_close.is_set():
            checking_close.set()
            threading.Thread(target=review_close, daemon=True).start()
        return False
    window.events.closing += closing
    if gui_report:
        def capture_window(name):
            # PrintWindow targets this process's titled app window, never a desktop screenshot.
            script = Path(gui_temp.name) / 'capture.ps1'
            script.write_text(r'''Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class CaptureWindow {
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
}
"@
$p=Get-Process -Id $args[0]; $h=$p.MainWindowHandle
if ($h -eq [IntPtr]::Zero) { throw "No app window" }
Add-Type @"
using System; using System.Runtime.InteropServices;
public class WindowBounds {
 [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
}
"@
$r=New-Object WindowBounds+RECT; if (-not [WindowBounds]::GetWindowRect($h,[ref]$r)) { throw "No bounds" }
$w=$r.R-$r.L; $height=$r.B-$r.T; if ($w -le 0 -or $height -le 0 -or $w -gt 2400 -or $height -gt 1600) { throw "Invalid bounds" }
$b=New-Object Drawing.Bitmap $w,$height; $g=[Drawing.Graphics]::FromImage($b); $dc=$g.GetHdc()
try { if (-not [CaptureWindow]::PrintWindow($h,$dc,2)) { throw "PrintWindow failed" } } finally { $g.ReleaseHdc($dc) }
$b.Save($args[1],[Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $b.Dispose()
''', encoding='utf-8')
            output = Path(gui_report).parent / ('windows-gui-' + name + '.png')
            subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(script),str(os.getpid()),str(output)], check=True, timeout=15, capture_output=True)
            return output.name
        def inspect_window():
            try:
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    ready = window.evaluate_js("!!document.querySelector('[data-testid=workspace-ready]') && !!document.querySelector('[data-testid=app-shell]')")
                    if ready: break
                    time.sleep(.2)
                if not ready: raise RuntimeError('Native React workspace did not become ready')
                def set_theme(theme):
                    if window.evaluate_js("document.documentElement.dataset.theme") != theme:
                        window.evaluate_js("document.querySelector('[data-testid=theme-toggle]').click()")
                    until = time.monotonic() + 5
                    while time.monotonic() < until:
                        if window.evaluate_js("document.documentElement.dataset.theme") == theme: return
                        time.sleep(.1)
                    raise RuntimeError('Theme change did not apply')
                state = json.loads(window.evaluate_js("JSON.stringify({title:document.title,app_shell:!!document.querySelector('[data-testid=app-shell]'),sidebar:!!document.querySelector('[data-testid=sidebar]'),import_folder:!!document.querySelector('[data-testid=import-folder]'),consent_unchecked:!!document.querySelector('[data-testid=consent]')&&!document.querySelector('[data-testid=consent]').checked,generate_disabled:!!document.querySelector('[data-testid=generate]')&&document.querySelector('[data-testid=generate]').disabled,close_safety:typeof window.dialogueCloseSafety==='function'&&window.dialogueCloseSafety().persistent_workspace===true})"))
                report['screenshots'] = []
                window.resize(1280, 800); set_theme('light')
                report['screenshots'].append(capture_window('light-1280'))
                window.resize(1024, 700); set_theme('dark')
                report['screenshots'].append(capture_window('dark-1024'))
                state['theme_restore'] = downloads.get_preferences()['theme'] == 'dark' and NativeDownloads(url, state_dir / 'preferences.json', native_token=native_token, workspace_access=server.native_access).get_preferences()['theme'] == 'dark'
                state['scope_note'] = 'Real packaged React UI with one synthetic local XML pair; no live speech or update installation; screenshots require visual review.'
                report.update(state)
                report['passed'] = all(state[k] for k in ('app_shell', 'sidebar', 'import_folder', 'consent_unchecked', 'generate_disabled', 'close_safety', 'theme_restore'))
                if not report['passed']: report['error'] = 'Native React controls or privacy defaults failed verification'
            except Exception as exc: report['error'] = str(exc)
            finally: window.destroy()
        window.events.loaded += lambda: threading.Thread(target=inspect_window,daemon=True).start()
        timeout = threading.Timer(40, lambda: window.destroy())
        timeout.daemon=True;timeout.start()
    try:
        webview.start(gui='edgechromium', debug=False, private_mode=True)
    except Exception as exc:
        if gui_report: report['error']=str(exc)
        else: raise
    finally:
        server.shutdown(); server.server_close()
        if getattr(server, 'workspaces', None): server.workspaces.close()
        server.jobs.close()
        downloads._close_folders()
        if getattr(server, 'native_access', None): server.native_access.close()
        if installation_lease: installation_lease.close()
        if gui_temp: gui_temp.cleanup()
        if gui_report:
            timeout.cancel()
            Path(gui_report).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if not gui_report or report['passed'] else 1


def main():
    from web_dialogue.updater_cli import dispatch
    if dispatch(sys.argv[1:]): return 0
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        from web_dialogue.worker import main as worker_main
        worker_main(sys.argv[2])
        return 0
    if len(sys.argv) > 2 and sys.argv[1] == "--self-test":
        return self_test(sys.argv[2], "--live" in sys.argv[3:])
    if len(sys.argv) > 2 and sys.argv[1] == "--gui-test":
        return run_window(sys.argv[2])
    return run_window()


if __name__ == "__main__":
    try:
        result = main()
    except Exception:
        result = 1
        if len(sys.argv) > 2 and sys.argv[1] in {"--self-test", "--gui-test"}:
            import traceback
            Path(sys.argv[2]).write_text(json.dumps({"passed":False,"error":traceback.format_exc()},ensure_ascii=False,indent=2),encoding="utf-8")
        if sys.platform == "win32" and not (len(sys.argv) > 1 and sys.argv[1] in {"--worker", "--self-test", "--gui-test"}):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "应用未能启动，请确认文件完整后重试。原文件未被修改。", "对白", 0x10)
        if not getattr(sys, "frozen", False):
            raise
    sys.exit(result or 0)
