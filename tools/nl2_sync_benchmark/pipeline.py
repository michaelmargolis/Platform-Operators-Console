# pipeline.py ver 2
"""Run production hot-path implementations with in-memory IO substitutes.

No POC QObject/GUI or hardware constructor is created. Setup, load/reset,
arming/parking, external waiting and rendering are explicitly NOT benchmarked.
"""
from __future__ import annotations
from contextlib import ExitStack, contextmanager
import inspect
import math
import struct
import sys
import time as real_time
from unittest.mock import patch

from config.platforms import PLATFORMS
from core.platform_controller import SopPlatformBackend, PlatformController
from core.telemetry_config import TelemetryConfig
from core.types import ExperienceState, PlatformState, PlatformMotionState
from core.digital_twin import DigitalTwinSender
from output.festo_itf import Festo
from output.muscle_output import MuscleOutput
from sims.nolimits2 import sim as sim_module, nl2_protocol as protocol_module, station_modes
from sims.nolimits2.variant_store import VariantStore
from tools.nl2_sync.matcher import Matcher, UnityProfile


class NoNetwork:
    """Defence-in-depth CPython audit guard, independent of the null sinks."""
    def __init__(self):self.enabled=False;self.attempts=0
    def audit(self,event,args):
        if self.enabled and event in ('socket.__new__','socket.connect','socket.sendto','socket.getaddrinfo'):
            self.attempts+=1
            raise RuntimeError('Network access forbidden by offline benchmark: '+event)
    def __enter__(self):
        sys.addaudithook(self.audit);self.enabled=True;return self
    def __exit__(self,*exc):self.enabled=False


class ReplayClock:
    def __init__(self):self.now=1000.
    def monotonic(self):return self.now
    def monotonic_ns(self):return int(self.now*1e9)
    def __getattr__(self,name):return getattr(real_time,name)


class NullFesto(Festo):
    """Use real EasyIP serialization, discard bytes; NO Festo.__init__ call."""
    def __init__(self):
        self.wait=False;self.out_pressures=[0]*6;self.packet_count=0;self.bytes=0
    def _output_festo_packet(self,packet,wait_ack):
        if wait_ack:raise RuntimeError('Offline benchmark cannot wait for hardware ACK')
        data=packet.pack();self.packet_count+=1;self.bytes+=len(data)
        return None
    def __del__(self):pass
    def get_ip_address(self):return 'OFFLINE-NO-SOCKET'
    def get_pressure(self):return list(self.out_pressures[:6])
    def enable_poll_pressure(self,state):
        if state:raise RuntimeError('Offline benchmark forbids hardware polling')
    def set_wait_ack(self,state):
        if state:raise RuntimeError('Offline benchmark forbids hardware acknowledgements')


class NullSocket:
    def __init__(self,*args):self.bytes=0;self.packets=0
    def setblocking(self,value):pass
    def sendto(self,data,address):self.bytes+=len(data);self.packets+=1;return len(data)
    def close(self):pass


class ReplayProtocol(protocol_module.NL2Protocol):
    def __init__(self,identity,station):
        super().__init__('OFFLINE-NO-SOCKET')
        self.is_connected=True;self.version='offline-replay'
        self.identity=identity;self.station=tuple(station)
        self.current_coaster_index,self.current_station_index=self.station
        self.sent_commands=0
    def connect(self):raise RuntimeError('Offline client must never connect')
    def _send(self,data):
        # Production message builders still run. Only writes are replaced.
        msg,req,size=struct.unpack('>HIH',data[1:9]);self.sent_commands+=1
        if msg==self.N_MSG_GET_STATION_STATE:
            self._rx_queue.put((self.N_MSG_STATION_STATE,req,struct.pack('>I',2)))
        elif msg==self.N_MSG_GET_CURRENT_COASTER_AND_NEAREST_STATION:
            self._rx_queue.put((self.N_MSG_INT_VALUE_PAIR,req,struct.pack('>ii',*self.station)))
        elif msg==self.N_MSG_GET_VERSION:
            self._rx_queue.put((self.N_MSG_VERSION,req,bytes([2,6,8,1])))
        elif msg!=self.N_MSG_GET_TELEMETRY:
            raise RuntimeError('Unexpected control command in steady-running benchmark: '+str(msg))
    def disconnect(self):self.is_connected=False


