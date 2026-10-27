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
        data = {"data": config["example"][:2], "array_path": "", "speaker_key": "speaker", "text_key": "text", "rate": 1, "pause_ms": 250,
                "voices": {"A": "zh-CN-XiaoxiaoNeural", "B": "zh-CN-XiaoyiNeural"}, "consent": True}
        def post(path, payload):
            request = urllib.request.Request(base + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "X-App-Token": config["token"]})
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.load(response)
        preview = post("/api/preview", data)
        report["preview"] = len(preview["rows"]) == 2
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
    from web_dialogue.app import make_server
    server = make_server(0)
    # The native window close control owns the shutdown lifecycle.
    server.can_shutdown = False
    service = threading.Thread(target=server.serve_forever, daemon=True)
    service.start()
    webview.settings['ALLOW_DOWNLOADS'] = True
    webview.settings['ALLOW_FILE_URLS'] = False
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
    webview.settings['IGNORE_SSL_ERRORS'] = False
    url = f"http://127.0.0.1:{server.server_address[1]}"
    window = webview.create_window('对白', url, width=1180, height=840, min_size=(760,620), background_color='#F5F7F8')
    report = {'frozen':bool(getattr(sys,'frozen',False)), 'platform':sys.platform, 'passed':False}
    def closing():
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
                    if state['rows'] == 6 and state['roles'] == 2:
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
        if gui_report:
            timeout.cancel()
            Path(gui_report).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if not gui_report or report['passed'] else 1


def main():
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
