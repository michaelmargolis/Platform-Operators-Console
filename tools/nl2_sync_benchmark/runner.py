# runner.py ver 1
"""Two replay modes: deterministic throughput and actual 20Hz scheduling.

The same prerecorded packet arrivals feed both baseline and B+C runs. Rows are
loaded/encoded before timing. Output files are written after each replay, not
inside the measured tick. Realtime lateness still includes Python bookkeeping.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import asdict,dataclass
import hashlib
import json
import logging
import math
from pathlib import Path
import struct
import time
import numpy as np
from tools.nl2_timing_study.analysis import read_trial,group_key,html_table,markdown_table
from tools.nl2_timing_study.common import write_json,write_table,unique_id,sha256
from tools.nl2_timing_study.quality import choose_reference
from tools.nl2_sync.matcher import Reference,Settings
from tools.nl2_sync.host import host_details
from .pipeline import Pipeline,NoNetwork

INT_FIELDS=('state','frame','view_mode','coaster_index','coaster_style','train','car','seat')
FLOAT_FIELDS=('speed','pos_x','pos_y','pos_z','quat_x','quat_y','quat_z','quat_w','g_force_x','g_force_y','g_force_z')


@dataclass
class TrialStub:
    path: Path
    meta: dict
    summary: dict
    issues: list
    @property
    def machine(self):
        return self.meta.get('machine', {}).get('host') or self.meta.get('machine', {}).get('label', 'unknown')
    @property
    def name(self):
        stem=self.path.stem
        return stem[:-6] if stem.endswith('_trial') else stem


def collect(inputs,rides):
    paths=set()
    for path in inputs:
        path=Path(path).resolve()
        if path.is_file() and path.name.endswith('_trial.json'):paths.add(path)
        elif path.is_dir():paths.update(path.rglob('*_trial.json'))
        elif path.name=='session.json' and path.is_file():paths.update(path.parent.rglob('*_trial.json'))
        else:raise ValueError('Expected extracted session directory or *_trial.json: '+str(path))
    traces=[];skipped=[];seen=set()
    for p in sorted(paths):
        try:
            trace=read_trial(p)
            if rides and trace.meta['park_key'] not in rides:continue
            signature=(trace.meta.get('started_utc'),trace.meta.get('file_sha256',{}).get('timing'))
            if signature in seen:continue
            seen.add(signature)
            if trace.issues:skipped.append({'trial':str(p),'reason':'; '.join(trace.issues)})
            else:traces.append(TrialStub(trace.path,trace.meta,trace.summary,trace.issues))
        except (ValueError,OSError,KeyError) as exc:skipped.append({'trial':str(p),'reason':str(exc)})
    return traces,skipped


def packets(trace):
    start=trace.meta['marks']['first_motion'];end=trace.meta['marks']['view_train_returned']
    rows=[]
    for r in trace.raw:
        t=float(r['elapsed_s'])
        if t>end:break
        values=[int(r.get(k,0) or 0) for k in INT_FIELDS]+[float(r[k]) for k in FLOAT_FIELDS]
        rows.append((t-start,struct.pack('>IIIIIIIIfffffffffff',*values)))
    if not rows:raise ValueError('No wire telemetry to replay')
    return rows


def percentiles(rows,key):
    a=np.asarray([r[key] for r in rows],dtype=float)
    return {key+'_'+name:float(np.percentile(a,p)) for name,p in (('median',50),('p95',95),('p99',99),('max',100))} if len(a) else {}


def replay(root,trace,reference,platform,args,sync,encoded):
    ref=Reference.from_trace(reference)
    settings=Settings(window_s=args.window,position_tolerance_m=args.position_tolerance,
                      orientation_tolerance_deg=args.orientation_tolerance,
                      resync_window_s=args.resync_window)
    pipe=Pipeline(root,platform,trace.meta['park_key'],trace,ref,settings,sync,
                  not args.no_twin_serialization,args.payload,args.intensity,args.variant,args.gains)
    start=min(0.,encoded[0][0]);end=trace.meta['marks']['view_train_returned']-trace.meta['marks']['first_motion']
    if args.max_seconds is not None:end=min(end,args.max_seconds)
    live=start;deadline=time.perf_counter();wall_start=deadline;cursor=0
    rows=[];digest=hashlib.sha256();skipped_ticks=0;last_start=None
    # First tick includes pre-roll to establish the real filter history. The
    # summary excludes negative ride time, but the tick CSV keeps all rows.
    try:
        while live<=end+1e-9:
            if args.mode=='realtime':
                delay=deadline-time.perf_counter()
                if delay>0:time.sleep(delay)
                actual=time.perf_counter()
                live=start+actual-wall_start
                if live>end+1e-9:break
                lateness=max(0.,actual-deadline)
            else:
                actual=time.perf_counter();lateness=0.
            oldcursor=cursor
            while cursor<len(encoded) and encoded[cursor][0]<=live+1e-9:cursor+=1
            pipe.feed([p for _,p in encoded[oldcursor:cursor]])
            row=pipe.tick(live)
            interval=(actual-last_start)*1000 if last_start is not None else None
            last_start=actual
            row.update(packets_received=cursor-oldcursor,lateness_ms=lateness*1000,
                       start_interval_ms=interval,
                       start_jitter_ms=(abs(interval-50) if interval is not None and args.mode=='realtime' else 0.),
                       input_age_ms=1000*(live-encoded[cursor-1][0]) if cursor else None,
                       over_50ms=row['total_ms']>50,skipped_deadlines=0)
            # Output equivalence is checked outside the timing envelope.
            values=[float(v) for v in pipe.backend.muscle_lengths]+[float(v) for v in pipe.backend.sent_pressures]
            if not all(math.isfinite(v) for v in values):raise ValueError('Nonfinite output calculation')
            digest.update(struct.pack('>12d',*values))
            if args.mode=='realtime':
                deadline+=.05
                after=time.perf_counter()
                missed=max(0,math.floor((after-deadline)/.05)+1) if after>deadline else 0
                deadline+=missed*.05;skipped_ticks+=missed
                row['skipped_deadlines']=missed
            else:live+=.05
            rows.append(row)
        active=[r for r in rows if r['elapsed_s']>=0]
        summary=dict(park=trace.meta['park_key'],platform=platform,sync='B+C' if sync else 'baseline',
            mode=args.mode,reference=reference.name,target=trace.name,ticks=len(active),
            replay_duration_s=end,partial_replay=args.max_seconds is not None and end<(trace.meta['marks']['view_train_returned']-trace.meta['marks']['first_motion']),
            wall_execution_s=time.perf_counter()-wall_start,
            overruns_50ms=sum(r['over_50ms'] for r in active),skipped_deadlines=skipped_ticks,
            applied_ticks=sum(r['applied'] for r in active),invalid_telemetry_ticks=sum(not r['valid'] for r in active),
            lost_sync_ticks=sum(r['match_status'].startswith(('lost','reacquir')) for r in active),
            # A matched XYZ point can coexist with a delayed profile lookup.
            profile_lag_over_250ms_ticks=sum(r.get('profile_lag_s') is not None
                                              and abs(r['profile_lag_s'])>.25 for r in active),
            max_abs_profile_lag_s=max((abs(r['profile_lag_s']) for r in active
                                       if r.get('profile_lag_s') is not None),default=0.),
            max_abs_stationary_profile_lag_s=max((abs(r['profile_lag_s']) for r in active
                                                   if r.get('profile_lag_s') is not None
                                                   and r['match_status']=='stationary_time'),default=0.),
            workspace_limited_ticks=sum(r['workspace_limited'] for r in active),
            final_clip_ticks=sum(r['final_length_clip'] for r in active),
            max_ik_calls=max((r['ik_calls'] for r in active),default=0),
            null_festo_packets=pipe.sink.packet_count,reference_bytes=ref.bytes,profile_bytes=pipe.profile.gains.nbytes,
            output_digest=digest.hexdigest(),variant=pipe.snapshot['label'],angular_cue=pipe.snapshot['angular_cue'],
            unity_output_equal=None)
        for key in ('total_ms','service_ms','read_transform_ms','sync_lookup_ms','washout_ms','motion_ms','kinematics_ms','pressure_ms','twin_ms','lateness_ms','start_jitter_ms'):
            summary.update(percentiles(active,key))
        summary['p99_frame_budget_percent']=summary.get('total_ms_p99',0)*2
        summary['matcher_p99_goal_met']=summary.get('sync_lookup_ms_p99',0)<1. if sync else None
        detail=dict(reference_nl2_version=reference.meta.get('nl2_version'),target_nl2_version=trace.meta.get('nl2_version'),
                    park_sha256=trace.meta.get('park_sha256'),reference_trial=str(reference.path),target_trial=str(trace.path),
                    variant_snapshot=pipe.snapshot,telemetry_settings=pipe.telem.snapshot(),
                    matcher_settings=asdict(settings),limits=list(pipe.backend.cfg.LIMITS_1DOF_TRANFORM),
                    workspace_mode=pipe.backend.workspace_limit_mode,pressure_mapping=pipe.backend.cfg.MUSCLE_PRESSURE_MAPPING_FILE)
        return summary,rows,detail
    finally:
        pipe.close()


def run(args,root):
    rides=None if args.rides=='all' else set(args.rides.split(','))
    traces,skipped=collect(args.inputs,rides)
    groups=defaultdict(list)
    for t in traces:groups[group_key(t)].append(t)
    if args.reference is not None:
        explicit=read_trial(args.reference)
        if explicit.issues:raise ValueError('Explicit reference is not complete and qualified')
        groups.setdefault(group_key(explicit),[])
    else:explicit=None
    jobs=[]
    for key,items in sorted(groups.items(),key=lambda x:str(x[0])):
        ref=explicit if explicit is not None and group_key(explicit)==key else choose_reference(items,args.reference_machine)
        if explicit is not None and group_key(explicit)!=key:continue
        targets=[t for t in sorted(items,key=lambda x:str(x.path)) if t.path!=ref.path]
        if args.targets_per_ride:targets=targets[:args.targets_per_ride]
        for t in targets:jobs.append((ref,t))
        if not targets:skipped.append(dict(trial=str(ref.path),reason='No independent target run in this compatibility group'))
    if not jobs:raise ValueError('Need at least two complete compatible recordings of a selected park')
    output=args.output.resolve()/unique_id('benchmark')
    output.mkdir(parents=True)
    platforms=('chair_v3','platform_v2') if args.platforms=='both' else (args.platforms,)
    hardware_before=host_details()
    summaries=[];details=[]
    names=['core/platform_controller.py','core/telemetry_config.py','core/platform_geometry.py',
           'sims/nolimits2/sim.py','sims/nolimits2/nl2_protocol.py','sims/nolimits2/station_modes.py',
           'sims/nolimits2/angular_cues.py','sims/nolimits2/ride_config.py','sims/nolimits2/telemetry_config.ini',
           'kinematics/kinematics_V3.py','kinematics/cfg_SuspendedChairV3.py',
           'kinematics/cfg_SuspendedChair.py','kinematics/cfg_SuspendedPlatformMdx.py',
           'output/d_to_p.py','output/muscle_output.py','output/festo_itf.py',
           'output/chair_DtoP_synthesized.csv','output/wheelchair_DtoP.csv',
           'tools/nl2_sync/matcher.py','tools/nl2_sync_benchmark/pipeline.py']
    source_hashes={n:sha256(root/n) for n in names}
    previous_log_level=logging.getLogger().level
    logging.getLogger().setLevel(logging.ERROR)
    try:
        with NoNetwork() as guard:
            for ref_stub,trace_stub in jobs:
                reference=read_trial(ref_stub.path)
                trace=read_trial(trace_stub.path)
                encoded=packets(trace)
                for platform in platforms:
                    for rep in range(1,args.passes+1):
                        modes=[False,True] if args.sync=='both' else [args.sync=='on']
                        # Alternate order on even passes to expose warm-cache bias.
                        if rep%2==0:modes.reverse()
                        batch=[]
                        for enabled in modes:
                            print(f"{trace.meta['park_key']} / {platform} / {'B+C' if enabled else 'baseline'} / pass {rep}",flush=True)
                            result,rows,detail=replay(root,trace,reference,platform,args,enabled,encoded)
                            result['pass']=rep
                            number=len(summaries)+1
                            name=f"{number:03d}_{trace.meta['park_key']}_{platform}_{'sync' if enabled else 'baseline'}"
                            write_table(output/(name+'_ticks.csv'),rows)
                            result['tick_file']=name+'_ticks.csv'
                            summaries.append(result);batch.append(result);details.append(dict(job=name,**detail))
                            print(f"  p99 tick {result.get('total_ms_p99',0):.3f}ms; sync+lookup {result.get('sync_lookup_ms_p99',0):.3f}ms; missed deadlines {result['skipped_deadlines']}",flush=True)
                        if len(batch)==2 and args.mode=='throughput':
                            equal=batch[0]['output_digest']==batch[1]['output_digest']
                            for r in batch:r['unity_output_equal']=equal
                write_json(output/'progress.json',dict(status='running',completed_jobs=len(summaries)))
            if guard.attempts:raise RuntimeError('An attempted network operation was blocked; benchmark not valid')
    except BaseException as exc:
        write_json(output/'progress.json',dict(status='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',
            completed_jobs=len(summaries),error=f'{type(exc).__name__}: {exc}'))
        if summaries:write_table(output/'partial_summary.csv',summaries)
        raise
    finally:logging.getLogger().setLevel(previous_log_level)
    hardware_after=host_details()
    try:
        import resource
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        memory=dict(process_peak_rss=peak,units='bytes' if __import__('sys').platform=='darwin' else 'KiB')
    except ImportError:memory=dict(process_peak_rss=None,note='resource module unavailable on this OS')
    report=dict(schema='poc.nl2.sync-benchmark/1',status='complete',hardware_before=hardware_before,
                hardware_after=hardware_after,memory=memory,source_hashes=source_hashes,
                jobs=summaries,details=details,skipped=skipped,network_attempts=0,
                note='Offline steady-running compute benchmark, not full Qt/GUI/network/VR POC scheduling or hardware validation.')
    write_json(output/'report.json',report);write_table(output/'summary.csv',summaries)
    write_table(output/'skipped.csv',skipped,None if skipped else ['trial','reason'])
    columns=['park','platform','sync','mode','total_ms_p99','sync_lookup_ms_p99','p99_frame_budget_percent',
             'overruns_50ms','skipped_deadlines','lost_sync_ticks','unity_output_equal']
    text='''# Headless B+C processing benchmark

This is the processing path from the installed POC source, with no network or
physical outputs. The full GUI application is NOT run. Source/settings hashes
are in report.json. Detailed per-tick timings are in the companion CSVs.

Included: production NL2 packet parser + steady-running adapter service/read,
quaternion/cue/heave conversion, gain/washout, backend kinematics/workspace/floor
limiting, distance-to-pressure conversion, in-memory EasyIP packing and optional
Twin JSON encoding. B+C additionally performs local XYZ/quaternion matching and
six-channel unity profile interpolation. Applied lengths/pressures must match
the baseline bit-for-bit in deterministic throughput mode.

Excluded: sockets, acknowledgements, listener thread scheduling, SHS, GPIO,
serial/fan IO, Qt signals/UI painting and park loading/activation transitions.
Those need a later end-to-end deployed test. A 1ms p99 matcher+lookup target is
an engineering goal, not a guarantee. Total service+data compute has a 50ms
budget; real-time deadline misses and lateness are reported separately.

Throughput replays run a deterministic virtual 20Hz clock WITHOUT sleeping.
Realtime replays use actual 20Hz deadlines, skip missed deadlines instead of
catch-up bursts, and retain arrival gaps. Recording preparation and output file
writing are excluded from timed callbacks. No dynamic gains or anticipatory
cue changes have been implemented. Unity factors do not optimize motion.

## Results

'''+markdown_table(summaries,columns)+'\n'
    (output/'report.md').write_text(text,encoding='utf-8')
    (output/'report.html').write_text('<!doctype html><meta charset="utf-8"><title>POC headless benchmark</title><style>body{font:15px system-ui;margin:30px}table{border-collapse:collapse}td,th{padding:7px;border:1px solid #aaa}pre{white-space:pre-wrap}</style><h1>Headless B+C benchmark</h1>'+html_table(summaries,columns)+'<pre>'+__import__('html').escape(text)+'</pre>',encoding='utf-8')
    write_json(output/'progress.json',dict(status='complete',completed_jobs=len(summaries)))
    print('Report:',output/'report.html',flush=True)
    return output
