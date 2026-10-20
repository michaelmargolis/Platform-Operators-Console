<!-- Project Ideas.md ver 2 -->
# Platform Operators Console — Final-Year Project Ideas

**Audience:** Design, Engineering, mechanical, mechatronics, electronics, robotics, computing, HCI and interdisciplinary students  

**Companion:** [Architecture and Hardware Guide](ARCHITECTURE.md)

## Why this is a useful engineering platform

The Middlesex motion system combines an **inverted six-degree-of-freedom Stewart mechanism** with six pneumatic muscles, a Raspberry Pi operator console, networked simulator software, empirical pressure calibration and an optional digital twin. It poses a mix of design questions: accessible mechanical architecture, pneumatic power and dynamic response, sensing, kinematics, motion cueing, software reliability, human experience and application design.

The current experience centres on roller coaster VR simulation. There is also a flight simulator adapter. **These do not define the platform's limits as a research subject.** Students can improve the hardware or software, or explore entirely different uses, including education, teleoperation, vehicle or marine motion, motion perception and accessible interactive exhibits.

A strong final-year project has **meaningful testable question**, a manageable prototype, and measurable success criteria. A proof-of-concept using an emulator, recorded data or a benchtop subsystem can be a valid outcome.

Projects involving people or moving hardware require supervisory approval, a documented risk assessment, appropriate guarding, independent emergency stops and staged testing. Healthcare, disability-related and training use cases require additional user/ethics review and must not be presented as clinically validated.

## Project selection at a glance

| # | Project | Main focus | Best starting point | Indicative scope |
| --- | --- | --- | --- | --- |
| 1 | Camera-based platform pose measurement | Vision/metrology | One camera + rigid marker board | Substantial |
| 2 | Non-intrusive muscle-length sensor | Mechanical/electronics | One actuator instrument | Substantial |
| 3 | Measured-pressure and airflow logging | Pneumatics/embedded | Bench or one valve channel | Moderate–substantial |
| 4 | Dynamic actuator identification | Modelling | Synchronized command/measurement dataset | Substantial |
| 5 | Improved length-to-pressure calibration | Pneumatics/data | Existing tables + controlled measurements | Moderate–substantial |
| 6 | Motion limits and collision-envelope validation | CAD/kinematics | CAD + mock kinematics | Moderate |
| 7 | Parking/prop position verification | Mechanisms/safety engineering | Instrumented latch/park test rig | Substantial |
| 8 | Accessible platform layout and boarding design | Product/design/HCI | Mock-up and user-centred evaluation | Substantial |
| 9 | Closed-loop motion-control feasibility | Controls | Offline model and captured measurements | Advanced |
| 10 | Alternative motion-cue source adapter | Software/integration | Recorded or emulated telemetry | Moderate |
| 11 | Low-cost motion-perception laboratory | Human factors/education | Virtual prototype, supervised protocol | Moderate–substantial |
| 12 | Accessible immersive museum/exhibit experience | Experience/HCI | Storyboard + simulator prototype | Moderate–substantial |
| 13 | Marine/vehicle/industrial motion training | Application/control | Synthetic motion scenarios | Moderate–substantial |
| 14 | Teleoperation / remote-machine feedback | Robotics/HCI | Recorded robot telemetry | Advanced |

Challenges are indicative, not prerequisites. Several teams could use the same documented test datasets while working on different sensors, models and software.

---

## A. Instrumentation, pneumatics and mechanical improvement

### 1. Measure actual 6-DOF platform pose with a fixed camera

**Question:** Can a calibrated camera and AprilTag/ArUco target provide sufficiently accurate, low-latency motion measurements for validation of platform pose?

**Build:** A fixed camera and rigid target, extrinsic/intrinsic calibration, timestamps and a transform from camera coordinates to platform coordinates. Estimate six-degree-of-freedom pose, then derive six attachment-to-attachment lengths using the known geometry.

**Possible deliverables:** Calibration jig or procedure, capture/logger application, uncertainty report. **Evaluate:** static accuracy, repeatability, delay, frame rate, and pose-derived length error. Begin with stationary and slow-axis tests.

### 2. Design a removable actuator-length instrument

**Question:** Can a pneumatic muscle's actual contraction be measured without interfering with its movement?

**Build:** One draw-wire encoder, optical scale, linear sensor or alternative instrument, with suitable cable routing, guards and mounting brackets.

**Deliverables:** One-channel working prototype, calibration procedure, data logger, mechanical design and failure-mode assessment. **Evaluate:** accuracy, resolution, hysteresis, sampling rate, dynamic response, friction and robustness. Possibly expand from one to six actuators after validating the first.

### 3. Instrument the pneumatic supply and valve response

**Question:** How closely do commanded pressures track measured pressure, and what are the dominant delays?

