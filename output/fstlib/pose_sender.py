# pose_sender.py ver 3
"""POC standalone six-DOF EasyIP pose sender.

Run from the POC project root (or from any directory):
    python output/fstlib/pose_sender.py

Target defaults to 192.168.0.10:995. For emulator testing, enter the IP of
its host. The utility optionally polls EasyIP actual-pressure words 10..15 for display.
No pressure response is required to arm. Arming requires explicit operator
acknowledgement and unpark requires a second click. Start physically PARKED.

WARNING: This is an engineering utility, NOT a safety controller. It does not
measure platform pose or know if the mechanical prop is actually engaged.
Pressure readback is not proof of safe motion. Keep the hardware emergency stop
accessible; do not operate with people on or near the platform.
"""
from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5 import QtCore, QtGui, QtWidgets

from config.platforms import PLATFORMS
from output.fstlib import easyip
from output.fstlib.pose_sender_logic import (
    DOF_NAMES, DEFAULT_TARGET_IP, FRAME_PERIOD_S, MAX_PRESSURE_MBAR,
    PoseMotion, PressureMonitor, internal_to_user_real, slew_pressures,
    user_to_internal_normalized,
)

INTERVAL_MS = int(FRAME_PERIOD_S * 1000)


def selected_platform_from_poc():
    config_path = PROJECT_ROOT / "poc_config.json"
    if not config_path.is_file():
        return "platform_v2"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    key = data.get("selected_platform", "platform_v2")
    if key == "wheelchair":
        key = "platform_v2"
    if key not in PLATFORMS:
        raise RuntimeError(f"Unknown selected_platform {key!r} in {config_path}")
    return key


