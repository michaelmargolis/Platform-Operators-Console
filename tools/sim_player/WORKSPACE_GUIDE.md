# Sim Player Task Workspace v0.2.10

## Installation

| Named file | Action |
|---|---|
| `Sim_Player_Task_Workspace_v0_2_10_delta_root_ready.zip` | Recommended for an existing v0.2.9 installation. Close Sim Player and extract directly into the existing POC repository root, replacing the included `tools/sim_player/...` paths. |
| `Sim_Player_Task_Workspace_v0_2_10_full.zip` | Alternative complete Sim Player package, not an additional step. Extract directly into the same existing POC repository root. It is not a complete POC checkout. |
| `Sim_Player_Task_Workspace_v0_2_10_Guide.md` | Reference only; no installation. |
| `Sim_Player_Task_Workspace_v0_2_10_Verification.md` | Reference only; no installation. |
| `Sim_Player_Task_Workspace_v0_2_10_Changed_Files.txt` | Reference only; exact files changed relative to v0.2.9. |

This update **requires an existing complete checkout with prior updates applied**.
All archive entries use full repository-relative paths.

## What changed in v0.2.10

- Each collapsible inspector section now uses one shared frame around both its title QToolButton and its content.
- The title button sits directly at the top-left of that frame with no gap, giving the section a connected tab/panel appearance.
- The compact Ride and candidate row uses the same attached treatment.
- Inspector dock titles now use `Inspector : Review`, `Inspector : Tune` and `Inspector : Validate`.
- No workflow, analysis, plotting, overlay, video, matching or motion-processing logic changed.
