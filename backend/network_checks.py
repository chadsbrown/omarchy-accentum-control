"""One bounded metadata subprocess; the hardware controller never waits on HTTPS."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

KINDS = {'plugin_update_check', 'firmware_check'}
DEADLINE_SECONDS = 10
MAX_RESULT_BYTES = 128 * 1024


def failed(kind, installed, error):
    result = {'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    if kind == 'firmware_check':
        result['devices'] = {key: {'installed_at_check': installed.get(key),
                                  'status': 'check-failed', 'error': error}
                             for key in ('dongle', 'headphones')}
    else:
        result.update(installed_at_check=installed, status='check-failed', error=error)
    return result


@dataclass
class _Job:
    kind: str
    installed: object
    process: subprocess.Popen
    deadline: float
    output: bytearray = field(default_factory=bytearray)
    eof: bool = False


class NetworkChecks:
    def __init__(self, timeout=DEADLINE_SECONDS, command=None, clock=time.monotonic):
        if not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError('Invalid update-check deadline')
        self.timeout, self.clock = timeout, clock
        self.command = command or [sys.executable, '-B', str(Path(__file__).resolve())]
        self.job = None
        self._retiring = []

    @property
    def running(self):
        return self.job is not None

    def fileno(self):
        if self.job is not None and not self.job.eof:
            return self.job.process.stdout.fileno()
        return None

    def wait_timeout(self, maximum):
        if self.job is None:
            return maximum
        # stdout can close just before waitpid observes process exit. Recheck
        # promptly instead of leaving a completed check pending for five seconds.
        if self.job.eof:
            maximum = min(maximum, 0.05)
        return max(0, min(maximum, self.job.deadline - self.clock()))

    def start(self, kind, installed):
        if kind not in KINDS:
            raise ValueError('Unknown update check')
        if self.job is not None:
            return False
        payload = json.dumps(installed, allow_nan=False).encode()
        if len(payload) > 4096:
            raise ValueError('Update-check input is too large')
        deadline = self.clock() + self.timeout
        process = subprocess.Popen([*self.command, kind, str(deadline)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, close_fds=True, bufsize=0)
        self.job = _Job(kind, installed, process, deadline)
        try:
            os.set_blocking(process.stdout.fileno(), False)
            # Only public version strings go to this child, never device handles,
            # addresses, credentials or the rest of the controller snapshot.
            process.stdin.write(payload)
            process.stdin.close()
        except Exception:
            self.close()
            raise
        return True

    def _read(self, job):
        while not job.eof:
            try:
                chunk = os.read(job.process.stdout.fileno(), 65536)
            except BlockingIOError:
                break
            if not chunk:
                job.eof = True
                break
            job.output.extend(chunk)
            if len(job.output) > MAX_RESULT_BYTES:
                raise ValueError('Update-check result is too large')

    def _finish(self, result):
        job, self.job = self.job, None
        job.process.stdout.close()
        if job.process.poll() is None:
            job.process.kill()
        # A killed child is reaped separately without delaying controls. Normally
        # poll() already reaped it. Keep the Popen object alive until it exits.
        if job.process.poll() is None:
            self._retiring.append(job.process)
        return job.kind, result

    def poll(self):
        self._retiring = [p for p in self._retiring if p.poll() is None]
        job = self.job
        if job is None:
            return None
        try:
            self._read(job)
        except (OSError, ValueError):
            return self._finish(failed(job.kind, job.installed, 'Invalid update-check response.'))
        code = job.process.poll()
        if code == -signal.SIGALRM or (code is None and self.clock() >= job.deadline):
            return self._finish(failed(job.kind, job.installed,
                                      f'Update check timed out after {self.timeout:g} seconds. Try again.'))
        if code is None:
            return None
        try:
            self._read(job)
            if code != 0:
                raise ValueError('Update worker failed')
            result = json.loads(job.output)
            if not isinstance(result, dict):
                raise ValueError('Invalid update result')
        except (OSError, ValueError):
            result = failed(job.kind, job.installed, 'Could not complete the update check. Try again.')
        return self._finish(result)

    def close(self):
        processes = getattr(self, '_retiring', [])
        if self.job is not None:
            process = self.job.process
            if not process.stdin.closed:
                process.stdin.close()
            process.stdout.close()
            processes = [*processes, process]
            self.job = None
        for process in processes:
            if process.poll() is None:
                process.kill()
        for process in processes:
            # Shutdown only; never called in the normal device command loop.
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        self._retiring = []


def worker(kind, deadline):
    if kind not in KINDS or not math.isfinite(deadline):
        raise ValueError('Invalid update-check request')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SystemExit(1)
    # Use the default OS action, not a Python exception handler: SIGALRM must
    # terminate even a stalled DNS resolver, TLS handshake or trickling read.
    # The absolute deadline includes interpreter startup and module imports.
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    installed = json.loads(sys.stdin.buffer.read(4097))
    if kind == 'firmware_check':
        from firmware import check
    else:
        from plugin_updates import check
    result = check(installed)
    print(json.dumps(result, allow_nan=False), flush=True)


if __name__ == '__main__':
    worker(sys.argv[1], float(sys.argv[2]))
