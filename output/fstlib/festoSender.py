# festoSender.py ver 1

import socket
import sys
import time

from PyQt5 import QtCore, QtGui, QtWidgets

import easyip


PRESSURE_CHANNELS = 6
MAX_PRESSURE_MBAR = 6000
UPDATE_HZ = 20
UPDATE_INTERVAL_MS = int(1000 / UPDATE_HZ)
SLEW_RATE_MBAR_PER_S = 6000
SLEW_STEP_MBAR = SLEW_RATE_MBAR_PER_S / UPDATE_HZ
RESPONSE_TIMEOUT_S = 1.0


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        self.target_pressures = [0] * PRESSURE_CHANNELS
        self.output_pressures = [0.0] * PRESSURE_CHANNELS
        self.last_sent_pressures = None
        self.counter = 0
        self.sent_anything = False
        self.last_response_time = None

        self._build_ui()
        self._setup_socket()
        self._setup_timers()
        self._setup_shortcuts()

    def _build_ui(self):
        self.setWindowTitle("Festo Pressure Sender")
        self.resize(760, 470)
        self.setMinimumSize(600, 410)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)

        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        destination_group = QtWidgets.QGroupBox("Destination")
        destination_layout = QtWidgets.QGridLayout(destination_group)
        destination_layout.setColumnStretch(1, 1)

        destination_layout.addWidget(QtWidgets.QLabel("Target Festo IP:"), 0, 0)
        self.txt_target_ip = QtWidgets.QLineEdit()
        self.txt_target_ip.setPlaceholderText("e.g. 192.168.0.3")
        self.txt_target_ip.textChanged.connect(self._target_changed)
        destination_layout.addWidget(self.txt_target_ip, 0, 1)

        destination_layout.addWidget(QtWidgets.QLabel("Status:"), 1, 0)
        self.lbl_status = QtWidgets.QLabel("Not tested")
        self.lbl_status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        destination_layout.addWidget(self.lbl_status, 1, 1)

        root.addWidget(destination_group)

        pressure_group = QtWidgets.QGroupBox("Commanded pressure")
        pressure_layout = QtWidgets.QGridLayout(pressure_group)
        pressure_layout.setHorizontalSpacing(12)
        pressure_layout.setVerticalSpacing(10)
        pressure_layout.setColumnStretch(1, 1)

        pressure_layout.addWidget(QtWidgets.QLabel("Muscle"), 0, 0)
        pressure_layout.addWidget(QtWidgets.QLabel("Pressure"), 0, 1)

        value_header = QtWidgets.QLabel("Target")
        value_header.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        pressure_layout.addWidget(value_header, 0, 2)

        gang_header = QtWidgets.QLabel("Gang")
        gang_header.setAlignment(QtCore.Qt.AlignCenter)
        pressure_layout.addWidget(gang_header, 0, 3)

        self.sliders = []
        self.value_labels = []
        self.gang_checks = []

        value_width = self.fontMetrics().horizontalAdvance("6000 mbar") + 12

        for index in range(PRESSURE_CHANNELS):
            name = QtWidgets.QLabel(str(index))
            name.setAlignment(QtCore.Qt.AlignCenter)
            pressure_layout.addWidget(name, index + 1, 0)

            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(0, MAX_PRESSURE_MBAR)
            slider.setValue(0)
            slider.setSingleStep(10)
            slider.setPageStep(100)
            slider.setTickPosition(QtWidgets.QSlider.NoTicks)
            slider.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Fixed,
            )
            slider.valueChanged.connect(
                lambda value, i=index: self._slider_changed(i, value)
            )
            pressure_layout.addWidget(slider, index + 1, 1)
            self.sliders.append(slider)

            value_label = QtWidgets.QLabel("0 mbar")
            value_label.setMinimumWidth(value_width)
            value_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            pressure_layout.addWidget(value_label, index + 1, 2)
            self.value_labels.append(value_label)

            gang = QtWidgets.QCheckBox()
            gang.setChecked(True)
            gang.setToolTip(
                "When this slider and other sliders are checked, moving any checked "
                "slider moves all checked sliders to the same target value."
            )
            gang_container = QtWidgets.QWidget()
            gang_layout = QtWidgets.QHBoxLayout(gang_container)
            gang_layout.setContentsMargins(0, 0, 0, 0)
            gang_layout.addStretch(1)
            gang_layout.addWidget(gang)
            gang_layout.addStretch(1)
            pressure_layout.addWidget(gang_container, index + 1, 3)
            self.gang_checks.append(gang)

        root.addWidget(pressure_group, 1)

        controls = QtWidgets.QHBoxLayout()

        self.chk_slew = QtWidgets.QCheckBox(
            "Slew pressure changes at 6000 mbar/s"
        )
        self.chk_slew.setChecked(True)
        controls.addWidget(self.chk_slew)
        controls.addStretch(1)

        self.btn_reset = QtWidgets.QPushButton("Reset all to 0")
        self.btn_reset.clicked.connect(self._reset_all)
        controls.addWidget(self.btn_reset)

        root.addLayout(controls)

        hint = QtWidgets.QLabel("Press Esc to exit")
        hint.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        root.addWidget(hint)

    def _setup_socket(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.bind(("", 0))

        self.socket_notifier = QtCore.QSocketNotifier(
            self.sock.fileno(), QtCore.QSocketNotifier.Read, self
        )
        self.socket_notifier.activated.connect(self._socket_ready)

    def _setup_timers(self):
        self.update_timer = QtCore.QTimer(self)
        self.update_timer.setTimerType(QtCore.Qt.PreciseTimer)
        self.update_timer.setInterval(UPDATE_INTERVAL_MS)
        self.update_timer.timeout.connect(self._control_tick)
        self.update_timer.start()

        self.status_timer = QtCore.QTimer(self)
        self.status_timer.setInterval(200)
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start()

    def _setup_shortcuts(self):
        self.escape_shortcut = QtWidgets.QShortcut(
            QtGui.QKeySequence(QtCore.Qt.Key_Escape), self
        )
        self.escape_shortcut.setContext(QtCore.Qt.WindowShortcut)
        self.escape_shortcut.activated.connect(self.close)

    def _slider_changed(self, index, value):
        self.target_pressures[index] = value
        self.value_labels[index].setText(f"{value} mbar")

        if not self.gang_checks[index].isChecked():
            return

        for other in range(PRESSURE_CHANNELS):
            if other == index or not self.gang_checks[other].isChecked():
                continue

            self.target_pressures[other] = value
            self.value_labels[other].setText(f"{value} mbar")

            blocker = QtCore.QSignalBlocker(self.sliders[other])
            self.sliders[other].setValue(value)
            del blocker

    def _reset_all(self):
        for index, slider in enumerate(self.sliders):
            self.target_pressures[index] = 0
            self.value_labels[index].setText("0 mbar")
            blocker = QtCore.QSignalBlocker(slider)
            slider.setValue(0)
            del blocker

    def _control_tick(self):
        changed = False

        for index in range(PRESSURE_CHANNELS):
            current = self.output_pressures[index]
            target = float(self.target_pressures[index])

            if self.chk_slew.isChecked():
                if current < target:
                    new_value = min(current + SLEW_STEP_MBAR, target)
                elif current > target:
                    new_value = max(current - SLEW_STEP_MBAR, target)
                else:
                    new_value = current
            else:
                new_value = target

            if new_value != current:
                self.output_pressures[index] = new_value
                changed = True

        values = [int(round(value)) for value in self.output_pressures]

        if changed or self.last_sent_pressures is None:
            self._send_pressures(values)

    def _send_pressures(self, pressures):
        target = self._target_address()
        if target is None:
            return

        packet = easyip.Factory.send_flagword(self.counter, pressures, offset=0)

        try:
            self.sock.sendto(packet.pack(), (target, easyip.EASYIP_PORT))
        except OSError:
            self.sent_anything = True
            self.last_response_time = None
            self.lbl_status.setText("No Response")
            return

        self.last_sent_pressures = list(pressures)
        self.sent_anything = True
        self.counter = (self.counter + 1) & 0xFFFF

    def _target_address(self):
        target = self.txt_target_ip.text().strip()
        if not target:
            return None

        try:
            socket.inet_aton(target)
        except OSError:
            self.lbl_status.setText("Invalid IP address")
            return None

        return target

    def _target_changed(self):
        self.sent_anything = False
        self.last_response_time = None
        self.last_sent_pressures = None
        self.lbl_status.setText("Not tested")

    @QtCore.pyqtSlot()
    def _socket_ready(self, _socket_descriptor=None):
        current_target = self.txt_target_ip.text().strip()

        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break

            if addr[0] != current_target:
                continue

            try:
                packet = easyip.Packet(data)
            except Exception:
                continue

            if packet.flags != easyip.Flags.RESPONSE:
                continue

            self.last_response_time = time.monotonic()
            self.lbl_status.setText("Responding")

    def _update_status(self):
        if not self.sent_anything:
            return

        if self.last_response_time is None:
            self.lbl_status.setText("No Response")
            return

        if time.monotonic() - self.last_response_time <= RESPONSE_TIMEOUT_S:
            self.lbl_status.setText("Responding")
        else:
            self.lbl_status.setText("No Response")

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
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
