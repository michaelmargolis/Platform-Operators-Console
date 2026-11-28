# platforms.py ver 8
"""Physical-platform metadata used by the prototype selector.

The module paths match the current POC kinematics configuration. The canonical
Ground-Loading Platform deployment key remains ``platform_v2`` for saved-config and wire compatibility.
POC does not hot-switch physical platforms; a changed selection is persisted and
applied on restart.
"""

PLATFORMS = {
    "platform_v2": {
        "name": "Ground-Loading Platform",
        # Keep the established protocol name for wire compatibility.
        "platform_name": "wheelchair_platform",
        "sop_config_module": "kinematics.cfg_SuspendedPlatformMdx",
        "festo_valve0_side": "right",
    },
    "chair_v3": {
        "name": "V3 Chair",
        "sop_config_module": "kinematics.cfg_SuspendedChairV3",
        "festo_valve0_side": "left",
    },
}

# Keep these keys readable in machine-local wiring settings for rollback.
DEFERRED_PLATFORMS = frozenset({"chair_v2", "Falcon"})
