"""Adversarial network timing and shared-controller availability regressions."""
import http.server
import json
import multiprocessing
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from network_checks import NetworkChecks, MAX_RESULT_BYTES
from shared import Broker

BACKEND = str(Path(__file__).resolve().parents[1] / 'backend')


def command(code):
    return [sys.executable, '-B', '-c', code]


def stalled_worker(kind='plugin_update_check'):
    # Install the real worker deadline, then stall inside the selected checker.
    module = 'plugin_updates' if kind == 'plugin_update_check' else 'firmware'
    return command(f'''
import sys,time
sys.path.insert(0,{BACKEND!r})
import {module} as target
from network_checks import worker
def stall(*args,**kwargs): time.sleep(30)
target.check=stall
worker(sys.argv[1],float(sys.argv[2]))
''')


def wait_result(checks, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = checks.poll()
        if result is not None:
            return result
        time.sleep(.005)
    raise AssertionError('Update check did not finish within its bound')


class DeadlineTests(unittest.TestCase):
    def manager(self, **kwargs):
        checks = NetworkChecks(**kwargs)
        self.addCleanup(checks.close)
        return checks

    def test_os_deadline_expires_even_when_parent_is_not_polling(self):
        for kind, installed in [('plugin_update_check', '0.1.1'),
                                ('firmware_check', {'dongle': '3.11.0'})]:
            with self.subTest(kind=kind):
                checks = self.manager(timeout=.5, command=stalled_worker(kind))
                checks.start(kind, installed)
                process = checks.job.process
                time.sleep(.8)  # Simulate the controller executing another command.
                self.assertEqual(process.poll(), -signal.SIGALRM)
                action, result = checks.poll()
                self.assertEqual(action, kind)
                entries = result['devices'].values() if kind == 'firmware_check' else [result]
                self.assertTrue(all(r['status'] == 'check-failed' and 'timed out' in r['error'] for r in entries))
                self.assertFalse(checks.running)

    def test_parent_deadline_also_kills_an_uncooperative_child_and_allows_retry(self):
        checks = self.manager(timeout=.25, command=command('import time;time.sleep(30)'))
        checks.start('plugin_update_check', '0.1.1')
        process = checks.job.process
        self.assertFalse(checks.start('firmware_check', {}))
        started = time.monotonic()
        _, result = wait_result(checks)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(result['status'], 'check-failed')
        process.wait(timeout=1)
        checks.command = command('import sys,json;sys.stdin.read();print(json.dumps({"status":"up-to-date"}))')
        self.assertTrue(checks.start('plugin_update_check', '0.1.1'))
        self.assertEqual(wait_result(checks)[1]['status'], 'up-to-date')

    def test_close_kills_and_reaps_an_active_check(self):
        checks = self.manager(timeout=5, command=stalled_worker())
        checks.start('plugin_update_check', '0.1.1')
        process = checks.job.process
        checks.close()
        self.assertIsNotNone(process.poll())
        self.assertFalse(checks.running)

    def test_invalid_or_oversized_worker_results_fail_without_sticking_busy(self):
        for expression in [repr('not-json'), repr('[]'), f'"x" * {MAX_RESULT_BYTES + 1}']:
            with self.subTest(output=expression):
                checks = self.manager(timeout=2, command=command(
                    'import sys;sys.stdin.read();sys.stdout.write(' + expression + ')'))
                checks.start('plugin_update_check', '0.1.1')
                self.assertEqual(wait_result(checks)[1]['status'], 'check-failed')
                self.assertFalse(checks.running)

    def test_eof_pipe_does_not_spin_while_child_is_stalled(self):
        checks = self.manager(timeout=.3, command=command('import os,time;os.close(1);time.sleep(30)'))
        checks.start('plugin_update_check', '0.1.1')
        deadline = time.monotonic() + .2
        while checks.fileno() is not None and time.monotonic() < deadline:
            checks.poll()
            time.sleep(.005)
        self.assertIsNone(checks.fileno())
        self.assertTrue(checks.running)
        self.assertGreater(checks.wait_timeout(5), 0)
        self.assertLessEqual(checks.wait_timeout(5), .05)
        self.assertEqual(wait_result(checks)[1]['status'], 'check-failed')

    def test_slowly_continuing_http_body_cannot_extend_the_total_deadline(self):
        class Trickle(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '1000')
                self.end_headers()
                try:
                    for _ in range(50):
                        self.wfile.write(b' ')
                        self.wfile.flush()
                        time.sleep(.04)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Trickle)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_port}/release'
        code = f'''
import sys,urllib.request
sys.path.insert(0,{BACKEND!r})
import plugin_updates
from network_checks import worker
original=urllib.request.urlopen
def local(request,timeout):
    response=original({url!r},timeout=.15)
    response.url=plugin_updates.LATEST_URL
    return response
plugin_updates.urllib.request.urlopen=local
worker(sys.argv[1],float(sys.argv[2]))
'''
        checks = self.manager(timeout=.6, command=command(code))
        started = time.monotonic()
        checks.start('plugin_update_check', '0.1.1')
        result = wait_result(checks)[1]
        self.assertEqual(result['status'], 'check-failed')
        self.assertIn('timed out', result['error'])
        self.assertLess(time.monotonic() - started, 1.5)


class SharedAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Fake hardware only. Network children use the real deadline mechanism.
        worker_code = f'''
import sys,time
sys.path.insert(0,{BACKEND!r})
import service,firmware,plugin_updates
from network_checks import NetworkChecks
class HP:
    def __init__(self): self.sock=True;self.values={{'battery':80,'firmware':'3.33.3','maintenance':0}}
    def status(self): return {{'connected':True,'values':self.values}}
    def poll_connection(self): self.values['maintenance']+=1;return True
    def set_value(self,key,value): self.values[key]=value
    def close(self): self.sock=None
class Dongle:
    def status(self): return {{'connected':True,'firmware':'3.11.0'}}
    def is_present(self): return True
    def close(self): pass
class Reconnect:
    def __init__(self,*args): pass
    def poll(self): pass
    def close(self): pass
class Presets:
    def names(self): return []
service.Headphones=HP;service.Dongle=Dongle;service.Reconnector=Reconnect;service.Presets=Presets
service.devices=lambda:[]
child={('import sys,time,json;sys.path.insert(0,'+repr(BACKEND)+');import firmware,plugin_updates;from network_checks import worker;firmware.check=lambda x:time.sleep(30);plugin_updates.check=lambda x:{"status":"up-to-date"};worker(sys.argv[1],float(sys.argv[2]))')!r}
checks=NetworkChecks(timeout=.8,command=[sys.executable,'-B','-c',child])
service.run(maintenance_interval=.05,checks=checks)
'''
        self.server = multiprocessing.Process(target=Broker(self.root, command(worker_code)).serve)
        self.server.start()
        self.clients = []
        self.addCleanup(self.stop)

    def stop(self):
        for sock, stream in self.clients:
            stream.close()
            sock.close()
        self.server.join(3)
        if self.server.is_alive():
            self.server.terminate()
            self.server.join(2)

    def connect(self):
        deadline = time.monotonic() + 3
        while True:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(3)
            try:
                sock.connect(str(self.root/'control.sock'))
                pair = (sock, sock.makefile('rb'))
                self.clients.append(pair)
                return pair
            except (FileNotFoundError, ConnectionRefusedError):
                sock.close()
                if time.monotonic() > deadline: raise
                time.sleep(.01)

    def until(self, pair, condition):
        deadline = time.monotonic() + 3
        state = None
        while time.monotonic() < deadline:
            raw = pair[1].readline()
            self.assertTrue(raw, 'Controller exited unexpectedly')
            state = json.loads(raw)
            if condition(state): return state
        self.fail(f"Timed out waiting for controller state; last state: {state}")

    def test_stalled_check_keeps_both_monitors_controls_and_maintenance_available(self):
        first, second = self.connect(), self.connect()
        self.until(first, lambda s:s['headphones']['connected'])
        self.until(second, lambda s:s['headphones']['connected'])
        first[0].sendall(b'{"action":"firmware_check"}\n')
        for pair in (first, second):
            pending = self.until(pair, lambda s:s['update_check_busy'])
            self.assertFalse(pending['busy'])
        second[0].sendall(b'{"action":"setting","key":"transparency","value":25}\n')
        for pair in (first, second):
            changed = self.until(pair, lambda s:s['headphones']['values'].get('transparency')==25 and not s['busy'])
            self.assertTrue(changed['update_check_busy'])
        maintained = self.until(first, lambda s:s['update_check_busy'] and s['headphones']['values']['maintenance']>=3)
        self.assertFalse(maintained['busy'])
        for pair in (first, second):
            expired = self.until(pair, lambda s:not s['update_check_busy'] and 'firmware_check' in s)
            self.assertTrue(all(r['status']=='check-failed' and 'timed out' in r['error']
                                for r in expired['firmware_check']['devices'].values()))
        second[0].sendall(b'{"action":"plugin_update_check"}\n')
        for pair in (first, second):
            completed = self.until(pair, lambda s:s.get('plugin_update',{}).get('status')=='up-to-date')
            self.assertFalse(completed['update_check_busy'])
            self.assertFalse(completed['busy'])


if __name__ == '__main__':
    unittest.main()
