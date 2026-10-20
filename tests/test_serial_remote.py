# test_serial_remote.py  ver 1
from controls.serial_remote import SerialRemote


def test_serial_remote_maps_physical_commands_to_common_actions():
    events = []
    remote = SerialRemote(
        {
            "activate": lambda: events.append(("activate",)),
            "deactivate": lambda: events.append(("deactivate",)),
            "dispatch": lambda: events.append(("dispatch",)),
            "pause": lambda: events.append(("pause",)),
            "reset_vr": lambda: events.append(("reset_vr",)),
            "intensity": lambda level: events.append(("intensity", level)),
        }
    )

    for message in (
        "activate\r\n",
        "deactivate\r\n",
        "dispatch\r\n",
        "pause\r\n",
        "reset\r\n",
        "intensity=0\r\n",
        "intensity=1\r\n",
        "intensity=2\r\n",
    ):
        remote._rx_queue.put(message)
    remote.service()

    assert events == [
        ("activate",),
        ("deactivate",),
        ("dispatch",),
        ("pause",),
        ("reset_vr",),
        ("intensity", 0),
        ("intensity", 1),
        ("intensity", 2),
    ]
    assert remote.last_event == "intensity=2"


def test_serial_remote_ignores_bad_or_unknown_messages():
    events = []
    remote = SerialRemote({"intensity": lambda level: events.append(level)})
    for message in ("MdxRemote_V1\r\n", "intensity=bad\r\n", "unknown\r\n"):
        remote._rx_queue.put(message)
    remote.service()
    assert events == []
