<!-- README.md ver 1 -->
# Canonical ride media (MDX Ride Analyzer)

Use **Open > Ride reference video...** in MDX Ride Analyzer to import an existing
video and the original telemetry CSV recorded with it. Do not place a lone MP4
here: the trajectory and calibration are necessary for reuse across captures.

The dialog creates `<park_key>/reference.json`, pointing to an import directory
containing `video.<extension>`, `trajectory.npz`, and a `source/` folder holding
the original CSV and available event/sync/trial sidecars. Original files are
copied, not moved or edited. Replacing an active reference retains old imports.

This directory belongs to the offline Ride Analyzer, not the runtime POC
controller. The same reference serves both platforms and all compatible motion
candidates. To transfer the library to another PC, copy each **whole ride
directory**, not just its video. Relative asset paths survive relocation under
this directory.

Git ignores imported ride directories; videos and recordings should not be
committed to the code repo.
