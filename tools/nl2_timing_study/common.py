# common.py ver 1
"""Study metadata and IO; standard library only."""
from __future__ import annotations
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import sys
from .system_info import os_details
from datetime import datetime, timezone
import uuid

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "poc.nl2.timing-study/1"


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def unique_id(prefix):
    return f"{prefix}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def sha256(path):
    h = hashlib.sha256()
    try:
        with Path(path).open("rb") as f:
            for block in iter(lambda: f.read(1024*1024), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def machine_info(conditions):
    """Machine identity is always the OS hostname, never a caller-supplied label."""
    import platform
    hostname = socket.gethostname()
    return dict(label=hostname, host=hostname, **os_details(),
                python=sys.version, executable=sys.executable,
                logical_cpus=os.cpu_count(),
                processor=os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor(),
                conditions=conditions, clock="perf_counter_ns mapped to monotonic epoch",
                clock_resolution_s=__import__('time').get_clock_info('perf_counter').resolution,
                clock_details=__import__(__package__ + ".clock", fromlist=["description"]).description())


def source_hashes():
    names = ("tools/capture_nl2_rides.py", "sims/nolimits2/sim.py",
             "sims/nolimits2/nl2_protocol.py", "sims/nolimits2/station_modes.py",
             "sims/nolimits2/ride_config.py", "sims/nolimits2/config.py",
             "tools/nl2_timing_study/capture.py", "tools/nl2_timing_study/protocol.py",
             "tools/nl2_timing_study/system_info.py", "tools/nl2_timing_study/analysis.py",
             "tools/nl2_timing_study/clock.py", "tools/nl2_timing_study/presets.py")
    return {n: sha256(ROOT/n) for n in names}


def finite(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def write_table(path, rows, fields=None):
    rows = list(rows)
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
