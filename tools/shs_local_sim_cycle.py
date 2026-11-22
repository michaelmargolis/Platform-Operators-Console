#shs_local_sim_cycle.py ver 6
"""Local Windows stress test for repeated simulator startup/switching.

Run this on the SHS Windows PC with the normal Sim Host Service stopped. The
script reuses SHS WindowsSimManager for simulator close/select behavior, but
launches SpaceCoaster directly when test instrumentation is enabled so each
Unity start can receive its own -logFile path.

At startup choose one of three modes:
  NoLimits     - repeatedly close/restart NoLimits2 and wait for play mode.
  SpaceCoaster - repeatedly close/restart SpaceCoaster and wait for READY.
  Alternate    - select NoLimits2, wait ready, then select SpaceCoaster and
                 wait ready; repeat.

NoLimits2 is deliberately monitored with the raw telemetry protocol. The normal
POC NoLimits2 adapter loads/configures rides on connection and is therefore not
appropriate for this startup-switch stress test: the launcher already opens the
park with --openpark --openpaused.

SpaceCoaster normally reports WAITING (state,1) first. This utility sends the
same delayed control,ready message used by the deployed POC/SHS path, then
retries it once per second while SpaceCoaster remains in WAITING. Every
SpaceCoaster launch gets a unique Unity log. OVRService is restarted before
each SpaceCoaster launch by default; use --dont_restart_oculus to disable that
for comparison testing.
"""
from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim_host_service.server import WindowsSimManager, load_config
from sims.nolimits2.nl2_protocol import NL2Protocol


LOCALHOST = "127.0.0.1"
SPACECOASTER_PORT = 10009
DEFAULT_TIMEOUT_S = 120.0
POLL_S = 0.05
NL2_TELEMETRY_POLL_S = 0.10
NL2_RECONNECT_S = 0.50
SPACECOASTER_READY_DELAY_S = 1.0
SPACECOASTER_READY_RETRY_S = 1.0
SPACECOASTER_MAX_READY_ATTEMPTS = 10
DEFAULT_OCULUS_SETTLE_S = 2.0

MODE_NOLIMITS = "nolimits"
MODE_SPACECOASTER = "spacecoaster"
MODE_ALTERNATE = "alternate"

_SPACECOASTER_EXE_RE = re.compile(r'"([^"\r\n]+?\.exe)"', re.IGNORECASE)


def _format_elapsed(started: float) -> str:
    return f"{time.monotonic() - started:.1f}s"


def _wall_time() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def wait_for_nolimits_ready(timeout_s: float, protocol_factory=NL2Protocol) -> float:
    """Wait for NL2's own telemetry play-mode flag without issuing ride commands.

    The NL2 telemetry specification defines state bit 0 as "in play mode". For
    an attraction park opened paused, this is the signal that loading has
    finished and the park is ready to run. Expected "Not in play mode" replies
    during startup are informational only.
    """
    client = protocol_factory(LOCALHOST, connect_timeout=0.5)
    started = time.monotonic()
    deadline = started + timeout_s
    next_connect = started
    next_telemetry_request = started
    last_text = None
    last_error = None

    def report(text: str) -> None:
        nonlocal last_text
        if text != last_text:
            print(f"  NL2 [{_format_elapsed(started)}] {text}", flush=True)
            last_text = text

    try:
        while time.monotonic() < deadline:
            now = time.monotonic()

            if not client.is_connected:
                if now >= next_connect:
                    if client.connect():
                        report("telemetry TCP connected; waiting for play mode")
                        next_telemetry_request = now
                    else:
                        report("waiting for telemetry TCP connection")
                    next_connect = now + NL2_RECONNECT_S
                time.sleep(POLL_S)
                continue

            client.service()
            now = time.monotonic()
            telemetry = client.latest_telemetry
            if telemetry is not None and telemetry.in_play_mode:
                paused = bool(int(telemetry.state) & 0x04)
                report(
                    f"play mode ready (state={int(telemetry.state)}, "
                    f"frame={int(telemetry.frame)}, paused={paused})"
                )
                return now - started

            error = str(client.last_error or "").strip()
            if error and error != last_error:
                last_error = error
                if "not in play mode" in error.casefold():
                    report("park loading; telemetry not in play mode yet")
                else:
                    report(f"telemetry API: {error}")

            if now >= next_telemetry_request:
                try:
                    client.request_telemetry()
                except ConnectionError:
                    client.disconnect()
                    report("telemetry connection lost; reconnecting")
                    next_connect = now + NL2_RECONNECT_S
                next_telemetry_request = now + NL2_TELEMETRY_POLL_S

            time.sleep(POLL_S)
    finally:
        client.disconnect()

    raise TimeoutError(
        f"NoLimits2 did not enter telemetry play mode within {timeout_s:g}s; "
        f"last status: {last_text or 'no status'}"
    )