**Build:** A bench/safely isolated valve or actuator instrumentation rig with suitable pressure transducers and, if justified, airflow sensing. Timestamp Festo commands independently of measurements. Compare Festo pressure readings at valve to readings at muscles.

**Deliverables:** Sensor/electronics design, calibration, synchronized plots, response characterisation. **Evaluate:** step response, rising/falling asymmetry, settling time, pressure ripple and dependence on supply conditions.

### 4. Identify the dynamic muscle/platform response

**Question:** Can a practical model predict platform motion under time-varying commands better than a static pressure-to-length lookup?

**Build:** System-identification analysis using controlled, synchronized pressure, length and/or vision-pose measurements; compare asymmetric slew, time-constant and load-aware models.

**Deliverables:** Reproducible notebooks, calibrated model, held-out validation data and optional digital-twin integration. **Evaluate:** position/length error, phase delay, response bandwidth, hysteresis and model runtime cost. Keep a simple baseline for comparison.

### 5. Improve load-dependent pressure calibration

**Question:** How accurately do existing displacement-to-pressure lookup tables work over different supported loads and contractions?

**Build:** A repeatable calibration workflow for selected payloads, noting platform tare, six-way load assumptions and possible unequal load sharing with off-centre occupants.

**Deliverables:** Validated test procedure, experimental tables, interpolation comparison, quality and uncertainty report. **Evaluate:** repeatability, calibration residuals, load sensitivity and effect on reconstructed pose. Do not replace the deployed table until reviewed and commissioned.

### 6. Validate and improve the physical motion envelope

**Question:** Do the current kinematic limits accurately predict floor clearance and actuator limits across coupled motions?

**Build:** CAD model and independent numerical geometry checker for the MDX moving-platform underside and six actuator paths. Compare the configured `WORKSPACE_LIMIT_MODE="geometry"` results against actual clearances at safely chosen test poses.

**Deliverables:** Verified collision/clearance model, worst-case pose study and test fixtures or non-contact measurements. **Evaluate:** minimum clearance margin, coverage, conservatism and runtime cost. Never use the student model as the sole live safety protection.

### 7. Verify platform parked position and chair support mechanism

**Question:** Can low-cost sensing distinguish a commanded parking state from a genuinely supported/latched platform?

**Build:** Candidate limit switches, proximity/position sensors, prop feedback or a mechanically independent confirmation method, initially on a mock-up. Define fault conditions such as missed engagement, sensor disagreement and lost power.

**Deliverables:** Sensor mounting, test rig, fault-detection logic prototype and acceptance protocol. **Evaluate:** repeatability, false acceptance/rejection, failure detectability and independence from POC timing. Do not modify the production safety chain without a formal review.

### 8. Make wheelchair access and serviceability easier

**Question:** How can the inverted-platform arrangement be made more accessible, comfortable and maintainable while preserving safety and workspace?

**Build:** Research-backed design proposals for boarding geometry, wheel restraint/interface, occupant positioning, protective guards, service clearances and ergonomic operator controls. Use CAD, physical mock-ups and stakeholder consultations where approved.

**Deliverables:** Design specifications, concepts, prototype subsystem, accessibility and hazard assessment. **Evaluate:** ease of transfer/boarding, reach/clearances, repeatability, maintenance access and acceptability. Do not assert accessibility compliance from CAD alone.


---

## B. Software, modelling and control projects


### 9. Assess closed-loop control with real measurements

**Question:** Could reliable pose/length feedback improve tracking of commanded motion beyond the existing open-loop pressure lookup?

**Build:** An **offline** controller using measured response data, then a simulated closed-loop rig. Compare feed-forward correction, simple feedback and model-based methods with actuator pressure/rate constraints.

**Deliverables:** Calibrated model, tracking/error plots, disturbance tests and safety/latency feasibility report. **Evaluate:** tracking error, overshoot, delay tolerance and failure behaviour. Live closed-loop experiments require a separate approved safety case and independent safeguards.

### 10. Applications beyond roller coasters and flight simulation

**Question:** How easily could alternative motion sources be used to drive the platform?

**Build:** A new `SimInterface` adapter for a recorded vehicle/marine/robot dataset or a synthetic motion source. 

**Deliverables:** Adapter, documented input schema, runnable offline demo and tests. **Evaluate:** interoperability, rate/timing, error handling and compatibility with the existing POC state transitions.

These are **exploratory use cases**, not validated products. For an application-oriented project, the first deliverable may be a user scenario and synthetic-data demonstration rather than physical motion with participants.

### 11. A 6-DOF motion-perception teaching laboratory

**Question:** Which combinations of acceleration, tilt, vibration and washout are perceptually distinguishable, and how do visual cues affect the judgement?

