# test_dynamic_optimizer_packaging_v204.py ver 1
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_dynamic_optimizer_entrypoint_imports():
    from tools.nl2_dynamic_profiles import __main__ as dynamic_main
    from tools.sim_player import dynamic_optimizer
    assert dynamic_optimizer.main is dynamic_main.main


def test_dynamic_optimizer_help_runs_as_module():
    root = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        [sys.executable, '-m', 'tools.sim_player.dynamic_optimizer', '--help'],
        cwd=root, text=True, capture_output=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert 'Phase 1 offline dynamic motion profiles' in completed.stdout
