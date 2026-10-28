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

def normalize_source(payload, catalog=None):
    if not isinstance(payload, dict) or payload.get("format") != "xml_pair" or "data" in payload:
        raise InputError("仅支持双声道 XML 输入。")
    result = normalize(payload, catalog)
    from .output_names import pair_filename
    try:
        result['filename'] = pair_filename(payload['xml_files'])
    except ValueError as exc:
        raise InputError(str(exc)) from exc
    return result

def audition_script(payload, catalog=None):
    if payload.get("consent") is not True:
        raise InputError("请先允许发送试听文本至微软语音服务。")
    sample = {"format": "xml_pair", "xml_files": XML_EXAMPLE,
              "voices": payload.get("voices", {}), "rate": payload.get("rate", 1),
              "pause_ms": payload.get("pause_ms", 250)}
    script = normalize_source(sample, catalog)["script"]
    script["lines"] = script["lines"][:2]
    return script

MAX_BODY = 64 * 1024 * 1024
ROOT = Path(__file__).resolve().parent.parent


class Jobs:
    def __init__(self, timeout_seconds=900):
        self.timeout_seconds = timeout_seconds
        self.request_ids = {}
        self.temp = tempfile.TemporaryDirectory(prefix="dialogue-tts-")
        self.root = Path(self.temp.name)
        self.items = {}
        self.lock = threading.RLock()

    def start(self, script, request_id=None, batch_owner=None, fingerprint=None, audition=False, filename='audition.mp3'):
        import hashlib
        from .output_names import valid_output_name
        if not audition and not valid_output_name(filename):
            raise InputError('输出文件名无效。')
        binding = hashlib.sha256(json.dumps([script, fingerprint, filename, audition], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            if getattr(self, "batch_active", None) and batch_owner != self.batch_active:
                raise InputError("已有批次正在合成，请等待完成或先取消。")
            if request_id is not None:
                if not isinstance(request_id, str) or not 8 <= len(request_id) <= 100 or not all(c.isalnum() or c in "-_" for c in request_id):
                    raise InputError("请求标识无效，请刷新页面。")
                if request_id in self.request_ids:
                    old_id = self.request_ids[request_id]
                    if old_id in self.items:
                        if self.items[old_id]['request_binding'] != binding:
                            raise InputError('同一请求标识的内容或文件名发生变化，请重新生成请求。')
                        return self.snapshot(old_id)
            for ident in self.items:
                if self.snapshot(ident)["status"] in {"queued", "running"}:
                    raise InputError("已有任务正在合成，请等待完成或先取消。")
            audition_ids = [key for key, item in self.items.items() if item.get("audition")]
            victim = audition_ids[0] if audition and len(audition_ids) >= 3 else None
            production_ids = [key for key, item in self.items.items() if not item.get("audition")]
            if not audition and len(production_ids) >= 30:
                victim = production_ids[0]
            if victim is not None:
                shutil.rmtree(self.items[victim]["folder"], ignore_errors=True)
                del self.items[victim]
                self.request_ids = {k:v for k,v in self.request_ids.items() if v != victim}
            ident = uuid.uuid4().hex
            folder = self.root / ident
            folder.mkdir(mode=0o700)
            (folder / "normalized.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
            log = open(folder / "process.log", "wb")
            try:
                process = subprocess.Popen(
                    ([sys.executable, "--worker", str(folder)] if getattr(sys, "frozen", False) else [sys.executable, "-m", "web_dialogue.worker", str(folder)]), cwd=str(ROOT),
                    stdout=log, stderr=log, start_new_session=(os.name != "nt"),
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )
            finally:
                log.close()
            self.items[ident] = {"folder": folder, "process": process, "total": len(script["lines"]),
                                 "fingerprint": fingerprint, "filename": filename, "request_binding": binding, "audition": audition, "started": time.monotonic(), "cancelled": False, "timed_out": False}
            timer = threading.Timer(self.timeout_seconds, self.expire, args=(ident,))
            timer.daemon = True
            self.items[ident]["timer"] = timer
            if request_id:
                self.request_ids[request_id] = ident
            timer.start()
            return self.snapshot(ident)

    def expire(self, ident):
        with self.lock:
            item = self.items.get(ident)
            if item and item["process"].poll() is None:
                item["timed_out"] = True
                self.kill(item)

    def kill(self, item):
        proc = item["process"]
        if proc.poll() is None:
            try:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGTERM)
                else:
                    # Frozen one-file builds have a bootloader child: terminate the
                    # process tree we started, not unrelated app processes.
                    taskkill = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32" / "taskkill.exe"
                    try:
                        killed = subprocess.run([str(taskkill), "/PID", str(proc.pid), "/T", "/F"],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
                        if killed.returncode != 0 and proc.poll() is None:
                            item["termination_uncertain"] = True
                            proc.terminate()
                    except (OSError, subprocess.TimeoutExpired):
                        item["termination_uncertain"] = True
                        proc.terminate()
                proc.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if proc.poll() is None:
                    if os.name != "nt":
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                    proc.wait(timeout=3)

    def snapshot(self, ident):
        with self.lock:
            if ident not in self.items:
                raise KeyError("任务不存在，或服务器已重启。")
            item = self.items[ident]
            result = {"id": ident, "status": "queued", "completed": 0, "total": item["total"], "filename": item['filename']}
            status_file = item["folder"] / "worker_status.json"
            if status_file.exists():
                try:
                    result.update(json.loads(status_file.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass  # atomic writes; a transient read never means success
            if item.get("termination_uncertain"):
                result.update(status="error", error="已停止启动器，但无法确认后台完全退出。请关闭应用后重试。")
            elif item["cancelled"]:
                result["status"] = "cancelled"
            elif item["timed_out"]:
                result.update(status="error", error="任务超过 15 分钟，已停止。请拆分较长对话后再试。")
            elif item["process"].poll() is not None and result["status"] not in {"done", "error"}:
                result.update(status="error", error="合成进程意外结束，请检查依赖或重新运行。")
            if result["status"] == "done":
                if not (item["folder"] / "dialogue.mp3").is_file():
                    result.update(status="error", error="输出文件不存在，不能认定合成成功。")
                else:
                    result["download_url"] = f"/api/jobs/{ident}/audio"
                    result["normalized_url"] = f"/api/jobs/{ident}/normalized"
                    result["timeline_url"] = f"/api/jobs/{ident}/timeline"
            if item.get("fingerprint"):
                result["fingerprint"] = item["fingerprint"]
            return result

    def cancel(self, ident):
        with self.lock:
            item = self.items.get(ident)
            if item is None:
                raise KeyError("任务不存在。")
            if self.snapshot(ident)["status"] in {"queued", "running"}:
                item["cancelled"] = True
                item["timer"].cancel()
                self.kill(item)
                for filename in ("dialogue.mp3", "timeline.json"):
                    (item["folder"] / filename).unlink(missing_ok=True)
            return self.snapshot(ident)

    def close(self):
        if hasattr(self, "workspaces"):
            self.workspaces.close()
        if hasattr(self, "batches"):
            self.batches.close()
        with self.lock:
            for item in self.items.values():
                item["timer"].cancel()
                self.kill(item)
            self.temp.cleanup()


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
                target = self.server.jobs.items[parts[2]]["folder"] / filename
                size = target.stat().st_size
                # Byte-range support lets browser audio seek without downloading again.
                start, end, http_status = 0, size - 1, 200
                raw_range = self.headers.get("Range")
                if raw_range and parts[3] == "audio":
                    import re
                    match = re.fullmatch(r"bytes=(\d+)-(\d*)", raw_range)
                    if not match:
                        return self.json({"error": "不支持的字节范围。"}, 416)
                    start = int(match[1])
                    end = min(int(match[2]) if match[2] else size - 1, size - 1)
                    if start > end or start >= size:
                        return self.json({"error": "字节范围超出文件。"}, 416)
                    http_status = 206
                self.headers_for(http_status, mime, end - start + 1)
                self.send_header("Accept-Ranges", "bytes")
                if parts[3] == "audio" and status.get("fingerprint"):
                    self.send_header("X-Dialogue-Fingerprint", status["fingerprint"])
                download_name = self.server.jobs.items[parts[2]]['filename'] if parts[3] == 'audio' else filename
                self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
                if http_status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                with open(target, "rb") as handle:
                    handle.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = handle.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
            except KeyError as exc:
                return self.json({"error": str(exc)}, 404)
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
            if (private or urlsplit(self.path).path.startswith('/api/workspaces')) and length > 256 * 1024:
                return self.json({'error':'工作区请求超过大小限制。'},413)
            self.connection.settimeout(15)
            payload = json.loads(self.rfile.read(length).decode("utf-8-sig"))
            path = urlsplit(self.path).path
            if path.startswith('/api/workspaces') or path.startswith('/api/native/'):
                if not isinstance(payload, dict):
                    raise InputError('请求必须为对象。')
                from .workspace_http import post_workspace
                return self.json(post_workspace(self, path, payload))
            if path == "/api/update/lock":
                if getattr(self.server, 'workspaces', None) and any(self.server.workspaces.safety().values()):
                    return self.json({'error':'请先暂停任务并保存完整音频，再更新。'},409)
                with self.server.jobs.lock, self.server.batches.lock:
                    if any(item["process"].poll() is None for item in self.server.jobs.items.values()) or self.server.batches.active:
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
            if isinstance(exc, InputError):
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
    server.jobs = Jobs()
    from .batches import Batches
    server.batches = Batches(server.jobs, server.voice_catalog["voices"])
    server.jobs.batches = server.batches
    server.workspaces = None
    server.native_access = None
    server.workspace_access = None
    server.native_token = None
    native_token = native_token or os.environ.pop('DIALOGUE_NATIVE_TOKEN', None)
    if state_dir is not None and native_token:
        if bind != '127.0.0.1' or public_origin:
            server.server_close()
            server.jobs.close()
            raise ValueError('原生工作区只能监听本机。')
        from native_workspace import NativeWorkspaceAccess
        from .workspaces import Workspaces
        server.native_token = native_token
        server.native_access = NativeWorkspaceAccess(state_dir)
        server.workspace_access = server.native_access
        server.workspaces = Workspaces(state_dir, server.jobs, server.native_access, server.voice_catalog)
        server.jobs.workspaces = server.workspaces
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
        server.jobs.close()


if __name__ == "__main__":
    main()
