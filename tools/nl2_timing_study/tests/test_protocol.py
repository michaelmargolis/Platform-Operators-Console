import struct
import time
import pytest
from tools.nl2_timing_study.protocol import TimingProtocol, ReceiptQueue


class Sink:
    def __init__(self):self.sent=[]
    def sendall(self,packet):self.sent.append(packet)


def body(frame):
    return struct.pack(TimingProtocol.TELEMETRY_FORMAT,
                       3,frame,1,0,0,0,0,0, 1.,2.,3.,4.,0.,0.,0.,1.,0.,1.,0.)


def test_nonlocal_address_refused():
    with pytest.raises(ValueError,match="localhost"):TimingProtocol("192.168.0.4")


def test_unmodified_packets_and_true_receipt_queue_time():
    p=TimingProtocol();p.is_connected=True;p._sock=Sink()
    p.request_telemetry()
    data=p._sock.sent[0]
    msg,req,size=struct.unpack(">HIH",data[1:9])
    assert msg==5 and size==0 and data[:1]==b"N" and data[-1:]==b"L"
    p._rx_queue.put((6,req,body(5)))
    time.sleep(.005)
    p.service()
    recs=p.drain_study_records()
    tx,rx=recs
    assert rx["receive_ns"]>=tx["send_ns"]
    assert rx["process_ns"]-rx["receive_ns"]>=1_000_000
    assert p.latest_telemetry.received_at>=rx["receive_ns"]/1e9
    assert rx["telemetry"].frame==5


def test_multiple_replies_all_retained_not_only_latest():
    p=TimingProtocol();p.is_connected=True;p._sock=Sink()
    for frame in [1,2,2,3]:
        p.request_telemetry()
        req=struct.unpack(">HIH",p._sock.sent[-1][1:9])[1]
        p._rx_queue.put((6,req,body(frame)))
    p.service()
    rs=p.drain_study_records()
    assert [r["telemetry"].frame for r in rs if r["kind"]=="rx"]==[1,2,2,3]
    assert p.latest_telemetry.frame==3


def test_overflow_explicit_not_silent():
    p=TimingProtocol();p.MAX_RECORDS=2
    for i in range(4):p._record({"i":i})
    assert p.study_overflow==2 and len(p.drain_study_records())==2


def test_invalid_size_does_not_duplicate_cached_telemetry():
    p=TimingProtocol()
    p._rx_queue.put((6,1,body(5)))
    p._rx_queue.put((6,2,b"bad"))
    p.service()
    r=p.drain_study_records()
    assert r[0]["telemetry"].frame==5 and r[1]["telemetry"] is None