def _parse_spacecoaster_state(message: str) -> Optional[int]:
    fields = message.strip().split(",")
    if len(fields) < 2 or fields[0] != "state":
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def _is_udp_connection_reset(exc: OSError) -> bool:
    """True for Windows UDP WSAECONNRESET (WinError 10054)."""
    return getattr(exc, "winerror", None) == 10054 or getattr(exc, "errno", None) == 10054


def wait_for_spacecoaster_ready(sock: socket.socket, timeout_s: float) -> float:
    started = time.monotonic()
    deadline = started + timeout_s
    endpoint = None
    next_ready_at = None
    ready_attempts = 0
    udp_resets = 0
    retries_exhausted_reported = False
    last_state = None

    while time.monotonic() < deadline:
        now = time.monotonic()
        if (
            last_state == 1
            and endpoint is not None
            and next_ready_at is not None
            and now >= next_ready_at
            and ready_attempts < SPACECOASTER_MAX_READY_ATTEMPTS
        ):
            ready_attempts += 1
            target = endpoint
            try:
                sock.sendto(b"control,ready", target)
            except OSError as exc:
                if not _is_udp_connection_reset(exc):
                    raise
                udp_resets += 1
                print(
                    f"  SpaceCoaster [{_format_elapsed(started)}] UDP 10054 while sending "
                    f"ready attempt {ready_attempts}/{SPACECOASTER_MAX_READY_ATTEMPTS} "
                    f"to {target[0]}:{target[1]}; endpoint reset, continuing",
                    flush=True,
                )
                endpoint = None
                next_ready_at = None
                continue
            print(
                f"  SpaceCoaster [{_format_elapsed(started)}] ready attempt "
                f"{ready_attempts}/{SPACECOASTER_MAX_READY_ATTEMPTS} -> "
                f"{target[0]}:{target[1]}",
                flush=True,
            )
            next_ready_at = now + SPACECOASTER_READY_RETRY_S
        elif (
            last_state == 1
            and ready_attempts >= SPACECOASTER_MAX_READY_ATTEMPTS
            and not retries_exhausted_reported
        ):
            print(
                f"  SpaceCoaster [{_format_elapsed(started)}] "
                "ready retries exhausted; still waiting for state,2",
                flush=True,
            )
            retries_exhausted_reported = True

        try:
            raw, peer = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError as exc:
            if not _is_udp_connection_reset(exc):
                raise
            udp_resets += 1
            old_endpoint = (
                "none" if endpoint is None else f"{endpoint[0]}:{endpoint[1]}"
            )
            print(
                f"  SpaceCoaster [{_format_elapsed(started)}] UDP 10054 from stale "
                f"endpoint {old_endpoint}; clearing endpoint and continuing",
                flush=True,
            )
            endpoint = None
            next_ready_at = None
            continue

        try:
            message = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue

        state = _parse_spacecoaster_state(message)
        if state is None:
            continue

        if state != last_state:
            print(
                f"  SpaceCoaster [{_format_elapsed(started)}] "
                f"state,{state} from {peer[0]}:{peer[1]}",
                flush=True,
            )
            last_state = state

        if state == 1:
            if endpoint != peer:
                old_endpoint = (
                    "none" if endpoint is None else f"{endpoint[0]}:{endpoint[1]}"
                )
                endpoint = peer
                print(
                    f"  SpaceCoaster [{_format_elapsed(started)}] "
                    f"WAITING endpoint {old_endpoint} -> {endpoint[0]}:{endpoint[1]}",
                    flush=True,
                )
            if next_ready_at is None:
                next_ready_at = time.monotonic() + SPACECOASTER_READY_DELAY_S
        elif state == 2:
            return time.monotonic() - started

    endpoint_text = "none" if endpoint is None else f"{endpoint[0]}:{endpoint[1]}"
    raise TimeoutError(
        f"SpaceCoaster did not report READY (state,2) within {timeout_s:g}s; "
        f"last state: {last_state!r}; Unity endpoint: {endpoint_text}; "
        f"ready attempts: {ready_attempts}; UDP resets: {udp_resets}"
    )


