<!-- POSE_SENDER_README.md ver 3 -->
# POC Pose Sender v165

Standalone Qt utility for the six normalized DOF commands (Surge, Sway, Heave,
Roll, Pitch, Yaw, -1.00 to +1.00). Operator-facing signs follow the rider-frame
convention: +X forward, +Y left, +Z up, +roll right side down, +pitch nose down,
+yaw CCW. Heave is inverted at the UI boundary so positive slider motion means up.
Starts with the platform from
`poc_config.json` (`selected_platform`); no changes to POC saved settings.

From the repository root, on Windows/Python with POC dependencies installed:

```powershell
python output/fstlib/pose_sender.py
```

The EasyIP UDP destination defaults to **192.168.0.10:995**. Actual-pressure
registers **10 through 15** are polled every 200 ms for **optional display**;
a reply is **not required** to arm, unpark, run or park. Motion commands are
sent at 20 Hz when armed. Missing/stale feedback is shown as such and must not
be mistaken for confirmation of actual pressure or mechanical position.

## Sequence

1. Run only ONE controller against the same Festo target; stop POC and
   `festoSender.py`. Verify the target and selected platform match the test.
2. Start the emulator (preferably on a separate host) and Platform Visualizer
   in EasyIP mode. Readings may remain unavailable in Visualizer EasyIP mode. The sender initially
   transmits **read requests only**, not pressure commands.
3. Verify the platform is **physically parked**, clear of people, and the
   external emergency stop is effective. Treat all actual pressures as unknown unless independently verified.
4. Click **Acknowledge / Arm**, confirm the safety dialog, and then click
   **Slow UNPARK → neutral**. The six sliders become active when unpark finishes.
5. Adjust DOF sliders. The displayed requested and applied real DOFs use
   POC's current platform limits, geometric floor clearance and actuator
   workspace limiter. **Zero DOFs** targets the neutral pose.
6. Click **Slow PARK**. Wait for commanded pressure to return to zero and
   for the slow park sequence to finish and all six **commanded** pressures
   to reach zero before automatic disarm. This does not prove physical parking.
7. Exit only after the real platform has been checked safe.

Park/unpark uses POC's **50 mm/s muscle-length transitions at 20 Hz**. Active
pressure changes additionally have a **6000 mbar/s per-channel slew limit**.
For platforms configured with `HAS_PISTON`, the existing POC prop sequence and
seventh EasyIP flagword are used. The prop has **no position sensor**.

## Important safety limitations

This is engineering test software, **not** a safety-rated motion controller.
Neither valid EasyIP pressure feedback nor the workspace limiter proves that
muscle tensions are feasible, that the platform is parked, or that the area is
clear. The software **assumes a physically parked start** after explicit
operator confirmation, without measuring pose or prop state. POC configurations
with `WORKSPACE_LIMIT_MODE=off` have no geometric floor-clearance enforcement;
the utility displays a warning for such configurations.

Missing or stale pressure feedback **does not stop transmission**. A motion
output or socket fault still stops transmission and latches a fault. **Stopping UDP does not vent the PLC, release pressures, or stop motion**:
use the external emergency stop and isolate pneumatic power according to the
platform operating procedure. A fault requires application restart and fresh
physical inspection before rearming. Closing while moving likewise requires
explicit E-stop confirmation. The button "STOP TX" is **not** an emergency stop.

Testing order: emulator → visualizer EasyIP mode → physical platform only after
formal assessment of the mechanical and pneumatic risk, emergency stop and
operator safeguards. Avoid commanding displacements with occupants or people
within the platform's reach.
