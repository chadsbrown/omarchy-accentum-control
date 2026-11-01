# Changelog

## 0.1.2

- Move plugin and firmware metadata checks out of the shared hardware worker.
  Device controls and passive connection maintenance remain available while
  a check runs; only update-check buttons show the pending state.
- Enforce a ten-second wall-clock deadline for each whole check with a separate
  process and an OS alarm, plus a controller-side kill fallback. Bound results,
  cancel and reap checks on shutdown, and allow retries after timeout.
- Read installed firmware versions from the current snapshot for catalog checks,
  avoiding extra device traffic just to start a network request.
- Add regressions for slowly continuing HTTP responses, stalled workers, process
  cleanup and controls remaining available from two monitors.

## 0.1.1

- Stop recurring bulk HID/GAIA status queries during playback. A listening
  comparison was clean with polling paused and crackled when polling resumed;
  the fixed controller has remained clean in subsequent listening so far.
- Keep cached settings between explicit reads. Refresh on opening, requested
  refresh, control changes and new device connections. Passive USB enumeration
  and socket checks retain unplug detection and reconnect behavior.
- Keep local preset operations and plugin update checks free of additional
  device status queries.
- Add an on-demand plugin update check in Updates, using public stable GitHub
  Releases and the local manifest version. Show version comparisons, failure
  states, release notes and installation-specific update guidance. Mark Updates
  with a dot when a newer release is found.

Plugin updates are manual. Firmware installation remains unsupported. Hardware
reads and mocked tests do not establish firmware flashing or recovery safety.
All existing license, no-warranty and liability terms continue to apply.