def _open_spacecoaster_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.1)
    try:
        sock.bind((LOCALHOST, SPACECOASTER_PORT))
    except OSError:
        sock.close()
        raise RuntimeError(
            f"Unable to bind {LOCALHOST}:{SPACECOASTER_PORT}. Stop the normal Sim Host Service "
            "before running this local switch test."
        )
    return sock


def select_sim(manager: WindowsSimManager, sim_key: str) -> None:
    ok, detail = manager.select_sim(sim_key)
    print(f"  select {sim_key}: {detail}", flush=True)
    if not ok:
        raise RuntimeError(detail)


def restart_sim(manager: WindowsSimManager, sim_key: str) -> None:
    """Close the selected simulator if running, then start/select it again."""
    if manager.is_running(sim_key):
        ok, detail = manager.close_sim(sim_key)
        print(f"  close {sim_key}: {detail}", flush=True)
        if not ok:
            raise RuntimeError(detail)
    select_sim(manager, sim_key)


def _spacecoaster_exe(manager: WindowsSimManager) -> Path:
    """Read the executable path from the configured SpaceCoaster start batch."""
    cfg = manager.sims["spacecoaster"]
    if not cfg.start_batch:
        raise RuntimeError("SpaceCoaster has no start_batch configured")
    batch_path = manager.sims_dir / cfg.start_batch
    try:
        batch_text = batch_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RuntimeError(f"Unable to read SpaceCoaster launcher {batch_path}: {exc}") from exc

    for raw_line in batch_text.splitlines():
        line = raw_line.strip()
        if not line or line.casefold().startswith(("rem ", "::")):
            continue
        match = _SPACECOASTER_EXE_RE.search(line)
        if match:
            return Path(os.path.expandvars(match.group(1)))

    raise RuntimeError(
        f"Could not find a quoted .exe path in SpaceCoaster launcher {batch_path}"
    )


