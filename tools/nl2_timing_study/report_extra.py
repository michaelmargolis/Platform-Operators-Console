# report_extra.py ver 1
"""v2 report additions kept separate from the established landmark analyser."""
from __future__ import annotations
import html
import json
from collections import defaultdict
from .common import write_table,write_json
from .quality import choose_reference,interruptions,angular_events,compare_angular,recommendation,stationary_rotation_events


def extend_report(output,traces,groups,comparisons,hits,budget,max_gap):
    from .analysis import html_table,markdown_table
    from tools.nl2_sync.validation import evaluate
    report=json.loads((output/'report.json').read_text(encoding='utf-8'))
    interruptions_rows=[e for t in traces for e in interruptions(t,max_gap)]
    angular_rows=[e for t in traces if not t.issues for e in angular_events(t,max_gap=max_gap)]
    stationary_events=[e for t in traces if not t.issues for e in stationary_rotation_events(t,max_gap=max_gap)]
    angular_comparisons=[];sync=[];checks=[];transitions=[]
    by_name={t.name:t for t in traces}
    by_pair=defaultdict(list)
    for h in hits:by_pair[(h['reference'],h['target'])].append(h)
    for c in comparisons:
        ref,target=by_name[c['reference']],by_name[c['target']]
        hs=by_pair[(ref.name,target.name)]
        angular_comparisons.extend(compare_angular(ref,target,hs,max_gap))
        summary,points,events=evaluate(ref,target,hs)
        sync.append(summary);checks.extend(points);transitions.extend(events)
    advice=recommendation(traces,comparisons,budget)
    additions={'interruptions':interruptions_rows,'angular_events':angular_rows,
               'angular_comparisons':angular_comparisons,'stationary_rotation_events':stationary_events,'sync_summary':sync,
               'sync_checkpoints':checks,'sync_transitions':transitions,'recommendations':advice}
    for name,rows in additions.items():
        write_table(output/(name+'.csv'),rows,None if rows else ['park','note'])
    report.update(tool_version='2.1.0',**additions)
    write_json(output/'report.json',report)
    sections=[
        ('Capture clock quality',[t.summary for t in traces],['park','machine','repeat','equal_timestamp_observations','backwards_timestamp_observations','gaps_above_threshold','eligible']),
        ('Local B+C matcher: offline consistency',sync,['park','target','matched_samples','samples','lost_samples','longest_lost_s','max_abs_profile_lag_s','max_abs_stationary_profile_lag_s','profile_lag_over_250ms_samples','checkpoint_count','matcher_checkpoint_max_abs_s','slewed_profile_checkpoint_max_abs_s']),
        ('Protocol interruptions (not proof of visible stutter)',interruptions_rows,['park','trial','start_phase_s','gap_s','source_frame_increment','rtt_ms','classification']),
        ('Sharp roll/pitch events: matching',angular_comparisons,['park','target','axis','phase_s','value_before_250ms_deg','matched','motion_clock_error_s','target_sample_interval_s']),
        ('Stationary rotation threshold events',stationary_events,['park','trial','tilt_threshold_phase_s','threshold_bracket_s','peak_excursion_deg','first_98pct_excursion_phase_s','threshold_observed']),
        ('Additional tests suggested',advice,['park','complete_runs','recommendation','reasons','suggested_additional_runs'])]
    intro='''<h2>v2 additions and important boundaries</h2><p>Equal receive times no longer reject a run. They are collapsed only for interpolation. Backwards times still fail validation. Source recordings are unchanged. Interruptions and uncertain matches remain visible; their absence from an interpolated checkpoint is not zero error.</p><p>B+C results use a causal local XYZ/quaternion matcher and a short flagged time fallback followed by hold. A bounded wider window is used for reacquisition. Checkpoint consistency shares spatial evidence with the matcher; it is not independent ground truth. Stationary-time intervals are unobservable, not confident orientation matches. CPU cost in these reports is the analysis host, not the Pi benchmark.</p><p>Angular crossing comparisons are OFFLINE diagnostics near XYZ checkpoint alignment, not a live event detector. Euler pitch folds at 90 degrees; stationary quaternion excursion remains the more reliable description of a full tilt. No anticipatory cue or dynamic gain optimization is enabled.</p>'''
    doc=(output/'report.html').read_text(encoding='utf-8').replace('</html>','')+intro
    md=(output/'report.md').read_text(encoding='utf-8')+'\n\n## v2 additions\n\nB+C results are causal replay consistency, not external ground truth. Stationary-time intervals are explicitly unobservable. Angular-event matching is offline only. No production control changes.\n'
    for title,rows,cols in sections:
        doc+='<h2>'+html.escape(title)+'</h2>'+html_table(rows,cols)
        md+='\n\n## '+title+'\n\n'+markdown_table(rows,cols)
    (output/'report.html').write_text(doc+'</html>',encoding='utf-8')
    (output/'report.md').write_text(md+'\n',encoding='utf-8')
