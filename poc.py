#poc.py  ver 4
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from core.config_store import ConfigStore
from core.poc_core import PocCore
from ui.main_window import MainWindow


PYQT_UIC_DEBUG = False


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Platform Operators Console")
    parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="start full screen; Escape closes the application",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = _parse_args(sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if not PYQT_UIC_DEBUG:
        # PyQt's .ui parser inherits the root DEBUG level and otherwise emits
        # hundreds of property/parser messages during startup.
        logging.getLogger("PyQt5.uic").setLevel(logging.WARNING)
        logging.getLogger("PyQt5.uic.uiparser").setLevel(logging.WARNING)
        logging.getLogger("PyQt5.uic.properties").setLevel(logging.WARNING)
    root = Path(__file__).resolve().parent
    app = QtWidgets.QApplication(sys.argv)
    try:
        config = ConfigStore(root / "poc_config.json")
    except RuntimeError as exc:
        logging.getLogger(__name__).error("POC configuration error: %s", exc)
        QtWidgets.QMessageBox.critical(
            None,
            "POC Configuration Error",
            str(exc),
        )
        return 2

    try:
        # Catalog validation happens in PocCore before constructing its physical
        # backend. Invalid JSON must produce a visible error, not a Qt traceback.
        core = PocCore(config)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        logging.getLogger(__name__).error("POC startup configuration error: %s", exc)
        QtWidgets.QMessageBox.critical(None, "POC Configuration Error", str(exc))
        return 2
    window = MainWindow(core, config)
    if args.fullscreen:
        escape_shortcut = QtWidgets.QShortcut(
            QtGui.QKeySequence(QtCore.Qt.Key_Escape), window
        )
        escape_shortcut.activated.connect(window.close)
        # Keep the shortcut alive for the lifetime of the window.
        window._fullscreen_escape_shortcut = escape_shortcut
        window.showFullScreen()
    else:
        window.show()
    try:
        core.start()
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        logging.getLogger(__name__).error("POC startup failed: %s", exc)
        core.shutdown()
        QtWidgets.QMessageBox.critical(window, "POC Startup Error", str(exc))
        return 2
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
