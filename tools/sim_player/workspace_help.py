# workspace_help.py ver 1
"""Tabbed, modeless player help; no capture, settings or output side effects."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QLabel, QTabWidget, QTextBrowser,
    QVBoxLayout,
)

from . import PRODUCT_NAME, WORKSPACE_VERSION
from .workspace_help_content import help_topics, workflow_quick_help


class WorkspaceHelpDialog(QDialog):
    """One reusable help window owned by the player; retains the selected tab.

    show(), rather than exec(), deliberately leaves playback and the main
    inspector usable. It never changes the player's current state.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('workspaceHelpDialog')
        self.setWindowTitle(PRODUCT_NAME + ' - Task workspace help')
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setModal(False)
        self.setSizeGripEnabled(True)
        self.setMinimumSize(500, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        title = QLabel(PRODUCT_NAME + ' - Task Workspace ' + WORKSPACE_VERSION)
        title.setStyleSheet('font-weight: bold; font-size: 13pt;')
        layout.addWidget(title)
        hint = QLabel('Help / information icon / F1 open this same window. Playback is unchanged.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.tabs = QTabWidget()
        self.tabs.setObjectName('workspaceHelpTabs')
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self._topic_indices = {}
        self.pages = {}
        for topic in help_topics():
            browser = QTextBrowser()
            browser.setObjectName('workspaceHelp_' + topic.key)
            browser.setOpenExternalLinks(False)
            browser.setOpenLinks(False)
            browser.document().setDocumentMargin(14)
            browser.document().setDefaultStyleSheet(
                'body { color: #e4edf6; font-family: "Segoe UI", sans-serif; } '
                'h2 { color: #8cd4f1; margin-top: 4px; } '
                'h3 { color: #b6dbea; margin-top: 16px; } '
                'p { margin-bottom: 10px; } '
                'code { font-family: Consolas, monospace; color: #f5d494; }'
            )
            browser.setHtml(topic.html)
            index = self.tabs.addTab(browser, topic.title)
            self._topic_indices[topic.key] = index
            self.pages[topic.key] = browser
        layout.addWidget(self.tabs, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.setObjectName('workspaceHelpButtons')
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        # Stay usable on a small monitor without relying on a fixed large font.
        screen = parent.screen() if parent is not None else QApplication.primaryScreen()
        size = QSize(900, 600)
        if screen is not None:
            available = screen.availableGeometry()
            size = QSize(min(900, max(500, available.width() - 64)),
                         min(600, max(360, available.height() - 64)))
        self.resize(size)

    def select_topic(self, key):
        """Optional context entry point; invalid keys leave the current tab alone."""
        index = self._topic_indices.get(key)
        if index is not None:
            self.tabs.setCurrentIndex(index)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F1:
            event.accept()  # Help is already open; do not create another dialog.
            return
        super().keyPressEvent(event)


class WorkflowHelpDialog(QDialog):
    """Small workflow-specific guide opened from the Review/Tune/Validate group."""

    def __init__(self, workflow, parent=None):
        super().__init__(parent)
        guide = workflow_quick_help(workflow)
        if guide is None:
            raise ValueError(f'Unknown workflow: {workflow}')
        self.workflow = str(workflow).lower()
        self.setObjectName('workflowHelpDialog_' + self.workflow)
        self.setWindowTitle(PRODUCT_NAME + ' - ' + guide['title'])
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setModal(False)
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        title = QLabel(guide['title'])
        title.setStyleSheet('font-weight: bold; font-size: 13pt;')
        layout.addWidget(title)
        purpose = QLabel(guide['purpose'])
        purpose.setWordWrap(True)
        purpose.setObjectName('workflowHelpPurpose')
        layout.addWidget(purpose)
        browser = QTextBrowser()
        browser.setObjectName('workflowHelpSteps')
        browser.setOpenExternalLinks(False)
        browser.setOpenLinks(False)
        browser.document().setDocumentMargin(8)
        steps = ''.join(f'<li>{step}</li>' for step in guide['steps'])
        browser.setHtml('<h3>What to do</h3><ol>' + steps + '</ol>')
        browser.setMinimumHeight(210)
        layout.addWidget(browser, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(610, 390)