class PoseSenderWindow(QtWidgets.QMainWindow):
    def __init__(self, platform_key):
        super().__init__()
        self.model = PoseMotion(platform_key, PROJECT_ROOT)
        self.monitor = PressureMonitor()
        self.armed = False
        self.fault_latched = False
        self.last_sent = None
        self.commanded = [0] * 6
        self.last_socket_error = None
        self._preview_dirty = True
        self._build_ui()
        self._setup_udp()
        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(QtCore.Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start(INTERVAL_MS)
        self._refresh_ui()

    def _build_ui(self):
        self.setWindowTitle(f"POC Pose Sender — {self.model.platform_info['name']}")
        self.resize(1080, 690)
        self.setMinimumSize(980, 620)
        root_widget = QtWidgets.QWidget(self)
        self.setCentralWidget(root_widget)
        root = QtWidgets.QHBoxLayout(root_widget)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(12)

        main_col = QtWidgets.QVBoxLayout()
        main_col.setSpacing(7)
        root.addLayout(main_col, 1)

        side_col = QtWidgets.QVBoxLayout()
        side_col.setSpacing(10)
        root.addLayout(side_col)

        top = QtWidgets.QGroupBox("Destination and platform")
        topgrid = QtWidgets.QGridLayout(top)
        topgrid.addWidget(QtWidgets.QLabel("Festo IP:"), 0, 0)
        self.ip_edit = QtWidgets.QLineEdit(DEFAULT_TARGET_IP)
        self.ip_edit.setMaximumWidth(165)
        self.ip_edit.editingFinished.connect(self._ip_edited)
        topgrid.addWidget(self.ip_edit, 0, 1)
        topgrid.addWidget(QtWidgets.QLabel(f"EasyIP UDP {easyip.EASYIP_PORT}"), 0, 2)
        self.lbl_link = QtWidgets.QLabel("Pressure feedback optional")
        topgrid.addWidget(self.lbl_link, 0, 3)
        topgrid.addWidget(QtWidgets.QLabel("Platform:"), 1, 0)
        topgrid.addWidget(QtWidgets.QLabel(self.model.platform_info["name"]), 1, 1, 1, 2)
        topgrid.addWidget(QtWidgets.QLabel("Payload:"), 1, 3)
        self.payload_combo = QtWidgets.QComboBox()
        for i, payload in enumerate(self.model.backend.cfg.PAYLOAD_WEIGHTS):
            self.payload_combo.addItem(f"{payload} kg + platform", i)
        self.payload_combo.setCurrentIndex(1)
        self.payload_combo.currentIndexChanged.connect(self._payload_changed)
        topgrid.addWidget(self.payload_combo, 1, 4)
        main_col.addWidget(top)

        pose_group = QtWidgets.QGroupBox("Normalized transform (−1.00 to +1.00)")
        posegrid = QtWidgets.QGridLayout(pose_group)
        posegrid.setColumnStretch(1, 1)
        convention = QtWidgets.QLabel(
            "Rider-frame convention: +X forward, +Y left, +Z up, +roll right side down, +pitch nose down, +yaw CCW."
        )
        convention.setWordWrap(True)
        posegrid.addWidget(convention, 0, 0, 1, 5)
        posegrid.addWidget(QtWidgets.QLabel("DOF"), 1, 0)
        posegrid.addWidget(QtWidgets.QLabel("Normalized"), 1, 1)
        posegrid.addWidget(QtWidgets.QLabel("Requested real"), 1, 3)
        posegrid.addWidget(QtWidgets.QLabel("Applied real"), 1, 4)
        self.sliders = []
        self.norm_labels = []
        self.real_labels = []
        self.applied_labels = []
        for i, name in enumerate(DOF_NAMES):
            row = i + 2
            posegrid.addWidget(QtWidgets.QLabel(name), row, 0)
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(-100, 100)
            slider.setValue(0)
            slider.setSingleStep(1)
            slider.setPageStep(10)
            slider.valueChanged.connect(self._slider_changed)
            posegrid.addWidget(slider, row, 1)
            norm_label = QtWidgets.QLabel("+0.00")
            real_label = QtWidgets.QLabel("0 mm" if i < 3 else "0.0°")
            applied_label = QtWidgets.QLabel(real_label.text())
            for lbl in (norm_label, real_label, applied_label):
                lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                lbl.setMinimumWidth(70)
            posegrid.addWidget(norm_label, row, 2)
            posegrid.addWidget(real_label, row, 3)
            posegrid.addWidget(applied_label, row, 4)
            self.sliders.append(slider)
            self.norm_labels.append(norm_label)
            self.real_labels.append(real_label)
            self.applied_labels.append(applied_label)
        main_col.addWidget(pose_group, 1)

        pressure_group = QtWidgets.QGroupBox("Festo pressure feedback (mbar)")
        pressuregrid = QtWidgets.QGridLayout(pressure_group)
        self.actual_labels = []
        self.sent_labels = []
        for col, label in enumerate(("Muscle", "Last sent", "Actual (registers 10–15)")):
            pressuregrid.addWidget(QtWidgets.QLabel(label), 0, col)
        for i in range(6):
            pressuregrid.addWidget(QtWidgets.QLabel(str(i)), i + 1, 0)
            sent = QtWidgets.QLabel("—")
            actual = QtWidgets.QLabel("—")
            sent.setAlignment(QtCore.Qt.AlignRight)
            actual.setAlignment(QtCore.Qt.AlignRight)
            pressuregrid.addWidget(sent, i + 1, 1)
            pressuregrid.addWidget(actual, i + 1, 2)
            self.sent_labels.append(sent)
            self.actual_labels.append(actual)
        main_col.addWidget(pressure_group)

        self.lbl_state = QtWidgets.QLabel("DISARMED — assumed physically parked")
        self.lbl_state.setStyleSheet("font-weight: bold; color: #a22727;")
        main_col.addWidget(self.lbl_state)
        warn = QtWidgets.QLabel(
            "Engineering test only — do NOT run POC or another EasyIP sender at the same time. "
            "Keep physical E-stop accessible. Loss of UDP does NOT vent pressure."
        )
        warn.setWordWrap(True)
        main_col.addWidget(warn)
        if self.model.backend.workspace_limit_mode != "geometry":
            limit_warning = QtWidgets.QLabel(
                "WARNING: selected platform uses workspace mode "
                f"{self.model.backend.workspace_limit_mode!r}; no geometry floor-clearance check is active."
            )
            limit_warning.setStyleSheet("color:#b02b2b;font-weight:bold")
            limit_warning.setWordWrap(True)
            main_col.addWidget(limit_warning)

        self.arm_button = QtWidgets.QPushButton("Acknowledge / Arm")
        self.arm_button.clicked.connect(self._arm)
        side_col.addWidget(self.arm_button)
        self.unpark_button = QtWidgets.QPushButton("Slow UNPARK → neutral")
        self.unpark_button.clicked.connect(self._unpark)
        side_col.addWidget(self.unpark_button)
        self.park_button = QtWidgets.QPushButton("Slow PARK")
        self.park_button.clicked.connect(self._park)
        side_col.addWidget(self.park_button)
        self.zero_button = QtWidgets.QPushButton("Zero DOFs")
        self.zero_button.clicked.connect(self._zero_sliders)
        side_col.addWidget(self.zero_button)
        self.stop_button = QtWidgets.QPushButton("STOP TX (not E-stop)")
        self.stop_button.setToolTip("Stops UDP output. Does not depressurize the valves or replace the physical E-stop.")
        self.stop_button.clicked.connect(self._stop_transmission)
        side_col.addWidget(self.stop_button)
        side_col.addStretch(1)
        for button in (self.arm_button, self.unpark_button, self.park_button, self.zero_button, self.stop_button):
            button.setMinimumWidth(165)

        self._slider_changed()

    def _setup_udp(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.bind(("", 0))
        self.notifier = QtCore.QSocketNotifier(self.sock.fileno(), QtCore.QSocketNotifier.Read, self)
        self.notifier.activated.connect(self._receive)

    def _payload_changed(self):
        if not self.armed:
            self.model.backend.set_payload_level(self.payload_combo.currentData())

    def _slider_changed(self, *_args):
        user_values = [s.value() / 100.0 for s in self.sliders]
        self.model.set_normalized(user_to_internal_normalized(user_values))
        self._preview_dirty = True
        for label, v in zip(self.norm_labels, user_values):
            label.setText(f"{v:+.2f}")

    def _zero_sliders(self):
        for slider in self.sliders:
            slider.setValue(0)

    def _ip_edited(self):
        if self.armed:
            return
        try:
            ip = self.ip_edit.text().strip()
            self.monitor.set_target(ip)
            self.ip_edit.setText(self.monitor.target_ip)
        except ValueError:
            QtWidgets.QMessageBox.warning(self, "Invalid IP", "Enter a valid IPv4 destination.")
            self.ip_edit.setText(self.monitor.target_ip)
        self.last_sent = None
        self.commanded = [0] * 6
        self._refresh_ui()

    def _receive(self, _fd=None):
        while True:
            try:
                data, address = self.sock.recvfrom(2048)
            except (BlockingIOError, InterruptedError):
                break
            except OSError as exc:
                self.last_socket_error = str(exc)
                break
            self.monitor.decode_response(data, address[0], time.monotonic())

    def _tick(self):
        now = time.monotonic()
        try:
            packet = self.monitor.make_request(now)
            if packet is not None:
                self.sock.sendto(packet, (self.monitor.target_ip, easyip.EASYIP_PORT))
        except OSError as exc:
            self.last_socket_error = str(exc)

        if self.armed:
            try:
                target = self.model.tick()
                if target is not None and (
                    self.model.mode != "parked"
                    or self.commanded != list(target[:6])
                ):
                    words = slew_pressures(self.commanded, target)
                    self.sock.sendto(
                        self.monitor.make_output(words),
                        (self.monitor.target_ip, easyip.EASYIP_PORT),
                    )
                    self.commanded = list(words[:6])
                    self.last_sent = words
                # This confirms only that the park sequence has finished and
                # zero pressures have been COMMANDED. No actual position or
                # pressure is inferred from an absent EasyIP reply.
                if (
                    self.model.mode == "parked"
                    and target is not None
                    and self.commanded == [0] * 6
                ):
                    self.armed = False
            except Exception as exc:
                self._latch_fault(f"Motion output fault: {exc}; use physical E-stop if necessary.")
        self._refresh_ui()

    def _refresh_ui(self):
        now = time.monotonic()
        fresh = self.monitor.fresh(now)
        if fresh:
            self.lbl_link.setText("Pressure response OK")
            for label, value in zip(self.actual_labels, self.monitor.actual):
                label.setText(str(value))
        else:
            self.lbl_link.setText("No fresh pressure response")
            for label in self.actual_labels:
                label.setText("— stale —")
        try:
            if self._preview_dirty:
                self._preview_values = self.model.preview()
                self._preview_dirty = False
            requested, applied = self._preview_values
            requested = internal_to_user_real(requested)
            applied = internal_to_user_real(applied)
            for i, (req, out) in enumerate(zip(requested, applied)):
                if i >= 3:
                    req, out = (req * 180.0 / 3.141592653589793, out * 180.0 / 3.141592653589793)
                    unit = "°"
                else:
                    unit = " mm"
                self.real_labels[i].setText(f"{req:+.1f}{unit}")
                self.applied_labels[i].setText(f"{out:+.1f}{unit}")
        except Exception as exc:
            self.lbl_link.setText(f"Kinematics: {exc}")
        for index, label in enumerate(self.sent_labels):
            label.setText(str(self.last_sent[index]) if self.last_sent is not None else "—")
        self.arm_button.setEnabled(not self.armed and not self.fault_latched and self.model.mode == "parked")
        self.unpark_button.setEnabled(self.armed and self.model.mode == "parked")
        self.park_button.setEnabled(self.armed and self.model.mode == "active")
        self.stop_button.setEnabled(self.armed)
        self.payload_combo.setEnabled(not self.armed)
        self.ip_edit.setEnabled(not self.armed)
        self.zero_button.setEnabled(self.model.mode == "active" and self.armed)
        if self.fault_latched:
            self.lbl_state.setText("FAULT — output stopped; physical state UNKNOWN. Restart only after physical inspection.")
            self.lbl_state.setStyleSheet("font-weight:bold;color:#ad1e1e")
        elif self.armed:
            self.lbl_state.setText(f"OUTPUT ARMED — {self.model.mode.upper()}")
            self.lbl_state.setStyleSheet("font-weight:bold;color:#b06c00")
        else:
            self.lbl_state.setText("DISARMED — physically parked state must be verified before arming")
            self.lbl_state.setStyleSheet("font-weight:bold;color:#a22727")

    def _arm(self):
        if self.armed or self.fault_latched or self.model.mode != "parked":
            return
        prop_text = (
            "\nThis platform has a piston/prop. POC prop sequences will be commanded WITHOUT position feedback."
            if self.model.has_piston else ""
        )
        message = (
            "Confirm ALL of the following:\n"
            "• Platform is physically PARKED, not merely assumed parked in software.\n"
            "• Nobody is on or near the moving platform.\n"
            "• External emergency stop is working and within reach.\n"
            "• Pressure calibration, payload, geometry and target IP are correct.\n"
            "• Readback is OPTIONAL and may be absent; initial pressure is UNKNOWN.\n\n"
            "Arming will not move the platform. A separate UNPARK click is required."
            + prop_text
        )
        if QtWidgets.QMessageBox.question(
            self, "Operator acknowledgement", message,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        ) != QtWidgets.QMessageBox.Yes:
            return
        # Do not seed commands from pressure readback: it can be absent (Twin)
        # or unrelated to the current requested pressure setpoints.
        self.commanded = [0] * 6
        self.armed = True
        self._refresh_ui()

    def _unpark(self):
        if not self.armed or self.model.mode != "parked":
            return
        self._zero_sliders()
        self.model.begin_unpark()
        self._refresh_ui()

    def _park(self):
        if not self.armed or self.model.mode != "active":
            return
        self.model.begin_park()
        self._refresh_ui()

    def _latch_fault(self, message):
        self.armed = False
        self.fault_latched = True
        self.lbl_state.setText("FAULT: " + message)
        QtWidgets.QMessageBox.critical(self, "Pose Sender output stopped", message)

    def _stop_transmission(self):
        if not self.armed:
            return
        self.armed = False
        self.fault_latched = True
        QtWidgets.QMessageBox.warning(
            self, "Output stopped (NOT emergency stop)",
            "No more UDP commands will be sent. Existing valve pressures may remain. "
            "Use the physical emergency stop to make the platform safe before restarting."
        )

    def closeEvent(self, event):
        if self.armed or self.model.mode != "parked" or self.fault_latched:
            result = QtWidgets.QMessageBox.warning(
                self, "Physical safety check",
                "The platform may NOT be safely parked. Closing stops UDP output "
                "but does not vent pressure. Has the physical E-stop been applied "
                "and the area made safe?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if result != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return
        self.timer.stop()
        self.notifier.setEnabled(False)
        self.sock.close()
        event.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    try:
        window = PoseSenderWindow(selected_platform_from_poc())
    except Exception as exc:
        QtWidgets.QMessageBox.critical(None, "Pose Sender startup error", str(exc))
        return 2
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
