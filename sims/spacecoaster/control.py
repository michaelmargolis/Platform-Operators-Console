#control.py  ver 4
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

SPACECOASTER_CONTROL_DEBUG = True


class SpaceCoasterControl:
    """Send semantic SpaceCoaster controls through Sim Host Service.

    SHS forwards these as ``control,<command>`` UDP messages to the Unity
    endpoint learned from SpaceCoaster telemetry.
    """

    def __init__(self, host_service=None):
        self.host_service = host_service

    def set_host_service(self, host_service) -> None:
        self.host_service = host_service

    def _send(self, command: str) -> bool:
        if self.host_service is None:
            log.warning("SpaceCoaster %s ignored: no Sim Host Service client", command)
            return False
        ok = bool(self.host_service.command("spacecoaster", command))
        if SPACECOASTER_CONTROL_DEBUG:
            log.info(
                "SpaceCoaster control request: command=%s queued=%s shs_connected=%s",
                command, ok, bool(getattr(self.host_service, "connected", False)),
            )
        if not ok:
            log.warning("SpaceCoaster control %s was not queued for SHS", command)
        return ok

    def pause(self) -> bool:
        return self._send("pause")

    def resume(self) -> bool:
        return self._send("resume")

    def ready(self) -> bool:
        return self._send("ready")

    def run(self) -> bool:
        return self._send("run")

    def reset_vr(self) -> bool:
        return self._send("reset_vr")

    def lobby(self) -> bool:
        return self._send("lobby")
