# test_shs.py  ver 2

import itertools
import json
import socket
import threading
import time

SHS_IP = "192.168.1.93"   # Windows sim PC / SHS address
SHS_PORT = 10040
TELEMETRY_PORT = 10009
RECOVERY_TIMEOUT_S = 20.0
READY_DELAY_S = 1.0

request_ids = itertools.count(1)
running = True
recovery_active = False
recovery_deadline = 0.0
ready_due = None

control_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
control_sock.bind(("", 0))
control_sock.settimeout(0.5)

telemetry_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
telemetry_sock.bind(("", TELEMETRY_PORT))
telemetry_sock.settimeout(0.5)


def stamp():
    return time.strftime("%H:%M:%S")


def send_message(message):
    data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
    control_sock.sendto(data, (SHS_IP, SHS_PORT))


def send_command(command):
    request_id = next(request_ids)
    print(f"\n{stamp()} TX command: {command}")
    send_message({
        "type": "command",
        "id": request_id,
        "protocol": 1,
        "target": "spacecoaster",
        "command": command,
    })


def heartbeat_loop():
    while running:
        try:
            send_message({"type": "ping", "id": next(request_ids), "protocol": 1})
        except OSError:
            return
        time.sleep(1.0)


def shs_rx_loop():
    while running:
        try:
            data, addr = control_sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError:
            return
        text = data.decode("utf-8", errors="replace").strip()
        text = data.decode("utf-8", errors="replace").strip()
        if '"type":"pong"' not in text:
            print(f"\n{stamp()} SHS {addr}: {text}")


def telemetry_rx_loop():
    global recovery_active, ready_due

    while running:
        try:
            data, addr = telemetry_sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            return

        text = data.decode("ascii", errors="replace").strip()

        # Print control/state/config messages; don't flood the screen with xyz telemetry.
        if text.startswith(("state,", "command,", "config,")):
            print(f"\n{stamp()} SC  {addr}: {text}")

        if not recovery_active or not text.startswith("state,"):
            continue

        try:
            state = int(text.split(",", 1)[1])
        except ValueError:
            continue

        if state == 1 and ready_due is None:       # WAITING / lobby
            ready_due = time.monotonic() + READY_DELAY_S
            print(f"\n{stamp()} Recovery: WAITING detected; ready in {READY_DELAY_S:.1f}s")
        elif state == 2:                           # READY for dispatch
            recovery_active = False
            ready_due = None
            print(f"\n{stamp()} RECOVERY COMPLETE - READY FOR DISPATCH")


def recovery_loop():
    global recovery_active, ready_due

    while running:
        now = time.monotonic()
        if recovery_active:
            if ready_due is not None and now >= ready_due:
                ready_due = None
                send_command("ready")
            if now >= recovery_deadline:
                recovery_active = False
                ready_due = None
                print(f"\n{stamp()} RECOVERY TIMEOUT - READY not detected")
        time.sleep(0.05)


def start_recovery():
    global recovery_active, recovery_deadline, ready_due
    recovery_active = True
    ready_due = None
    recovery_deadline = time.monotonic() + RECOVERY_TIMEOUT_S
    print(f"\n{stamp()} Starting E-stop recovery test")
    send_command("lobby")


def show_menu():
    print("\nSpaceCoaster SHS control test")
    print("  d  dispatch")
    print("  p  pause")
    print("  u  resume")
    print("  l  return to MenuMSU lobby")
    print("  r  ready")
    print("  v  reset VR")
    print("  e  E-stop recovery test (lobby -> waiting -> ready)")
    print("  q  quit\n")


def main():
    global running

    threads = [
        threading.Thread(target=heartbeat_loop, daemon=True),
        threading.Thread(target=shs_rx_loop, daemon=True),
        threading.Thread(target=telemetry_rx_loop, daemon=True),
        threading.Thread(target=recovery_loop, daemon=True),
    ]
    for thread in threads:
        thread.start()

    show_menu()
    try:
        while True:
            key = input("> ").strip().lower()
            if key == "q":
                break
            elif key == "d":
                send_command("run")
            elif key == "p":
                send_command("pause")
            elif key == "u":
                send_command("resume")
            elif key == "l":
                send_command("lobby")
            elif key == "r":
                send_command("ready")
            elif key == "v":
                send_command("reset_vr")
            elif key == "e":
                start_recovery()
            else:
                print("Unknown command")
    finally:
        running = False
        control_sock.close()
        telemetry_sock.close()


if __name__ == "__main__":
    main()
