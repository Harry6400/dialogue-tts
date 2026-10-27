import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from .app import make_server
from .xml_example import XML_EXAMPLE
XML_PAYLOAD={"format":"xml_pair", "xml_files":XML_EXAMPLE}


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = make_server(0)
        cls.t = threading.Thread(target=cls.s.serve_forever, daemon=True)
        cls.t.start()
        cls.base = 'http://127.0.0.1:' + str(cls.s.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.s.shutdown(); cls.s.server_close(); cls.s.jobs.close()

    def request(self, path, data=None, headers=None):
        h = {'X-App-Token': self.s.token, 'Content-Type': 'application/json'}
        if headers: h.update(headers)
        b = json.dumps(data).encode() if data is not None else None
        try:
            r = urllib.request.urlopen(urllib.request.Request(self.base+path, data=b, headers=h))
        except urllib.error.HTTPError as e:
            r = e
        return r.status, r.read(), r.headers

    def test_page_and_config(self):
        status, body, h = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(b'<!doctype html', body.lower())
        status, body, h = self.request('/api/config')
        self.assertEqual(json.loads(body)['token'], self.s.token)
        self.assertEqual(h['X-Frame-Options'], 'DENY')

    def test_preview_no_synthesis(self):
        status, body, _ = self.request('/api/preview', XML_PAYLOAD)
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)['rows']), 5)
        self.assertEqual(len(self.s.jobs.items), 0)

    def test_origin_and_token_and_host(self):
        for headers in ({'Origin':'https://evil.example'}, {'X-App-Token':'wrong'}, {'Host':'evil.example'}):
            status, _, _ = self.request('/api/preview', XML_PAYLOAD, headers)
            self.assertEqual(status, 403)

    def test_no_consent_no_task(self):
        status, _, _ = self.request('/api/jobs', XML_PAYLOAD)
        self.assertEqual(status, 400)
        self.assertEqual(len(self.s.jobs.items), 0)

    def test_invalid_input(self):
        status, body, _ = self.request('/api/preview', {'data':[{'speaker':'A'}]})
        self.assertEqual(status, 400)
        self.assertIn('XML', json.loads(body)['error'])

    def test_batch_preview_auth_and_consent(self):
        payload = {'files': XML_EXAMPLE}
        status, body, _ = self.request('/api/batches/preview', payload)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['counts']['ready'], 1)
        self.assertEqual(self.request('/api/batches',payload)[0],400)
        self.assertEqual(self.request('/api/batches/preview',payload,{'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/batches/unknown/retry',{})[0],400)
        self.assertEqual(self.request('/api/batches/unknown')[0],404)
        self.assertEqual(self.request('/api/batches/unknown/audio/../../../etc/passwd')[0],404)

    def test_json_source_rejected_at_all_public_entrypoints(self):
        raw={'data':[{'speaker':'A','text':'不能作为输入'}], 'consent':True}
        for path in ('/api/preview','/api/jobs'):
            self.assertEqual(self.request(path,raw)[0],400)
        mixed={**XML_PAYLOAD,**raw}
        self.assertEqual(self.request('/api/preview',mixed)[0],400)
        batch={'files':[{'name':'input.json','content':'[]'}],'consent':True,'request_id':'json-rejected'}
        self.assertEqual(self.request('/api/batches',batch)[0],400)
        config=json.loads(self.request('/api/config')[1])
        self.assertNotIn('example',config)
        self.assertEqual(len(config['xml_example']),2)

    def test_unknown_job_and_path(self):
        self.assertEqual(self.request('/api/jobs/unknown')[0],404)
        self.assertEqual(self.request('/../../etc/passwd')[0],404)
        self.assertEqual(self.request('/api/jobs/unknown/cancel',{})[0],404)


if __name__ == '__main__': unittest.main()
