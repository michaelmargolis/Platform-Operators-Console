#sim_tabs.py  ver 2
from __future__ import annotations

from pathlib import Path
from PyQt5 import uic
from PyQt5.QtWidgets import QWidget


class SimTabManager:
    """Loads zero or more simulator-owned tabs into the POC tab widget.

    Prototype NL2 and SpaceCoaster intentionally provide no simulator-specific
    tabs. The manager exists now so later simulator UI does not affect PocCore.
    """

    def __init__(self, tab_widget, debug_tab=None):
        self.tab_widget = tab_widget
        self.debug_tab = debug_tab
        self._tabs: list[QWidget] = []

    def clear(self) -> None:
        for widget in list(self._tabs):
            index = self.tab_widget.indexOf(widget)
            if index >= 0:
                self.tab_widget.removeTab(index)
            widget.deleteLater()
        self._tabs.clear()

    def load(self, ui_files) -> None:
        self.clear()
        for spec in ui_files or ():
            path, title = spec
            widget = uic.loadUi(str(Path(path)))
            debug_index = self.tab_widget.indexOf(self.debug_tab) if self.debug_tab is not None else -1
            if debug_index >= 0:
                self.tab_widget.insertTab(debug_index, widget, title)
            else:
                self.tab_widget.addTab(widget, title)
            self._tabs.append(widget)
