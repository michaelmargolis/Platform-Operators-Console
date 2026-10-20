# sim_registry.py ver 1
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module


@dataclass(frozen=True)
class SimDefinition:
    key: str
    display_name: str
    module: str
    class_name: str = "Sim"
    operator_selectable: bool = True


SIMS = (
    SimDefinition("nolimits2", "NoLimits2 Coaster", "sims.nolimits2.sim"),
    SimDefinition("spacecoaster", "SpaceCoaster", "sims.spacecoaster.sim"),
    # Retained for development and flight testing, but hidden from the
    # deployed operator selector until X-Plane commissioning resumes.
    SimDefinition(
        "xplane", "X-Plane", "sims.xplane.sim", operator_selectable=False
    ),
)

OPERATOR_SIMS = tuple(item for item in SIMS if item.operator_selectable)


def definition_for(key: str) -> SimDefinition:
    for item in SIMS:
        if item.key == key:
            return item
    raise KeyError(key)


def create_sim(key: str, address: str):
    definition = definition_for(key)
    module = import_module(definition.module)
    cls = getattr(module, definition.class_name)
    return cls(address=address)
