"""Actual Qt help-dialog smoke tests (skipped when PyQt6 is unavailable)."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PyQt6')
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QMainWindow
from tools.sim_player.workspace_help import WorkspaceHelpDialog
from tools.sim_player.workspace_help_content import help_topics


@pytest.fixture(scope='module')
def app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def dialog(app):
    parent = QMainWindow()
    window = WorkspaceHelpDialog(parent)
    window.show()
    app.processEvents()
    yield window
    window.close()
    parent.close()
    window.deleteLater()
    parent.deleteLater()
    app.processEvents()


def test_six_real_qt_tabs_with_read_only_pages(dialog):
    assert dialog.tabs.count() == 6
    assert [dialog.tabs.tabText(i) for i in range(6)] == [t.title for t in help_topics()]
    for page in dialog.pages.values():
        assert page.isReadOnly()
        assert not page.openExternalLinks()
        assert not page.openLinks()
        assert len(page.toPlainText()) > 200


def test_help_is_not_modal(dialog):
    assert not dialog.isModal()
    assert dialog.windowModality() == Qt.WindowModality.NonModal


def test_selection_survives_close_reopen(dialog, app):
    dialog.select_topic('washout')
    chosen = dialog.tabs.currentIndex()
    dialog.close()
    dialog.show()
    app.processEvents()
    assert dialog.tabs.currentIndex() == chosen


def test_invalid_topic_preserves_selection(dialog):
    previous = dialog.tabs.currentIndex()
    dialog.select_topic('not_a_topic')
    assert dialog.tabs.currentIndex() == previous


def test_escape_closes_help(dialog, app):
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    app.processEvents()
    assert not dialog.isVisible()


def test_close_button_closes_help(dialog, app):
    QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Close),
                     Qt.MouseButton.LeftButton)
    app.processEvents()
    assert not dialog.isVisible()


def test_f1_in_help_does_not_close_it_or_change_tab(dialog, app):
    dialog.select_topic('video')
    selected = dialog.tabs.currentIndex()
    QTest.keyClick(dialog, Qt.Key.Key_F1)
    app.processEvents()
    assert dialog.isVisible()
    assert dialog.tabs.currentIndex() == selected
