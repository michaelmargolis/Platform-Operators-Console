# panels.py ver 3
"""Native Qt6 panels and timeline widgets for sim_player.

Presentation only.  Future strip chart and tension panels should be separate
QDockWidgets registered by the player UI, not calculations in these widgets.
"""
from __future__ import annotations
from PyQt6.QtCore import Qt, QRectF, QPointF, QPoint, pyqtSignal, QSortFilterProxyModel
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QWidget, QSlider, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView,
)
from .angular_cues import MODE_LABELS, MODES
from .recording import AXES
from .theme import PANEL, RAIL, TEXT, MUTED, ACCENT, GREEN, ORANGE
from .tuning import muscle_flags
from .muscle_extrema import RelativeMuscleRuler
from .marker_intervals import event_at_x, event_span


def clock_label(seconds):
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60):02d}:{seconds % 60:05.2f}"

class ResponsiveBars(QWidget):
    """Fast and resize-safe 6-channel bars; later strip charts are separate docks."""

    def __init__(self, signed, parent=None):
        super().__init__(parent)
        self.signed = signed
        self.values = (0.0,) * 6
        self.requested_values = None
        self.request_oob = (False,) * 6
        self.final_clipped = (False,) * 6
        self.extrema = None  # Requested min/max visited during current replay.
        self.plot_bounds = None  # Fixed per-experiment scale, including excesses.
        self.lo, self.hi = 750.0, 1000.0
        self.axis_context = "PROCESSED"
        self.labels = AXES if signed else tuple(f"M{i+1}" for i in range(6))
        self.setMinimumSize(245, 190)

    def clear_extrema(self):
        self.extrema = None
        self.plot_bounds = None
        self.update()

    def update_values(self, values, lo=None, hi=None, *, requested=None, request_oob=None,
                      final_clipped=None, extrema=None, plot_bounds=None):
        self.values = tuple(values)
        self.requested_values = tuple(requested) if requested is not None else None
        self.request_oob = tuple(request_oob) if request_oob is not None else (False,) * 6
        self.final_clipped = tuple(final_clipped) if final_clipped is not None else (False,) * 6
        self.extrema = tuple(extrema) if extrema is not None else None
        self.plot_bounds = tuple(plot_bounds) if plot_bounds is not None else None
        if lo is not None:
            self.lo = float(lo)
        if hi is not None:
            self.hi = float(hi)
        warnings = []
        if not self.signed:
            for i, flagged in enumerate(self.request_oob):
                if flagged:
                    req = self.requested_values[i] if self.requested_values else None
                    suffix = f"request {req:.1f} mm" if req is not None else "request out of range"
                    warnings.append(f"M{i+1}: {suffix} (permitted {self.values[i]:.1f} mm)")
            for i, flagged in enumerate(self.final_clipped):
                if flagged:
                    warnings.append(f"M{i+1}: final output clamped")
        if self.extrema is not None and not self.signed:
            for i, (minimum, maximum) in enumerate(self.extrema):
                if minimum is not None and maximum is not None:
                    warnings.append(f"M{i+1}: requested min {minimum:.1f}, max {maximum:.1f} mm")
        warnings.append("Vertical lines: blue = minimum request; amber = maximum request; "
                        "red = outside permitted range. Bars = final permitted length. "
                        "Valid min/max are aligned across platforms; outside-range "
                        "warning gutters are compressed (read exact mm above).")
        self.setToolTip("\n".join(warnings))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        painter.fillRect(self.rect(), QColor(PANEL))
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor(MUTED))
        flagged_any = any(self.request_oob) or any(self.final_clipped)
        heading = (self.axis_context + " TRANSFORM   -100% to +100%") if self.signed else (
            f"MUSCLE LENGTHS {self.lo:.0f}–{self.hi:.0f} mm  |  "
            "MIN blue · MAX gold · over-limit red" if self.extrema else
            f"MODELED MUSCLE LENGTH   {self.lo:.0f}–{self.hi:.0f} mm")
        if width < 440:
            heading = (self.axis_context + "  6DOF (%)") if self.signed else (
                "MUSCLE LENGTHS | RED = OUT-OF-RANGE" if flagged_any else "MUSCLE LENGTHS (mm)")
        painter.drawText(10, 6, width - 20, 22, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, heading)
        # Prioritise the length plot.  Two-line values still fit at the right.
        left = 46 if not self.signed else 72
        right = (113 if width >= 410 else 104) if not self.signed else 67
        bar_width = max(30, width - left - right - 8)
        top, bottom = 30, 5
        row_height = max(22, (height - top - bottom) / 6)
        bar_height = max(9, min(17, row_height * .43))
        if not self.signed:
            # Valid minimum/maximum remain aligned for 800 and 1000 mm
            # platforms. Only the outer warning gutters compress out-of-range
            # requests; 50 mm graticules inside the valid range remain linear.
            plot_lo, plot_hi = self.plot_bounds or (
                self.lo - 0.05 * (self.hi - self.lo),
                self.hi + 0.05 * (self.hi - self.lo))
            ruler = RelativeMuscleRuler(self.lo, self.hi, plot_lo, plot_hi)
            def guide_x(mm):
                return left + bar_width * ruler.position(mm)
            first_tick = int(__import__('math').ceil(self.lo / 50.0)) * 50
            last_tick = int(__import__('math').floor(self.hi / 50.0)) * 50
            for mm in range(first_tick, last_tick + 1, 50):
                x = guide_x(mm)
                painter.setPen(QPen(QColor('#3c5061'), 0.65))
                painter.drawLine(QPointF(x, top + 1), QPointF(x, height - bottom))
            for limit in (self.lo, self.hi):
                x = guide_x(limit)
                painter.setPen(QPen(QColor('#7891a5'), 1.2))
                painter.drawLine(QPointF(x, top + 1), QPointF(x, height - bottom))
        for i, (label, value) in enumerate(zip(self.labels, self.values)):
            y = top + i * row_height
            by = y + (row_height - bar_height) / 2
            bar = QRectF(left, by, bar_width, bar_height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(RAIL))
            painter.drawRoundedRect(bar, 3, 3)
            if self.signed:
                clamped = max(-1.0, min(1.0, float(value)))
                centre = bar.center().x()
                half = bar_width / 2
                bx = centre + min(0.0, clamped) * half
                fill = QRectF(bx, by, max(0.9, abs(clamped) * half), bar_height)
                painter.setBrush(QColor(ORANGE if i in (3, 4) else ACCENT))
                painter.drawRoundedRect(fill, 2, 2)
                painter.setPen(QPen(QColor("#d6e4f2"), 1))
                painter.drawLine(QPointF(centre, by - 2), QPointF(centre, by + bar_height + 2))
                display = f"{float(value) * 100:+.0f}%"
            else:
                # Requested extrema remain visible beyond the common legal
                # limits in the two warning gutters, never on the green rail.
                # Their millimetre distances remain exact in labels/tooltips.
                def x_at(mm):
                    return left + bar_width * ruler.position(mm)

                legal_left, legal_right = x_at(self.lo), x_at(self.hi)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#354d60"))
                painter.drawRoundedRect(QRectF(legal_left, by, max(1.0, legal_right - legal_left),
                                               bar_height), 2, 2)
                permitted_end = x_at(value)
                red = self.request_oob[i] or self.final_clipped[i]
                painter.setBrush(QColor("#f36b6b" if red else GREEN))
                painter.drawRoundedRect(
                    QRectF(legal_left, by, max(.9, permitted_end - legal_left), bar_height), 2, 2)
                painter.setPen(QPen(QColor("#6a879d"), 1))
                for limit_x in (legal_left, legal_right):
                    painter.drawLine(QPointF(limit_x, by - 1),
                                     QPointF(limit_x, by + bar_height + 1))
                if red:
                    painter.setPen(QPen(QColor("#f36b6b"), 1.2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(QRectF(legal_left, by, max(1.0, legal_right - legal_left),
                                                   bar_height), 2, 2)
                low_peak = high_peak = None
                if self.extrema and len(self.extrema) == 6:
                    low_peak, high_peak = self.extrema[i]
                    for peak, normal_color in ((low_peak, "#78c9ed"),
                                               (high_peak, "#ffd17b")):
                        if peak is None:
                            continue
                        peak_x = x_at(peak)
                        color = ("#ff6666" if peak < self.lo - 1.e-7 or
                                 peak > self.hi + 1.e-7 else normal_color)
                        painter.setPen(QPen(QColor(color), 2.0))
                        painter.drawLine(QPointF(peak_x, by - 5),
                                         QPointF(peak_x, by + bar_height + 5))
                value_text = f"{value:.0f} mm"
                if red and self.requested_values is not None and width >= 410:
                    value_text = f"{value:.0f} (req {self.requested_values[i]:.0f})"
                x_label = int(left + bar_width + 7)
                if low_peak is not None and high_peak is not None:
                    painter.setPen(QColor("#f36b6b" if red else TEXT))
                    painter.drawText(x_label, int(y), right - 4, int(row_height * .52),
                                     Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                     value_text)
                    painter.setPen(QColor("#ffa6a6" if (low_peak < self.lo or high_peak > self.hi)
                                          else MUTED))
                    painter.drawText(x_label, int(y + row_height * .49), right - 4,
                                     int(row_height * .48),
                                     Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                     f"{low_peak:.0f}..{high_peak:.0f} mm")
                else:
                    painter.setPen(QColor("#f36b6b" if red else TEXT))
                    painter.drawText(x_label, int(y), right - 4, int(row_height),
                                     Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                     value_text)
                # Labels for the muscle itself are drawn below; do not paint
                # the right-side data twice as in the signed transform panel.
                display = None
            painter.setPen(QColor("#f36b6b" if not self.signed and
                                  (self.request_oob[i] or self.final_clipped[i]) else TEXT))
            painter.drawText(9, int(y), 54, int(row_height), Qt.AlignmentFlag.AlignVCenter, label)
            if display is not None:
                painter.drawText(int(left + bar_width + 7), int(y), right, int(row_height),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, display)
        painter.end()


class TimelineMarkers(QWidget):
    seek_requested = pyqtSignal(float)
    inspected = pyqtSignal(str)
    marker_selected = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 1.0
        self.current = 0.0
        self.events = ()
        self.category = "ride"
        self.setMinimumHeight(42)
        self.setMaximumHeight(52)
        self.setMouseTracking(True)

    def set_recording(self, events, duration, *, category=None):
        self.events = events
        if category is not None:
            self.category = category
        self.duration = max(0.01, duration)
        self.update()

    def set_current(self, seconds):
        self.current = seconds
        self.update()

    def x_at(self, seconds):
        return 18 + (self.width() - 36) * max(0.0, min(1.0, seconds / self.duration))

    def closest_event(self, x, y=None):
        if not self.events:
            return None
        if self.category == "kinematic":
            # Require the pointer to be close to the orange bar, not the caption.
            if y is not None and abs(y - 18) > 9:
                return None
            return event_at_x(self.events, x, self.width(), self.duration)
        event = min(self.events, key=lambda e: abs(self.x_at(e.seconds) - x))
        return event if abs(self.x_at(event.seconds) - x) < 12 else None

    def mouseMoveEvent(self, event):
        chosen = self.closest_event(event.position().x(), event.position().y())
        if chosen:
            if self.category == "kinematic":
                self.setToolTip(
                    f"{chosen.name} — {clock_label(chosen.start_s)}–"
                    f"{clock_label(chosen.end_s)} ({chosen.duration_s:.2f} s)\n"
                    f"Peak at {clock_label(chosen.seconds)}\n{chosen.details}"
                )
            else:
                self.setToolTip(f"{chosen.name} — {clock_label(chosen.seconds)}\n{chosen.details}")
        else:
            self.setToolTip("Click to seek; hover over an event for details")

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        chosen = self.closest_event(event.position().x(), event.position().y())
        if chosen is not None:
            self.seek_requested.emit(chosen.seconds)
            self.marker_selected.emit(chosen)
            self.inspected.emit(f"{chosen.name} at {clock_label(chosen.seconds)}")
        else:
            ratio = (event.position().x() - 18) / max(1, self.width() - 36)
            self.seek_requested.emit(self.duration * max(0.0, min(1.0, ratio)))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        painter.setPen(QPen(QColor("#52677a"), 2))
        painter.drawLine(18, 18, w - 18, 18)
        if self.category == "kinematic":
            # Orange horizontal spans mean the *duration* of each anomaly.
            # Paint longer intervals first so short overlapping events remain
            # visible. No per-event vertical ticks or peak markers are drawn.
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(ORANGE), 6,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for marker in sorted(self.events,
                                 key=lambda ev: ev.end_s - ev.start_s, reverse=True):
                left, right = event_span(marker, w, self.duration)
                painter.drawLine(QPointF(left, 18), QPointF(right, 18))
        else:
            # Existing ride-event point markers and captions are unchanged.
            last_label_end = -1000
            for marker in self.events:
                x = self.x_at(marker.seconds)
                color = QColor(marker.color)
                painter.setPen(QPen(color, 1.5))
                painter.drawLine(int(x), 10, int(x), 27)
                painter.setBrush(color)
                painter.drawEllipse(QRectF(x - 2.5, 15.5, 5, 5))
                if x - 32 > last_label_end and 45 < x < w - 45:
                    painter.setFont(QFont("Segoe UI", 7))
                    painter.setPen(QColor(MUTED))
                    short = marker.name.replace(" request", "").replace(" train", "")
                    painter.drawText(int(x - 36), 29, 72, 16,
                                     Qt.AlignmentFlag.AlignHCenter, short)
                    last_label_end = x + 40
        if self.category == "kinematic" and self.events:
            first_x = self.x_at(min(marker.start_s for marker in self.events))
            painter.setFont(QFont("Segoe UI", 8))
            painter.setPen(QColor(MUTED))
            painter.drawText(QRectF(first_x, 29, max(0, w - 18 - first_x), 18),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             "Hover over an event for details")
        x = self.x_at(self.current)
        painter.setPen(QPen(QColor("#eef7ff"), 2))
        painter.drawLine(int(x), 3, int(x), 30)
        painter.end()


class JumpSlider(QSlider):
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            range_size = self.maximum() - self.minimum()
            if range_size > 0:
                proportion = max(0, min(1, (event.position().x() - 9) / max(1, self.width() - 18)))
                self.setValue(round(self.minimum() + proportion * range_size))
                event.accept()
        super().mousePressEvent(event)


class RangeJumpSlider(JumpSlider):
    """Existing playhead slider with independently draggable A/B markers.

    The user can click anywhere except within the marker hit areas to seek.
    A marker drag emits range_edited only (never valueChanged or sliderPressed),
    so it cannot trigger a costly video seek.
    """
    range_edited = pyqtSignal(str, float)  # marker: 'start' or 'end', seconds

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._duration = 0.0
        self._start = 0.0
        self._end = 0.0
        self._drag_marker = None
        self._mode = "full"
        self.setMouseTracking(True)
        self.setMinimumHeight(24)
        self.setToolTip("Drag red A or gold B marker to select a playback range; click elsewhere to seek")

    def set_playback_range(self, duration, start, end, mode="full"):
        self._duration = max(0.0, float(duration))
        self._mode = mode
        self._start = max(0.0, float(start))
        self._end = min(self._duration, float(end))
        self.update()

    def _x_at(self, seconds):
        return 9.0 + max(0.0, min(1.0, seconds / max(self._duration, 0.001))) * max(1.0, self.width() - 18.0)

    def _seconds_at(self, x):
        return self._duration * max(0.0, min(1.0, (x - 9.0) / max(1.0, self.width() - 18.0)))

    def _marker_at(self, x):
        if self._duration <= 0:
            return None
        distances = ((abs(x - self._x_at(self._start)), "start"),
                     (abs(x - self._x_at(self._end)), "end"))
        distance, marker = min(distances)
        return marker if distance <= 11.0 else None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            marker = self._marker_at(event.position().x())
            if marker is not None:
                self._drag_marker = marker
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_marker is not None:
            self.range_edited.emit(self._drag_marker, self._seconds_at(event.position().x()))
            event.accept()
            return
        hovering = self._marker_at(event.position().x())
        self.setCursor(Qt.CursorShape.SizeHorCursor if hovering else Qt.CursorShape.ArrowCursor)
        if hovering:
            label = "A" if hovering == "start" else "B"
            timestamp = self._start if hovering == "start" else self._end
            self.setToolTip(f"{label}: {clock_label(timestamp)} — drag to change playback boundary")
        else:
            self.setToolTip("Drag red A or gold B to edit playback range; click elsewhere to seek")
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_marker is not None:
            # Also take the final release position if outside the widget.
            self.range_edited.emit(self._drag_marker, self._seconds_at(event.position().x()))
            self._drag_marker = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._duration <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ax, bx = self._x_at(self._start), self._x_at(self._end)
        mid_y = self.height() / 2.0
        # Subtle blue region behind the two coloured draggable markers.
        painter.fillRect(QRectF(ax, mid_y - 3.0, max(0.0, bx - ax), 6.0),
                         QColor(72, 158, 202, 35 if self._mode == "full" else 85))
        for x, color in ((ax, "#fa625b"), (bx, "#edb65b")):
            marker_color = QColor(color)
            if self._mode == "full":
                marker_color.setAlpha(125)
            painter.setPen(QPen(marker_color, 2.8))
            painter.drawLine(QPointF(x, 2), QPointF(x, self.height() - 2))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(marker_color)
            painter.drawRoundedRect(QRectF(x - 4, 0, 8, 5), 2, 2)
        painter.end()


class RideCsvDialogFilter(QSortFilterProxyModel):
    """Hide state-event CSV sidecars while preserving the folder tree."""

    def filterAcceptsRow(self, source_row, source_parent):
        source = self.sourceModel()
        item = source.index(source_row, 0, source_parent)
        # The file dialog already filters for .csv; we only hide event sidecars.
        name = str(source.data(item, Qt.ItemDataRole.DisplayRole) or "")
        return not name.casefold().endswith("_state_events.csv")




class CueComparisonPanel(QWidget):
    """Read-only metrics for cue modes run through the identical POC analyser.

    A dash means that mode has not yet been calculated. This widget never
    generates or processes motion; the coordinator supplies metrics objects.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.help_label = QLabel(
            "Per-mode full-ride metrics. Select each cue to calculate it. "
            "All muscle lengths are modeled targets, not measured motion."
        )
        self.help_label.setWordWrap(True)
        layout.addWidget(self.help_label)
        self.table = QTableWidget(len(MODES), 5)
        self.table.setHorizontalHeaderLabels((
            "Mode", "Peak step", ">10 mm", "Limited", "Min floor",
        ))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for row, mode in enumerate(MODES):
            self.table.setItem(row, 0, QTableWidgetItem(MODE_LABELS[mode]))
            for col in range(1, 5):
                self.table.setItem(row, col, QTableWidgetItem("—"))
        self.table.setMinimumSize(280, 135)
        layout.addWidget(self.table)
        self.summary = QLabel("Peak step = largest change in one muscle between successive samples.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self._metrics = {}
        self._active_mode = MODES[0]

    def reset(self):
        self._metrics.clear()
        for row in range(len(MODES)):
            for col in range(1, 5):
                self.table.item(row, col).setText("—")
        self.set_active(MODES[0])

    def add_metrics(self, mode, metrics):
        self._metrics[mode] = metrics
        row = MODES.index(mode)
        values = (
            f"{metrics.peak_step_mm:.1f} mm",
            str(metrics.steps_over_10mm),
            f"{metrics.workspace_frames}/{metrics.samples}",
            (f"{metrics.min_clearance_mm:.1f} mm"
             if metrics.min_clearance_mm is not None else "n/a"),
        )
        for col, label in enumerate(values, 1):
            self.table.item(row, col).setText(label)
        self.set_active(self._active_mode)

    def set_active(self, mode):
        self._active_mode = mode
        for row, choice in enumerate(MODES):
            item = self.table.item(row, 0)
            font = item.font()
            font.setBold(choice == mode)
            item.setFont(font)
            item.setToolTip("Active experiment" if choice == mode else "Select from the angular cue control")
        if mode in self._metrics:
            metric = self._metrics[mode]
            self.summary.setText(
                f"Current: {MODE_LABELS[mode]}  •  peak processed Roll "
                f"{metric.max_abs_roll_pct:.1f}%  •  Pitch "
                f"{metric.max_abs_pitch_pct:.1f}%. "
                "Research estimates only; no actuator dynamics or tension constraints."
            )
        else:
            self.summary.setText("No calculated metrics yet for the selected cue mode.")
