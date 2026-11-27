# conftest.py ver 2
"""Never read/write the operator's working variant catalogs in automated tests."""
import sys
import time

import pytest


@pytest.fixture(autouse=True)
def isolated_variant_files(tmp_path, monkeypatch):
    monkeypatch.setenv("POC_RIDE_VARIANTS_DIR", str(tmp_path / "isolated_variants"))


@pytest.fixture(autouse=True)
def high_resolution_monotonic(monkeypatch):
    """Keep successive fake telemetry replies distinct on Windows."""
    if sys.platform == "win32":
        monkeypatch.setattr(time, "monotonic", time.perf_counter)
