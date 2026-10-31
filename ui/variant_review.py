# variant_review.py ver 1
"""Touch/encoder variant review dialog; deliberately no undo/editor controls."""
from PyQt5 import QtCore, QtWidgets


class VariantReviewDialog(QtWidgets.QDialog):
    """Modeless-modal: GPIO/20 Hz servicing continues while the dialog is open."""
    PREFERRED = 1
    REJECTED = 2

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review ride variant")
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.setMinimumWidth(360)
        self.setMaximumWidth(590)
        layout = QtWidgets.QVBoxLayout(self)
        text = QtWidgets.QLabel(f"{info['label']}\n{info['platform_key']}", self)
        text.setWordWrap(True)
        text.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(text)
        explanation = QtWidgets.QLabel(
            "Select as preferred rejects all other variants for this park.\n"
            "Reject hides this variant; its loaded settings remain until you select another.", self)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.warning = QtWidgets.QLabel(self)
        self.warning.setWordWrap(True)
        self.warning.setTextFormat(QtCore.Qt.PlainText)
        self.warning.setText(
            f"Warning: rejecting this variant leaves no available rides for {info['park_name']}."
            if info['last_for_park'] else "")
        self.warning.setVisible(bool(info['last_for_park']))
        self.warning.setStyleSheet("font-weight: bold; color: #b04700;")
        layout.addWidget(self.warning)
        self.buttons = []
        for caption, result in (("Select as preferred", self.PREFERRED),
                                ("Reject this variant", self.REJECTED), ("Cancel", 0)):
            button = QtWidgets.QPushButton(caption, self)
            button.setMinimumHeight(44)
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False, value=result: self.done(value))
            layout.addWidget(button)
            self.buttons.append(button)
        self.choice = 2
        self._highlight()

    def _highlight(self):
        for i, button in enumerate(self.buttons):
            selected = i == self.choice
            button.setDefault(selected)
            button.setStyleSheet("QPushButton {border: 3px solid #1976b9; font-weight: bold;}"
                                 if selected else "")
        self.buttons[self.choice].setFocus(QtCore.Qt.OtherFocusReason)

    def rotate(self, direction):
        if direction:
            self.choice = (self.choice + (1 if direction > 0 else -1)) % len(self.buttons)
            self._highlight()

    def press(self):
        self.buttons[self.choice].click()

    def showEvent(self, event):
        super().showEvent(event)
        self.choice = 2
        self._highlight()
