# __main__.py ver 1
"""python -m tools.nl2_sync_benchmark SESSION_ROOT ..."""
from __future__ import annotations
import argparse
import math
import os
from pathlib import Path
import sys
from tools.nl2_timing_study.__main__ import number,count,zero_count


def gains(text):
    try:v=[float(x) for x in text.split(',')]
    except ValueError:raise argparse.ArgumentTypeError('Six comma-separated gain percentages required')
    if len(v)!=6 or any(not math.isfinite(x) or x<0 or x>200 for x in v):
        raise argparse.ArgumentTypeError('Six finite percentages 0..200 required (surge,sway,heave,roll,pitch,yaw)')
    return v


def parser():
    p=argparse.ArgumentParser(description='Offline POC hot-path + local XYZ/orientation sync benchmark. NO network or physical outputs.')
    p.add_argument('session_root',type=Path,help='Directory containing extracted session* subdirectories (one level below this path)')
    p.add_argument('--rides',default='blackbird,fenrir,forestmountain,tiltcoaster',help='Comma-separated park keys or all')
    p.add_argument('--platforms',choices=('both','chair_v3','platform_v2'),default='both')
    p.add_argument('--mode',choices=('throughput','realtime'),default='throughput')
    p.add_argument('--sync',choices=('both','on','off'),default='both')
    p.add_argument('--passes',type=count,default=1,help='Repeat processing passes, alternating baseline/sync order')
    p.add_argument('--targets-per-ride',type=zero_count,default=1,help='Independent live traces per ride; 0 = all')
    p.add_argument('--reference',type=Path,help='Explicit *_trial.json reference; only compatible targets used')
    p.add_argument('--reference-machine',help='Prefer this capture PC when choosing a reference')
    p.add_argument('--max-seconds',type=number,help='Stop after this many ride seconds (flagged as partial)')
    p.add_argument('--window',type=number,default=2.,help='Local search half-width in reference seconds')
    p.add_argument('--resync-window',type=number,default=8.,help='Bounded recovery search half-width')
    p.add_argument('--position-tolerance',type=number,default=1.,help='XYZ residual gate in metres, a study setting not a safety tolerance')
    p.add_argument('--orientation-tolerance',type=number,default=20.)
    p.add_argument('--payload',type=int,choices=(0,1,2),default=1)
    p.add_argument('--intensity',type=number,default=100.)
    p.add_argument('--variant',help='Existing named variant read-only; select only its park')
    p.add_argument('--gains',type=gains,help='Optional test gain override, never saved to POC')
    p.add_argument('--no-twin-serialization',action='store_true')
    p.add_argument('--output',type=Path,default=Path('studies/nl2_sync_benchmark'))
    return p


def select_session_root(root, *, prompt=None, interactive=None, output_stream=None, error_stream=None):
    """List direct session* directories and require a numbered selection.

    No directories are created; the selected extracted session is the only input
    passed to the existing replay runner. A noninteractive caller cannot silently
    pick an arbitrary session.
    """
    out = output_stream if output_stream is not None else sys.stdout
    err = error_stream if error_stream is not None else sys.stderr
    if interactive is None:
        interactive = sys.stdin.isatty()
    if prompt is None:
        prompt = input
    try:
        base = Path(root).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: Cannot resolve session root {root}: {exc}", file=err)
        return None
    try:
        if not base.exists():
            print(f"ERROR: Session root does not exist: {base}", file=err)
            print("Copy/extract the timing-study session folders under an existing root directory.", file=err)
            return None
        if not base.is_dir():
            text = 'Extract the archive and provide its parent directory.' if base.suffix.lower()=='.zip' else 'Supply the directory containing session* folders.'
            print(f"ERROR: Session root is not a directory: {base}. {text}", file=err)
            return None
        # Only immediate real subdirectories, not arbitrary nested folders or
        # symlink targets elsewhere in the filesystem.
        with os.scandir(base) as listing:
            sessions = sorted(
                (Path(entry.path) for entry in listing
                 if entry.name.startswith('session') and entry.is_dir(follow_symlinks=False)),
                key=lambda path: path.name, reverse=True)
    except OSError as exc:
        print(f"ERROR: Cannot access session root {base}: {exc}", file=err)
        print("Check permissions and that the drive is mounted.", file=err)
        return None
    if not sessions:
        print(f"ERROR: No immediate session* subdirectories found in: {base}", file=err)
        print("Extract a complete timing-study session here. If you supplied a session directory, supply its parent instead.", file=err)
        return None
    print(f"Available timing-study sessions in {base} (newest name first):", file=out)
    for index, session in enumerate(sessions, 1):
        print(f"  {index}. {session.name}", file=out)
    if not interactive:
        print("ERROR: Numbered selection requires an interactive terminal; no session selected.", file=err)
        return None
    while True:
        try:
            response = prompt(f"Select session [1-{len(sessions)}] (0 to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("Session selection cancelled.", file=err)
            return None
        if response == '0':
            print("Session selection cancelled.", file=err)
            return None
        try:
            selected = int(response)
        except ValueError:
            selected = -1
        if 1 <= selected <= len(sessions):
            path = sessions[selected-1]
            try:
                if not any(path.rglob('*_trial.json')):
                    print(f"ERROR: Selected session has no *_trial.json recordings: {path}", file=err)
                    print("Copy/extract the complete session (including companion CSVs) and retry.", file=err)
                    return None
            except OSError as exc:
                print(f"ERROR: Cannot read selected session {path}: {exc}", file=err)
                return None
            print(f"Selected session: {path}", file=out)
            return path
        print(f"Please enter a number from 1 to {len(sessions)}, or 0 to cancel.", file=out)


def main(argv=None):
    p=parser();a=p.parse_args(argv)
    if a.intensity>100:p.error('--intensity must not exceed 100')
    if a.resync_window<a.window:p.error('--resync-window must be at least --window')
    selected = select_session_root(a.session_root)
    if selected is None:
        return 2
    a.inputs=[selected]
    a.output=a.output.expanduser().resolve()
    if a.reference is not None:a.reference=a.reference.expanduser().resolve()
    root=Path(__file__).resolve().parents[2]
    old=Path.cwd()
    try:
        os.chdir(root)
        from .runner import run
        output=run(a,root)
        import json
        results=json.loads((output/'report.json').read_text())
        if any(r.get('unity_output_equal') is False for r in results['jobs']):
            print('ERROR: baseline and unity-profile output differ; inspect report before relying on timings.',file=sys.stderr)
            return 2
        return 0
    except KeyboardInterrupt:
        print('Stopped. No physical outputs were sent.',file=sys.stderr);return 130
    except (OSError,ValueError,RuntimeError,ImportError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);return 1
    finally:os.chdir(old)


if __name__=='__main__':raise SystemExit(main())
