# station_modes.py ver 3
"""Identify and hold the viewed coaster's stations in manual mode.

No park-specific station list: probe contiguous zero-based NL2 station indices
using correlated GET_STATION_STATE replies. An explicit NL2 ERROR ends the list;
a missing reply does NOT establish that an index is absent. All commands are
limited to confirmed stations of the viewed coaster. This module never sends
DISPATCH or any seat, gate, or platform command.
"""
from __future__ import annotations

import time


MAX_STATIONS = 16
PROBE_TIMEOUT_S = 1.5
PROBE_RETRIES = 1
# NL2 temporarily rejects station queries with "Not in play mode" after
# RESET_PARK; retry that explicit transitional error without abandoning the scan.
PLAY_MODE_RETRY_S = 0.75
POLL_INTERVAL_S = 0.65
STATUS_MAX_AGE_S = 1.5
MANUAL_RETRY_S = 1.5
MAX_MANUAL_COMMANDS = 2


def _invalid_station_error(message):
    """Only an explicit invalid-index error establishes the end of a list.

    A generic NL2 ERROR could mean a temporary play-mode or API failure;
    silently treating that as no other station could permit auto operation.
    """
    error = str(message or '').casefold()
    return any(term in error for term in (
        'invalid index', 'invalid station', 'station index', 'no such station',
        'unknown station', 'station not found', 'out of range',
        'out of bounds',
    ))


def _temporary_play_mode_error(message):
    """NL2 can return this while RESET_PARK is still changing play mode."""
    return "not in play mode" in str(message or "").casefold()


