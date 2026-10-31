# conftest.py ver 1
"""Never read/write the operator's working variant catalogs in automated tests."""
import pytest


@pytest.fixture(autouse=True)
def isolated_variant_files(tmp_path, monkeypatch):
    monkeypatch.setenv("POC_RIDE_VARIANTS_DIR", str(tmp_path / "isolated_variants"))
