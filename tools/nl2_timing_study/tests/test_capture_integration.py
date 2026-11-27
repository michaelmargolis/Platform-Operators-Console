"""Real localhost TCP round trips against a deterministic fake NL2 server.

This exercises the production adapter/parser; it is NOT a live NoLimits test.
"""
from contextlib import contextmanager
import copy
import csv
import json
import math
from pathlib import Path
import socket
import struct
import threading
import time
import pytest
from tools.nl2_timing_study.__main__ import main
from sims.nolimits2 import ride_config


class FakeNL2:
    def __init__(self, no_return=False, reject_dispatch=False):
        self.socket=socket.socket()
        self.socket.bind(("127.0.0.1",0))
        self.socket.listen()
        self.socket.settimeout(.1)
        self.port=self.socket.getsockname()[1]
        self.stop=threading.Event()
        self.start=time.monotonic()
        self.manual={0:False,1:False}
        self.dispatch_at=None
        self.loaded=""
        self.paused=False
        self.flyer=False
        self.locked_at=None
        self.reset_at=None
        self.commands=[]
        self.resets=0
        self.departures=0
        self.no_return=no_return
        self.reject_dispatch=reject_dispatch
        self.errors=[]
        self.thread=threading.Thread(target=self.serve,daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(3)
        self.socket.close()
        assert not self.errors, self.errors

    def exact(self,c,n):
        data=b""
        while len(data)<n and not self.stop.is_set():
            try:block=c.recv(n-len(data))
            except socket.timeout:continue
            if not block:raise EOFError()
            data+=block
        if len(data)!=n:raise EOFError()
        return data

    def serve(self):
        while not self.stop.is_set():
            try:c,_=self.socket.accept()
            except socket.timeout:continue
            except OSError:break
            with c:
                c.settimeout(.1)
                try:
                    while not self.stop.is_set():
                        first=self.exact(c,1)
                        assert first==b"N"
                        msg,req,size=struct.unpack(">HIH",self.exact(c,8))
                        payload=self.exact(c,size) if size else b""
                        assert self.exact(c,1)==b"L"
                        self.commands.append((msg,payload))
                        response,body=self.handle(msg,payload)
                        c.sendall(b"N"+struct.pack(">HIH",response,req,len(body))+body+b"L")
                except (EOFError,ConnectionError,OSError):pass
                except BaseException as e:self.errors.append(repr(e))

    def handle(self,msg,data):
        now=time.monotonic()
        age=now-self.dispatch_at if self.dispatch_at else -1
        running=age>=0 and (age<1.5 or self.no_return)
        in_station=not running or age<.15
        if msg==3:return 4,bytes([2,6,4,0])
        if msg==24:
            self.loaded=data[1:].decode();self.paused=bool(data[0])
            self.flyer="Flying Beast" in self.loaded
            self.manual={0:False,1:False};self.dispatch_at=None;self.locked_at=None
            return 1,b""
        if msg==28:
            self.resets+=1;self.dispatch_at=None;self.locked_at=None
            self.paused=bool(data[0]);self.reset_at=now;self.start=now
            return 1,b""
        if msg==27:self.paused=bool(data[0]);return 1,b""
        if msg==16:
            coaster,station,value=struct.unpack(">ii?",data)
            self.manual[station]=value;return 1,b""
        if msg==21:
            coaster,station,value=struct.unpack(">iiB",data)
            assert value==0 and station==0
            self.locked_at=now;return 1,b""
        if msg==17:
            assert self.manual[0]
            if self.flyer:assert self.manual[1] and self.locked_at is not None
            if self.reject_dispatch:return 2,b"Test dispatch rejection"
            self.departures+=1;self.dispatch_at=now;return 1,b""
        if msg==11:return 12,struct.pack(">ii",0,0)
        if msg==14:
            coaster,station=struct.unpack(">ii",data)
            if self.reset_at and now-self.reset_at<.07:return 2,b"Not in play mode"
            if coaster!=0 or station>int(self.flyer):return 2,b"Invalid index"
            if station==1:
                flags=0x800 | (2 if self.manual[1] else 0)
            elif in_station:
                if self.flyer:
                    flags=0x1A50 if self.locked_at is None else 0x1E10 if now-self.locked_at<.12 else 0x1C14
                else:flags=0x1854
                if self.manual[0]:flags|=2
            else:flags=2 if self.manual[0] else 0
            return 15,struct.pack(">I",flags)
        if msg==5:
            frame=int((now-self.start)*60)
            speed=15. if running else 0.
            u=max(0.,min(1.,age/1.5))
            x=30.*math.sin(math.pi*u) if running else 0.
            z=10.*math.sin(2*math.pi*u) if running else 0.
            values=(7 if self.paused else 3,frame,1,0,0,0,0,0,
                    speed,x,10.,z,0.,0.,0.,1.,0.,1.,0.)
            return 6,struct.pack(">IIIIIIIIfffffffffff",*values)
        return 1,b""


@contextmanager
def local_park(monkeypatch,tmp_path,key):
    parks=copy.deepcopy(ride_config.PARKS)
    monkeypatch.setattr(ride_config,"PARKS",parks)
    rel=Path(parks[key]["path"]).relative_to(Path(ride_config.BASE_RIDE_PATH))
    root=tmp_path/"parks"
    park=root/rel;park.parent.mkdir(parents=True);park.write_bytes(b"FAKE PARK - TEST ONLY")
    yield root


def cli(tmp_path,server,key,root,repeats=1):
    return ["capture","--rides",key,"--repeats",str(repeats),"--machine","FAKE_TEST_PC",
            "--conditions","fake NL2 protocol test, not real simulator", "--port",str(server.port),
            "--parks-root",str(root),"--output",str(tmp_path/"captures"),"--prepare-timeout","8",
            "--depart-timeout","3","--max-duration","4","--pre-roll","0.1","--tail","0.1",
            "--between-runs","0"]


def test_repeat_capture_real_adapter_and_protocol(monkeypatch,tmp_path):
    with local_park(monkeypatch,tmp_path,"blackbird") as root:
        server=FakeNL2()
        try:code=main(cli(tmp_path,server,"blackbird",root,repeats=2))
        finally:server.close()
    assert code==0
    assert server.resets>=2 and server.departures==2
    trials=list((tmp_path/"captures").rglob("*_trial.json"))
    assert len(trials)==2
    for path in trials:
        m=json.loads(path.read_text())
        assert m["status"]=="complete",m
        assert not m["quality_issues"],m
        marks=m["marks"]
        assert marks["dispatch_tx"]<=marks["first_motion"]<marks["view_train_returned"]
        with (path.parent/m["files"]["capture"]).open() as f:rows=list(csv.DictReader(f))
        assert len(rows)>20 and "norm_roll" in rows[0]
        from tools.analyze_nl2_csv import read_capture
        from tools.sim_player.recording import _load_events
        csv_path=path.parent/m["files"]["capture"]
        assert len(read_capture(csv_path))==len(rows)
        sync=json.loads((path.parent/m["files"]["player_sync"]).read_text())
        events,warning=_load_events(path.parent/m["files"]["events"], sync, csv_path,
                                   float(rows[-1]["elapsed_s"]))
        assert events and not warning
        assert any(abs(e.seconds-marks["view_train_returned"])<.001 for e in events)
        with (path.parent/m["files"]["timing"]).open() as f:raw=list(csv.DictReader(f))
        assert len(raw)>=len(rows)-1
        assert all(float(r["queue_delay_ms"])>=0 for r in raw)
    assert list((tmp_path/"captures").rglob("report.html"))


def test_flying_beast_uses_both_manual_and_existing_lock(monkeypatch,tmp_path):
    with local_park(monkeypatch,tmp_path,"flyingbeast") as root:
        server=FakeNL2()
        try:code=main(cli(tmp_path,server,"flyingbeast",root))
        finally:server.close()
    assert code==0
    assert server.manual[0] and server.manual[1]
    assert server.departures==1
    assert len([c for c in server.commands if c[0]==21])==1


def test_failed_trial_retained_and_next_attempt_runs(monkeypatch,tmp_path):
    with local_park(monkeypatch,tmp_path,"blackbird") as root:
        server=FakeNL2(no_return=True)
        args=cli(tmp_path,server,"blackbird",root,repeats=2)
        args[args.index("--max-duration")+1]="2"
        try:code=main(args)
        finally:server.close()
    assert code==1 and server.departures==2
    trials=[json.loads(p.read_text()) for p in (tmp_path/"captures").rglob("*_trial.json")]
    assert len(trials)==2 and all(t["status"]=="incomplete" for t in trials)
    reports=list((tmp_path/"captures").rglob("report.json"))
    assert reports and not json.loads(reports[0].read_text())["comparisons"]


def test_dispatch_rejection_never_counts_complete(monkeypatch,tmp_path):
    with local_park(monkeypatch,tmp_path,"blackbird") as root:
        server=FakeNL2(reject_dispatch=True)
        try:code=main(cli(tmp_path,server,"blackbird",root))
        finally:server.close()
    assert code==1
    m=json.loads(next((tmp_path/"captures").rglob("*_trial.json")).read_text())
    assert m["status"]=="incomplete" and "dispatch_rejected" in m["quality_issues"]
