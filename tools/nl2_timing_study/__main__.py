# __main__.py ver 1
"""python -m tools.nl2_timing_study capture|analyse ..."""
from __future__ import annotations
import argparse
import math
from pathlib import Path
import sys
from .common import ROOT


def number(text):
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a positive, finite number")
    return value


def nonnegative(text):
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative, finite number")
    return value


def count(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def zero_count(text):
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return value


def parser():
    p = argparse.ArgumentParser(description="Local NoLimits 2 timing study: capture and cross-PC synchronization analysis. No physical platform outputs.")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("capture", help="Run local NoLimits using quick/standard/extended presets")
    c.add_argument("--list", action="store_true", help="List supported physical parks, without connecting")
    c.add_argument("--preset", choices=("quick", "standard", "extended"), default="quick", help="quick: 3 diagnostic rides x3; standard: enabled rides x3; extended: x5")
    c.add_argument("--rides", default=None, help="Override preset: all, or comma-separated park keys/names/list numbers")
    c.add_argument("--repeats", type=count, default=None, help="Override preset repeat count")
    c.add_argument("--warmups", type=zero_count, default=0, help="Optional extra runs per park, logged but excluded from analysis")
    c.add_argument("--conditions", default="", help="Record resolution, VSync, power plan, graphics and background load; VR is read from NL2")
    c.add_argument("--parks-root", type=Path, help="Local MDX parks root; overrides paths in memory only")
    c.add_argument("--port", type=int, default=15151, help="Local NL2 port (host is fixed to 127.0.0.1)")
    c.add_argument("--hz", type=number, default=20., help="Normal polling rate (default 20Hz)")
    c.add_argument("--prepare-timeout", type=number, default=180.)
    c.add_argument("--depart-timeout", type=number, default=90.)
    c.add_argument("--max-duration", type=number, default=None, help="Override adaptive per-ride return timeout (seconds)")
    c.add_argument("--telemetry-timeout", type=number, default=5.)
    c.add_argument("--motion-threshold", type=number, default=.1, help="First sustained movement threshold, metres/second")
    c.add_argument("--pre-roll", type=nonnegative, default=1.)
    c.add_argument("--tail", type=nonnegative, default=2.)
    c.add_argument("--between-runs", type=nonnegative, default=2.)
    c.add_argument("--output", type=Path, default=ROOT/"captures"/"nl2_timing_study")
    c.add_argument("--fail-fast", action="store_true", help="Otherwise failed trials are retained and the next attempt is made")
    c.add_argument("--no-report", action="store_true", help="Do not analyse automatically after capturing")
    c.add_argument("--load-retries", type=zero_count, default=3, help="Bounded retries when NL2 reports an operation in progress")
    c.add_argument("--retry-delay", type=number, default=3.)
    c.add_argument("--max-park-failures", type=count, default=2, help="Skip remaining attempts after this many consecutive failures of one park")
    c.add_argument("--extend-uncertain", action="store_true", help="After planned runs, add up to two if coverage/drift/gaps warrant it (at most five measured runs)")
    c.add_argument("--monitors", type=count, help="Declared connected display count")
    c.add_argument("--debug", action="store_true", help="Verbose adapter log; use the same setting on both PCs")
    a = sub.add_parser("analyse", aliases=["analyze"], help="Read one or more session folders, including copies from both PCs")
    a.add_argument("inputs", nargs="+", type=Path, help="Session folders or individual *_trial.json files; searched recursively")
    a.add_argument("--output", type=Path, default=ROOT/"studies"/"nl2_timing")
    a.add_argument("--reference-machine", help="Prefer this PC when choosing the representative reference")
    a.add_argument("--reference-policy", choices=("representative", "first"), default="representative")
    a.add_argument("--landmark-spacing", type=number, default=10., help="Reference distance between checkpoints (metres)")
    a.add_argument("--max-gap", type=number, default=.25, help="Do not interpolate across larger telemetry gaps (seconds)")
    a.add_argument("--position-tolerance", type=number, default=1., help="Reject checkpoint matches further away than this (metres)")
    a.add_argument("--anchor-every", type=number, default=5., help="Seconds between spatial re-anchors in causal replay evaluation")
    a.add_argument("--clock-budget", type=number, default=.2, help="Screening tolerance, NOT a physical safety guarantee (seconds)")
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        if args.command == "capture":
            if not 1 <= args.port <= 65535:
                p.error("--port must be between 1 and 65535")
            if args.hz > 100:
                p.error("--hz must not exceed 100; use the normal 20Hz for comparisons")
            from .capture import capture
            return capture(args)
        from .analysis import analyse_paths
        output = analyse_paths(args.inputs, args.output, args.reference_machine,
                               args.landmark_spacing, args.max_gap, args.position_tolerance,
                               args.anchor_every, args.clock_budget, reference_policy=args.reference_policy)
        print(f"Report: {output / 'report.html'}")
        print(f"Detailed data: {output}")
        return 0
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
