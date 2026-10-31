# platforms.py ver 6
"""Physical-platform metadata used by the prototype selector.

The module paths match the current POC kinematics configuration. The canonical
MDX deployment key remains ``platform_v2`` for saved-config and wire compatibility.
POC does not hot-switch physical platforms; a changed selection is persisted and
applied on restart.
"""

PLATFORMS = {
    "platform_v2": {
        "name": "MDX Suspended Platform",
        # Keep the established protocol name for wire compatibility.
        "platform_name": "wheelchair_platform",
        "sop_config_module": "kinematics.cfg_SuspendedPlatformMdx",
    },
    "chair_v3": {
        "name": "V3 Chair",
        "sop_config_module": "kinematics.cfg_SuspendedChairV3",
    },
    "Falcon": {
        "name": "Falcon Platform",
        "sop_config_module": "kinematics.cfg_SuspendedChairFalcon",
    },

}
