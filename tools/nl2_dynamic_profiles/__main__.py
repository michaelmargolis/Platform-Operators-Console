# __main__.py ver 2
"""Offline dynamic-profile batch. Use from a complete POC repository root."""
from __future__ import annotations
import argparse
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
import logging
from pathlib import Path
import sys
import uuid
import numpy as np

ROOT=Path(__file__).resolve().parents[2]


def parser():
    p=argparse.ArgumentParser(description='Phase 1 offline dynamic motion profiles; no NL2 or hardware connection.')
    p.add_argument('inputs',type=Path,nargs='+',help='Extracted timing-study session(s); each park needs >=2 completed compatible runs')
    p.add_argument('--rides',default='mdxlogo,blackbird',help='Comma-separated configured ride keys/names; Analyzer passes the selected session ride explicitly')
    p.add_argument('--platforms',choices=('both','chair_v3','platform_v2'),default='both')
    p.add_argument('--output',type=Path,default=Path('studies/nl2/dynamic_profiles'))
    p.add_argument('--list',action='store_true',help='Validate/discover input metadata without running optimisation')
    p.add_argument('--cue',choices=('auto','current','euler','sine','gravity'),default='auto',help='Auto retains current cue unless full-turn evidence: compares sine/gravity; Euler remains baseline and an explicit option')
    p.add_argument('--variant',help='Optional existing base variant label (one park only), read-only')
    p.add_argument('--fit-all',action='store_true',help='Use every run for fitting; explicitly forfeits held-out validation')
    p.add_argument('--evaluations',type=int,default=420,help='Bounded static seed search budget per park/platform')
    p.add_argument('--knot-seconds',type=float,default=1.,help='Approximate reference-time spacing of C1 gain control points')
    p.add_argument('--sweeps',type=int,default=2,help='Local curve refinement passes (1..3)')
    p.add_argument('--neutral-min',type=float,default=-60.)
    p.add_argument('--neutral-max',type=float,default=60.)
    p.add_argument('--fit-jitter',type=float,default=.1,help='Fit against reference phase offsets +/- this many seconds (default 0.1)')
    p.add_argument('--reserve-mm',type=float,default=2.,help='Fit reserve inside the requested constraints, NOT a validated physical safety margin')
    p.add_argument('--max-gain-rate',type=float,default=1.,help='Maximum multiplier derivative per reference second')
    p.add_argument('--no-stress',action='store_true',help='Skip optional phase-offset and loss-envelope diagnostics; nominal validation still mandatory')
    return p


def aggregate(values):
    out={}
    for key in ('maximum_requested_excess_mm','longest_excess_episode_s','max_output_step_mm','max_requested_step_mm'):
        out[key]=max(v[key] for v in values)
    for key in ('requested_excess_ticks','workspace_limited_ticks','final_clip_ticks','floor_violation_ticks','active_ticks'):
        out[key]=sum(v[key] for v in values)
    out['axis_motion_amplitudes']={a:float(np.mean([v['axis_motion_amplitudes'][a] for v in values])) for a in values[0]['axis_motion_amplitudes']}
    out['qualified']=all(v['qualified'] for v in values)
    return out