**Build:** Safe, supervised stimulus protocols, a student-facing cue designer and a repeatable questionnaire/measurement scheme. Explore how normalized transforms map through POC to a feasible platform command.

**Deliverables:** Educational software, repeatable set of cues, pilot study design and data analysis. **Evaluate:** reproducibility, cue discrimination and participant comfort where approvals permit. Ethical clearance and operator supervision are essential before human trials.

### 12. Inclusive interactive exhibit or museum installation

**Question:** Could a low-elevation motion platform enhance an interactive exhibit for visitors with differing mobility needs?

**Build:** A narrative/prototype for an underwater exploration, space station docking, planetary terrain or earthquake-awareness exhibit using a synthetic timeline of safe motion cues. Develop accessible start/stop and operator-flow concepts.

**Deliverables:** Experience storyboard, prerecorded motion-cue adapter, accessibility assessment, demo with digital twin and user-feedback plan. **Evaluate:** comprehension, usability, operator workload and motion comfort (only after approval). Aim for a fixed-duration, predictable experience rather than a full game.

### 13. Maritime, road-vehicle or industrial-equipment training

**Question:** Can limited 6-DOF tilt and translation cues support training or demonstration of vehicle/environment motion?

**Build:** Use synthetic or logged vessel roll/pitch/heave, road-surface profiles, off-road vehicle dynamics or mobile-equipment manoeuvres. Design a cue-shaping/washout method that respects the short stroke and pneumatic response bandwidth.

**Deliverables:** Scenario player, motion-mapping model, twin demonstration and objective criteria for cue timing/saturation. **Evaluate:** cue fidelity versus target acceleration/orientation, washout artifacts and input-state robustness. Training effectiveness itself requires a separately designed user study.

### 14. Robot teleoperation and remote-machine feedback

**Question:** Could controlled platform motion give an operator useful cues about a remote vehicle or robot, without literally reproducing its full movement?

**Build:** Translate recorded mobile-robot, rover or remote-machinery attitude/acceleration into bounded normalized 6-DOF cues. Start offline and visualize command-versus-feasible platform poses.

**Deliverables:** Data adapter, scaling/saturation strategy, simulator/digital-twin demo and HCI evaluation plan. **Evaluate:** delay, cue interpretability, stability during dropout and ability to signal hazards. Real-time human-in-the-loop testing requires additional safeguards.

**Other possible application directions:**   immersive art installations, low-frequency seismic-motion education and bench testing of stabilization algorithms. Scope these around measurable research questions rather than broad promises of realism or medical benefit.

## How to structure a successful final-year project

A practical 1-year scope is often: **requirements and hazards -> one subsystem prototype -> repeatable tests -> analysis -> design iteration**. Do not try to solve hardware sensing, modelling, new content generation and live feedback control within one project.

Suggested milestones:

1. **Discover:** Read [architecture](ARCHITECTURE.md), choose a platform/config, identify which data are commands versus observations and specify a testable hypothesis.
2. **Bound:** List platform operating limits, data-rate/latency targets, resources, access restrictions and safety/ethics requirements.
3. **Prototype:** Develop with a benchtop rig, synthetic source, emulator or recorded-data capture first.
4. **Measure:** Gather timestamped, versioned datasets, report units/frame definitions, repeat trials and characterize uncertainty.
5. **Validate:** Compare against a baseline, test edge cases/failures and document conditions under which the result does **not** work.
6. **Handover:** Submit reproducible code/CAD, wiring, calibration, test instructions and a concise recommendation for follow-on students.

### Useful common dataset

When instruments are available, aim to log **POC packet sequence/monotonic receive time, normalized request, applied real transform, six commanded lengths and pressures, commanded parking state, measured pose and/or sensor lengths, load assumptions, and validation flags**. Distinguish camera pose-derived muscle length from directly measured length; record how timestamps were aligned. Avoid personal or participant data unless approved.

For test procedures, sensor trade-offs and synchronization pitfalls, see the separate *Motion-platform instrumentation handover* supplied with the project. That handover predates some v150 fields; inspect the current `core/digital_twin.py` before parsing new recordings.

## Resources and contribution principles

- [Architecture and hardware guide](ARCHITECTURE.md)
- [Current POC architecture/controller](../core/poc_core.py)
- [Common simulator interface](../core/sim_interface.py)
- [Platform kinematics and configurations](../kinematics/README.md)
- [Parking operation and limitations](PARKING_OPERATION_V149.md)
- [Twin transport implementation](../core/digital_twin.py)
- [Existing automated tests](../tests/)

Research code **must not control the live platform** until reviewed by your tutor. Preserve small changes, documented units and interface compatibility; never substitute synthetic calibration data for validated physical measurements.
