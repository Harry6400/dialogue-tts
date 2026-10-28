import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .frontend_assets import serve_frontend


class Response:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.status = None
    def headers_for(self, status, mime, length):
        self.status, self.mime, self.length = status, mime, length
    def end_headers(self):
        pass
    def json(self, body, status=200):
        self.status, self.body = status, body


class FrontendAssetsTests(unittest.TestCase):
    def test_production_entry_and_assets_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'assets').mkdir()
            (root / 'index.html').write_text('<html>workbench</html>')
            (root / 'assets' / 'index-Ab12.js').write_text('export{};')
            (root / 'secret.txt').write_text('private')
            with patch('web_dialogue.frontend_assets.FRONTEND', root):
                for route, mime in [('/', 'text/html'), ('/assets/index-Ab12.js', 'text/javascript')]:
                    response = Response()
                    self.assertTrue(serve_frontend(response, route))
                    self.assertEqual(response.status, 200)
                    self.assertTrue(response.mime.startswith(mime))
                    self.assertEqual(response.length, len(response.wfile.getvalue()))
                for route in ('/assets/../secret.txt', '/assets/index-Ab12.js.map', '/assets/%2e%2e/secret.txt'):
                    response = Response()
                    self.assertTrue(serve_frontend(response, route))
                    self.assertEqual(response.status, 404)
                    self.assertNotIn(b'private', response.wfile.getvalue())
                self.assertFalse(serve_frontend(Response(), '/api/config'))

    def test_missing_build_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as folder, patch('web_dialogue.frontend_assets.FRONTEND', Path(folder)):
            response = Response()
            serve_frontend(response, '/')
            self.assertEqual(response.status, 503)

    def test_symlink_cannot_serve_arbitrary_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'assets').mkdir()
            (root / 'secret').write_text('private')
            try:
                (root / 'assets' / 'index-link.js').symlink_to(root / 'secret')
            except OSError:
                self.skipTest('Symbolic links unavailable')
            with patch('web_dialogue.frontend_assets.FRONTEND', root):
                response = Response()
                serve_frontend(response, '/assets/index-link.js')
                self.assertEqual(response.status, 404)
