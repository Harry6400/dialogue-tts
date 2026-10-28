"""Offline regressions for GUI system proxies and safe network errors."""
import ssl
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import aiohttp

from .worker import failure_message, speech_proxy, SpeechHTTP


class WorkerNetworkTests(unittest.TestCase):
    def test_macos_https_proxy_is_used_for_secure_websocket(self):
        with patch('web_dialogue.worker.getproxies', return_value={'https': 'http://127.0.0.1:7897'}), \
             patch('web_dialogue.worker.proxy_bypass', return_value=False):
            self.assertEqual(speech_proxy('wss://speech.example.test/service'), 'http://127.0.0.1:7897')

    def test_explicit_websocket_proxy_takes_precedence(self):
        with patch('web_dialogue.worker.getproxies', return_value={'wss': 'http://proxy.test:8080', 'https': 'http://other.test:8080'}), \
             patch('web_dialogue.worker.proxy_bypass', return_value=False):
            self.assertEqual(speech_proxy('wss://speech.example.test/service'), 'http://proxy.test:8080')

    def test_bypass_and_no_proxy_keep_direct_connection(self):
        for bypass, proxies in [(True, {'https': 'http://proxy.test:8080'}), (False, {})]:
            with self.subTest(bypass=bypass), patch('web_dialogue.worker.getproxies', return_value=proxies), \
                 patch('web_dialogue.worker.proxy_bypass', return_value=bypass):
                self.assertIsNone(speech_proxy('wss://speech.example.test/service'))

    def test_ssl_word_in_connector_error_is_not_certificate_failure(self):
        key = SimpleNamespace(host='speech.example.test', port=443, ssl=True)
        error = aiohttp.ClientConnectorError(key, OSError('connection failed ssl:default private detail'))
        self.assertIn('无法连接微软', failure_message(error))
        self.assertNotIn('证书', failure_message(error))
        self.assertNotIn('private detail', failure_message(error))

    def test_certificate_timeout_proxy_and_rejection_are_distinct(self):
        key = SimpleNamespace(host='proxy.test', port=8080, ssl=True)
        cases = [(ssl.SSLCertVerificationError('private'), 'TLS'),
                 (TimeoutError('private'), '超时'),
                 (aiohttp.ClientProxyConnectionError(key, OSError('private')), '系统代理'),
                 (aiohttp.ClientResponseError(None, (), status=403), '拒绝连接'),
                 (aiohttp.ClientResponseError(None, (), status=429), '限流')]
        for error, expected in cases:
            with self.subTest(kind=type(error).__name__):
                self.assertIn(expected, failure_message(error))
                self.assertNotIn('private', failure_message(error))

    def test_explicit_transport_never_uses_implicit_proxy_lookup(self):
        from unittest.mock import Mock
        http = Mock()
        SpeechHTTP(http).ClientSession(trust_env=True, timeout=12)
        http.ClientSession.assert_called_once_with(trust_env=False, timeout=12)

    def test_connection_reset_is_reported_without_private_details(self):
        key = SimpleNamespace(host='speech.example.test', port=443, ssl=True)
        error = aiohttp.ClientConnectorError(key, ConnectionResetError('private'))
        self.assertIn('被中断', failure_message(error))
        self.assertNotIn('切换', failure_message(error))
        self.assertNotIn('private', failure_message(error))

    def test_failure_preserves_route_and_last_completed_count(self):
        import json, tempfile
        from pathlib import Path
        from unittest.mock import AsyncMock
        from .worker import main
        key=SimpleNamespace(host='speech.example.test',port=443,ssl=True)
        error=aiohttp.ClientConnectorError(key,ConnectionResetError('private'))
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            (folder/'worker_status.json').write_text(json.dumps({'completed':2,'total':29,'connection_mode':'direct','phase':'connecting'}))
            with patch('web_dialogue.worker.generate',new=AsyncMock(side_effect=error)), self.assertRaises(SystemExit):main(folder)
            fact=json.loads((folder/'worker_status.json').read_text())
            self.assertIn('直连',fact['error']);self.assertIn('2/29 段',fact['error'])
            self.assertIn('被中断',fact['error']);self.assertNotIn('切换',fact['error']);self.assertNotIn('private',fact['error'])
            self.assertEqual(fact['failed_phase'],'connecting')

    def test_direct_generation_does_not_read_system_proxy(self):
        import asyncio, json, tempfile
        from pathlib import Path
        from .worker import generate
        audio=(Path(__file__).parent/'silence.mp3').read_bytes()
        class Stream:
            async def stream(self):
                yield {'type':'audio','data':audio}
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            (folder/'normalized.json').write_text(json.dumps({'lines':[{'id':'1','speaker':'AGENT','text':'虚构测试','voice':'zh-CN-XiaoxiaoNeural','speech_rate':1,'pause_after_ms':0}]}))
            with patch('edge_tts.Communicate',return_value=Stream()) as speak, patch('web_dialogue.worker.getproxies') as proxies:
                asyncio.run(generate(folder))
            proxies.assert_not_called()
            self.assertIsNone(speak.call_args.kwargs['proxy'])
            self.assertTrue((folder/'dialogue.mp3').is_file())

    def test_connector_reset_retries_four_times_then_succeeds_on_same_route(self):
        import asyncio, json, tempfile
        from pathlib import Path
        from .worker import generate
        audio=(Path(__file__).parent/'silence.mp3').read_bytes()
        key=SimpleNamespace(host='speech.example.test',port=443,ssl=True)
        class Stream:
            def __init__(self, fail=False): self.fail=fail
            async def stream(self):
                if self.fail: raise aiohttp.ClientConnectorError(key,ConnectionResetError())
                yield {'type':'audio','data':audio}
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            (folder/'normalized.json').write_text(json.dumps({'connection_mode':'system_proxy','retry_attempts':5,'lines':[{'id':'1','speaker':'AGENT','text':'虚构测试','voice':'zh-CN-XiaoxiaoNeural','speech_rate':1,'pause_after_ms':0}]}))
            with patch('web_dialogue.worker.speech_proxy',return_value='http://proxy.test:8080'), \
                 patch('web_dialogue.worker.retry_delay_seconds',return_value=0), \
                 patch('edge_tts.Communicate',side_effect=[Stream(True),Stream(True),Stream(True),Stream(True),Stream()]) as speak:
                asyncio.run(generate(folder))
            self.assertEqual(speak.call_count,5)
            self.assertTrue(all(c.kwargs['proxy']=='http://proxy.test:8080' for c in speak.call_args_list))
            progress=json.loads((folder/'worker_status.json').read_text())
            self.assertEqual(progress['attempt'],5)
            self.assertEqual(progress['attempt_limit'],5)
            self.assertEqual(progress['completed'],1)

    def test_retry_limit_stops_after_configured_attempts_and_reports_count(self):
        import json, tempfile
        from pathlib import Path
        from .worker import main
        key=SimpleNamespace(host='speech.example.test',port=443,ssl=True)
        class Stream:
            async def stream(self):
                raise aiohttp.ClientConnectorError(key,ConnectionResetError())
                yield
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            line={'id':'1','speaker':'AGENT','text':'虚构测试','voice':'zh-CN-XiaoxiaoNeural','speech_rate':1,'pause_after_ms':0}
            (folder/'normalized.json').write_text(json.dumps({'connection_mode':'direct','retry_attempts':5,'lines':[line]}))
            with patch('web_dialogue.worker.retry_delay_seconds',return_value=0), \
                 patch('edge_tts.Communicate',return_value=Stream()) as speak, self.assertRaises(SystemExit):
                main(folder)
            result=json.loads((folder/'worker_status.json').read_text())
            self.assertEqual(speak.call_count,5)
            self.assertEqual(result['attempt'],5)
            self.assertEqual(result['attempt_limit'],5)
            self.assertIn('连接已尝试 5/5 次',result['error'])
            self.assertEqual(result['completed'],0)

    def test_tls_and_authentication_errors_do_not_retry(self):
        import asyncio, json, tempfile
        from pathlib import Path
        from .worker import generate
        key=SimpleNamespace(host='speech.example.test',port=443,ssl=True)
        errors=[aiohttp.ClientConnectorError(key,ssl.SSLError('tls')),aiohttp.ClientResponseError(None,(),status=401)]
        class Stream:
            def __init__(self,error):self.error=error
            async def stream(self):raise self.error;yield
        for error in errors:
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as root:
                folder=Path(root)
                line={'id':'1','speaker':'AGENT','text':'虚构测试','voice':'zh-CN-XiaoxiaoNeural','speech_rate':1,'pause_after_ms':0}
                (folder/'normalized.json').write_text(json.dumps({'retry_attempts':30,'lines':[line]}))
                with patch('edge_tts.Communicate',return_value=Stream(error)) as speak, self.assertRaises(type(error)):
                    asyncio.run(generate(folder))
                self.assertEqual(speak.call_count,1)

    def test_bounded_diagnostics_never_copy_request_data(self):
        from .speech_metadata import safe_diagnostics
        value={'connection_mode':'direct','proxy_in_use':False,'text':'private','events':[{'event':'dns_done','dns_ms':12,'url':'secret','text':'private'}]*200}
        result=safe_diagnostics(value)
        self.assertEqual(len(result['events']),100)
        self.assertNotIn('secret',str(result));self.assertNotIn('private',str(result))

    def test_worker_emits_waiting_and_retry_phases_before_completion(self):
        from .speech_progress import SpeechProgress
        events=[]
        progress=SpeechProgress(lambda **data:events.append(data),29,'system_proxy',True)
        progress.emit();progress.update('waiting_audio','waiting_audio');progress.update('retrying','retrying',attempt=2);progress.update('receiving',completed=7)
        self.assertEqual([p['phase'] for p in events],['connecting','waiting_audio','retrying','receiving'])
        self.assertEqual(events[-1]['completed'],7);self.assertEqual(events[-1]['total'],29)
        self.assertTrue(events[-1]['diagnostics']['proxy_in_use'])
        self.assertEqual(events[-1]['attempt_limit'],5)
