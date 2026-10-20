# dummySHS.py ver 1
# Standalone test receiver/command sender for patched SpaceCoaster.
# Binds the existing telemetry port, learns Unity's ephemeral source endpoint,
# then sends control commands back to that same socket.

import socket
import threading
import time

BIND = ("192.168.1.93", 10009)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(BIND)
sock.settimeout(0.5)

unity_addr = None
lock = threading.Lock()
running = True
packet_count = 0
last_rate_time = time.monotonic()


def rx_loop():
    global unity_addr, running, packet_count, last_rate_time
    while running:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError:
            break

        with lock:
            unity_addr = addr
        packet_count += 1

        text = data.decode("ascii", errors="replace").strip()
        # Always show state/control-like packets. Rate-limit the very busy telemetry display.
        if text.startswith("state,") or text.startswith("command,") or text.startswith("config,"):
            print(f"\nRX {addr}: {text}")

        now = time.monotonic()
        if now - last_rate_time >= 1.0:
            print(f"\rTelemetry receiving: {packet_count} packets/s   Unity={addr}          ", end="", flush=True)
            packet_count = 0
            last_rate_time = now


def send_command(command):
    with lock:
        addr = unity_addr
    if addr is None:
        print("\nNo Unity endpoint yet. Start SpaceCoaster and wait for telemetry.")
        return
    payload = ("control," + command + "\n").encode("ascii")
    sock.sendto(payload, addr)
    print(f"\nTX {addr}: {payload.decode().strip()}")


thread = threading.Thread(target=rx_loop, daemon=True)
thread.start()

print("dummySHS listening on 127.0.0.1:10009")
print("Start SpaceCoaster, then use:")
print("  p  pause")
print("  u  resume")
print("  l  return to MenuMSU lobby")
print("  a  show learned Unity address")
print("  q  quit")

try:
    while True:
        cmd = input("\ncommand> ").strip().lower()
        if cmd in ("p", "pause"):
            send_command("pause")
        elif cmd in ("u", "resume", "unpause"):
            send_command("resume")
        elif cmd in ("l", "lobby", "reset"):
            send_command("lobby")
        elif cmd in ("a", "addr", "address"):
            with lock:
                print("Unity endpoint:", unity_addr)
        elif cmd in ("q", "quit", "exit"):
            break
        elif cmd:
            print("Use p, u, l, a or q")
finally:
    running = False
    sock.close()
    thread.join(timeout=1.0)
