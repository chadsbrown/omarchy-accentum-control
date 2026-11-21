# SPDX-License-Identifier: MIT
# GAIA framing and connection handling derived from phi0x/omarchy-sennheiser-control
# (itself informed by hatemosphere/hdb630-control-macos); ACCENTUM command set from
# xndbogdan/sennheiser-control's protocol notes. See THIRD_PARTY.md.
"""Sennheiser ACCENTUM control over a Bluetooth Classic RFCOMM connection (GAIA v3)."""
import re
import select
import socket
import struct
import subprocess
import time
from build_helper import helper_path
import eq

VENDOR = 0x0495
CODECS = {0: 'SBC', 1: 'AAC', 2: 'aptX', 3: 'aptX Low Latency', 5: 'aptX HD',
          8: 'aptX Adaptive', 9: 'aptX Lossless', 10: 'LC3'}
# key: (read, write, allowed values)
SETTINGS = {
    'transparency': (0x1805, 0x1804, (0, 1)),   # 0 = noise cancelling, 1 = transparency
    'sidetone': (0x0806, 0x0805, (0, 1, 2)),    # own voice on calls: off / low / high
}
ANTI_WIND = 1                                    # mode id inside the 0x1A01 table
AUDIO_EQ, AUDIO_PODCAST = 1, 2


def packet(command, payload=b''):
    return struct.pack('>BBHHH', 0xFF, 3, len(payload), VENDOR, command) + payload


def words(values):
    return struct.pack(f'>{len(values)}h', *values)