class StationModeManager:
    def __init__(self):
        self.reset()

    def reset(self):
        self.coaster = None
        self.phase = 'idle'
        self.stations = ()
        self.error = ''
        self._probe_index = 0
        self._probe_sent = 0.0
        self._probe_request_id = None
        self._probe_attempts = 0
        self._probe_retry_at = 0.0
        self._waiting_for_play_mode = False
        self._last_poll = {}
        self._last_set = {}
        self._set_attempts = {}

    def _emit(self, callback, name, **details):
        if callback is not None:
            callback(name, **details)

    def _start(self, coaster, callback):
        self.reset()
        self.coaster = int(coaster)
        self.phase = 'discover'
        self._emit(callback, 'station_discovery_started', coaster=self.coaster,
                   max_stations=MAX_STATIONS)

    def _fail(self, message, callback):
        if self.phase == 'failed':
            return
        self.error = str(message)
        self.phase = 'failed'
        self._emit(callback, 'station_manual_setup_failed', coaster=self.coaster,
                   detail=self.error)

    def _snapshot(self, client, identity, now):
        getter = getattr(client, 'station_snapshot', None)
        snapshot = getter(identity) if callable(getter) else None
        if snapshot is None:
            return None
        raw, received_at = snapshot
        received_at = float(received_at)
        if not (0 < received_at <= now and now - received_at <= STATUS_MAX_AGE_S):
            return None
        return (int(raw), received_at)

    def ready(self, client, now=None):
        now = time.monotonic() if now is None else now
        return bool(self.phase == 'maintain' and self.stations and
                    all((snapshot := self._snapshot(client, identity, now)) is not None
                        and (snapshot[0] & 0x0002)
                        for identity in self.stations))

    def step(self, client, coaster, now=None, callback=None):
        """Advance a non-blocking discovery/verification step.

        Only call this with fresh play-mode telemetry for the viewed coaster.
        Request/reply correlation is inherited from production NL2Protocol.
        """
        now = time.monotonic() if now is None else float(now)
        if coaster is None:
            return
        if self.coaster != int(coaster):
            self._start(coaster, callback)
        if self.phase == 'failed':
            return

        if self.phase == 'discover':
            if now < self._probe_retry_at:
                return
            identity = (self.coaster, self._probe_index)
            if self._probe_sent:
                snapshot = self._snapshot(client, identity, now)
                if snapshot is not None and snapshot[1] >= self._probe_sent:
                    if self._waiting_for_play_mode:
                        self._emit(callback, 'station_discovery_resumed', station=identity)
                        self._waiting_for_play_mode = False
                    self._probe_retry_at = 0.0
                    self.stations += (identity,)
                    self._emit(callback, 'station_discovered', station=identity,
                               status=f'0x{snapshot[0]:08x}')
                    self._probe_index += 1
                    self._probe_sent = 0.0
                    self._probe_request_id = None
                    self._probe_attempts = 0
                    if self._probe_index >= MAX_STATIONS:
                        self._fail(f'Station scan exceeded {MAX_STATIONS} indices; cannot '
                                   'confirm all stations are manual', callback)
                    return
                get_result = getattr(client, 'request_result', None)
                result = (get_result(self._probe_request_id)
                          if callable(get_result) and self._probe_request_id is not None
                          else None)
                if result is not None and result[0] == 'error':
                    if _temporary_play_mode_error(result[1]):
                        # This is an NL2 RESET_PARK transition, not evidence
                        # of a missing station or invalid coaster. Preserve
                        # every confirmed station and retry the SAME index.
                        # This never authorises manual or dispatch commands.
                        if not self._waiting_for_play_mode:
                            self._emit(callback, 'station_discovery_waiting_for_play',
                                       station=identity, detail=str(result[1]))
                        self._waiting_for_play_mode = True
                        self._probe_sent = 0.0
                        self._probe_request_id = None
                        self._probe_attempts = 0
                        self._probe_retry_at = now + PLAY_MODE_RETRY_S
                        return
                    if not self.stations:
                        self._fail(f'No station 0 on coaster {self.coaster}: {result[1]}', callback)
                    elif _invalid_station_error(result[1]):
                        self.phase = 'maintain'
                        self._emit(callback, 'station_discovery_complete',
                                   coaster=self.coaster, stations=self.stations)
                    else:
                        self._fail(f'Cannot determine whether station {identity} exists: '
                                   f'{result[1]}', callback)
                    return
                if now - self._probe_sent < PROBE_TIMEOUT_S:
                    return
                if self._probe_attempts > PROBE_RETRIES:
                    self._fail(f'No confirmed response for station {identity}; '
                               'not assuming an absent station from a timeout', callback)
                    return
            # First poll or a bounded retry. Never issue any control command
            # to a station until a reply for its exact identity has arrived.
            self._probe_request_id = client.request_station_state(*identity)
            self._probe_sent = now
            self._probe_attempts += 1
            return

        if self.phase == 'maintain':
            for identity in self.stations:
                snapshot = self._snapshot(client, identity, now)
                poll_at = self._last_poll.get(identity, 0.0)
                if not snapshot or now - poll_at >= POLL_INTERVAL_S:
                    client.request_station_state(*identity)
                    self._last_poll[identity] = now
                if snapshot is None:
                    continue
                bits, received_at = snapshot
                if bits & 0x0002:
                    if identity in self._last_set:
                        self._emit(callback, 'station_manual_confirmed', station=identity,
                                   status=f'0x{bits:08x}')
                    self._set_attempts.pop(identity, None)
                    self._last_set.pop(identity, None)
                    continue
                last_set = self._last_set.get(identity, 0.0)
                if last_set and received_at <= last_set:
                    # Wait for status strictly newer than our command.
                    continue
                if last_set and now - last_set < MANUAL_RETRY_S:
                    continue
                attempts = self._set_attempts.get(identity, 0)
                if attempts >= MAX_MANUAL_COMMANDS:
                    self._fail(f'NL2 did not confirm manual mode at {identity} '
                               f'after {attempts} commands', callback)
                    return
                client.set_manual_mode(True, *identity)
                self._last_set[identity] = now
                self._set_attempts[identity] = attempts + 1
                self._emit(callback, 'station_manual_requested', station=identity,
                           attempt=attempts + 1, status=f'0x{bits:08x}')
