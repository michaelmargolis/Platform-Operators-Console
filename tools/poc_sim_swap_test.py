#poc_sim_swap_test.py ver 1
"""Lean POC-side simulator swap exerciser.

Run on the Raspberry Pi with the normal POC application stopped and the normal
Windows Sim Host Service running. The script alternates NoLimits2 and
SpaceCoaster through SHS, waits for NL2 play-mode telemetry, and waits for
SpaceCoaster READY. It does not connect to Festo or drive the platform.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import socket
import sys
import time
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim_host_service.client import SimHostClient
from sims.nolimits2.nl2_protocol import NL2Protocol


DEFAULT_SHS_HOST = "192.168.0.4"
DEFAULT_SHS_PORT = 10040
SPACECOASTER_PORT = 10009
DEFAULT_TIMEOUT_S = 120.0
POLL_S = 0.05
NL2_POLL_S = 0.10
NL2_RECONNECT_S = 0.50
SPACECOASTER_READY_DELAY_S = 1.0
SPACECOASTER_READY_RETRY_S = 1.0
SPACECOASTER_READY_MAX_ATTEMPTS = 10


def _elapsed(started: float) -> str:
    return f"{time.monotonic() - started:.1f}s"


def _parse_spacecoaster_state(message: str) -> Optional[int]:
    fields = message.strip().split(",")
    if len(fields) < 2 or fields[0] != "state":
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def _open_spacecoaster_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.1)
    try:
        sock.bind(("", SPACECOASTER_PORT))
    except OSError:
        sock.close()
        raise RuntimeError(
            f"Unable to bind UDP {SPACECOASTER_PORT}. Stop the normal POC "
            "application before running this swap test."
        )
    return sock


def _drain_socket(sock: socket.socket) -> None:
    sock.setblocking(False)
    try:
        while True:
            try:
                sock.recvfrom(65535)
            except BlockingIOError:
                return
    finally:
        sock.settimeout(0.1)


def wait_for_shs(client: SimHostClient, timeout_s: float) -> float:
    started = time.monotonic()
    deadline = started + timeout_s
    while time.monotonic() < deadline:
        client.service()
        if client.connected:
            return time.monotonic() - started
        time.sleep(POLL_S)
    raise TimeoutError(
        f"Sim Host Service did not connect within {timeout_s:g}s "
        f"({client.address}:{client.port}); last error: {client.last_error or 'none'}"
    )


def select_and_wait_for_shs(
    client: SimHostClient,
    sim_key: str,
    timeout_s: float,
) -> float:
    if not client.connected:
        wait_for_shs(client, timeout_s)
    if not client.select_sim(sim_key):
        raise RuntimeError(client.last_error or f"Unable to select {sim_key}")

    started = time.monotonic()
    deadline = started + timeout_s
    last_active = None
    while time.monotonic() < deadline:
        client.service()
        active = client.active_sim
        if active != last_active:
            print(
                f"  SHS [{_elapsed(started)}] active={active or '<none>'}",
                flush=True,
            )
            last_active = active
        if client.connected and active == sim_key:
            return time.monotonic() - started
        time.sleep(POLL_S)

    raise TimeoutError(
        f"SHS did not report active simulator {sim_key!r} within {timeout_s:g}s; "
        f"active={client.active_sim!r}; ack={client.last_ack!r}; "
        f"error={client.last_error!r}"
    )


def wait_for_nolimits_play_mode(
    host: str,
    timeout_s: float,
    protocol_factory=NL2Protocol,
) -> float:
    client = protocol_factory(host, connect_timeout=0.5)
    started = time.monotonic()
    deadline = started + timeout_s
    next_connect = started
    next_request = started
    last_status = None

    def report(text: str) -> None:
        nonlocal last_status
        if text != last_status:
            print(f"  NL2 [{_elapsed(started)}] {text}", flush=True)
            last_status = text

    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if not client.is_connected:
                if now >= next_connect:
                    if client.connect():
                        report("telemetry connected; waiting for play mode")
                        next_request = now
                    else:
                        report("waiting for telemetry connection")
                    next_connect = now + NL2_RECONNECT_S
                time.sleep(POLL_S)
                continue

            client.service()
            telemetry = client.latest_telemetry
            if telemetry is not None and telemetry.in_play_mode:
                report(
                    f"play mode ready (state={int(telemetry.state)}, "
                    f"frame={int(telemetry.frame)})"
                )
                return time.monotonic() - started

            error = str(client.last_error or "").strip()
            if error:
                if "not in play mode" in error.casefold():
                    report("park loading; not in play mode yet")
                else:
                    report(f"telemetry API: {error}")

            now = time.monotonic()
            if now >= next_request:
                try:
                    client.request_telemetry()
                except ConnectionError:
                    client.disconnect()
                    report("telemetry connection lost; reconnecting")
                    next_connect = now + NL2_RECONNECT_S
                next_request = now + NL2_POLL_S
            time.sleep(POLL_S)
    finally:
        client.disconnect()

    raise TimeoutError(
        f"NoLimits2 did not enter play mode within {timeout_s:g}s; "
        f"last status: {last_status or 'none'}"
    )


def wait_for_spacecoaster_ready(
    client: SimHostClient,
    sock: socket.socket,
    timeout_s: float,
) -> float:
    started = time.monotonic()
    deadline = started + timeout_s
    last_state = None
    next_ready_at = None
    ready_attempts = 0

    while time.monotonic() < deadline:
        client.service()
        now = time.monotonic()

        if (
            last_state == 1
            and next_ready_at is not None
            and now >= next_ready_at
            and ready_attempts < SPACECOASTER_READY_MAX_ATTEMPTS
        ):
            ready_attempts += 1
            queued = client.command("spacecoaster", "ready")
            print(
                f"  SpaceCoaster [{_elapsed(started)}] ready attempt "
                f"{ready_attempts}/{SPACECOASTER_READY_MAX_ATTEMPTS} "
                f"queued={queued}",
                flush=True,
            )
            next_ready_at = now + SPACECOASTER_READY_RETRY_S

        try:
            raw, peer = sock.recvfrom(65535)
        except socket.timeout:
            time.sleep(POLL_S)
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
                f"  SpaceCoaster [{_elapsed(started)}] state,{state} "
                f"via {peer[0]}:{peer[1]}",
                flush=True,
            )
            last_state = state

        if state == 1 and next_ready_at is None:
            next_ready_at = time.monotonic() + SPACECOASTER_READY_DELAY_S
        elif state == 2:
            return time.monotonic() - started
        elif state != 1:
            next_ready_at = None

    raise TimeoutError(
        f"SpaceCoaster did not reach READY within {timeout_s:g}s; "
        f"last state={last_state!r}; ready attempts={ready_attempts}; "
        f"SHS active={client.active_sim!r}; error={client.last_error!r}"
    )


def run_cycles(
    client: SimHostClient,
    sc_sock: socket.socket,
    *,
    host: str,
    cycles: int,
    timeout_s: float,
) -> int:
    cycle = 0
    while cycles <= 0 or cycle < cycles:
        cycle += 1
        print(f"\n=== Cycle {cycle}: NoLimits2 ===", flush=True)
        select_and_wait_for_shs(client, "nolimits2", timeout_s)
        elapsed = wait_for_nolimits_play_mode(host, timeout_s)
        print(f"  NoLimits2 ready after {elapsed:.1f}s", flush=True)

        print(f"\n=== Cycle {cycle}: SpaceCoaster ===", flush=True)
        _drain_socket(sc_sock)
        select_and_wait_for_shs(client, "spacecoaster", timeout_s)
        elapsed = wait_for_spacecoaster_ready(client, sc_sock, timeout_s)
        print(f"  SpaceCoaster ready after {elapsed:.1f}s", flush=True)
    return cycle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lean POC-side NoLimits2/SpaceCoaster swap exerciser"
    )
    parser.add_argument("--host", default=DEFAULT_SHS_HOST, help="SHS/simulator PC address")
    parser.add_argument("--port", type=int, default=DEFAULT_SHS_PORT, help="SHS UDP port")
    parser.add_argument(
        "--cycles", type=int, default=0,
        help="number of NL2 -> SpaceCoaster cycles; 0 means run until Ctrl+C/failure",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_S,
        help=f"timeout for each readiness step (default {DEFAULT_TIMEOUT_S:g}s)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.cycles < 0:
        print("ERROR: --cycles must be 0 or greater", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("ERROR: --timeout must be greater than zero", file=sys.stderr)
        return 2

    client = SimHostClient(args.host, args.port, heartbeat_interval_s=0.5)
    try:
        with _open_spacecoaster_socket() as sc_sock:
            client.start()
            elapsed = wait_for_shs(client, args.timeout)
            print("POC simulator swap test", flush=True)
            print(f"SHS connected at {args.host}:{args.port} after {elapsed:.1f}s", flush=True)
            print("Normal POC application must remain stopped while this test runs.", flush=True)
            completed = run_cycles(
                client,
                sc_sock,
                host=args.host,
                cycles=args.cycles,
                timeout_s=args.timeout,
            )
    except KeyboardInterrupt:
        print("\nStopped by operator.", flush=True)
        return 0
    except Exception as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        client.stop()

    print(f"\nCompleted {completed} cycle(s).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
