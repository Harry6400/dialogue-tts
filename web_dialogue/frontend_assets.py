"""Serve only the built workbench entry and its hashed JS/CSS assets."""
from pathlib import Path
import re

FRONTEND = Path(__file__).resolve().parent.parent / 'frontend' / 'dist'
ASSET = re.compile(r'/assets/[A-Za-z0-9_-]+\.(?:js|css)')


def serve_frontend(handler, path):
    if path != '/' and not path.startswith('/assets/'):
        return False
    if path != '/' and not ASSET.fullmatch(path):
        handler.json({'error': '界面资源不存在。'}, 404)
        return True
    target = FRONTEND / ('index.html' if path == '/' else path.lstrip('/'))
    if not target.is_file() or target.is_symlink():
        handler.json({'error': '工作台资源未构建，请运行 npm run build --workspace frontend 后重新启动。'}, 503 if path == '/' else 404)
        return True
    mime = {'html': 'text/html; charset=utf-8', 'js': 'text/javascript; charset=utf-8',
            'css': 'text/css; charset=utf-8'}[target.suffix[1:]]
    body = target.read_bytes()
    handler.headers_for(200, mime, len(body))
    handler.end_headers()
    handler.wfile.write(body)
    return True
