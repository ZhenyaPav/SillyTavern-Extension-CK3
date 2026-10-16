import json
from pathlib import Path
import socket
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from test_bridge import ROOT, packet


class HttpAndLauncher(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.logs = self.root/'logs'
        self.logs.mkdir()
        self.log = self.logs/'game.log'
        self.log.write_text('')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        self.command = [sys.executable, str(ROOT/'companion/bridge.py'), '--state-dir', str(self.root/'state'),
                        '--logs', str(self.logs), '--port', str(self.port), '--origin', 'https://st.example.test']
        self.server = subprocess.Popen(self.command+['serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(100):
            try:
                self.token = (self.root/'state/token').read_text()
                self.call('/health')
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.03)
        else:
            self.server.terminate()
            self.fail('Test companion failed to start')

    def call(self, path, data=None, headers=None, method=None):
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}'+path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'X-CK3-Token': self.token, 'Content-Type': 'application/json', **(headers or {})}, method=method)
        return self.opener.open(req, timeout=3)

    def tearDown(self):
        try:
            self.call('/shutdown', {}).close()
            self.server.wait(timeout=4)
        finally:
            if self.server.poll() is None:
                self.server.kill()
                self.server.wait()
            self.temp.cleanup()

    def test_authenticated_cors_delivery_from_log_and_replay(self):
        with self.call('/health', headers={'Origin': 'https://st.example.test'}) as response:
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'https://st.example.test')
        for headers, code in [({'Origin': 'https://evil.test'}, 403), ({'X-CK3-Token': 'wrong'}, 401), ({'Host': 'evil.test'}, 403)]:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.call('/health', headers=headers)
            self.assertEqual(caught.exception.code, code)
        with self.call('/events', headers={'Origin': 'https://st.example.test'}, method='OPTIONS') as response:
            self.assertEqual(response.status, 204)
        with self.log.open('a') as out:
            out.write(packet('milestone', date='1081.3.19', text='A child was born.'))
        for _ in range(50):
            with self.call('/events?after=0') as response:
                events = json.load(response)['events']
            if events:
                break
            time.sleep(0.03)
        self.assertEqual(events[0]['text'], 'A child was born.')
        with self.call(f'/events?after={events[0]["seq"]}') as response:
            self.assertEqual(json.load(response)['events'], [])

    def test_launch_preserves_argv_working_directory_and_exit_code(self):
        args = ['with spaces', 'literal$(not-a-command)', "an'apostrophe"]
        code = 'import json,os,sys; print(json.dumps([os.getcwd(),sys.argv[1:]])); sys.exit(7)'
        result = subprocess.run(self.command+['run', sys.executable, '-c', code, *args], cwd=self.root,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(json.loads(result.stdout), [str(self.root), args])
        with self.call('/events?after=0') as response:
            self.assertEqual([e['kind'] for e in json.load(response)['events']], ['game_opened', 'game_closed'])

    def test_termination_signal_reaches_launched_process(self):
        code = 'import time; print("ready",flush=True); time.sleep(30)'
        child = subprocess.Popen(self.command+['run', sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            child.send_signal(signal.SIGTERM)
            child.communicate(timeout=5)
            self.assertEqual(child.returncode, 143)
            with self.call('/health') as response:
                self.assertFalse(json.load(response)['gameRunning'])
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()


if __name__ == '__main__':
    unittest.main()
