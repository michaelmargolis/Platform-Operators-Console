#test_fan_serial.py  ver 1
from sim_host_service.fan_serial import FanSerial


class FakeSerial:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.append(data)

    def close(self):
        self.closed = True


def test_successful_open_saves_only_selected_fan_port(tmp_path):
    config = tmp_path / "sim_host_service.ini"
    config.write_text(
        "# retained comment\n[server]\nport = 10040\n\n[fan]\nenabled = true\nport = COM2\n",
        encoding="utf-8",
    )
    streams = []

    def factory(**kwargs):
        stream = FakeSerial(**kwargs)
        streams.append(stream)
        return stream

    fan = FanSerial(config, enabled=False, serial_factory=factory)
    assert fan.open_port("COM9") is True
    assert streams[0].writes == [b"power,0\n"]
    text = config.read_text(encoding="utf-8")
    assert "# retained comment" in text
    assert "port = 10040" in text
    assert "port = COM9" in text


def test_serial_commands_use_comma_protocol_and_close_at_zero(tmp_path):
    config = tmp_path / "sim_host_service.ini"
    config.write_text("[fan]\nport =\n", encoding="utf-8")
    streams = []

    def factory(**kwargs):
        stream = FakeSerial(**kwargs)
        streams.append(stream)
        return stream

    fan = FanSerial(config, enabled=False, serial_factory=factory)
    assert fan.open_port("COM4") is True
    assert fan.set_power(37) is True
    fan.close()
    assert streams[0].writes == [b"power,0\n", b"power,37\n", b"power,0\n"]
    assert streams[0].closed is True