class TimedBackend(SopPlatformBackend):
    def reset_times(self):self.kinematics_ns=0;self.pressure_ns=0;self.ik_calls=0
    def _lengths_for_transform(self,transform):
        start=real_time.perf_counter_ns()
        try:return super()._lengths_for_transform(transform)
        finally:self.kinematics_ns+=real_time.perf_counter_ns()-start
    def _apply_lengths(self,lengths):
        start=real_time.perf_counter_ns()
        try:return super()._apply_lengths(lengths)
        finally:self.pressure_ns+=real_time.perf_counter_ns()-start


class Pipeline:
    def __init__(self,root,platform_key,park_key,trace,reference,settings=None,sync=True,
                 twin=True,payload=1,intensity=100.,variant=None,gain_percent=None):
        self.clock=ReplayClock();self.patches=ExitStack();self.started=False
        for module in (sim_module,protocol_module,station_modes):
            self.patches.enter_context(patch.object(module,'time',self.clock))
        try:
            if 'festo_interface' not in inspect.signature(MuscleOutput).parameters:
                raise RuntimeError('Installed MuscleOutput lacks the safe injected sink (POC v163+ required). No hardware fallback is allowed.')
            self.sink=NullFesto()
            self.backend=TimedBackend(PLATFORMS[platform_key]['sop_config_module'],festo_interface=self.sink)
            self.backend.reset_times()
            self.backend.set_payload_level(payload)
            orig_ik=self.backend.k.inverse_kinematics
            def counted_ik(*args,**kwargs):
                self.backend.ik_calls+=1
                return orig_ik(*args,**kwargs)
            self.backend.k.inverse_kinematics=counted_ik
            identity=trace.meta['view_identity'];station=trace.meta['departure_station']
            self.client=ReplayProtocol(identity,station)
            store=VariantStore.load_readonly(platform_key) if variant else VariantStore.for_capture(platform_key)
            self.sim=sim_module.Sim(client=self.client,platform_key=platform_key,variant_store=store,capture_mode=True)
            selected=self.sim._experiences.index(variant) if variant else self.sim._park_keys.index(park_key)
            if self.sim._park_keys[selected]!=park_key:raise ValueError('Selected variant belongs to a different park')
            self.sim._selected=self.sim._loaded_index=selected
            self.sim._apply_loaded_variant(selected)
            self.snapshot=self.sim.loaded_variant_snapshot()
            self.telem=TelemetryConfig(root/'sims/nolimits2/telemetry_config.ini')
            self.telem.set_variant_gain_scales(gain_percent or [self.snapshot['gain_scale_pct'][a] for a in ('surge','sway','heave','roll','pitch','yaw')])
            self.telem.set_angular_washout_overrides(self.snapshot['roll_washout'],self.snapshot['pitch_washout'],reset=True)
            self.telem.set_temporary_classical_z(self.snapshot['heave_mode']=='vertical_g')
            self.backend.set_ride_neutral_height_delta_mm(self.snapshot['neutral_height_delta_mm'])
            self.intensity=intensity
            self.sim._connected_sync_started=True;self.sim._data_connected=True
            self.sim.state=ExperienceState.RUNNING;self.sim._initial_sync=False
            self.sim._view_train_departed=True;self.sim._departure_motion_seen=True
            self.sim._ride_station=tuple(station);self.sim._ride_train=tuple(identity[:2])
            self.sim._ride_dispatch_at=self.clock.now;self.sim._aux_dispatch_authorized=False
            manager=self.sim._station_modes
            manager.coaster=station[0];manager.phase='maintain';manager.stations=(tuple(station),)
            self.client._station_status_by_identity[tuple(station)]=(2,self.clock.now)
            self.platform=PlatformController(self.backend)
            self.platform.state=PlatformState.ACTIVATED;self.platform.parking_state=PlatformMotionState.UNPARKED
            self.matcher=Matcher(reference,settings) if sync else None
            self.profile=UnityProfile(reference)
            self.last_match=None
            self.twin=DigitalTwinSender('127.0.0.1' if twin else '-',socket_factory=NullSocket,time_func=self.clock.monotonic)
            self.source_count=0
        except BaseException:
            self.patches.close();raise

    def close(self):
        self.patches.close()

    def feed(self,packets):
        # Input bytes have been prepared before the timed loop. Equivalent to
        # listener delivery, not artificially included in main-thread CPU time.
        for packet in packets:self.client._rx_queue.put((self.client.N_MSG_TELEMETRY,0,packet))
        self.source_count+=len(packets)

    def tick(self,elapsed_s):
        self.clock.now=1000.+elapsed_s
        self.backend.reset_times()
        t0=real_time.perf_counter_ns()
        self.sim.service()
        t1=real_time.perf_counter_ns()
        self.platform.service(.05)
        sample=self.sim.read()
        t2=real_time.perf_counter_ns()
        tm=self.client.latest_telemetry
        gains=(1.,)*6
        if self.matcher is not None and elapsed_s>=0 and tm is not None and sample.telemetry_valid:
            self.last_match=self.matcher.update((tm.pos_x,tm.pos_y,tm.pos_z),
                (tm.quat_x,tm.quat_y,tm.quat_z,tm.quat_w),elapsed_s,tm.frame,tm.speed)
            gains=self.profile.lookup(self.last_match.profile_phase_s)
        t3=real_time.perf_counter_ns()
        norm=self.telem.process(sample.transform,self.intensity,.05) if sample.telemetry_valid else (0.,)*6
        # Unity factors exercise the proposed lookup/multiply boundary AFTER
        # existing washout. No optimized profile or anticipatory cue is loaded.
        transform=tuple(x*g for x,g in zip(norm,gains)) if self.matcher is not None else norm
        t4=real_time.perf_counter_ns()
        applied=sample.telemetry_valid and sample.state is ExperienceState.RUNNING
        if applied:self.platform.move(transform)
        t5=real_time.perf_counter_ns()
        if self.twin.enabled:
            cfg=self.backend.cfg
            lengths=self.backend.muscle_lengths
            self.twin.send_snapshot(platform=self.sim.platform_key,platform_state='ACTIVATED',
                geometry_id=self.backend.geometry_id,channel_to_pair=self.backend.channel_to_pair,
                simulator_state=sample.state.name,telemetry_valid=sample.telemetry_valid,
                transform_applied=applied,dtop_load_kg_per_muscle=self.backend.dto_p.selected_load,
                transform=transform,muscle_length_mm=lengths,
                contraction_pct=[100.*(cfg.MUSCLE_MAX_LENGTH-x)/cfg.MUSCLE_LENGTH_RANGE for x in lengths],
                command_pressure_mbar=self.backend.sent_pressures,piston_disengaged=None,
                parking_state='UNPARKED',
                applied_transform_real=self.backend.last_applied_real_transform if applied else None)
        t6=real_time.perf_counter_ns()
        if self.twin.last_error:raise RuntimeError(self.twin.last_error)
        return dict(elapsed_s=elapsed_s,total_ms=(t6-t0)/1e6,service_ms=(t1-t0)/1e6,
                    read_transform_ms=(t2-t1)/1e6,sync_lookup_ms=(t3-t2)/1e6,
                    washout_ms=(t4-t3)/1e6,motion_ms=(t5-t4)/1e6,twin_ms=(t6-t5)/1e6,
                    kinematics_ms=self.backend.kinematics_ns/1e6,pressure_ms=self.backend.pressure_ns/1e6,
                    ik_calls=self.backend.ik_calls,valid=sample.telemetry_valid,applied=applied,
                    workspace_limited=getattr(self.backend,'workspace_limited',False),
                    final_length_clip=bool(self.backend.last_length_clip),
                    match_status=('no_fresh_telemetry' if self.matcher is not None and not sample.telemetry_valid
                                  else self.last_match.status if self.last_match else ('disabled' if self.matcher is None else 'waiting')),
                    match_phase_s=self.last_match.phase_s if self.last_match else None,
                    profile_phase_s=self.last_match.profile_phase_s if self.last_match else None,
                    # Keep phase/lookup disagreement visible even if no sync is
                    # technically lost. A many-second offset would mistime a
                    # non-unity gain profile despite matching XYZ successfully.
                    profile_lag_s=(self.last_match.phase_s-self.last_match.profile_phase_s)
                                  if self.last_match else None,
                    match_position_residual_m=self.last_match.position_residual_m if self.last_match else None)
