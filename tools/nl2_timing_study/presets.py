# presets.py ver 1
"""Capture-only defaults, not operator park configuration."""
from __future__ import annotations

QUICK = ("blackbird", "fenrir", "tiltcoaster")
# Scenic Run has ambiguous completion; explicit --rides scenicrun still works.
DEFERRED = ("scenicrun",)
PRESETS = {"quick": (QUICK, 3), "standard": (None, 3), "extended": (None, 5)}
# Conservative initial timeout hints from the supplied timing studies, seconds.
# These are not simulator clocks or expected exact durations. Explicit
# --max-duration overrides them, and successful same-session durations update
# the allowance. No ride or operational configuration is modified.
DURATION_HINTS = dict(mdxlogo=107, blackbird=89, fenrir=48, flyingbeast=160,
                     forestmountain=146, hybris=172, tiltcoaster=145,
                     wilderness=165, wildernesspark=165, scenicrun=240)


def resolve_plan(args):
    keys, repeats = PRESETS[args.preset]
    spec = args.rides if args.rides is not None else (",".join(keys) if keys else "all")
    args.rides = spec
    if args.repeats is None:
        args.repeats = repeats
    return args


def ride_timeout(key, args, completed_durations=()):
    if args.max_duration is not None:
        return float(args.max_duration)
    known = list(completed_durations)
    nominal = max(known) if known else DURATION_HINTS.get(key)
    return min(600., max(90., 1.5 * nominal + 45.)) if nominal else 600.
