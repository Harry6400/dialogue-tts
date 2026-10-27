"""Double-click Windows entry point. Embedded runtime; no external server install."""
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import urllib.request
import webbrowser


from native_windows_folders import NativeFolderMixin
from native_windows_updater import NativeUpdaterMixin


class NativeDownloads(NativeFolderMixin, NativeUpdaterMixin):
    """Only app-owned successful saves may be revealed; JS never supplies a path."""
    def __init__(self, base):
        self._base = base
        self._window = None
        self._saved = {}
        self._lock = threading.Lock()
        self._setup_folders()

    def save_download(self, route):
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
            name = 'dialogues.zip' if is_zip else ('dialogue-' + route.rsplit('/', 1)[1] + '.mp3' if '/batches/' in route else 'dialogue.mp3')
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
            os.replace(temporary, target); temporary = None
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
                    with tempfile.TemporaryDirectory() as folder:
                        target = Path(folder) / 'saved.mp3'
                        callbacks = []
                        bridge = NativeDownloads(base)
                        bridge._window = SimpleNamespace(create_file_dialog=lambda *a, **k: (str(target),), evaluate_js=callbacks.append)
                        with patch.dict(sys.modules, {'webview': SimpleNamespace(FileDialog=SimpleNamespace(SAVE=30))}):
                            saved = bridge.save_download(status['download_url'])
                        with patch('os.startfile', create=True) as reveal:
                            opened = bridge.reveal_saved(saved.get('save_id'))
                            report['native_save_reveal'] = bool(saved.get('ok') and target.read_bytes() == audio and callbacks and opened.get('ok') and reveal.call_args.args[0] == str(target.parent) and not bridge.reveal_saved('unknown').get('ok'))
                        if not report['native_save_reveal']: raise RuntimeError('Native save/reveal test failed')
                        bridge._setup_folders(Path(folder) / 'private-preferences.json')
                        folder_cap = bridge._authorize(Path(folder), 'output')['folder_id']
                        folder_saved = bridge.save_to_output(status['download_url'], folder_cap)
                        duplicate = bridge.save_to_output(status['download_url'], folder_cap)
                        completed = bridge.check_completed(folder_cap, [preview['fingerprint']])
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
    server = make_server(0)
    # The native window close control owns the shutdown lifecycle.
    server.can_shutdown = False
    service = threading.Thread(target=server.serve_forever, daemon=True)
    service.start()
    webview.settings['ALLOW_DOWNLOADS'] = False
    webview.settings['ALLOW_FILE_URLS'] = False
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
    webview.settings['IGNORE_SSL_ERRORS'] = False
    url = f"http://127.0.0.1:{server.server_address[1]}"
    downloads = NativeDownloads(url)
    window = webview.create_window('对白', url, js_api=downloads, width=1180, height=840, min_size=(760,620), background_color='#F5F7F8')
    downloads._window = window
    downloads._setup_updater(server)
    def acknowledge_update():
        if len(sys.argv)==4 and sys.argv[1]=="--update-receipt":
            from web_dialogue.update_helper import acknowledge_receipt
            acknowledge_receipt(sys.argv[2],sys.argv[3])
    window.events.loaded += acknowledge_update
    report = {'frozen':bool(getattr(sys,'frozen',False)), 'platform':sys.platform, 'passed':False}
    def closing():
        if getattr(server,"update_in_progress",False) and downloads._updater.state.get("state") != "waiting_for_exit": return False
        if gui_report: return True
        with server.jobs.lock:
            running = any(item['process'].poll() is None for item in server.jobs.items.values())
        if running:
            import ctypes
            return ctypes.windll.user32.MessageBoxW(None, '正在合成，退出会停止本次任务。确定退出？', '对白', 0x24) == 6
        return True
    window.events.closing += closing
    if gui_report:
        def inspect_window():
            try:
                ready = time.monotonic()+10
                while time.monotonic()<ready:
                    if window.evaluate_js("typeof config !== 'undefined' && config !== null"): break
                    time.sleep(.1)
                window.evaluate_js("document.getElementById('example').click()")
                deadline = time.monotonic()+15
                while time.monotonic()<deadline:
                    result = window.evaluate_js("JSON.stringify({title:document.title, rows:document.querySelectorAll('#preview .line').length, roles:document.querySelectorAll('#voices select').length, update:!!document.getElementById('checkupdate')})")
                    state = json.loads(result)
                    if state['rows'] == 5 and state['roles'] == 2:
                        report.update(state)
                        report['passed']=state['update']
                        break
                    time.sleep(.2)
                if not report['passed']: report['error']='Native UI did not show the expected example'
            except Exception as exc: report['error']=str(exc)
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
        server.shutdown(); server.server_close(); server.jobs.close()
        if installation_lease: installation_lease.close()
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
