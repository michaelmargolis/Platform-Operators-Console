<!-- README.md ver 1 -->
# Maintenance and diagnostic tools

This directory contains standalone utilities that are useful for commissioning
or diagnosis but are not imported by the POC runtime.

- `diagnostics/`: Raspberry Pi, SHS and SpaceCoaster test utilities.
- `spacecoaster_patch/`: source and compiled DLL for the patched SpaceCoaster
  telemetry/control integration.

The W5100S Festo emulator remains under `output/fstlib/firmware/` because it is
maintained alongside the EasyIP implementation it emulates.
