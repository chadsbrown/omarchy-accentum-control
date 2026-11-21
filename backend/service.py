#!/usr/bin/python
# Worker loop adapted from phi0x/omarchy-sennheiser-control; see THIRD_PARTY.md.
"""Serialized headphone controls: one JSON request per line in, one state snapshot per line out."""
import json
import os
from pathlib import Path
import re
import select
import sys
import eq
from headphones import Headphones, AUDIO_EQ, AUDIO_PODCAST
from reconnect import Reconnector

STATE_DIR = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'accentum-control'
ADDRESS = re.compile(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}')


def devices():
    import dbus
    bus = dbus.SystemBus()
    manager = dbus.Interface(bus.get_object('org.bluez', '/'), 'org.freedesktop.DBus.ObjectManager')
    found = []
    objects = manager.GetManagedObjects()
    for path, interfaces in objects.items():
        p = interfaces.get('org.bluez.Device1', {})
        name = str(p.get('Name', p.get('Alias', '')))
        if name.upper().startswith('ACCENTUM'):
            adapter = objects.get(str(path).split('/dev_')[0], {}).get('org.bluez.Adapter1', {})
            found.append(dict(name=name, address=str(p['Address']), paired=bool(p.get('Paired', False)),
                              adapter_powered=bool(adapter.get('Powered', False))))
    return found


class EqMemory:
    """Last EQ the user chose. Podcast mode overwrites the EQ stored on the
    headphones, so it has to be sent again when Podcast mode is turned off."""
    def __init__(self, path=None):
        self.path = path or STATE_DIR / 'eq.json'

    def load(self):
        try:
            data = json.loads(self.path.read_text())
            return dict(gains=eq.validate(data['gains']), bass_boost=data['bass_boost'] is True)
        except (OSError, ValueError, KeyError, TypeError):
            return dict(gains=[0.0] * eq.BANDS, bass_boost=False)

    def save(self, gains, bass_boost):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = self.path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as file:
            json.dump(dict(gains=gains, bass_boost=bass_boost), file)
        temp.replace(self.path)


def run(maintenance_interval=5):
    headphones = Headphones()
    first_run = not (STATE_DIR / 'connection.json').exists()
    reconnect = Reconnector(headphones, devices, STATE_DIR / 'connection.json')
    memory = EqMemory()
    state = dict(headphones={}, devices=[], message='', busy=False, operation='',
                 eq=dict(memory.load(), active=False, needs_apply=False),
                 presets=[dict(name=name, gains=gains) for name, gains in eq.PRESETS.items()],
                 bands=eq.LABELS, eq_revision=0)

    def emit():
        print(json.dumps(state, allow_nan=False), flush=True)

    def read_eq():
        mode = state['headphones'].get('values', {}).get('audio_mode')
        stored = headphones.read_eq() if mode == AUDIO_EQ else None
        if stored and stored['configured']:
            memory.save(stored['gains'], stored['bass_boost'])
            shown, needs_apply = stored, False
        else:
            shown, needs_apply = memory.load(), mode == AUDIO_EQ
        new = dict(gains=shown['gains'], bass_boost=shown['bass_boost'],
                   active=mode == AUDIO_EQ, needs_apply=needs_apply)
        if new != state['eq']:
            state['eq'] = new
            state['eq_revision'] += 1

    def refresh(full=True):
        # Bulk reads can disturb wireless playback, so settings are read only on
        # request, after a change, or on a new connection. Idle ticks just check
        # that the socket is still alive.
        was_connected = bool(headphones.sock)
        if not full and was_connected and not headphones.poll_connection():
            reconnect.lost()
        reconnect.poll()
        if headphones.sock:
            try:
                if full or not was_connected:
                    state['headphones'] = headphones.status()
                    read_eq()
            except Exception:
                reconnect.lost()
                state['headphones'] = reconnect.status()
        else:
            state['headphones'] = reconnect.status()
        emit()

    input_buffer = bytearray()
    input_fd = sys.stdin.fileno()
    try:
        try:
            state['devices'] = devices()
            paired = [d for d in state['devices'] if d['paired']]
            if first_run and len(paired) == 1:
                reconnect.connect(paired[0]['address'])
        except Exception as exc:
            state['message'] = f'Bluetooth: {exc}'
        refresh()
        while True:
            if b'\n' not in input_buffer:
                ready, _, _ = select.select([input_fd], [], [], maintenance_interval)
                if not ready:
                    refresh(full=False)
                    continue
                chunk = os.read(input_fd, 65536)
                if not chunk:
                    break
                input_buffer.extend(chunk)
                if len(input_buffer) > 65536:
                    input_buffer.clear()
                continue
            raw_line, _, remainder = input_buffer.partition(b'\n')
            input_buffer[:] = remainder
            action = ''
            try:
                req = json.loads(raw_line.decode('utf-8'))
                action = req.get('action')
                state['busy'] = True
                state['operation'] = action
                emit()
                if action == 'connect':
                    address = req['address']
                    if not isinstance(address, str) or not ADDRESS.fullmatch(address):
                        raise ValueError('Invalid Bluetooth address')
                    state['devices'] = devices()
                    known = next((d for d in state['devices'] if d['address'] == address), None)
                    if not known or not known['paired']:
                        raise ValueError('Pair the headphones in the Bluetooth panel first.')
                    reconnect.connect(address)
                elif action == 'disconnect':
                    reconnect.disconnect()
                elif action == 'setting':
                    key, value = req['key'], req['value']
                    headphones.set_value(key, value)
                    if key == 'audio_mode' and value == AUDIO_EQ:
                        # Leaving Podcast mode: put the user's EQ back.
                        saved = memory.load()
                        headphones.set_eq(saved['gains'], saved['bass_boost'])
                elif action == 'eq_write':
                    if state['headphones'].get('values', {}).get('audio_mode') == AUDIO_PODCAST:
                        raise ValueError('Turn off Podcast mode to change the equalizer.')
                    headphones.set_eq(req['gains'], req['bass_boost'])
                    memory.save(eq.validate(req['gains']), req['bass_boost'])
                elif action == 'paired_read':
                    state['paired_devices'] = headphones.paired_devices()
                elif action != 'refresh':
                    raise ValueError('Unknown action')
                state['message'] = ''
            except Exception as exc:
                state['message'] = f'{action or "Request"}: {exc}'
                if action == 'eq_write':
                    state['message'] += ' — the equalizer may be partly changed; apply it again.'
            finally:
                state['busy'] = False
                if action == 'eq_write':
                    # Rebuild the sliders from what the headphones now hold.
                    state['eq_revision'] += 1
            refresh(full=action != 'paired_read')
    finally:
        reconnect.close()


if __name__ == '__main__':
    run()
