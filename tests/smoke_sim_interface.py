#smoke_sim_interface.py  ver 1
"""Small no-Qt smoke test for the temporary simulator contract."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.types import ExperienceState
from sims.spacecoaster.sim import Sim

sim = Sim()
sim.connect()
assert sim.read().state is ExperienceState.READY
sim.run()
assert sim.read().state is ExperienceState.RUNNING
sim.pause()
assert sim.read().state is ExperienceState.PAUSED
sim.reset_vr()
assert sim.read().state is ExperienceState.PAUSED
sim.disconnect()
assert sim.read().state is ExperienceState.DISCONNECTED
print("sim interface smoke test passed")
