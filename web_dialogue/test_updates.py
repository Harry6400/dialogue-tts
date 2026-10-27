import unittest
import urllib.error
from unittest.mock import patch
from .updates import allowed_release_url, check_update, evaluate_release, version_tuple, RELEASES_URL


class UpdateTests(unittest.TestCase):
    def test_semantic_order(self):
        self.assertGreater(version_tuple('v0.10.0'), version_tuple('0.9.99'))
        self.assertEqual(version_tuple('v0.1.0+build.1'), (0,1,0))
        for s in ('v0.1','1.0.0-rc1','1.02.3',None,'http://evil'):
            with self.subTest(s=s),self.assertRaises(ValueError):version_tuple(s)
    def test_latest(self):
        self.assertEqual(evaluate_release({'tag_name':'v0.1.0'})['state'],'latest')
    def test_newer_and_platform_asset(self):
        names = {'darwin': 'DialogueTTS-macOS-arm64.zip', 'win32': 'DialogueTTS-Windows-x64.zip'}
        assets = [{'name': name, 'browser_download_url': RELEASES_URL+'/download/v0.2.0/'+name} for name in names.values()]
        for platform, expected in names.items():
            with self.subTest(platform=platform), patch('web_dialogue.updates.sys.platform', platform):
                r = evaluate_release({'tag_name':'v0.2.0','html_url':RELEASES_URL+'/tag/v0.2.0','assets':assets})
                self.assertEqual(r['state'], 'available')
                self.assertEqual(r['download_url'], RELEASES_URL+'/download/v0.2.0/'+expected)
    def test_bad_url_rejected(self):
        for u in ('https://evil.example/x','https://github.com/evil/dialogue-tts/releases/tag/v0.2.0',
                  'https://github.com@evil.example/Harry6400/dialogue-tts/releases/tag/v0.2.0',
                  RELEASES_URL+'/tag/../evil',RELEASES_URL+'/tag/%2e%2e/evil'):
            self.assertFalse(allowed_release_url(u))
            with self.assertRaises(ValueError):evaluate_release({'tag_name':'v0.2.0','html_url':u})
    def test_prerelease_rejected(self):
        with self.assertRaises(ValueError):evaluate_release({'tag_name':'v0.2.0','prerelease':True})
    def test_unreleased_and_network_error(self):
        with patch('web_dialogue.updates.urllib.request.build_opener') as op:
            op.return_value.open.side_effect=urllib.error.HTTPError('https://api.github.com',404,'Not Found',None,None)
            self.assertEqual(check_update()['state'],'unreleased')
            op.return_value.open.side_effect=TimeoutError()
            self.assertEqual(check_update()['state'],'error')


if __name__ == '__main__':unittest.main()
