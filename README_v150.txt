POC v150 - Twin applied real transform, parking state, Output-tab switch
=====================================================================
Apply this delta to the COMPLETE POC v149 installation.

POC_TWIN protocol version 1 adds TWO optional fields:
  parking_state: "PARKED" | "UNPARKED" | "PARKING" | "UNPARKING"
  applied_transform_real: [X,Y,Z,roll,pitch,yaw] or null
Translations are mm; rotations radians; pose is the backend's last transform
actually used to calculate the actuator lengths after workspace limiting.
Existing normalized 'transform' is unchanged. Snapshots continue to contain
commanded pressures and lengths. The additional fields are part of the same
snapshot, not an independently observed physical state.

Platform Output tab, full-size and compact layouts:
  Twin checkbox enables/disables UDP output for this RUN only.
  Initially checked when the configured digital_twin destination is enabled.
  Caption shows the address:port from poc_config.json; '-' means unconfigured
  and disables the checkbox. This UI NEVER edits poc_config.json.

Use the configured 'digital_twin': {'address': 'a.b.c.d', 'port': 10020}
for output. '-' remains disabled. If the checkbox is off, transmissions stop;
checking it again opens a new UDP sender at the same configured destination.

Only output/protocol/UI files were changed. No POC motion or parking controller
state machine changes. Run python version_check.py after extracting.
