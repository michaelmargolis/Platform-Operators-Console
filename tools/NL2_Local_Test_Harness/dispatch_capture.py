# dispatch_capture.py ver 1
"""Read-only, request-correlated NL2 dispatch readiness capture.

The recorder stores observations, not inferred causes. The published NL2 API
cannot report block occupancy or an interlock failure reason directly.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
import time

try:
    from .harness_core import station_flags
except ImportError:  # running harness.py directly
    from harness_core import station_flags

COLUMNS = (
    'time_local', 'elapsed_s', 'event', 'request_id', 'request_msg_id',
    'reply_msg_id', 'coaster', 'station', 'status_hex', 'station_bits',
    'can_dispatch', 'can_lock_flyer', 'can_unlock_flyer', 'train_present',
    'view_train_here', 'telemetry_frame', 'telemetry_coaster',
    'telemetry_train', 'telemetry_speed_mps', 'telemetry_state',
    'nearest_coaster', 'nearest_station', 'note',
)

class DispatchRecorder:
    """Write full polling samples plus human-readable state transition summary."""

    def __init__(self, directory: Path, coaster: int, stations, *, mono=None, wall=None):
        self.start_mono = time.monotonic() if mono is None else float(mono)
        self.start_wall = datetime.now() if wall is None else wall
        self.coaster = int(coaster)
        self.stations = tuple(sorted(set(int(x) for x in stations)))
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        stem = 'nl2_dispatch_%s' % self.start_wall.strftime('%Y%m%d_%H%M%S_%f')
        self.csv_path = directory / (stem + '.csv')
        self.summary_path = directory / (stem + '_summary.txt')
        self.file = self.csv_path.open('w', newline='', encoding='utf-8')
        self.writer = csv.DictWriter(self.file, fieldnames=COLUMNS)
        self.writer.writeheader()
        self.row_count = 0
        self.lock_events = []
        self.ready_events = []
        self.ack_events = []
        self.milestones = []
        self.last_station = {}
        self.status_samples = []
        self.closed = False
        self.record('CAPTURE_START', note='coaster=%d stations=%s; 4 Hz polling when active; NL2 block occupancy unavailable' % (self.coaster, list(self.stations)), when=self.start_mono)

    def _stamp(self, when):
        when = time.monotonic() if when is None else float(when)
        dt = self.start_wall + timedelta(seconds=when - self.start_mono)
        return when, dt.isoformat(timespec='milliseconds'), '%.3f' % (when-self.start_mono)

    def record(self, kind, *, when=None, request_id='', request_msg_id='', reply_msg_id='',
               coaster='', station='', raw=None, telemetry=None, nearest=None, note=''):
        if self.closed:
            return
        when, wall, elapsed = self._stamp(when)
        fields = {k:'' for k in COLUMNS}
        fields.update(time_local=wall, elapsed_s=elapsed, event=str(kind),
                      request_id=request_id, request_msg_id=request_msg_id,
                      reply_msg_id=reply_msg_id, coaster=coaster, station=station,
                      note=str(note))
        if raw is not None:
            flags = station_flags(raw)
            fields.update(status_hex='0x%08X' % raw,
                          station_bits=' '.join('%s=%d' % (k,int(v)) for k,v in flags.items()),
                          can_dispatch=int(flags['CAN_DISPATCH']),
                          can_lock_flyer=int(flags['CAN_LOCK_FLYER']),
                          can_unlock_flyer=int(flags['CAN_UNLOCK_FLYER']),
                          train_present=int(flags['TRAIN_PRESENT']),
                          view_train_here=int(flags['VIEW_TRAIN_HERE']))
        if telemetry is not None:
            fields.update(telemetry_frame=telemetry.frame,
                          telemetry_coaster=telemetry.coaster_index,
                          telemetry_train=telemetry.train,
                          telemetry_speed_mps='%.4f'%telemetry.speed,
                          telemetry_state=telemetry.state)
        if nearest is not None:
            fields['nearest_coaster'],fields['nearest_station']=nearest
        self.writer.writerow(fields)
        self.file.flush()
        self.row_count+=1
        return when

    def command(self, msgid:int, reqid:int, payload:bytes, *, when=None, automatic=False):
        when = time.monotonic() if when is None else when
        coaster=station=''
        if msgid in (14,16,17,18,19,20,21) and len(payload)>=8:
            import struct
            coaster,station=struct.unpack('>ii',payload[:8])
        action='AUTO_TX' if automatic else 'CONTROL_TX'
        self.record(action,when=when,request_id=reqid,request_msg_id=msgid,
                    coaster=coaster,station=station,note=payload.hex(' '))
        if msgid==21 and len(payload)>=9 and payload[8]==0 and not automatic:
            self.lock_events.append((when,coaster,station,reqid))
            self.milestones.append((when,'LOCK command coaster=%s station=%s req=%s'%(coaster,station,reqid)))
        if msgid==17 and not automatic:
            self.milestones.append((when,'DISPATCH command coaster=%s station=%s req=%s'%(coaster,station,reqid)))

    def reply(self, msgid:int, reqid:int, request_msgid:int, payload:bytes, *, when=None, coaster='', station=''):
        when = time.monotonic() if when is None else when
        if msgid in (1,2):
            note = 'OK' if msgid==1 else payload.decode('utf-8','replace')
            self.record('CONTROL_REPLY' if request_msgid not in (3,5,11,14) else 'QUERY_REPLY',
                        when=when,request_id=reqid,request_msg_id=request_msgid,
                        reply_msg_id=msgid,coaster=coaster,station=station,note=note)
            if request_msgid in (17,21):
                self.ack_events.append((when,request_msgid,reqid,msgid,note))
                self.milestones.append((when,'REPLY to %s req=%s: %s'%('LOCK' if request_msgid==21 else 'DISPATCH',reqid,note)))
        elif msgid==15 and len(payload)>=4:
            import struct
            raw=struct.unpack('>I',payload[:4])[0]
            self.station_sample(coaster,station,raw,when=when,request_id=reqid)
        elif msgid==2:
            self.record('ERROR',when=when,request_id=reqid,note=payload.decode('utf-8','replace'))

    def station_sample(self,coaster,station,raw,*,when=None,request_id=''):
        when = time.monotonic() if when is None else when
        key=(int(coaster),int(station))
        prev=self.last_station.get(key)
        self.record('STATION_SAMPLE',when=when,request_id=request_id,
                    request_msg_id=14,reply_msg_id=15,coaster=key[0],station=key[1],raw=raw)
        self.status_samples.append((when,key,station_flags(raw)))
        if prev!=raw:
            old=station_flags(prev) if prev is not None else {}
            new=station_flags(raw)
            delta='initial' if prev is None else ', '.join('%s:%d>%d'%(k,int(old[k]),int(v)) for k,v in new.items() if old[k]!=v)
            self.record('STATION_CHANGE',when=when,coaster=key[0],station=key[1],raw=raw,note=delta)
            self.milestones.append((when,'STATION %s status=0x%08X changes=%s'%(key,raw,delta)))
            if new['CAN_DISPATCH'] and not (prev is not None and old['CAN_DISPATCH']):
                self.ready_events.append((when,key))
            self.last_station[key]=raw

    def telemetry_sample(self,tm,*,when=None,request_id=''):
        self.record('TELEMETRY_SAMPLE',when=when,request_id=request_id,
                    request_msg_id=5,reply_msg_id=6,telemetry=tm)

    def nearest_sample(self,coaster,station,*,when=None,request_id=''):
        self.record('NEAREST_SAMPLE',when=when,request_id=request_id,request_msg_id=11,
                    reply_msg_id=12,nearest=(coaster,station))

    def marker(self,message,*,when=None):
        when=time.monotonic() if when is None else when
        self.record('OPERATOR_MARKER',when=when,note=message)
        self.milestones.append((when,'OPERATOR: '+message))

    def close(self,*,when=None):
        if self.closed:
            return
        when=time.monotonic() if when is None else when
        self.record('CAPTURE_STOP',when=when)
        self.file.close()
        self.closed=True
        with self.summary_path.open('w',encoding='utf-8') as f:
            f.write('NoLimits 2 dispatch readiness capture\n')
            f.write('Captured %.1f seconds | %d events | coaster=%d stations=%s\n\n'%(
                when-self.start_mono,self.row_count,self.coaster,list(self.stations)))
            f.write('Interpretation: station flags show command AVAILABILITY only, not physical lock state.\n')
            f.write('The public NL2 API does not provide block occupancy or a reason for dispatch inhibition.\n')
            f.write('Manually mark control-panel readiness and other train movement where possible.\n\n')
            f.write('Lock to subsequent first dispatch-available observation, for each lock command:\n')
            for lock_time,coaster,station,reqid in self.lock_events:
                key=(coaster,station)
                states=[(t,flags) for t,k,flags in self.status_samples if k==key and t>=lock_time]
                f.write('  req=%d station=(%d,%d):\n'%(reqid,coaster,station))
                for label,bit,want in (
                    ('CAN_LOCK_FLYER became unavailable','CAN_LOCK_FLYER',False),
                    ('CAN_UNLOCK_FLYER available','CAN_UNLOCK_FLYER',True),
                    ('CAN_DISPATCH available','CAN_DISPATCH',True),
                ):
                    observations=[t for t,flags in states if flags[bit]==want]
                    if observations:
                        f.write('     %-38s +%.3fs\n'%(label,observations[0]-lock_time))
                    else:
                        f.write('     %-38s not observed\n'%label)
            if not self.lock_events:
                f.write('  No raw-state-0 flyer lock command recorded.\n')
            f.write('\nTimeline of key changes (seconds since start):\n')
            for t,desc in self.milestones:
                f.write('  %8.3f %s\n'%(t-self.start_mono,desc))
            f.write('\nFull 4 Hz samples and request/reply identifiers are in the companion CSV.\n')
