#PARKING_OPERATION_V149.md  ver 1
# POC v149 — Parked-between-rides operating sequence

## Scope

This revision changes dispatch/park sequencing; it does not alter CAD geometry,
DtoP tables, gain/washout configuration, simulator wire protocols, or the
external hardware emergency stop.

The source of truth for this delta is the supplied `poc_v148.zip`.

## Physical and logical state

- Startup **assumes** the platform is parked, as v148 did. It does not move
  or verify the platform before enforcing an OFF-to-ON Activate switch cycle.
- `PlatformState.ACTIVATED` / `DEACTIVATED` indicate logical operator activation.
- `PlatformMotionState.PARKED`, `UNPARKING`, `UNPARKED`, `PARKING`
  indicate the commanded (time-based) physical sequence.
- Activate while DEACTIVATED+PARKED only arms the platform. It does not unpark.
- Deactivate while PARKED disarms without physical motion.
- In other active positions, Deactivate aborts pending dispatch, pauses a
  RUNNING simulator (never resumes a PAUSED simulator), and parks.
- Experience/simulator changes, mode changes, and POC shutdown still require
  DEACTIVATED **and** parking complete.

## Dispatch

1. Requires activation, PARKED, a verified simulator-ready condition, no
   E-stop latch, no transition, and the requisite connection/data conditions.
2. Obtains **current valid READY transform** from the adapter. SpaceCoaster
   falls back to the first frame of its recorded CSV motion profile.
3. Applies the existing gain/intensity scaling without updating washout.
4. Unparks using the existing Festo/piston sequence and waits for completion.
5. Re-checks readiness before `sim.run()`; if readiness was lost, returns to
   PARKED without dispatching.
6. No double dispatch, or auto-dispatch after interrupted unparking.

## Normal end-of-ride

- NoLimits2: a ride dispatched by POC must have reached RUNNING and then
  returned to verified READY in station before auto-parking.
- SpaceCoaster: native RUNNING -> COMPLETED event parks while its established
  lobby/ready sequence continues.
- X-Plane: no normal automatic park; Deactivate initiates a controlled abort.
- Normal completion leaves logical activation ON. Re-dispatch is permitted
  after parking and simulator readiness return.
- Ordinary PAUSE behavior remains unchanged: the platform holds position.

## Recovery

- Deactivating while RUNNING or otherwise physically unparked/transitioning
  latches the software controlled-abort/E-stop state.
- Following deliberate OFF-to-ON reactivation, NoLimits2 and X-Plane skip
  recovery when the adapter *verifies* ready-to-start. SpaceCoaster invokes
  its existing lobby recovery after a latched abort, even if READY appeared;
  this is conservative and needs real-system testing.
- The existing external hardware E-stop removes valve/piston power. Software
  parking cannot be assumed to work when hardware power is removed.

## Safety and deployment limitations

- PARKED is **commanded and time-based**, not position-sensed. The 0.5 s prop
  actuator wait is retained; no prop-engagement feedback has been added.
- Chair V2 (`HAS_PISTON=True`) engages prop and extends muscles on parking.
  MDX (`HAS_PISTON=False`) extends muscles to its configured deactivated lengths.
- `WORKSPACE_LIMIT_MODE=off` for Chair V2 remains unchanged.
- The existing output transport is UDP. Packet transmission does not verify
  physical movement or prop engagement.
- The POC v148 archive supplied for this work omits
  `output/wheelchair_DtoP.csv` used by the MDX backend. Preserve the local
  original file when deploying; it was **not** recreated or modified.
- Do not deploy on an occupied platform before controlled no-load tests of
  startup, prop clearance, timed engagement, normal parking, interrupted
  unparking, delayed simulator commands, and external hardware E-stop.

## Suggested acceptance tests

1. Start with the physical Activate switch ON: no motion until OFF-to-ON.
2. Activate while parked: no piston release and no raise; Ready only if sim ready.
3. Deactivate at station: no physical command; experience change permitted.
4. Dispatch Chair V2: prop release precedes length transition; simulator Run
   only after unpark finished.
5. Dispatch MDX: rise precedes simulator Run; geometry limiter unchanged.
6. E-stop during unpark: cancel pending Run, park, disallow reactivation until parked.
7. E-stop during running and paused: simulator remains paused, platform parks.
8. End NL2/SpaceCoaster normally: park and retain logical ACTIVATED.
9. Restart after abort: only recover/reset where required; no unsolicited Run.
10. Lose sim link while unparking: abort and park, do not dispatch.
11. Disconnect hardware power: verify behaviour of mechanical failsafes separately.