def unwords(data):
    return list(struct.unpack(f'>{len(data) // 2}h', data[:len(data) // 2 * 2]))


class FrameBuffer:
    def __init__(self):
        self.data = bytearray()

    def feed(self, data):
        self.data.extend(data)
        frames = []
        while len(self.data) >= 8:
            if self.data[:2] != b'\xff\x03':
                del self.data[0]
                continue
            size, vendor, cmd = struct.unpack('>HHH', self.data[2:8])
            if size > 4096:
                raise OSError('Invalid GAIA frame length')
            if len(self.data) < 8 + size:
                break
            frames.append((vendor, cmd, bytes(self.data[8:8 + size])))
            del self.data[:8 + size]
        return frames


class Headphones:
    def __init__(self):
        self.sock = None
        self.address = None
        self.frames = FrameBuffer()

    def close(self):
        if self.sock:
            self.sock.close()
        self.sock, self.address = None, None
        self.frames = FrameBuffer()

    def connect(self, address):
        if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', address):
            raise ValueError('Invalid Bluetooth address')
        self.close()
        helper = helper_path()
        try:
            result = subprocess.run([str(helper), address], capture_output=True, text=True, timeout=8)
        except subprocess.TimeoutExpired:
            raise OSError('Headphones are not reachable for control discovery') from None
        if result.returncode:
            raise OSError(result.stderr.strip() or 'Could not discover the headphone control service')
        channel = int(result.stdout.strip())
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.settimeout(8)
        try:
            sock.connect((address, channel))
        except Exception:
            sock.close()
            raise
        self.sock, self.address = sock, address

    def request(self, command, payload=b''):
        if not self.sock:
            raise OSError('Connect the headphones first')
        try:
            self.sock.sendall(packet(command, payload))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                self.sock.settimeout(max(.01, deadline - time.monotonic()))
                data = self.sock.recv(4096)
                if not data:
                    raise OSError('Headphone connection closed')
                for vendor, cmd, reply in self.frames.feed(data):
                    if vendor != VENDOR:
                        continue
                    if cmd == command | 0x0180:
                        raise ValueError(f'Headphones rejected command 0x{command:04x}: {reply.hex()}')
                    if cmd == command | 0x0100:
                        return reply
            raise TimeoutError(f'Headphones did not answer 0x{command:04x}')
        except OSError:
            # A late reply after timeout could be mistaken for the next request.
            self.close()
            raise

    def get(self, command, minimum=1, payload=b''):
        data = self.request(command, payload)
        if len(data) < minimum:
            raise ValueError(f'Short headphone reply for 0x{command:04x}')
        return data

    def poll_connection(self):
        """Detect socket loss and drain notifications without sending commands."""
        if not self.sock:
            return False
        try:
            for _ in range(64):
                if not select.select([self.sock], [], [], 0)[0]:
                    return True
                try:
                    data = self.sock.recv(4096, socket.MSG_DONTWAIT)
                except BlockingIOError:
                    return True
                if not data:
                    self.close()
                    return False
                self.frames.feed(data)
            return True
        except (OSError, ValueError):
            self.close()
            return False

    def anti_wind(self):
        table = self.get(0x1A01, 2)
        pairs = dict(zip(table[0::2], table[1::2]))
        if ANTI_WIND not in pairs:
            raise ValueError('Wind noise setting missing from reply')
        return pairs[ANTI_WIND]

    def status(self):
        values, errors = {}, {}
        readers = {
            'battery': lambda: self.get(0x0603)[0],
            'charging': lambda: self.get(0x0602)[0],
            'firmware': lambda: '.'.join(str(n) for n in struct.unpack('>3H', self.get(0x1201, 6)[:6])),
            'model': lambda: self.get(0x1206).split(b'\0', 1)[0].decode('ascii', 'replace'),
            'codec': lambda: CODECS.get(self.get(0x0800)[0], ''),
            'anti_wind': self.anti_wind,
            'audio_mode': lambda: self.get(0x0804)[-1],
        }
        readers.update({key: (lambda command=spec[0]: self.get(command)[0]) for key, spec in SETTINGS.items()})
        for key, read in readers.items():
            try:
                values[key] = read()
            except ValueError as exc:
                errors[key] = str(exc)
        return dict(connected=True, address=self.address, values=values, errors=errors)

    def set_value(self, key, value):
        if type(value) is not int:
            raise ValueError('Setting value must be a whole number')
        if key in SETTINGS:
            _, command, allowed = SETTINGS[key]
            if value not in allowed:
                raise ValueError('Setting outside allowed range')
            self.request(command, bytes([value]))
        elif key == 'anti_wind':
            if value not in (0, 1, 2):
                raise ValueError('Choose Off, Automatic or Maximum')
            self.request(0x1A00, bytes([ANTI_WIND, value]))
        elif key == 'audio_mode':
            if value not in (AUDIO_EQ, AUDIO_PODCAST):
                raise ValueError('Unsupported sound mode')
            self.request(0x0803, bytes([0, value]))
        else:
            raise ValueError('Unknown setting')

    def read_eq(self):
        """Stored EQ as dict(configured, gains, bass_boost). When not configured the
        gains are meaningless: the EQ was never written or another mode replaced it."""
        types = unwords(self.get(0x1003, 18, words(eq.TYPE_QUERY)))
        knob = struct.unpack('>H', self.get(0x1005, 2, struct.pack('>H', eq.KNOB_Y_INDEX))[:2])[0]
        bass = eq.knob_bass_boost(knob)
        if not eq.configured(types) or bass is None:
            return dict(configured=False, gains=[0.0] * eq.BANDS, bass_boost=False)
        shift = struct.unpack('b', self.get(0x1007)[:1])[0]
        gains = eq.decode(unwords(self.get(0x1003, 22, words(eq.GAIN_QUERY))), shift, bass)
        return dict(configured=True, gains=gains, bass_boost=bass)

    def set_eq(self, gains, bass_boost):
        if type(bass_boost) is not bool:
            raise ValueError('Bass boost must be on or off')
        params, shift, knob = eq.encode(gains, bass_boost)   # validates before any write
        self.request(0x1002, words(params))
        self.request(0x1006, struct.pack('b', shift))
        self.request(0x1004, struct.pack('>HH', eq.KNOB_Y_INDEX, knob))

    def paired_devices(self):
        count = int.from_bytes(self.get(0x1400, 2)[:2], 'big')
        if count > 16:
            raise ValueError('Invalid paired-device count')
        own = self.get(0x1407)[0]
        result = []
        for index in range(count):
            data = self.get(0x1401, 3, bytes([index]))
            if data[0] != index:
                raise ValueError('Wrong paired-device index')
            result.append(dict(index=index, name=data[3:].rstrip(b'\0').decode('utf-8', 'replace'),
                               connected=bool(data[2]), own=index == own))
        return result
