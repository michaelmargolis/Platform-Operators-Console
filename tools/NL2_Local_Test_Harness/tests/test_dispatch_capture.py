# test_dispatch_capture.py ver 1
import csv
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dispatch_capture import DispatchRecorder

class TestDispatchCapture(unittest.TestCase):
    def test_writes_correlated_events_and_milestones(self):
        with tempfile.TemporaryDirectory() as td:
            r=DispatchRecorder(Path(td),0,[0,1],mono=10.0,wall=datetime(2026,11,5,8,0,0))
            r.station_sample(0,0,0x1A52,when=10.10,request_id=3)
            r.station_sample(0,1,0x0802,when=10.10,request_id=4)
            r.command(21,7,b'\0'*9,when=11.0)
            r.reply(1,7,21,b'',when=11.05,coaster=0,station=0)
            r.station_sample(0,0,0x1C12,when=11.25,request_id=8)
            r.telemetry_sample(SimpleNamespace(frame=50,coaster_index=0,train=0,speed=0.0,state=3),when=11.3,request_id=9)
            r.marker('Other train crossed brake',when=12.0)
            r.station_sample(0,0,0x1C16,when=13.2,request_id=10)
            r.command(17,11,b'\0'*8,when=13.3)
            r.reply(1,11,17,b'',when=13.35,coaster=0,station=0)
            r.close(when=14.0)
            with r.csv_path.open(newline='',encoding='utf-8') as f:
                rows=list(csv.DictReader(f))
            self.assertTrue(any(e['event']=='STATION_SAMPLE' and e['request_id']=='10' and e['can_dispatch']=='1' for e in rows))
            self.assertTrue(any(e['event']=='CONTROL_TX' and e['request_msg_id']=='21' for e in rows))
            self.assertTrue(any(e['event']=='OPERATOR_MARKER' for e in rows))
            self.assertTrue(any(e['event']=='TELEMETRY_SAMPLE' and e['telemetry_train']=='0' for e in rows))
            summary=r.summary_path.read_text(encoding='utf-8')
            self.assertIn('CAN_DISPATCH available',summary)
            self.assertIn('+2.200s',summary)
            self.assertIn('Other train crossed brake',summary)
            self.assertIn('block occupancy',summary)

    def test_no_lock_does_not_claim_interlock_cause(self):
        with tempfile.TemporaryDirectory() as td:
            r=DispatchRecorder(Path(td),0,[0],mono=0,wall=datetime(2026,11,5))
            r.station_sample(0,0,0,when=0.1)
            r.close(when=1.1)
            self.assertIn('No raw-state-0 flyer lock command',r.summary_path.read_text())

if __name__=='__main__': unittest.main()
