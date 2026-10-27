import base64
import threading
import unittest
import urllib.request
import urllib.error
from .app import make_server

class DeploymentTests(unittest.TestCase):
    def test_network_mode_requires_https_and_key(self):
        for kw in ({'bind':'0.0.0.0'}, {'bind':'0.0.0.0','public_origin':'http://example.com','access_key':'a'*30},
                   {'bind':'0.0.0.0','public_origin':'https://example.com','access_key':'short'},
                   {'bind':'0.0.0.0','public_origin':'https://user:pass@example.com','access_key':'a'*30}):
            with self.subTest(kw=kw),self.assertRaises(ValueError): make_server(0,**kw)
    def test_basic_auth_on_loopback_only(self):
        # Auth tested on loopback; never binds externally during automated tests.
        s=make_server(0,access_key='a'*30)
        threading.Thread(target=s.serve_forever,daemon=True).start()
        url='http://127.0.0.1:'+str(s.server_address[1])+'/api/config'
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm: urllib.request.urlopen(url)
            self.assertEqual(cm.exception.code,401)
            auth=base64.b64encode(('tts:'+('a'*30)).encode()).decode()
            self.assertEqual(urllib.request.urlopen(urllib.request.Request(url,headers={'Authorization':'Basic '+auth})).status,200)
        finally:
            s.shutdown();s.server_close();s.jobs.close()

if __name__=='__main__': unittest.main()
