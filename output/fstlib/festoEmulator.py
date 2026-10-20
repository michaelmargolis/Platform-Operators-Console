# festoEmulator.py ver 1

import logging
import random
import socket
import sys
import time
import traceback

from PyQt5 import QtCore, QtGui, QtWidgets

import easyip


log = logging.getLogger(__name__)

PRESSURE_CHANNELS = 6
MAX_PRESSURE = 6000
IDLE_TIMEOUT_S = 1.0
MOVING_AVERAGE_WINDOW = 10


class StreamingMovingAverage:
    def __init__(self, window_size):
        self.window_size = window_size
        self.values = []
        self.sum = 0.0

    def next(self, value):
        self.values.append(value)
        self.sum += value
        if len(self.values) > self.window_size:
            self.sum -= self.values.pop(0)
        return self.sum / len(self.values)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        self.requested_pressures = [0] * PRESSURE_CHANNELS
        self.prev_message_time = None
        self.last_message_time = None
        self.ma = StreamingMovingAverage(MOVING_AVERAGE_WINDOW)

        self._build_ui()
        self._setup_socket()
        self._setup_timers()
        self._setup_shortcuts()

        try:
            host_ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            host_ip = "unknown"
        log.info("Festo emulator running on %s", host_ip)

    def _build_ui(self):
        self.setWindowTitle("Festo Emulator")
        self.resize(640, 400)
        self.setMinimumSize(480, 330)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)

        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        status_group = QtWidgets.QGroupBox("Connection")
        status_layout = QtWidgets.QGridLayout(status_group)
        status_layout.setColumnStretch(1, 1)

        status_layout.addWidget(QtWidgets.QLabel("Status:"), 0, 0)
        self.lbl_connection = QtWidgets.QLabel("Idle")
        self.lbl_connection.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.lbl_connection.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Preferred,
        )
        status_layout.addWidget(self.lbl_connection, 0, 1)

        status_layout.addWidget(QtWidgets.QLabel("Message interval:"), 1, 0)
        interval_row = QtWidgets.QHBoxLayout()
        self.lbl_interval = QtWidgets.QLabel("-")
        self.lbl_interval.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        interval_width = self.fontMetrics().horizontalAdvance("000000")
        self.lbl_interval.setMinimumWidth(interval_width)
        interval_row.addWidget(self.lbl_interval)
        interval_row.addWidget(QtWidgets.QLabel("ms"))
        interval_row.addStretch(1)
        status_layout.addLayout(interval_row, 1, 1)

        root.addWidget(status_group)

        pressure_group = QtWidgets.QGroupBox("Actuator pressure")
        pressure_layout = QtWidgets.QGridLayout(pressure_group)
        pressure_layout.setHorizontalSpacing(12)
        pressure_layout.setVerticalSpacing(10)
        pressure_layout.setColumnStretch(1, 1)

        pressure_layout.addWidget(QtWidgets.QLabel("Muscle"), 0, 0)
        pressure_layout.addWidget(QtWidgets.QLabel("Pressure"), 0, 1)
        value_header = QtWidgets.QLabel("Value")
        value_header.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        pressure_layout.addWidget(value_header, 0, 2)

        self.pressure_bars = []
        self.txt_muscles = []
        value_width = self.fontMetrics().horizontalAdvance(str(MAX_PRESSURE)) + 16

        for idx in range(PRESSURE_CHANNELS):
            name = QtWidgets.QLabel(str(idx))
            name.setAlignment(QtCore.Qt.AlignCenter)
            pressure_layout.addWidget(name, idx + 1, 0)

            bar = QtWidgets.QProgressBar()
            bar.setRange(0, MAX_PRESSURE)
            bar.setValue(0)
            bar.setTextVisible(False)
            bar.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Preferred,
            )
            pressure_layout.addWidget(bar, idx + 1, 1)
            self.pressure_bars.append(bar)

            value = QtWidgets.QLabel("0")
            value.setMinimumWidth(value_width)
            value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            pressure_layout.addWidget(value, idx + 1, 2)
            self.txt_muscles.append(value)

        root.addWidget(pressure_group, 1)

        hint = QtWidgets.QLabel("Press Esc to exit")
        hint.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        root.addWidget(hint)

    def _setup_socket(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.bind(("", easyip.EASYIP_PORT))

        self.socket_notifier = QtCore.QSocketNotifier(
            self.sock.fileno(), QtCore.QSocketNotifier.Read, self
        )
        self.socket_notifier.activated.connect(self._socket_ready)

    def _setup_timers(self):
        self.idle_timer = QtCore.QTimer(self)
        self.idle_timer.setInterval(250)
        self.idle_timer.timeout.connect(self._update_connection_state)
        self.idle_timer.start()

    def _setup_shortcuts(self):
        self.escape_shortcut = QtWidgets.QShortcut(
            QtGui.QKeySequence(QtCore.Qt.Key_Escape), self
        )
        self.escape_shortcut.setContext(QtCore.Qt.WindowShortcut)
        self.escape_shortcut.activated.connect(self.close)

    @QtCore.pyqtSlot()
    def _socket_ready(self, _socket_descriptor=None):
        while True:
            try:
                data, addr = self.sock.recvfrom(1024)
            except (BlockingIOError, InterruptedError):
                break
            except OSError as exc:
                log.error("Festo emulator socket error: %s", exc)
                break

            try:
                self._handle_datagram(data, addr)
            except Exception as exc:
                log.error(
                    "Festo emulator receive error: %s\n%s",
                    exc,
                    traceback.format_exc(),
                )

    def _handle_datagram(self, data, addr):
        now = time.monotonic()
        packet = easyip.Packet(data)
        log.debug("received msg from %s: %s", addr, packet)

        # Never acknowledge an acknowledgement/response.  For every valid
        # EasyIP request received from a client, send exactly one response.
        if packet.flags & easyip.Flags.RESPONSE:
            log.debug("ignoring EasyIP response packet from %s", addr)
            return

        if packet.senddata_type == easyip.Operands.FLAG_WORD:
            values = packet.decode_payload(easyip.Packet.DIRECTION_SEND)
            self.show_pressures(values)

        if packet.reqdata_type == easyip.Operands.FLAG_WORD:
            log.debug("acknowledging pressure read request")
            self._send_pressure_response(packet, addr)
        else:
            log.debug("acknowledging EasyIP request")
            self._send_acknowledgement(packet, addr)

        self.lbl_connection.setText("Connected to " + addr[0])

        if self.prev_message_time is not None:
            interval_ms = (now - self.prev_message_time) * 1000.0
            avg_ms = int(round(self.ma.next(interval_ms)))
            self.lbl_interval.setText(str(avg_ms))

        self.prev_message_time = now
        self.last_message_time = now

    def show_pressures(self, pressures):
        values = list(pressures)
        if len(values) < PRESSURE_CHANNELS:
            log.warning(
                "Received %d pressure values; expected %d",
                len(values),
                PRESSURE_CHANNELS,
            )
            return

        values = values[:PRESSURE_CHANNELS]
        if values != self.requested_pressures:
            print("set pressure:", values)

        self.requested_pressures = values
        self.display_pressure_bars(values)

    def display_pressure_bars(self, pressures):
        for idx, pressure in enumerate(pressures):
            value = int(pressure)
            self.txt_muscles[idx].setText(str(value))
            self.pressure_bars[idx].setMaximum(max(MAX_PRESSURE, value))
            self.pressure_bars[idx].setValue(max(0, value))

    def get_emulated_pressures(self):
        return [
            max(int(pressure) + random.randint(-100, 100), 0)
            for pressure in self.requested_pressures
        ]

    def _send_pressure_response(self, request_packet, addr):
        """Return the six emulated pressure words requested by the client."""
        values = self.get_emulated_pressures()
        response = easyip.Factory.response(request_packet, 0)
        response.senddata_type = easyip.Operands.FLAG_WORD
        response.senddata_offset = request_packet.reqdata_offset_client
        response.senddata_size = response.encode_payload(
            values, easyip.Packet.DIRECTION_SEND
        )
        self.sock.sendto(response.pack(), addr)

    def _send_acknowledgement(self, request_packet, addr):
        """Acknowledge a valid EasyIP request with a matching response counter."""
        response = easyip.Factory.response(request_packet, 0)
        self.sock.sendto(response.pack(), addr)

    def _update_connection_state(self):
        if self.last_message_time is None:
            return

        elapsed = time.monotonic() - self.last_message_time
        if elapsed >= IDLE_TIMEOUT_S:
            if self.lbl_connection.text() != "Idle":
                self.lbl_connection.setText("Idle")
                log.debug("Festo emulator idle")
            self.lbl_interval.setText(str(int(round(elapsed * 1000.0))))

    def closeEvent(self, event):
        if hasattr(self, "socket_notifier"):
            self.socket_notifier.setEnabled(False)
        if hasattr(self, "sock"):
            try:
                self.sock.close()
            except OSError:
                pass
        event.accept()


def main():
    log.info("starting Festo emulator")
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    exit_code = app.exec_()
    log.info("Finishing Festo emulator")
    return exit_code


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
    sys.exit(main())
