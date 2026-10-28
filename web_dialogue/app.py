"""Loopback-only browser UI; never exposes a public server automatically."""
import argparse
import base64
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid

from .normalize import VOICES, InputError, normalize
from .xml_example import XML_EXAMPLE
from .jobs import Jobs
from .source_processing import normalize_source, audition_script



MAX_BODY = 64 * 1024 * 1024
ROOT = Path(__file__).resolve().parent.parent

def close_resources(server):
    """Composition-root shutdown; failed components never skip their peers."""
    lock = getattr(server, '_resources_lock', None)
    if lock is None:
        lock = server._resources_lock = threading.RLock()
    with lock:
        closed = getattr(server, '_closed_resources', set())
        server._closed_resources = closed
        errors = []
        for name in ('workspaces', 'batches', 'jobs', 'reports', 'audio_captures', 'native_access'):
            component = getattr(server, name, None)
            if component is None or name in closed:
                continue
            try:
                component.close()
            except Exception:
                errors.append(name)
            else:
                closed.add(name)
        return {'ok': not errors, 'errors': errors}




class Handler(BaseHTTPRequestHandler):
    server_version = "DialogueTTS/1.0"

    def log_message(self, fmt, *args):
        pass  # Avoid logging dialogue, user-supplied filenames, or tokens.

    def valid_host(self):
        return self.headers.get("Host", "") in self.server.allowed_hosts

    def headers_for(self, status, mime, length):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.headers_for(status, "application/json; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

    def authenticated(self):
        key = getattr(self.server, "access_key", None)
        if not key:
            return True
        try:
            kind, encoded = self.headers.get("Authorization", "").split(" ", 1)
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
            okay = kind.lower() == "basic" and username == "tts" and hmac.compare_digest(password.encode("utf-8"), key.encode("utf-8"))
        except (ValueError, UnicodeError):
            okay = False
        if not okay:
            body = "需要访问密码。".encode("utf-8")
            self.headers_for(401, "text/plain; charset=utf-8", len(body))
            self.send_header("WWW-Authenticate", 'Basic realm="Private Dialogue TTS", charset="UTF-8"')
            self.end_headers()
            self.wfile.write(body)
        return okay

    def do_GET(self):
        if not self.authenticated():
            return
        if not self.valid_host():
            return self.json({"error": "此服务仅允许本机访问。"}, 403)
        path = urlsplit(self.path).path
        if getattr(self.server, 'workspaces', None):
            from .frontend_assets import serve_frontend
            if serve_frontend(self, path):
                return
        if path.startswith('/api/workspaces'):
            from .workspace_http import get_workspace
            if get_workspace(self, path):
                return
        if path == "/":
            body = (ROOT / "web_dialogue" / "index.html").read_bytes()
            self.headers_for(200, "text/html; charset=utf-8", len(body))
            self.end_headers()
            return self.wfile.write(body)
        if path == "/api/update":
            from .updates import check_update
            return self.json(check_update())
        if path == "/api/config":
            from .updates import CURRENT_VERSION, MAC_BUILD_NUMBER
            return self.json({"token": self.server.token, "voices": self.server.voice_catalog["voices"], "catalog_revision": self.server.voice_catalog["revision"], "catalog_source": self.server.voice_catalog["source"], "xml_example": XML_EXAMPLE, "can_shutdown": getattr(self.server, "can_shutdown", False), "app_version": CURRENT_VERSION, "mac_build_number": MAC_BUILD_NUMBER if sys.platform == 'darwin' else None})
        if path == "/api/voices":
            return self.json(self.server.voice_catalog)
        parts = path.strip("/").split("/")
        if len(parts) in (3, 4, 5) and parts[:2] == ["api", "batches"]:
            try:
                if len(parts) == 3:
                    return self.json(self.server.batches.snapshot(parts[2]))
                if (len(parts) == 4 and parts[3] == 'zip') or (len(parts) == 5 and parts[3] == 'audio'):
                    handle, filename, size = self.server.batches.open_output(parts[2], parts[4] if len(parts) == 5 else None)
                    with handle:
                        self.headers_for(200, 'audio/mpeg' if len(parts) == 5 else 'application/zip', size)
                        self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
                        if len(parts) == 5:
                            self.send_header('X-Dialogue-Fingerprint', self.server.batches.audio_fingerprint(parts[2], parts[4]))
                        self.end_headers()
                        shutil.copyfileobj(handle, self.wfile)
                    return
            except KeyError:
                return self.json({'error': '批次或输出不存在。'}, 404)
            except InputError as exc:
                return self.json({'error': str(exc)}, 409)
            except OSError:
                return self.json({'error': '输出暂时被占用，请稍后重试下载。'}, 409)
        if len(parts) in (3, 4) and parts[:2] == ["api", "jobs"]:
            try:
                status = self.server.jobs.snapshot(parts[2])
                if len(parts) == 3:
                    return self.json(status)
                kinds = {"audio": ("dialogue.mp3", "audio/mpeg"),
                         "normalized": ("normalized.json", "application/json; charset=utf-8"),
                         "timeline": ("timeline.json", "application/json; charset=utf-8")}
                if parts[3] not in kinds or status["status"] != "done":
                    return self.json({"error": "音频尚未成功生成。"}, 404)
                filename, mime = kinds[parts[3]]
                with self.server.jobs.open_result(parts[2],parts[3]) as handle:
                    size=handle.size
                    start,end,http_status=0,size-1,200
                    raw_range=self.headers.get('Range')
                    if raw_range and parts[3]=='audio':
                        import re
                        match=re.fullmatch(r'bytes=(\d+)-(\d*)',raw_range)
                        if not match:return self.json({'error':'不支持的字节范围。'},416)
                        start=int(match[1]);end=min(int(match[2]) if match[2] else size-1,size-1)
                        if start>end or start>=size:return self.json({'error':'字节范围超出文件。'},416)
                        http_status=206
                    self.headers_for(http_status,mime,end-start+1)
                    self.send_header('Accept-Ranges','bytes')
                    if handle.fingerprint:self.send_header('X-Dialogue-Fingerprint',handle.fingerprint)
                    self.send_header('Content-Disposition',f'attachment; filename="{handle.filename}"')
                    if http_status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                    self.end_headers();handle.seek(start);remaining=end-start+1
                    while remaining:
                        chunk=handle.read(min(65536,remaining))
                        if not chunk:break
                        self.wfile.write(chunk);remaining-=len(chunk)
                return
            except KeyError as exc:
                return self.json({"error": str(exc)}, 404)
            except (ValueError,OSError):
                return self.json({'error':'合成结果暂不可读取，请核验本机缓存后重试。'},409)
        self.json({"error": "页面不存在。"}, 404)

    def do_POST(self):
        with self.server.update_lock:
            if self.server.update_in_progress and urlsplit(self.path).path != "/api/update/unlock":
                return self.json({"error":"正在准备更新，请稍候。"}, 409)
            return self._do_POST_locked()

    def _do_POST_locked(self):
        if not self.authenticated():
            return
        if not self.valid_host():
            return self.json({"error": "此服务仅允许本机访问。"}, 403)
        origin = self.headers.get("Origin")
        if origin and origin not in self.server.allowed_origins:
            return self.json({"error": "拒绝跨站请求。"}, 403)
        private = urlsplit(self.path).path.startswith('/api/native/')
        expected = getattr(self.server, 'native_token', None) if private else self.server.token
        token = self.headers.get("X-Native-Token" if private else "X-App-Token", "")
        if not expected or not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
            return self.json({"error": "页面会话已过期，请刷新后重试。"}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return self.json({"error": "请求为空或超过64MiB，请缩小输入文件。"}, 413)
            if (private or urlsplit(self.path).path.startswith(('/api/workspaces','/api/reports/'))) and length > 256 * 1024:
                return self.json({'error':'工作区请求超过大小限制。'},413)
            self.connection.settimeout(15)
            payload = json.loads(self.rfile.read(length).decode("utf-8-sig"))
            path = urlsplit(self.path).path
            from .report_http import is_report_post, post_report
            if is_report_post(path):
                if urlsplit(self.path).query:
                    raise InputError('清单固定导出整个批次，不接受查询参数。')
                return self.json(post_report(self.server, path, payload))
            if path.startswith('/api/workspaces') or path.startswith('/api/native/'):
                if not isinstance(payload, dict):
                    raise InputError('请求必须为对象。')
                from .workspace_http import post_workspace
                return self.json(post_workspace(self, path, payload))
            if path == "/api/update/lock":
                from .runtime_safety import combined_safety
                if any(combined_safety(self.server.workspaces,self.server.reports,self.server.audio_captures,self.server.jobs).values()):
                    return self.json({'error':'请先暂停任务并保存完整音频，再更新。'},409)
                with self.server.jobs.transaction(), self.server.batches.transaction():
                    if self.server.jobs.has_running() or self.server.batches.has_running():
                        return self.json({"error":"正在合成，请稍后更新"}, 409)
                self.server.update_in_progress = True
                return self.json({"ok":True})
            if path == "/api/update/unlock":
                self.server.update_in_progress = False
                return self.json({"ok":True})
            if path == "/api/shutdown":
                if not getattr(self.server, "can_shutdown", False):
                    return self.json({"error": "此入口未启用应用退出。"}, 403)
                self.json({"status": "closing"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if not isinstance(payload, dict):
                raise InputError('请求必须为对象。')
            supplied_revision = payload.get('catalog_revision')
            if supplied_revision is not None and supplied_revision != self.server.voice_catalog['revision']:
                raise InputError('音色目录已改变，请重新载入后选择。')
            if path == '/api/batches/preview':
                from .batch_import import preview_batch
                return self.json(preview_batch(payload, self.server.voice_catalog["voices"]))
            if path == '/api/batches':
                return self.json(self.server.batches.start(payload), 202)
            batch_parts = path.strip('/').split('/')
            if len(batch_parts) == 4 and batch_parts[:2] == ['api', 'batches']:
                if batch_parts[3] == 'cancel':
                    return self.json(self.server.batches.cancel(batch_parts[2]))
                if batch_parts[3] == 'retry':
                    if payload.get('consent') is not True:
                        raise InputError('请确认将脱敏文本发送至微软语音服务。')
                    return self.json(self.server.batches.retry(batch_parts[2]))
            if path == "/api/preview":
                return self.json(normalize_source(payload, self.server.voice_catalog["voices"]))
            if path == "/api/audition":
                script = audition_script(payload, self.server.voice_catalog["voices"])
                return self.json(self.server.jobs.start(script, payload.get("request_id"), audition=True), 202)
            if path == "/api/jobs":
                if not isinstance(payload, dict) or payload.get("consent") is not True:
                    raise InputError("请先确认：生成时会将脱敏后的文本发送至微软语音服务。")
                normalized = normalize_source(payload, self.server.voice_catalog["voices"])
                return self.json(self.server.jobs.start(normalized["script"], payload.get("request_id"), fingerprint=normalized["fingerprint"], filename=normalized['filename']), 202)
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
                return self.json(self.server.jobs.cancel(parts[2]))
            return self.json({"error": "接口不存在。"}, 404)
        except (ValueError, UnicodeError, RecursionError) as exc:
            from native_audio import AudioCaptureError
            if isinstance(exc, (InputError,AudioCaptureError)):
                message = str(exc)
            else:
                message = "请求格式无效，请重新载入 XML 后重试。"
            return self.json({"error": message}, 400)
        except KeyError as exc:
            return self.json({"error": str(exc)}, 404)
        except OSError:
            return self.json({"error": "无法创建临时文件或合成进程，请检查本机可用空间与权限。"}, 500)


def make_server(port=8765, bind="127.0.0.1", public_origin=None, access_key=None, catalog=None,
                state_dir=None, native_token=None):
    if access_key and (not access_key.isascii() or any(c.isspace() for c in access_key) or ":" in access_key):
        raise ValueError("专用访问密码必须为无空格的 ASCII 字符，不可包含冒号。")
    if bind not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("只支持本机监听，或明确配置的反向代理模式。")
    if bind != "127.0.0.1":
        parsed = urlsplit(public_origin or "")
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("网络部署须提供 HTTPS public-origin，并由反向代理负责 TLS。")
        if not access_key or len(access_key) < 24 or ":" in access_key:
            raise ValueError("网络部署须设置至少 24 位的 TTS_ACCESS_KEY，不可使用个人账户密码。")
    server = ThreadingHTTPServer((bind, port), Handler)
    actual_port = server.server_address[1]
    server.allowed_hosts = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}
    server.allowed_origins = {"http://" + host for host in server.allowed_hosts}
    if public_origin:
        parsed = urlsplit(public_origin)
        server.allowed_hosts.add(parsed.netloc)
        server.allowed_origins.add(public_origin.rstrip("/"))
    server.access_key = access_key
    server.token = secrets.token_urlsafe(32)
    server.update_lock = threading.RLock()
    server.update_in_progress = False
    from .voice_catalog import load_catalog
    server.voice_catalog = catalog if catalog is not None else load_catalog()
    native_token = native_token or os.environ.pop('DIALOGUE_NATIVE_TOKEN', None)
    if state_dir is not None and native_token and (bind!='127.0.0.1' or public_origin):
        server.server_close()
        raise ValueError('原生工作区只能监听本机。')
    server._resources_lock=threading.RLock()
    server._closed_resources=set()
    try:
        server.jobs = Jobs(state_dir=state_dir if state_dir is not None and native_token else None)
        from .batches import Batches
        server.batches = Batches(server.jobs, server.voice_catalog["voices"])
        server.workspaces = None
        server.reports = None
        server.audio_captures = None
        server.native_access = None
        server.workspace_access = None
        server.native_token = None
        if state_dir is not None and native_token:
            from native_workspace import NativeWorkspaceAccess
            from .workspaces import Workspaces
            server.native_token = native_token
            server.native_access = NativeWorkspaceAccess(state_dir)
            server.workspace_access = server.native_access
            server.workspaces = Workspaces(state_dir, server.jobs, server.native_access, server.voice_catalog)
            from .workspace_reports import WorkspaceReports
            server.reports = WorkspaceReports(server.workspaces.path, Path(state_dir) / 'reports')
            from native_audio import NativeAudioCaptures
            server.audio_captures = NativeAudioCaptures(state_dir,server.native_access)
    except BaseException:
        close_resources(server)
        server.server_close()
        raise
    return server


def main():
    parser = argparse.ArgumentParser(description="对话配音：仅监听本机的中文浏览器界面")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bind", choices=["127.0.0.1", "0.0.0.0"], default="127.0.0.1")
    parser.add_argument("--public-origin", default=None, help="仅在 HTTPS 私有反向代理部署时设置")
    args = parser.parse_args()
    server = make_server(args.port, args.bind, args.public_origin, os.environ.get("TTS_ACCESS_KEY"))
    address = args.public_origin or f"http://127.0.0.1:{server.server_address[1]}"
    mode = "私有反向代理模式：必须确保代理启用 HTTPS 且后端端口不直接暴露。" if args.bind != "127.0.0.1" else "仅本机访问，未公开部署。"
    print(f"对话配音已启动：{address}\n{mode}按 Ctrl+C 关闭；关闭后临时文件删除，请先下载音频。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        close_resources(server)



if __name__ == "__main__":
    main()
