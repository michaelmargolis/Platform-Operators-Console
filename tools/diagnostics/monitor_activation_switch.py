#!/usr/bin/env python3
# monitor_activation_switch.py ver 1
"""Read-only diagnostic monitor for the POC Activate selector on BCM GPIO 24.

Run this with POC stopped.  It mirrors the current POC button polling logic:
internal pull-up, 50 ms polling, 60 ms debounce, and events on both edges.
It does not send platform or simulator commands.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import time
import warnings


DEFAULT_PIN = 24
DEFAULT_POLL_MS = 50
DEFAULT_DEBOUNCE_MS = 60
DEFAULT_HEARTBEAT_S = 1.0
DEFAULT_LOG = Path.home() / ".local" / "state" / "poc" / "activation_switch_monitor.log"


def state_name(level: int) -> str:
    return "ACTIVATE" if int(level) == 0 else "DEACTIVATE"


class ActivationDebouncer:
    """POC-compatible debounce state with diagnostic event descriptions."""

    def __init__(self, initial_level: int, debounce_s: float):
        self.last_pin_value = int(initial_level)
        self.debounce_s = float(debounce_s)
        self.is_changing = False
        self.last_pin_change_wall = time.time()
        self.accepted_level = int(initial_level)

    def service(self, pin_value: int, wall_now: float) -> list[tuple[str, int, float]]:
        pin_value = int(pin_value)
        events: list[tuple[str, int, float]] = []

        if pin_value != self.last_pin_value:
            self.is_changing = True
            self.last_pin_change_wall = wall_now
            events.append(("raw", pin_value, 0.0))

        self.last_pin_value = pin_value

        if self.is_changing:
            elapsed = wall_now - self.last_pin_change_wall
            if elapsed >= self.debounce_s:
                self.is_changing = False
                self.accepted_level = pin_value
                events.append(("accepted", pin_value, elapsed))

        return events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor only the POC Activate selector; no commands are sent."
    )
    parser.add_argument("--pin", type=int, default=DEFAULT_PIN, help="BCM GPIO pin")
    parser.add_argument("--poll-ms", type=int, default=DEFAULT_POLL_MS)
    parser.add_argument("--debounce-ms", type=int, default=DEFAULT_DEBOUNCE_MS)
    parser.add_argument("--heartbeat-s", type=float, default=DEFAULT_HEARTBEAT_S)
    parser.add_argument("--duration-s", type=float, default=0.0, help="0 runs until Ctrl+C")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    return parser.parse_args()


def configure_logging(log_path: Path) -> logging.Logger:
    log_path = log_path.expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("activation_monitor")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def main() -> int:
    args = parse_args()
    if args.poll_ms <= 0 or args.debounce_ms < 0 or args.heartbeat_s <= 0:
        raise SystemExit("poll-ms and heartbeat-s must be positive; debounce-ms cannot be negative")

    logger = configure_logging(args.log)
    try:
        import RPi.GPIO as GPIO
    except ImportError as exc:
        logger.error("RPi.GPIO is unavailable: %s", exc)
        return 2

    GPIO.setmode(GPIO.BCM)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            GPIO.setup(args.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        initial_level = int(GPIO.input(args.pin))
        debouncer = ActivationDebouncer(initial_level, args.debounce_ms / 1000.0)
        started_mono = time.monotonic()
        previous_wall = time.time()
        previous_mono = started_mono
        next_heartbeat = started_mono

        logger.info(
            "START pin=BCM%d pull=UP poll=%dms debounce=%dms initial_level=%d state=%s",
            args.pin,
            args.poll_ms,
            args.debounce_ms,
            initial_level,
            state_name(initial_level),
        )
        if initial_level == 0:
            logger.warning(
                "Selector is ACTIVATE at startup; POC would require OFF before a later activation edge"
            )
        logger.info("Writing log to %s; press Ctrl+C to stop", args.log.expanduser())

        while True:
            loop_mono = time.monotonic()
            wall_now = time.time()
            pin_value = int(GPIO.input(args.pin))

            wall_delta = wall_now - previous_wall
            mono_delta = loop_mono - previous_mono
            clock_error = wall_delta - mono_delta
            if abs(clock_error) >= 0.100:
                logger.warning(
                    "WALL CLOCK STEP delta=%+.3fs; POC debounce uses wall time",
                    clock_error,
                )
            previous_wall = wall_now
            previous_mono = loop_mono

            for event_type, level, elapsed in debouncer.service(pin_value, wall_now):
                if event_type == "raw":
                    logger.debug(
                        "RAW EDGE level=%d state=%s debounce_started",
                        level,
                        state_name(level),
                    )
                else:
                    logger.info(
                        "ACCEPTED EVENT level=%d action=%s stable_for=%.1fms",
                        level,
                        state_name(level).lower(),
                        elapsed * 1000.0,
                    )

            if loop_mono >= next_heartbeat:
                pending_ms = 0.0
                if debouncer.is_changing:
                    pending_ms = max(0.0, (wall_now - debouncer.last_pin_change_wall) * 1000.0)
                logger.debug(
                    "HEARTBEAT raw_level=%d raw_state=%s accepted_level=%d accepted_state=%s "
                    "debounce_pending=%s pending_for=%.1fms",
                    pin_value,
                    state_name(pin_value),
                    debouncer.accepted_level,
                    state_name(debouncer.accepted_level),
                    debouncer.is_changing,
                    pending_ms,
                )
                next_heartbeat = loop_mono + args.heartbeat_s

            if args.duration_s > 0 and loop_mono - started_mono >= args.duration_s:
                logger.info("Requested duration completed")
                break
            time.sleep(args.poll_ms / 1000.0)

    except KeyboardInterrupt:
        logger.info("Stopped by operator")
    except Exception:
        logger.exception("Monitor failed")
        return 1
    finally:
        try:
            GPIO.cleanup(args.pin)
        except Exception:
            logger.exception("GPIO cleanup failed")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
