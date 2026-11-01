#!/usr/bin/python
"""Serialized hardware controls with separate, bounded update-check subprocesses."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
from dongle import Dongle
from headphones import Headphones
from reconnect import Reconnector
from presets import Presets
from plugin_updates import installed as plugin_info
from network_checks import NetworkChecks, failed as check_failed


def devices():
    import dbus
    bus = dbus.SystemBus()
    manager = dbus.Interface(bus.get_object('org.bluez', '/'), 'org.freedesktop.DBus.ObjectManager')
    found = []
    objects = manager.GetManagedObjects()
    for path, interfaces in objects.items():
        p = interfaces.get('org.bluez.Device1', {})
        name = str(p.get('Name', p.get('Alias', '')))
        if 'HDB' in name.upper() and '630' in name:
            adapter = objects.get(str(path).split('/dev_')[0], {}).get('org.bluez.Adapter1', {})
            found.append(dict(name=name, address=str(p['Address']), paired=bool(p.get('Paired', False)),
                              adapter_powered=bool(adapter.get('Powered', False))))
    return found


def run(maintenance_interval=5, checks=None):
    checks = checks if checks is not None else NetworkChecks()
    dongle = None
    headphones = Headphones()
    reconnect = Reconnector(headphones, devices)
    presets = Presets()
    state = dict(dongle={}, headphones={}, devices=[], message='', busy=False,
                 plugin=plugin_info(), update_check_busy=False, update_check_operation="")
    def emit():
        print(json.dumps(state, allow_nan=False), flush=True)
    def refresh(full=True):
        # Bulk HID/GAIA reads can disturb wireless playback. Idle maintenance
        # checks host-side presence/socket state, retaining the last snapshot.
        # Read settings only on an explicit request or a new device connection.
        nonlocal dongle
        try:
            if dongle is None:
                dongle = Dongle()
            if not full and not dongle.is_present():
                dongle.close()
                state['dongle'] = dict(connected=False, error='BTD 700 not found. Plug in the dongle and retry.')
            elif full or not state['dongle'].get('connected'):
                state['dongle'] = dongle.status()
        except Exception as exc:
            if dongle:
                dongle.close()
            state['dongle'] = dict(connected=False, error=str(exc))
        was_connected = bool(headphones.sock)
        if not full and was_connected and not headphones.poll_connection():
            reconnect.lost()
            state.pop('peq', None)
            state.pop('graphic', None)
        reconnect.poll()
        if headphones.sock:
            try:
                if full or not was_connected:
                    state['headphones'] = headphones.status()
            except Exception as exc:
                reconnect.lost()
                state.pop('peq', None)
                state.pop('graphic', None)
                state['headphones'] = reconnect.status()
        else:
            state['headphones'] = reconnect.status()
        emit()
    # Read the pipe directly: TextIO.readline() can prefetch another monitor's
    # command, hiding it from select() until the next polling timeout.
    input_buffer = bytearray()
    try:
        input_fd = sys.stdin.fileno()
    except (AttributeError, OSError):
        input_fd = None
    try:
        try:
            state['devices'] = devices()
        except Exception as exc:
            state['message'] = f'Bluetooth: {exc}'
        try:
            state['presets'] = presets.names()
        except Exception as exc:
            state['message'] = f'Preset library: {exc}'
        refresh()
        while True:
            completed = checks.poll()
            if completed is not None:
                kind, result = completed
                state['firmware_check' if kind == 'firmware_check' else 'plugin_update'] = result
                state['update_check_busy'] = False
                state['update_check_operation'] = ''
                refresh(full=False)
            network_fd = checks.fileno()
            timeout = checks.wait_timeout(maintenance_interval)
            if input_fd is not None:
                if b'\n' not in input_buffer:
                    ready, _, _ = select.select([input_fd] + ([] if network_fd is None else [network_fd]), [], [], timeout)
                    if not ready:
                        refresh(full=False)
                        continue
                    if input_fd not in ready:
                        continue
                    chunk = os.read(input_fd, 65536)
                    if not chunk:
                        break
                    input_buffer.extend(chunk)
                    if b'\n' not in input_buffer:
                        continue
                raw_line, _, remainder = input_buffer.partition(b'\n')
                input_buffer[:] = remainder
                line = raw_line.decode('utf-8')
            else:
                ready, _, _ = select.select([sys.stdin] + ([] if network_fd is None else [network_fd]), [], [], timeout)
                if not ready:
                    refresh(full=False)
                    continue
                if sys.stdin not in ready:
                    continue
                line = sys.stdin.readline()
                if not line:
                    break
            action = ''
            try:
                req = json.loads(line)
                action = req.get('action')
                if action in ('firmware_check', 'plugin_update_check'):
                    if action == 'firmware_check':
                        installed = {'dongle': state.get('dongle', {}).get('firmware'),
                                     'headphones': state.get('headphones', {}).get('values', {}).get('firmware')}
                    else:
                        state['plugin'] = plugin_info()
                        installed = state['plugin']['version']
                    try:
                        if checks.start(action, installed):
                            state['update_check_busy'] = True
                            state['update_check_operation'] = action
                    except Exception:
                        state['firmware_check' if action == 'firmware_check' else 'plugin_update'] = check_failed(
                            action, installed, 'Could not start the update check. Try again.')
                    refresh(full=False)
                    continue
                state['busy'] = True
                state['operation'] = action
                emit()
                if action == 'mode':
                    dongle.set_mode(int(req['mode']), int(req['transport']))
                elif action == 'codec':
                    dongle.set_codec(int(req['mask']))
                elif action == 'scan':
                    result = subprocess.run(['bluetoothctl', '--timeout', '8', 'scan', 'on'],
                                            capture_output=True, text=True, timeout=12)
                    state['devices'] = devices()
                    if not state['devices']:
                        raise OSError('No HDB 630 found. Put headphones in pairing mode, then search again.')
                elif action == 'connect':
                    address = req['address']
                    # Validate before passing to bluetoothctl or SDP.
                    import re
                    if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', address):
                        raise ValueError('Invalid Bluetooth address')
                    known = next((d for d in devices() if d['address'] == address), None)
                    if not known:
                        raise ValueError('Headphones not discovered. Search first.')
                    if not known['paired']:
                        result = subprocess.run(['bluetoothctl', 'pair', address], capture_output=True,
                                                text=True, timeout=25)
                        if result.returncode or not any(d['paired'] for d in devices() if d['address'] == address):
                            raise OSError('Pairing incomplete. Pair HDB 630 in the Bluetooth panel, then retry.')
                    reconnect.connect(address)
                    state['headphones'] = headphones.status() if headphones.sock else reconnect.status()
                elif action == 'disconnect':
                    reconnect.disconnect()
                    state.pop('peq', None)
                    state['headphones'] = reconnect.status()
                elif action == 'setting':
                    headphones.set_value(req['key'], req['value'])
                elif action == 'broadcast_read':
                    state['broadcast'] = dongle.broadcast()
                elif action == 'broadcast_write':
                    dongle.set_broadcast(req['name'], req['public'], req['quality'], req['encrypted'], req.get('password'))
                    state['broadcast'] = dongle.broadcast()
                elif action == 'resolution':
                    if headphones.restart_resolution(req['high_resolution']):
                        reconnect.lost()
                        state.pop('peq', None)
                        state['message'] = 'Headphones restarting to apply resolution; reconnecting automatically.'
                    else:
                        state['message'] = 'Resolution preference is already active.'
                elif action == 'paired_read':
                    state['paired_devices'] = headphones.paired_devices()
                elif action == 'preset_save':
                    presets.save(req['name'], req['stages'], req['pregain'])
                    state['presets'] = presets.names()
                elif action == 'preset_load':
                    state['peq'] = presets.load(req['name'])
                elif action == 'preset_delete':
                    presets.delete(req['name'])
                    state['presets'] = presets.names()
                elif action == 'graphic_read':
                    state['graphic'] = headphones.graphic_eq()
                elif action == 'graphic_write':
                    headphones.set_graphic_eq(req['gains'])
                    state['graphic'] = headphones.graphic_eq()
                elif action == 'peq_read':
                    state['peq'] = headphones.peq()
                elif action == 'peq_write':
                    headphones.set_peq(req['stages'], req['pregain'])
                    state['peq'] = headphones.peq()
                elif action != 'refresh':
                    raise ValueError('Unknown action')
                # Frequent slider/switch changes should not insert/remove status rows.
                if action not in ('setting', 'resolution'):
                    state['message'] = 'Settings read' if action in ('refresh','peq_read') else 'Done'
            except Exception as exc:
                state['message'] = f'{action or "Request"}: {exc}'
                # Multi-setting writes can fail partway; invalidate only that form.
                if action in ('peq_write', 'broadcast_write', 'graphic_write'):
                    state.pop({'peq_write': 'peq', 'broadcast_write': 'broadcast', 'graphic_write': 'graphic'}[action], None)
                    state['message'] += ' — some settings may have changed; read them again.'
            finally:
                if action in ('broadcast_read', 'broadcast_write'):
                    state['broadcast_revision'] = state.get('broadcast_revision', 0) + 1
                if action in ('graphic_read', 'graphic_write'):
                    state['graphic_revision'] = state.get('graphic_revision', 0) + 1
                if action in ('peq_read', 'peq_write', 'preset_load'):
                    state['peq_revision'] = state.get('peq_revision', 0) + 1
                state['busy'] = False
            refresh(full=action not in ('preset_save', 'preset_load', 'preset_delete',
                                       'paired_read', 'plugin_update_check'))
    finally:
        checks.close()
        reconnect.close()
        if dongle:
            dongle.close()

if __name__ == '__main__':
    if '--probe' in sys.argv:
        d = Dongle()
        try:
            print(json.dumps(d.status(), indent=2))
        finally:
            d.close()
    else:
        run()