def run(args):
    # Delay importing POC until we know the inputs and options are valid.
    from tools.nl2_sync_benchmark.runner import collect
    from tools.nl2_timing_study.analysis import read_trial,group_key
    from tools.nl2_timing_study.quality import choose_reference
    from tools.nl2_sync_benchmark.pipeline import NoNetwork
    from tools.nl2_sync.matcher import Reference
    from tools.nl2_sync.profile import DynamicProfile,AXES
    from tools.sim_player.optimization_search import rotation_evidence
    from tools.sim_player.dynamic_optimization import static_seeds,Geometry,optimize
    from .replay import prepare,processing_identity,verify_production
    from .evaluate import calculate,metrics,diagnostic_rows
    from .output import json_write,csv_write,report
    from sims.nolimits2 import ride_config
    aliases={}
    for key,park in ride_config.PARKS.items():
        for value in (key,park['name'],Path(park['path']).stem):
            aliases[''.join(ch for ch in str(value).casefold() if ch.isalnum())]=key
    requested=[x.strip() for x in args.rides.split(',') if x.strip()]
    rides=set()
    unknown=[]
    for value in requested:
        token=''.join(ch for ch in value.casefold() if ch.isalnum())
        key=aliases.get(token)
        if key is None:
            unknown.append(value)
        else:
            rides.add(key)
    if unknown:
        raise ValueError('Unknown configured ride(s): '+', '.join(sorted(unknown)))
    if not rides:
        raise ValueError('At least one configured ride is required')
    if args.variant and len(rides)>1:raise ValueError('--variant requires exactly one park')
    if args.evaluations<80 or not .5<=args.knot_seconds<=5 or not 1<=args.sweeps<=3:
        raise ValueError('Need evaluations>=80, knot-seconds 0.5..5, sweeps 1..3')
    if not -100<=args.neutral_min<=args.neutral_max<=100 or not .05<=args.max_gain_rate<=3:
        raise ValueError('Invalid midpoint range or multiplier rate')
    if not 0<=args.reserve_mm<=5:raise ValueError('reserve-mm must be 0..5')
    if not 0<=args.fit_jitter<=.5:raise ValueError('fit-jitter must be 0..0.5')
    for path in args.inputs:
        if not path.exists():raise ValueError('Input does not exist: '+str(path))
        if path.suffix.lower()=='.zip':raise ValueError('Extract the complete timing session first: '+str(path))
    stubs,skipped=collect(args.inputs,rides)
    groups=defaultdict(list)
    for item in stubs:
        # Do not mix versions or absent park hashes under an apparently good group.
        if not item.meta.get('park_sha256'):
            skipped.append({'trial':str(item.path),'reason':'Missing park hash'});continue
        groups[(group_key(item),str(item.meta.get('nl2_version','unknown')))].append(item)
    if args.list:
        for (key,version),items in sorted(groups.items(),key=str):
            print('%s: %d complete runs, VR %s, NL2 %s, host(s) %s'%(key[0],len(items),key[-1],version,','.join(sorted({x.machine for x in items}))))
            for item in items:print('  '+str(item.path))
        for s in skipped:print('SKIPPED: '+s['trial']+' : '+s['reason'])
        return None
    selected=[]
    for park in sorted(rides):
        choices=[(key,items) for key,items in groups.items() if key[0][0]==park]
        if not choices:raise ValueError('No qualified complete recordings for '+park)
        if len(choices)!=1:
            raise ValueError('Multiple incompatible groups for %s (VR, park, version or view differ). Pass one session or compatible sessions; use --list.'%park)
        key,items=choices[0]
        if len(items)<2:raise ValueError(park+' needs at least two independent completed captures')
        selected.append((park,items))
    output=args.output.resolve()/('profiles_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+uuid.uuid4().hex[:8])
    output.mkdir(parents=True,exist_ok=False)
    results=[];platforms=('chair_v3','platform_v2') if args.platforms=='both' else (args.platforms,)
    settings={k:str(v) if isinstance(v,Path) else [str(x) for x in v] if k=='inputs' else v for k,v in vars(args).items()}
    settings.update(version='0.1.0',scope='offline only',output=str(output))
    json_write(output/'run_settings.json',settings)
    print('Output: '+str(output),flush=True)
    logging.basicConfig(level=logging.ERROR)
    with NoNetwork() as network:
      for park,items in selected:
        reference_stub=choose_reference(items)
        reference=read_trial(reference_stub.path)
        rest=sorted([x for x in items if x.path!=reference.path],key=lambda x:str(x.path))
        holdout=[] if args.fit_all else [rest[-1].path]
        traces=[reference]+[read_trial(x.path) for x in rest]
        fit_paths={x.path for x in traces if x.path not in holdout}
        fullturn=any(rotation_evidence(t.quat.tolist())['full_rotation'] for t in traces if t.path in fit_paths)
        for platform in platforms:
          print('\n%s / %s: %d fit, %d held out'%(park,platform,len(fit_paths),len(holdout)),flush=True)
          # Prepare all records with the actual capture variant and complete pre-roll.
          original=[prepare(ROOT,t,reference,platform,'euler',variant=args.variant) for t in traces]
          current=original[0].snapshot['angular_cue']
          # configure(euler) changes snapshot; obtain current label independently.
          from sims.nolimits2.variant_store import VariantStore
          from sims.nolimits2.ride_config import PARKS
          store=VariantStore.load_readonly(platform) if args.variant else VariantStore.for_capture(platform)
          current=store.snapshot(args.variant or PARKS[park]['name'])['angular_cue']
          cues=(('sine','gravity') if fullturn else (current,)) if args.cue=='auto' else ((current,) if args.cue=='current' else (args.cue,))
          prepared={'euler':original}
          for cue in dict.fromkeys((*cues,current)):
            if cue not in prepared:prepared[cue]=[prepare(ROOT,t,reference,platform,cue,variant=args.variant) for t in traces]
          fit={cue:[s for s in prepared[cue] if s.trace.path in fit_paths] for cue in cues}
          for cue,ss in fit.items():
            loss=sum(sum(x.startswith(('lost','reacquir','invalid_observation')) for x in s.status) for s in ss)
            if loss:raise ValueError('%s has %d lost sync ticks in fit captures; fix synchronization before optimizing'%(park,loss))
          seeds,ev=static_seeds(fit,platform,evaluations=args.evaluations,neutral_min=args.neutral_min,neutral_max=args.neutral_max,
                                progress=lambda text:print('  seed: '+text,flush=True))
          original_geo=Geometry(platform,prepared[current][0].snapshot['neutral_height_delta_mm'])
          baseline=aggregate([metrics(calculate(s,original_geo),original_geo,0.) for s in prepared[current]])
          for target,allowance_fraction in (('strict',0.),('relaxed5',.05)):
            options=seeds.get(target,[])
            if not options:
                results.append({'error':'No nonzero static seed found','park':park,'platform':platform,'target':target})
                continue
            p=options[0].parameters;ss=prepared[p.cue];train=[s for s in ss if s.trace.path in fit_paths]
            geo=Geometry(platform,p.neutral_mm);allowance=allowance_fraction*geo.stroke
            print('  %s: cue %s, neutral %+.1f mm, static seed %s'%(target,p.cue,p.neutral_mm,p.gains),flush=True)
            knots,controls,fallback,search=optimize(train,geo,np.array(p.gains)/100.,allowance=allowance,
                spacing=args.knot_seconds,max_slope=args.max_gain_rate,sweeps=args.sweeps,reserve=args.reserve_mm,jitter=args.fit_jitter,
                progress=lambda text:print('    '+text,flush=True))
            name=park+'_'+platform+'_'+target;directory=output/platform/park/target;directory.mkdir(parents=True)
            ref=Reference.from_trace(reference)
            np.savez_compressed(directory/'reference.npz',time_s=ref.time,xyz_m=ref.xyz,quaternion_xyzw=ref.quat,speed_mps=ref.speed)
            rhash=hashlib.sha256((directory/'reference.npz').read_bytes()).hexdigest()
            identity,context=processing_identity(ROOT,train[0],platform,p.neutral_mm,rhash)
            profile=DynamicProfile(knots,controls,fallback,identity,{'name':name,'target':target,
                 'allowance_mm':allowance,'reference_file':'reference.npz','reference_trial':reference.name,
                 'processing':context,'fit_trials':[s.trace.name for s in train],
                 'heldout_trials':[s.trace.name for s in ss if s.trace.path in holdout],
                 'search':search,'fallback_transition_s':.25,'max_observation_age_s':.25,
                 'qualification':'pending','non_physical':True,
                 'source_captures':[{'trial':s.trace.name,'timing_sha256':s.trace.meta.get('file_sha256',{}).get('timing')} for s in ss]})
            checks=[];stress=[];fallback_checks=[]
            for s in ss:
                data=calculate(s,geo,profile);m=metrics(data,geo,allowance)
                actual=verify_production(ROOT,s,reference,platform,profile)
                select=s.valid
                difference=float(np.max(np.abs(data['output'][select]-actual['output'][select]),initial=0.))
                rawdifference=float(np.max(np.abs(data['requested'][select]-actual['requested'][select]),initial=0.))
                production_metric=metrics(actual,geo,allowance)
                parity=difference<1e-6 and rawdifference<1e-6
                if not parity:raise RuntimeError('%s production parity failed: output %.9f raw %.9f'%(name,difference,rawdifference))
                lost=sum(1 for st,t in zip(s.status,s.time) if t>=0 and st.startswith(('lost','reacquir','invalid_observation')))
                m.update(sync_lost_ticks=lost,capture=s.trace.name,role='heldout' if s.trace.path in holdout else 'fit',
                         production_parity=parity,production_max_difference_mm=difference,
                         qualified=m['qualified'] and production_metric['qualified'] and lost==0)
                checks.append(m)
                fallback_checks.append(dict(capture=s.trace.name,**metrics(calculate(s,geo,constant=fallback),geo,0.)))
                csv_write(directory/(s.trace.name+'_dynamic_diagnostics.csv'),diagnostic_rows(data,s))
                if not args.no_stress:
                    for offset in (-.2,-.1,-.05,.05,.1,.2):
                        sm=metrics(calculate(s,geo,profile,phase_offset=offset),geo,allowance)
                        stress.append(dict(capture=s.trace.name,kind='phase_offset',offset_s=offset,**sm))
                    # Explicit matcher-loss envelope, not a claim to simulate a simulator stall.
                    active=np.where(s.time>=0)[0]
                    peak=int(active[np.argmax(np.linalg.norm(data['norm'][active,2:5],axis=1))])
                    start=float(s.time[peak])
                    sm=metrics(calculate(s,geo,profile,loss=(start,start+.75)),geo,allowance)
                    stress.append(dict(capture=s.trace.name,kind='forced_loss_envelope',start_s=start,duration_s=.75,**sm))
            agg=aggregate(checks)
            qualified=agg['qualified'] and all(m['qualified'] for m in fallback_checks)
            heldout_ok=all(c['qualified'] for c in checks if c['role']=='heldout') if holdout else None
            robust=all(s['qualified'] for s in stress if s['kind']=='phase_offset' and abs(s['offset_s'])<=.10001) if not args.no_stress else None
            profile.metadata['qualification']='nominal_all_captures_pass' if qualified else 'rejected_nominal_validation'
            profile.metadata['validation']={'nominal_qualified':qualified,'heldout_pass':heldout_ok,
                'robust_100ms':robust,'production_parity':True,'fallback_all_captures_pass':all(m['qualified'] for m in fallback_checks)}
            profile.save(directory/'profile.json')
            # Re-open the actual exported file and evaluate its interpolants, not a temporary object only.
            reopened=DynamicProfile.load(directory/'profile.json',identity=identity)
            if not np.array_equal(reopened.lookup_many(knots),controls):raise RuntimeError('Export roundtrip differs')
            csv_write(directory/'gain_curve.csv',[dict(reference_time_s=float(t),**dict(zip(AXES,[float(x) for x in row]))) for t,row in zip(knots,controls)])
            validation={'nominal':checks,'static_fallback':fallback_checks,'stress':stress,
                'note':'Stress cases never modify/repair the profile. Held-out data never participates in search. Nominal qualification is not physical or live approval.'}
            json_write(directory/'validation.json',validation)
            rel=directory.relative_to(output).as_posix()
            changes={a:(100.*(agg['axis_motion_amplitudes'][a]/baseline['axis_motion_amplitudes'][a]-1.)
                        if baseline['axis_motion_amplitudes'][a]>1e-6 else None) for a in AXES}
            record={'park':park,'platform':platform,'target':target,'cue':p.cue,'neutral_mm':p.neutral_mm,
                'qualified':qualified,'heldout_pass':heldout_ok,'robust_100ms':robust,'aggregate':agg,
                'baseline':baseline,'baseline_cue':current,'motion_change_pct':changes,
                'directory':rel,'profile':rel+'/profile.json','fit':[s.trace.name for s in train],
                'heldout':[s.trace.name for s in ss if s.trace.path in holdout],'captures':checks,
                'search':search,'knots':knots.tolist(),'gains':controls.tolist()}
            results.append(record)
            print('    %s: nominal %s; held out %s; max requested excess %.3f mm; parity PASS'%(name,qualified,heldout_ok,agg['maximum_requested_excess_mm']),flush=True)
          checkpoint=output/('checkpoint_%s_%s.json'%(park,platform))
          json_write(checkpoint,{'completed_candidates':len(results),'park':park,'platform':platform})
      settings['network_attempts']=network.attempts
    report(output,[r for r in results if 'error' not in r],skipped,settings)
    errors=[r for r in results if 'error' in r]
    if errors:json_write(output/'search_failures.json',errors)
    print('\nFinished. Open '+str(output/'report.html'),flush=True)
    return output


def main(argv=None):
    args=parser().parse_args(argv)
    # Production uses repo-relative pressure maps. Resolve input/output paths
    # BEFORE chdir, so CLI paths retain the user's working-directory meaning.
    args.inputs=[p.expanduser().resolve() for p in args.inputs];args.output=args.output.expanduser().resolve()
    import os
    os.chdir(ROOT)
    try:run(args)
    except (ValueError,OSError,RuntimeError,KeyError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr);return 2
    except KeyboardInterrupt:
        print('Interrupted. Source captures are unchanged; unfinished results are not qualified.',file=sys.stderr);return 130
    return 0

if __name__=='__main__':raise SystemExit(main())
