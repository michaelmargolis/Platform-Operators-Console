# harness.py ver 1
"""Independent NL2 TCP diagnostic console (Tkinter; no POC state machine).

Run from the POC repository: py -3 tools/nl2_local_test_harness/harness.py
Also supports: py -3 -m tools.nl2_local_test_harness
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import struct
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

if __package__:
    from .harness_core import (
        CLIENT_MESSAGE_NAMES, HEAVY_ACTION_IDS, READ_ONLY_IDS,
        DiagnosticProtocol, pair_payload, parse_hex_bytes,
        reply_summary, station_flags, station_summary, toggle_payload,
    )
else:
    from harness_core import (
        CLIENT_MESSAGE_NAMES, HEAVY_ACTION_IDS, READ_ONLY_IDS,
        DiagnosticProtocol, pair_payload, parse_hex_bytes,
        reply_summary, station_flags, station_summary, toggle_payload,
    )

ROOT_DIR = Path(__file__).resolve().parent
LOG_DIR = ROOT_DIR / 'logs'
POLL_PERIOD_S = 0.5
TELEM_PERIOD_S = 0.5
NEAREST_PERIOD_S = 2.0
VERSION_PERIOD_S = 6.0


class HarnessApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('NoLimits 2 - Local Protocol Console')
        self.root.minsize(1070, 750)
        self.root.geometry('1300x870')
        self.client = None
        self._closing = False
        self._last_station_status = {}
        self._last_send = {'station': 0.0, 'telemetry': 0.0, 'nearest': 0.0, 'version': 0.0}

        self.port = tk.StringVar(value='15151')
        self.coaster = tk.StringVar(value='0')
        self.station = tk.StringVar(value='0')
        self.station_a = tk.StringVar(value='0')
        self.station_b = tk.StringVar(value='1')
        self.enabled = tk.BooleanVar(value=False)
        self.trace_poll_replies = tk.BooleanVar(value=False)
        self.poll_enabled = tk.BooleanVar(value=True)
        self.connection_text = tk.StringVar(value='Disconnected')
        self.telemetry_text = tk.StringVar(value='Telemetry: —')
        self.nearest_text = tk.StringVar(value='Current coaster / nearest station: —')
        self.last_action = tk.StringVar(value='No command sent')
        self.message_id = tk.StringVar(value='14 - GET_STATION_STATE')
        self.raw_payload = tk.StringVar(value='00 00 00 00 00 00 00 00')
        self.selected_flags_text = tk.StringVar(value='Select a station row to see every decoded flag and set the command target')

        LOG_DIR.mkdir(exist_ok=True)
        self.log_file_path = LOG_DIR / ('nl2_harness_%s.log' % datetime.now().strftime('%Y%m%d_%H%M%S'))
        self.log_file = self.log_file_path.open('w', encoding='utf-8', buffering=1)

        self._build_ui()
        self._log('Harness started; localhost only; no outgoing commands until Connect and explicit user action.')
        self._log('Log file: %s' % self.log_file_path)
        self._log('IMPORTANT: Stop POC; no automatic station control, park reset or dispatch commands.')
        self.root.protocol('WM_DELETE_WINDOW', self._close)
        self.root.after(100, self._tick)

    def _build_ui(self):
        top = ttk.LabelFrame(self.root, text='Connection and target')
        top.pack(fill='x', padx=9, pady=(8, 5))
        ttk.Label(top, text='Host: 127.0.0.1 (local NL2)').grid(row=0, column=0, padx=5, pady=5, sticky='w')
        ttk.Label(top, text='Port').grid(row=0, column=1, padx=(15, 3))
        ttk.Entry(top, width=8, textvariable=self.port).grid(row=0, column=2)
        ttk.Button(top, text='Connect', command=self._connect).grid(row=0, column=3, padx=(15, 4))
        ttk.Button(top, text='Disconnect', command=self._disconnect).grid(row=0, column=4, padx=4)
        ttk.Label(top, textvariable=self.connection_text).grid(row=0, column=5, padx=(12, 4), sticky='w')
        top.columnconfigure(5, weight=1)

        ttk.Label(top, text='Target coaster').grid(row=1, column=0, sticky='e', padx=5, pady=(0, 7))
        ttk.Entry(top, width=8, textvariable=self.coaster).grid(row=1, column=1, padx=3, pady=(0, 7), sticky='w')
        ttk.Label(top, text='Target station').grid(row=1, column=2, sticky='e', padx=2, pady=(0, 7))
        ttk.Entry(top, width=8, textvariable=self.station).grid(row=1, column=3, sticky='w', pady=(0, 7))
        ttk.Checkbutton(top, text='Enable control commands', variable=self.enabled).grid(row=1, column=4, padx=6, sticky='w')
        ttk.Label(top, text='OFF by default; commands never fire on connect', foreground='#8B4E00').grid(row=1, column=5, padx=5, sticky='w')

        info = ttk.Frame(self.root)
        info.pack(fill='x', padx=10, pady=2)
        ttk.Label(info, textvariable=self.telemetry_text).pack(anchor='w')
        ttk.Label(info, textvariable=self.nearest_text).pack(anchor='w')

        watch = ttk.LabelFrame(self.root, text='Live station status (command-availability flags, not position sensors)')
        watch.pack(fill='x', padx=9, pady=5)
        head = ttk.Frame(watch)
        head.pack(fill='x', padx=4, pady=3)
        ttk.Checkbutton(head, text='Poll automatically (2 Hz)', variable=self.poll_enabled).pack(side='left', padx=4)
        ttk.Label(head, text='Station A').pack(side='left', padx=(30, 4))
        ttk.Entry(head, width=5, textvariable=self.station_a).pack(side='left')
        ttk.Label(head, text='Station B').pack(side='left', padx=(12, 4))
        ttk.Entry(head, width=5, textvariable=self.station_b).pack(side='left')
        ttk.Button(head, text='Read both now', command=self._read_now).pack(side='left', padx=15)
        ttk.Checkbutton(head, text='Log all polling replies (verbose)', variable=self.trace_poll_replies).pack(side='right', padx=5)

        fields = ('station', 'status', 'present', 'viewed', 'manual', 'can_dispatch', 'can_lock', 'can_unlock', 'gates', 'harness', 'platform', 'estop', 'age')
        labels = ('Station', 'Flags (hex)', 'Train', 'Viewed', 'Manual', 'Dispatch', 'Lock?', 'Unlock?', 'Gates C/O', 'Harness C/O', 'Platform R/L', 'E-stop', 'Age')
        widths = (76, 116, 62, 65, 67, 70, 67, 67, 96, 104, 118, 60, 75)
        self.table = ttk.Treeview(watch, columns=fields, show='headings', height=3, selectmode='browse')
        for fid, label, width in zip(fields, labels, widths):
            self.table.heading(fid, text=label)
            self.table.column(fid, anchor='center', width=width, minwidth=45, stretch=(fid == 'status'))
        self.table.pack(fill='x', padx=5, pady=3)
        self.table.bind('<<TreeviewSelect>>', self._on_select_station)
        ttk.Label(watch, textvariable=self.selected_flags_text, wraplength=1220, foreground='#485F78').pack(anchor='w', padx=6, pady=(1, 5))

        panels = ttk.Frame(self.root)
        panels.pack(fill='x', padx=8, pady=4)
        operations = ttk.LabelFrame(panels, text='Station controls - selected target coaster/station')
        operations.pack(side='left', fill='both', expand=True, padx=3)

        self._action_row(operations, 0, 'Manual mode', [('On (1)', 16, 1), ('Off (0)', 16, 0)])
        self._action_row(operations, 1, 'Flyer seats (ID 21)', [('Lock seats (0)', 21, 0), ('Unlock seats (1)', 21, 1)])
        self._action_row(operations, 2, 'Gates', [('Close (0)', 18, 0), ('Open (1)', 18, 1)])
        self._action_row(operations, 3, 'Harness', [('Close (0)', 19, 0), ('Open (1)', 19, 1)])
        self._action_row(operations, 4, 'Platform', [('Raise (0)', 20, 0), ('Lower (1)', 20, 1)])
        ttk.Label(operations, text='Dispatch requires manual mode + NL2 interlocks.').grid(row=5, column=0, columnspan=2, sticky='w', padx=6, pady=(5, 2))
        ttk.Button(operations, text='DISPATCH selected station', command=lambda: self._send_standard(17)).grid(row=6, column=0, columnspan=2, sticky='we', padx=5, pady=(1, 6))
        operations.columnconfigure(0, weight=1)
        operations.columnconfigure(1, weight=1)

        other = ttk.LabelFrame(panels, text='Read-only tools and raw commands')
        other.pack(side='left', fill='both', expand=True, padx=3)
        read_row = ttk.Frame(other)
        read_row.pack(fill='x', padx=5, pady=(6, 7))
        ttk.Button(read_row, text='Version', command=lambda: self._send_simple(3)).pack(side='left', padx=2)
        ttk.Button(read_row, text='Telemetry', command=lambda: self._send_simple(5)).pack(side='left', padx=2)
        ttk.Button(read_row, text='Nearest', command=lambda: self._send_simple(11)).pack(side='left', padx=2)
        ttk.Button(read_row, text='Coaster count', command=lambda: self._send_simple(7)).pack(side='left', padx=2)

        ttk.Label(other, text='Raw NL2 request ID (or choose a documented command):').pack(anchor='w', padx=6)
        self.raw_id_entry = ttk.Combobox(other, textvariable=self.message_id, values=[
            '%d - %s' % (i, name) for i, name in sorted(CLIENT_MESSAGE_NAMES.items())], width=43)
        self.raw_id_entry.pack(fill='x', padx=6, pady=3)
        ttk.Label(other, text='Payload bytes (hex, no header or request ID):').pack(anchor='w', padx=6)
        ttk.Entry(other, textvariable=self.raw_payload).pack(fill='x', padx=6, pady=4)
        ttk.Button(other, text='Send raw command (explicit)', command=self._send_raw).pack(fill='x', padx=6, pady=4)
        ttk.Label(other, text= 'Raw example: ID 14 / 00 00 00 00 00 00 00 00 reads station 0.\n'
                   'ID 21 / 00 00 00 00 00 00 00 00 00 requests flyer LOCK.\n'
                   'ID 21 / 00 00 00 00 00 00 00 00 01 requests UNLOCK.\n'
                   'Raw payloads are not updated by the target station fields.' , wraplength=570, foreground='#485F78').pack(anchor='w', padx=6, pady=(3, 5))

        trace = ttk.LabelFrame(self.root, text='Chronological protocol log (written to file automatically)')
        trace.pack(fill='both', expand=True, padx=10, pady=5)
        ttk.Label(trace, textvariable=self.last_action).pack(anchor='w', padx=5, pady=(3, 2))
        self.log_text = scrolledtext.ScrolledText(trace, wrap=tk.WORD, state='disabled', height=6, font=('Consolas', 9))
        self.log_text.pack(fill='both', expand=True, padx=5, pady=4)
        ttk.Label(self.root, text='No automatic control commands, seat locking, dispatch, park reset or retry. '
                  'Stop POC and disconnect the physical platform before testing.', foreground='#8B4E00').pack(anchor='w', padx=10, pady=(1, 6))

    def _action_row(self, parent, row, title, options):
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, columnspan=2, sticky='we', padx=4, pady=3)
        ttk.Label(frame, text=title, width=21).pack(side='left', padx=2)
        for label, msgid, value in options:
            ttk.Button(frame, text=label, command=lambda m=msgid, v=value: self._send_standard(m, v)).pack(side='left', fill='x', expand=True, padx=3)

    def _log(self, message):
        line = '%s %s' % (datetime.now().strftime('%H:%M:%S.%f')[:-3], message)
        self.log_file.write(line + '\n')
        self.log_text.configure(state='normal')
        self.log_text.insert('end', line + '\n')
        self.log_text.see('end')
        # Prevent hundreds of megabytes building up in the GUI when verbose tracing is enabled.
        if int(self.log_text.index('end-1c').split('.')[0]) > 5000:
            self.log_text.delete('1.0', '1001.0')
        self.log_text.configure(state='disabled')

    def _connect(self):
        self._disconnect(quiet=True)
        try:
            port = int(self.port.get())
            if not (1 <= port <= 65535):
                raise ValueError('Port must be 1..65535')
            self.client = DiagnosticProtocol('127.0.0.1', port, connect_timeout=1.3)
            if not self.client.connect():
                raise ConnectionError(self.client.last_error)
            self.connection_text.set('Connected to 127.0.0.1:%d' % port)
            self._last_station_status.clear()
            self._last_send = dict.fromkeys(self._last_send, 0.0)
            self._log('CONNECTED 127.0.0.1:%d - initial queries are read-only' % port)
        except (ValueError, OSError, ConnectionError) as exc:
            self.client = None
            self.connection_text.set('Disconnected')
            self._log('CONNECT ERROR: %s' % exc)
            messagebox.showerror('NL2 connection failed', str(exc))

    def _disconnect(self, quiet=False):
        if self.client is not None:
            self.client.disconnect()
            self.client = None
            if not quiet:
                self._log('DISCONNECTED')
        self.enabled.set(False)
        self.connection_text.set('Disconnected')

    def _target(self):
        coaster, station = int(self.coaster.get()), int(self.station.get())
        pair_payload(coaster, station)  # validates indices
        return coaster, station

    def _two_stations(self):
        coaster = int(self.coaster.get())
        a, b = int(self.station_a.get()), int(self.station_b.get())
        for s in (a, b):
            pair_payload(coaster, s)
        return [(coaster, a), (coaster, b)]

    def _send_standard(self, msgid, value=None):
        try:
            coaster, station = self._target()
            if msgid == 17:
                payload = pair_payload(coaster, station)
            else:
                payload = toggle_payload(coaster, station, value)
            self._issue_command(msgid, payload)
        except (ValueError, OSError, ConnectionError) as exc:
            self._log('COMMAND ERROR: %s' % exc)
            messagebox.showerror('Command not sent', str(exc))

    def _send_simple(self, msgid):
        try:
            self._issue_command(msgid, b'')
        except (ValueError, OSError, ConnectionError) as exc:
            self._log('QUERY ERROR: %s' % exc)
            messagebox.showerror('Query not sent', str(exc))

    def _send_raw(self):
        try:
            token = self.message_id.get().strip().split()[0]
            msgid = int(token, 0)
            payload = parse_hex_bytes(self.raw_payload.get())
            self._issue_command(msgid, payload, raw=True)
        except (ValueError, OSError, ConnectionError) as exc:
            self._log('RAW COMMAND ERROR: %s' % exc)
            messagebox.showerror('Raw command not sent', str(exc))

    def _issue_command(self, msgid, payload, raw=False):
        if self.client is None or not self.client.is_connected:
            raise ConnectionError('Connect to localhost first')
        if msgid not in READ_ONLY_IDS and not self.enabled.get():
            raise ValueError('Enable control commands checkbox before sending this request')
        title = CLIENT_MESSAGE_NAMES.get(msgid, 'UNKNOWN_TYPE')
        if raw and msgid not in CLIENT_MESSAGE_NAMES:
            if not messagebox.askyesno('Unknown NL2 message', 'Send undocumented message ID %s?\nPayload: %s' % (msgid, payload.hex(' '))):
                return
        if msgid in HEAVY_ACTION_IDS:
            warning = 'Send %s (ID %d) to the LOCAL NL2 simulator?\nPayload: %s\n\nThis can change the simulator state.' % (title, msgid, payload.hex(' '))
            if not messagebox.askyesno('Confirm NL2 control action', warning):
                self._log('COMMAND CANCELLED %s' % title)
                return
        request_id = self.client.send_command(msgid, payload)
        text = 'TX %s id=%d req=%d payload=%s' % (title, msgid, request_id, payload.hex(' ') or '(empty)')
        self._log(text)
        self.last_action.set('Last command: %s request %d (awaiting response)' % (title, request_id))

    def _read_now(self):
        try:
            if self.client is None or not self.client.is_connected:
                raise ConnectionError('Not connected')
            for coaster, station in self._two_stations():
                self.client.request_station_state(coaster, station)
            self._log('QUERY station status requested for %s' % self._two_stations())
        except (ValueError, OSError, ConnectionError) as exc:
            self._log('READ ERROR: %s' % exc)

    def _tick(self):
        if self._closing:
            return
        client = self.client
        if client is not None:
            try:
                client.service()  # decodes messages and stores station snapshots in POC module
                self._process_events(client)
                if not client.is_connected:
                    self._log('NL2 connection lost: %s' % client.last_error)
                    self._disconnect()
                else:
                    self.connection_text.set('Connected: NL2 %s' % (client.version or 'version pending'))
                    if self.poll_enabled.get():
                        self._automatic_poll(client)
                    self._refresh_display(client)
            except (ValueError, OSError, ConnectionError) as exc:
                self._log('SERVICE ERROR: %s' % exc)
        self.root.after(100, self._tick)

    def _automatic_poll(self, client):
        now = time.monotonic()
        if now - self._last_send['version'] >= VERSION_PERIOD_S:
            client.request_version()
            self._last_send['version'] = now
        if now - self._last_send['telemetry'] >= TELEM_PERIOD_S:
            client.request_telemetry()
            self._last_send['telemetry'] = now
        if now - self._last_send['nearest'] >= NEAREST_PERIOD_S:
            client.request_current_coaster_and_station()
            self._last_send['nearest'] = now
        if now - self._last_send['station'] >= POLL_PERIOD_S:
            for coaster, station in self._two_stations():
                client.request_station_state(coaster, station)
            self._last_send['station'] = now

    def _process_events(self, client):
        while client.events:
            received, msg_id, request_id, payload = client.events.popleft()
            action = client.raw_requests.get(request_id)
            if action is not None:
                req_type, req_data = action
                self._log('RX req=%d (%s) %s' % (
                    request_id, CLIENT_MESSAGE_NAMES.get(req_type, 'ID_%d' % req_type),
                    reply_summary(msg_id, payload)))
                self.last_action.set('Request %d: %s' % (request_id, reply_summary(msg_id, payload)))
                client.raw_requests.pop(request_id, None)
            elif msg_id == 2 and self.trace_poll_replies.get():
                self._log('RX polling request=%d %s' % (request_id, reply_summary(msg_id, payload)))
            elif self.trace_poll_replies.get():
                self._log('RX polling req=%d %s' % (request_id, reply_summary(msg_id, payload)))

        # Status changes are written even with verbose polling disabled.
        try:
            targets = self._two_stations()
        except ValueError:
            return
        for identity in targets:
            snapshot = client.station_snapshot(identity)
            if snapshot is not None:
                raw, received_at = snapshot
                prev = self._last_station_status.get(identity)
                if prev != raw:
                    self._log('STATION (%d,%d) %s' % (*identity, station_summary(raw)))
                    self._last_station_status[identity] = raw

    def _refresh_display(self, client):
        tm = client.latest_telemetry
        if tm is not None:
            self.telemetry_text.set('Telemetry: frame=%d  coaster=%d train=%d car=%d seat=%d  speed=%.3f m/s  play=%s paused=%s view=%d' % (
                tm.frame, tm.coaster_index, tm.train, tm.car, tm.seat,
                tm.speed, tm.in_play_mode, tm.paused, tm.view_mode))
        if client.current_coaster_index is not None:
            self.nearest_text.set('Current coaster / nearest station: %s / %s (not necessarily the viewed-train station)' % (
                client.current_coaster_index, client.current_station_index))
        try:
            targets = self._two_stations()
        except ValueError:
            return
        active_keys = []
        for coaster, station in targets:
            identity = (coaster, station)
            key = '%d:%d' % identity
            active_keys.append(key)
            snapshot = client.station_snapshot(identity)
            if snapshot is None:
                values = (key, 'no reply', '-', '-', '-', '-', '-', '-', '-', '-', '-', '-', '-')
            else:
                raw, received_at = snapshot
                f = station_flags(raw)
                def yes(k):
                    return 'Y' if f[k] else '-'
                age = max(0.0, time.monotonic() - received_at)
                fresh = age <= 2.0
                values = (key, '0x%08X' % raw, yes('TRAIN_PRESENT'), yes('VIEW_TRAIN_HERE'), yes('MANUAL'),
                          yes('CAN_DISPATCH'), yes('CAN_LOCK_FLYER'), yes('CAN_UNLOCK_FLYER'),
                          '%s/%s' % (yes('CAN_CLOSE_GATES'), yes('CAN_OPEN_GATES')),
                          '%s/%s' % (yes('CAN_CLOSE_HARNESS'), yes('CAN_OPEN_HARNESS')),
                          '%s/%s' % (yes('CAN_RAISE_PLATFORM'), yes('CAN_LOWER_PLATFORM')),
                          yes('E_STOP'), '%.1fs%s' % (age, '' if fresh else ' STALE'))
            if self.table.exists(key):
                self.table.item(key, values=values)
            else:
                self.table.insert('', 'end', iid=key, values=values)
        for old in self.table.get_children(''):
            if old not in active_keys:
                self.table.delete(old)
        self._on_select_station()

    def _on_select_station(self, _evt=None):
        if not self.table.selection():
            return
        selected = self.table.selection()[0]
        try:
            coaster, station = (int(x) for x in selected.split(':'))
        except ValueError:
            return
        if _evt is not None:
            self.station.set(str(station))
            self._log('TARGET changed by station selection: coaster=%d station=%d' % (coaster, station))
        client = self.client
        if client is None:
            return
        snapshot = client.station_snapshot((coaster, station))
        if snapshot is None:
            return
        raw, received = snapshot
        flag_items = station_flags(raw)
        bits = ' | '.join('%s=%d' % (name, int(active)) for name, active in flag_items.items())
        self.selected_flags_text.set('(%d,%d) %s | age %.1fs\n%s' % (
            coaster, station, station_summary(raw), time.monotonic() - received, bits))

    def _close(self):
        self._closing = True
        self._disconnect(quiet=True)
        self._log('Harness closed')
        self.log_file.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    HarnessApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
