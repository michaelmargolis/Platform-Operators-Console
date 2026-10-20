#ui_widgets.py  ver 6
import re

from PyQt5 import QtCore, QtGui, QtWidgets


class TouchComboItemDelegate(QtWidgets.QStyledItemDelegate):
    """Give a combo popup a fixed touch-friendly row height."""

    def __init__(self, row_height, parent=None):
        super().__init__(parent)
        self.row_height = max(1, int(row_height))

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), self.row_height))
        return size


def configure_touch_combo_popup(combo, row_scale=2.0, font_scale=2.0):
    """Enlarge only a QComboBox popup; leave the closed control unchanged."""
    closed_height = max(
        combo.height(), combo.minimumHeight(), combo.sizeHint().height(), 1
    )
    popup = combo.view()
    delegate = TouchComboItemDelegate(round(closed_height * row_scale), popup)
    popup.setItemDelegate(delegate)
    # Keep an explicit Python reference as well as the Qt parent relationship.
    combo._touch_popup_delegate = delegate

    popup_font = QtGui.QFont(combo.font())
    if popup_font.pointSizeF() > 0:
        popup_font.setPointSizeF(popup_font.pointSizeF() * font_scale)
    elif popup_font.pixelSize() > 0:
        popup_font.setPixelSize(round(popup_font.pixelSize() * font_scale))
    popup.setFont(popup_font)
    metrics = QtGui.QFontMetrics(popup_font)
    content_width = max(
        (metrics.horizontalAdvance(combo.itemText(index)) for index in range(combo.count())),
        default=0,
    )
    popup.setMinimumWidth(
        max(combo.width(), combo.minimumWidth(), content_width + 40)
    )
    popup.setStyleSheet("""
        QListView {
            background-color: white;
            border: 2px solid #606060;
            outline: 0;
        }
        QListView::item {
            color: black;
            padding-left: 12px;
            padding-right: 12px;
        }
        QListView::item:selected {
            background-color: #24689b;
            color: white;
        }
    """)
    return delegate


class ActivationButton(QtWidgets.QPushButton):
    """SOP activation button with a bottom-up green progress fill."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._activation_percent = 0
        self._background_color = QtCore.Qt.lightGray
        self._fill_color = QtCore.Qt.green
        self._label_text = self.text()
        self._border_radius = 15
        self.setCheckable(True)
        self.parse_stylesheet()

    def setText(self, text: str):
        super().setText(text)
        self._label_text = text
        self.update()

    def set_activation_percent(self, percent: int):
        self._activation_percent = max(0, min(100, int(percent)))
        self.update()

    def parse_stylesheet(self):
        ss = self.styleSheet()
        bg_match = re.search(r"background-color:\s*(.*?);", ss)
        fill_match = re.search(r"color:\s*(.*?);", ss)
        if bg_match:
            self._background_color = QtGui.QColor(bg_match.group(1).strip())
        if fill_match:
            self._fill_color = QtGui.QColor(fill_match.group(1).strip())

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect()
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(rect), self._border_radius, self._border_radius)
        painter.setClipPath(path)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(self._background_color)
        painter.drawRoundedRect(QtCore.QRectF(rect), self._border_radius, self._border_radius)
        if self._activation_percent > 0:
            bar_height = int((self._activation_percent / 100.0) * rect.height())
            fill_rect = QtCore.QRect(rect.x(), rect.bottom() - bar_height + 1, rect.width(), bar_height)
            painter.setBrush(self._fill_color)
            painter.drawRect(fill_rect)
        painter.setPen(QtCore.Qt.black if self._activation_percent >= 50 else QtCore.Qt.red)
        painter.setFont(self.font())
        painter.drawText(rect, QtCore.Qt.AlignCenter, self._label_text)


class VerticalTextButton(QtWidgets.QPushButton):
    """QPushButton that paints its caption as one centred character per row."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._checked_text_color = QtGui.QColor()

    def set_checked_text_color(self, color):
        """Set an optional caption colour used only while the button is checked."""
        self._checked_text_color = QtGui.QColor(color)
        self.update()

    def sizeHint(self):
        size = super().sizeHint()
        size.transpose()
        return size

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        size.transpose()
        return size

    def paintEvent(self, event):
        painter = QtWidgets.QStylePainter(self)
        option = QtWidgets.QStyleOptionButton()
        self.initStyleOption(option)
        text = option.text
        option.text = ""
        painter.drawControl(QtWidgets.QStyle.CE_PushButton, option)

        if not text:
            return

        painter.setFont(self.font())
        role = QtGui.QPalette.ButtonText
        text_color = option.palette.color(role)
        if self.isChecked() and self._checked_text_color.isValid():
            text_color = self._checked_text_color
        painter.setPen(text_color)
        metrics = QtGui.QFontMetrics(self.font())
        line_height = metrics.height()
        total_height = line_height * len(text)
        top = max(0, (self.height() - total_height) // 2)
        for index, char in enumerate(text):
            rect = QtCore.QRect(0, top + index * line_height, self.width(), line_height)
            painter.drawText(rect, QtCore.Qt.AlignCenter, char)
