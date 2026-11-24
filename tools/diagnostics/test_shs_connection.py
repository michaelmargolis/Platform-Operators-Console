# test_shs_connection.py ver 4
import argparse
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config_store import ConfigStore
from sim_host_service.client import SimHostClient


def configured_endpoint():
    config = ConfigStore(PROJECT_ROOT / "poc_config.json")
    host = config.get("sim_host_service", {})
    return host.get("address", "192.168.0.4"), int(host.get("port", 10040))


def main():
    try:
        default_address, default_port = configured_endpoint()
    except RuntimeError as exc:
        print(f"POC configuration error: {exc}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description="Test connectivity to the POC Sim Host Service")
    parser.add_argument("--address", default=default_address, help="SHS host address")
    parser.add_argument("--port", type=int, default=default_port, help="SHS UDP port")
    parser.add_argument("--seconds", type=float, default=10.0, help="test duration")
    args = parser.parse_args()

    client = SimHostClient(
        args.address,
        args.port,
        heartbeat_interval_s=0.5,
        heartbeat_timeout_s=2.0,
    )
    client.start()

    try:
        deadline = time.monotonic() + max(0.0, args.seconds)
        while time.monotonic() < deadline:
            client.service()
            print(
                f"connected={client.connected} "
                f"server={client.server_version or '-'} "
                f"active_sim={client.active_sim or '-'} "
                f"error={client.last_error or '-'}"
            )
            time.sleep(0.25)
    finally:
        client.stop()


if __name__ == "__main__":
    raise SystemExit(main())
