# output.py ver 1
from __future__ import annotations
import csv
import html
import json
from pathlib import Path
import numpy as np
from tools.nl2_sync.profile import AXES


def json_write(path,value):
    with Path(path).open('x',encoding='utf-8') as f:json.dump(value,f,indent=2,allow_nan=False);f.write('\n')


def csv_write(path,rows):
    it=iter(rows);first=next(it,None)
    with Path(path).open('x',encoding='utf-8',newline='') as f:
        if first is not None:
            writer=csv.DictWriter(f,fieldnames=list(first));writer.writeheader();writer.writerow(first);writer.writerows(it)


def table(rows,columns):
    esc=lambda x:html.escape(str(x))
    return '<table><thead><tr>'+''.join('<th>'+esc(x)+'</th>' for x in columns)+'</tr></thead><tbody>'+''.join(
        '<tr>'+''.join('<td>'+esc(r.get(c,''))+'</td>' for c in columns)+'</tr>' for r in rows)+'</tbody></table>'


def report(output,results,skipped,settings):
    summaries=[]
    for r in results:
        row={k:r.get(k,'') for k in ('park','platform','target','cue','neutral_mm','qualified','heldout_pass','robust_100ms')}
        m=r['aggregate'];row.update(excess_mm=round(m['maximum_requested_excess_mm'],3),
            baseline_excess_mm=round(r['baseline']['maximum_requested_excess_mm'],3),
            longest_excess_s=round(m['longest_excess_episode_s'],3),
            max_output_step_mm=round(m['max_output_step_mm'],3),
            profile=r['profile'])
        for a in ('roll','pitch','heave'):
            row[a+'_motion_change_pct']=round(r['motion_change_pct'][a],1) if r['motion_change_pct'][a] is not None else ''
        summaries.append(row)
    csv_write(output/'index.csv',summaries)
    json_write(output/'report.json',{'settings':settings,'results':results,'skipped':skipped})
    css='body{font:16px system-ui;max-width:1350px;margin:32px auto;padding:0 20px;color:#202b36}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:9px;border-bottom:1px solid #cbd3da;text-align:left}th{background:#e9eef3}code{background:#eef2f5}h2{margin-top:38px}.notice{padding:16px;background:#fff2ce}svg{width:100%;height:155px;border-bottom:1px solid #ddd}'
    body='<h1>Dynamic ride profiles - Phase 1</h1><p class="notice">OFFLINE RESEARCH ONLY. No POC variant catalogue changed. Requested kinematic feasibility is not measured actuator response, tension feasibility or live safety approval. These files are not loaded by the current Sim Player or live POC.</p>'
    body+='<p>Gains act after existing washout. Each profile has a fixed midpoint/cue and C1 gain curves. Fit captures and a held-out capture are identified below. No anticipation or motion lead is applied.</p>'
    body+=table(summaries,['park','platform','target','cue','neutral_mm','qualified','heldout_pass','excess_mm','baseline_excess_mm','roll_motion_change_pct','pitch_motion_change_pct','heave_motion_change_pct'])
    body+='<p>Motion change: requested normalized variation against the standard capture variant at its original midpoint and cue. It is not a measured physical-motion improvement. Cue changes alter the meaning of this comparison; see per-profile baseline.</p>'
    for r in results:
        body+='<h2>'+html.escape(r['park']+' / '+r['platform']+' / '+r['target'])+'</h2>'
        body+='<p><a href="'+html.escape(r['profile'])+'">Profile JSON</a> | <a href="'+html.escape(r['directory']+'/validation.json')+'">Complete validation</a> | <a href="'+html.escape(r['directory']+'/gain_curve.csv')+'">Gain control points</a></p>'
        body+='<p>Fit: '+html.escape(', '.join(r['fit']))+'<br>Held out: '+html.escape(', '.join(r['heldout']))+'</p>'
        body+='<p>Maximum gain slope '+str(round(r['search']['max_multiplier_rate_per_reference_s'],3))+' per reference second. 5 mm per 50 ms is only a soft smoothness preference. All six physical output length bounds remain active.</p>'
        t=np.array(r['knots']);g=np.array(r['gains']);colours=['#4277a2','#b37c25','#467d5f','#a54155','#6656a0','#607079']
        for j,a in enumerate(AXES):
            points=' '.join('%.2f,%.2f'%(35+float(x)/t[-1]*1120,135-float(y)*55) for x,y in zip(t,g[:,j]))
            body+='<div>'+a.title()+' multiplier (0-2)</div><svg viewBox="0 0 1190 160"><path d="M35 25V135H1155" fill="none" stroke="#777"/><path d="M35 80H1155" stroke="#ddd"/><polyline fill="none" stroke="'+colours[j]+'" stroke-width="2" points="'+points+'"/><text x="35" y="155">0 s</text><text x="1090" y="155">'+str(round(t[-1],1))+' s</text></svg>'
        body+=table([{'capture':v['capture'],'role':v['role'],'qualified':v['qualified'],'max_excess_mm':round(v['maximum_requested_excess_mm'],3),'clipped_ticks':v['final_clip_ticks'],'longest_excess_s':round(v['longest_excess_episode_s'],3),'production_parity':v['production_parity']} for v in r['captures']],['capture','role','qualified','max_excess_mm','clipped_ticks','longest_excess_s','production_parity'])
        body+='<p>Timing-offset and forced-loss checks are in validation.json. Failure under stress is retained as a warning, not removed or described as a qualified live fallback.</p>'
    if skipped:
        body+='<h2>Inputs not used</h2>'+table(skipped,['trial','reason'])
    (output/'report.html').write_text('<!doctype html><html><head><meta charset="utf-8"><title>Phase 1 dynamic profiles</title><style>'+css+'</style></head><body>'+body+'</body></html>',encoding='utf-8')
    (output/'README.md').write_text('# Offline dynamic profiles\n\nOpen `report.html` first. `index.csv` is the candidate summary. Each candidate contains a gain profile, its exact reference trajectory, gain-curve CSV, all-capture validation, and per-tick diagnostic CSVs.\n\nThese are NOT static optimiser study bundles and are not directly playable in unmodified Sim Player. Phase 2 will add review/playback. No candidate is installed in POC. Source recordings are unchanged.\n\n`qualified` requires every nominal observed capture to pass, including a held-out run. Additional phase-offset/forced-loss results are reported separately; do not interpret nominal qualification as robustness or hardware approval.\n',encoding='utf-8')
