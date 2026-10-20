#protocol.py  ver 3
from __future__ import annotations

import json
from typing import Any, Mapping

PROTOCOL_VERSION = 1
SERVER_VERSION = "0.2"

TYPE_PING = "ping"
TYPE_PONG = "pong"
TYPE_STARTUP = "startup"
TYPE_SELECT_SIM = "select_sim"
TYPE_COMMAND = "command"
TYPE_ACK = "ack"
TYPE_FAN_POWER = "fan_power"


def encode_message(message: Mapping[str, Any]) -> bytes:
    """Encode one SHS message as a newline-terminated UTF-8 JSON datagram."""
    return (json.dumps(dict(message), separators=(",", ":")) + "\n").encode("utf-8")


def decode_message(data: bytes | str) -> dict[str, Any]:
    if isinstance(data, bytes):
        text = data.decode("utf-8")
    else:
        text = str(data)
    message = json.loads(text.strip())
    if not isinstance(message, dict):
        raise ValueError("SHS message must be a JSON object")
    return message


def make_ack(request_id: int, ok: bool, **fields: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "type": TYPE_ACK,
        "id": int(request_id),
        "ok": bool(ok),
        "protocol": PROTOCOL_VERSION,
    }
    message.update(fields)
    return message
