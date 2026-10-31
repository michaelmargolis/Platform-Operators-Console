# test_review_dialog_gui_v178.py ver 1
"""Qt5 visual-contract tests; run on Pi/Windows with PyQt5 installed."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
pytest.importorskip('PyQt5')
from PyQt5 import QtWidgets
from ui.variant_review import VariantReviewDialog


def test_dialog_fits_compact_screen_and_cancel_is_default():
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog=VariantReviewDialog({'label':'Blackbird [Sine Roll 120%]', 'platform_key':'platform_v2',
                               'park_name':'Blackbird','last_for_park':True})
    dialog.show();app.processEvents()
    assert dialog.width()<=780 and dialog.height()<=450
    assert dialog.buttons[2].isDefault()
    assert dialog.warning.isVisible()
    assert all(b.height()>=44 for b in dialog.buttons)
    assert all(b.isVisible() for b in dialog.buttons)
    values=[];dialog.finished.connect(values.append)
    dialog.rotate(1);assert dialog.buttons[0].isDefault()
    dialog.press();app.processEvents()
    assert values==[VariantReviewDialog.PREFERRED]
    dialog.deleteLater()
