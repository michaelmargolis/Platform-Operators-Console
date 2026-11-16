# clock.py ver 1
"""High-resolution interval clock, expressed in the production monotonic epoch.

Windows/Python 3.8-3.10 monotonic can be coarser than perf_counter. Calibrate
ONCE, then advance exclusively with perf_counter_ns; never readjust during a
trial. Legacy *_monotonic_s columns keep their epoch, not their old resolution.
Production NL2 state-machine clocks are deliberately not patched by capture.
"""
import time

_p0 = time.perf_counter_ns()
_m = time.monotonic_ns()
_p1 = time.perf_counter_ns()
_OFFSET_NS = _m - (_p0 + _p1) // 2


def clock_ns():
    return time.perf_counter_ns() + _OFFSET_NS


def clock():
    return clock_ns() / 1e9


def description():
    return {"implementation": "perf_counter_ns + fixed monotonic epoch offset",
            "resolution_s": time.get_clock_info("perf_counter").resolution,
            "production_event_resolution_s": time.get_clock_info("monotonic").resolution,
            "epoch_calibration_span_ns": _p1 - _p0,
            "epoch_offset_ns": _OFFSET_NS}