def _is_admin() -> bool:
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def restart_oculus_service(settle_s: float) -> None:
    """Restart OVRService and wait for Running state; requires elevation."""
    if not _is_admin():
        raise RuntimeError(
            "Oculus restart requires an Administrator command prompt; "
            "use --dont_restart_oculus to disable it"
        )

    print(f"  [{_wall_time()}] restarting Oculus OVRService...", flush=True)
    command = (
        "$ErrorActionPreference='Stop'; "
        "Restart-Service -Name 'OVRService' -Force; "
        "$svc=Get-Service -Name 'OVRService'; "
        "$svc.WaitForStatus('Running',[TimeSpan]::FromSeconds(20)); "
        "Write-Output $svc.Status"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown PowerShell error").strip()
        raise RuntimeError(f"OVRService restart failed: {detail}")
    status = (result.stdout or "").strip() or "Running"
    print(f"  [{_wall_time()}] OVRService status: {status}", flush=True)
    if settle_s > 0:
        print(f"  waiting {settle_s:g}s for Oculus runtime settle", flush=True)
        time.sleep(settle_s)


def launch_spacecoaster_with_log(
    manager: WindowsSimManager,
    *,
    cycle: int,
    log_dir: Path,
) -> Path:
    """Launch SpaceCoaster directly with a unique Unity -logFile argument."""
    exe_path = _spacecoaster_exe(manager)
    if not exe_path.is_file():
        raise RuntimeError(f"SpaceCoaster executable not found: {exe_path}")

    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = (log_dir / f"spacecoaster_cycle_{cycle:03d}_{stamp}.log").resolve()
    try:
        proc = subprocess.Popen(
            [str(exe_path), "-logFile", str(log_path)],
            cwd=str(exe_path.parent),
        )
    except OSError as exc:
        raise RuntimeError(f"Unable to launch SpaceCoaster: {exc}") from exc

    print(f"  [{_wall_time()}] SpaceCoaster launch PID {proc.pid}", flush=True)
    print(f"  Unity log: {log_path}", flush=True)
    return log_path


def _close_if_running(manager: WindowsSimManager, sim_key: str) -> None:
    if not manager.is_running(sim_key):
        return
    ok, detail = manager.close_sim(sim_key)
    print(f"  close {sim_key}: {detail}", flush=True)
    if not ok:
        raise RuntimeError(detail)


def start_spacecoaster_test_instance(
    manager: WindowsSimManager,
    *,
    cycle: int,
    log_dir: Path,
    restart_oculus: bool,
    oculus_settle_s: float,
) -> Path:
    """Close competing sims, optionally reset Oculus, then launch instrumented SC."""
    _close_if_running(manager, "nolimits2")
    _close_if_running(manager, "spacecoaster")
    if restart_oculus:
        restart_oculus_service(oculus_settle_s)
    return launch_spacecoaster_with_log(manager, cycle=cycle, log_dir=log_dir)


def prompt_mode() -> str:
    aliases = {
        "n": MODE_NOLIMITS,
        "nl2": MODE_NOLIMITS,
        "nolimits": MODE_NOLIMITS,
        "nolimits2": MODE_NOLIMITS,
        "s": MODE_SPACECOASTER,
        "sc": MODE_SPACECOASTER,
        "spacecoaster": MODE_SPACECOASTER,
        "a": MODE_ALTERNATE,
        "alternate": MODE_ALTERNATE,
    }
    while True:
        answer = input("Test mode [NoLimits / SpaceCoaster / Alternate]: ").strip().casefold()
        mode = aliases.get(answer)
        if mode is not None:
            return mode
        print("Please enter NoLimits, SpaceCoaster, or Alternate.", flush=True)


def run_nolimits_cycles(manager: WindowsSimManager, *, cycles: int, timeout_s: float) -> int:
    cycle = 0
    while cycles <= 0 or cycle < cycles:
        cycle += 1
        print(f"\n=== Cycle {cycle}: NoLimits2 restart ===", flush=True)
        restart_sim(manager, "nolimits2")
        elapsed = wait_for_nolimits_ready(timeout_s)
        print(f"  NoLimits2 READY TO START after {elapsed:.1f}s", flush=True)
    return cycle


def run_spacecoaster_cycles(
    manager: WindowsSimManager,
    *,
    cycles: int,
    timeout_s: float,
    log_dir: Path,
    restart_oculus: bool,
    oculus_settle_s: float,
) -> int:
    cycle = 0
    while cycles <= 0 or cycle < cycles:
        cycle += 1
        print(f"\n=== Cycle {cycle}: SpaceCoaster restart ===", flush=True)
        with _open_spacecoaster_socket() as sock:
            log_path = start_spacecoaster_test_instance(
                manager,
                cycle=cycle,
                log_dir=log_dir,
                restart_oculus=restart_oculus,
                oculus_settle_s=oculus_settle_s,
            )
            try:
                elapsed = wait_for_spacecoaster_ready(sock, timeout_s)
            except Exception:
                print(f"  Failed-cycle Unity log: {log_path}", flush=True)
                raise
        print(f"  SpaceCoaster READY TO START after {elapsed:.1f}s", flush=True)
    return cycle


def run_alternate_cycles(
    manager: WindowsSimManager,
    *,
    cycles: int,
    timeout_s: float,
    log_dir: Path,
    restart_oculus: bool,
    oculus_settle_s: float,
) -> int:
    cycle = 0
    while cycles <= 0 or cycle < cycles:
        cycle += 1
        print(f"\n=== Cycle {cycle}: NoLimits2 ===", flush=True)
        select_sim(manager, "nolimits2")
        elapsed = wait_for_nolimits_ready(timeout_s)
        print(f"  NoLimits2 READY TO START after {elapsed:.1f}s", flush=True)

        print(f"\n=== Cycle {cycle}: SpaceCoaster ===", flush=True)
        with _open_spacecoaster_socket() as sock:
            log_path = start_spacecoaster_test_instance(
                manager,
                cycle=cycle,
                log_dir=log_dir,
                restart_oculus=restart_oculus,
                oculus_settle_s=oculus_settle_s,
            )
            try:
                elapsed = wait_for_spacecoaster_ready(sock, timeout_s)
            except Exception:
                print(f"  Failed-cycle Unity log: {log_path}", flush=True)
                raise
        print(f"  SpaceCoaster READY TO START after {elapsed:.1f}s", flush=True)

    return cycle


def run_cycles(
    manager: WindowsSimManager,
    *,
    mode: str,
    cycles: int,
    timeout_s: float,
    log_dir: Path,
    restart_oculus: bool,
    oculus_settle_s: float,
) -> int:
    if mode == MODE_NOLIMITS:
        return run_nolimits_cycles(manager, cycles=cycles, timeout_s=timeout_s)
    if mode == MODE_SPACECOASTER:
        return run_spacecoaster_cycles(
            manager,
            cycles=cycles,
            timeout_s=timeout_s,
            log_dir=log_dir,
            restart_oculus=restart_oculus,
            oculus_settle_s=oculus_settle_s,
        )
    if mode == MODE_ALTERNATE:
        return run_alternate_cycles(
            manager,
            cycles=cycles,
            timeout_s=timeout_s,
            log_dir=log_dir,
            restart_oculus=restart_oculus,
            oculus_settle_s=oculus_settle_s,
        )
    raise ValueError(f"unknown test mode: {mode}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repeatedly restart NoLimits2, restart SpaceCoaster, or alternate between them, "
            "advancing only when the selected simulator reports ready to start."
        )
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=0,
        help="number of test cycles; 0 means run until Ctrl+C/failure",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"ready-to-start timeout per simulator in seconds (default {DEFAULT_TIMEOUT_S:g})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "sim_host_service" / "sim_host_service.ini",
        help="SHS INI file used for simulator window/process definitions",
    )
    parser.add_argument(
        "--spacecoaster-log-dir",
        type=Path,
        default=None,
        help=(
            "directory for per-launch SpaceCoaster Unity logs; default is "
            "<repo>/logs/shs_local_sim_cycle/<session>"
        ),
    )
    parser.add_argument(
        "--dont_restart_oculus",
        action="store_true",
        help=(
            "do not restart OVRService before SpaceCoaster launches; by default "
            "the harness restarts it before every SpaceCoaster start"
        ),
    )
    parser.add_argument(
        "--oculus-settle",
        type=float,
        default=DEFAULT_OCULUS_SETTLE_S,
        help=(
            "seconds to wait after OVRService restart before launching SpaceCoaster "
            f"(default {DEFAULT_OCULUS_SETTLE_S:g})"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if os.name != "nt":
        print("ERROR: this test must be run on the Windows SHS PC.", file=sys.stderr)
        return 2
    if args.cycles < 0:
        print("ERROR: --cycles must be 0 or greater.", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("ERROR: --timeout must be greater than zero.", file=sys.stderr)
        return 2
    if args.oculus_settle < 0:
        print("ERROR: --oculus-settle must be zero or greater.", file=sys.stderr)
        return 2

    try:
        _server_cfg, sims = load_config(args.config.resolve())
        manager = WindowsSimManager(sims)
        # Preflight the SpaceCoaster port before changing either simulator.
        with _open_spacecoaster_socket():
            pass
        print("Local simulator startup/switch cycle test", flush=True)
        print(f"Config: {args.config.resolve()}", flush=True)
        print("Normal Sim Host Service must remain stopped while this test runs.", flush=True)
        mode = prompt_mode()
        print(f"Mode: {mode}", flush=True)

        session_stamp = time.strftime("%Y%m%d_%H%M%S")
        if args.spacecoaster_log_dir is None:
            log_dir = ROOT / "logs" / "shs_local_sim_cycle" / session_stamp
        else:
            log_dir = args.spacecoaster_log_dir.expanduser().resolve()
        if mode in {MODE_SPACECOASTER, MODE_ALTERNATE}:
            log_dir.mkdir(parents=True, exist_ok=True)
            print(f"SpaceCoaster Unity logs: {log_dir}", flush=True)
            print(
                "Oculus service reset before SpaceCoaster: "
                f"{'OFF' if args.dont_restart_oculus else 'ON'}",
                flush=True,
            )

        completed = run_cycles(
            manager,
            mode=mode,
            cycles=args.cycles,
            timeout_s=args.timeout,
            log_dir=log_dir,
            restart_oculus=not args.dont_restart_oculus,
            oculus_settle_s=args.oculus_settle,
        )
    except KeyboardInterrupt:
        print("\nStopped by operator.", flush=True)
        return 0
    except Exception as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr, flush=True)
        print("Leave the failed simulator running if Oculus/Windows logs are being captured.", flush=True)
        return 1

    print(f"\nCompleted {completed} cycle(s).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
