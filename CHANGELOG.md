# Changelog

## 0.2.0-preview.2

This private preview makes manual report recovery reliable on packaged macOS
and Windows builds.

### Added

- PowerShell runtime manager with version, source, architecture, and executable
  details.
- Detection outside the GUI application's restricted `PATH`, including
  Homebrew, Microsoft, Linux, and Windows installation locations.
- Persistent selection between detected system and managed portable PowerShell
  runtimes.
- Portable runtime import, removal, download shortcut, and rescan controls.
- Missing-report indicators plus one-click selection and retry of failed
  report runs.

### Improved

- Every included report now writes manual recovery snapshots into the active
  local, OneDrive, or SharePoint-synchronized history folder.
- Report generation remains open after a run so output and failures can be
  reviewed before retrying.

### Packaging notes

- macOS and Windows packages remain preview builds without production signing
  or notarization.

## 0.2.0-preview.1

This private preview focuses on long-history performance and the first Windows
package.

### Added

- Portable Windows x64 build with a native multi-resolution icon.
- Timeline ranges for 30 days, 90 days, one year, two years, and all history.
- Automatic daily, weekly, and monthly aggregation for long timelines.
- Progressive chart updates during snapshot analysis.
- Schema-change detection across report history.
- Scheduled-report health dashboard.

### Improved

- Incremental SQLite analysis cache with automatic migration from the previous
  JSON cache.
- Cache reuse when a synchronized report folder is relocated.
- Responsive window and chart sizing across compact displays.
- Movement-chart spacing so its heading never overlaps timeline dates.
- Background indexing and parsing for large OneDrive and SharePoint libraries.

### Packaging notes

- macOS and Windows packages are preview builds without production signing or
  notarization.
- Windows application data is stored under `%LOCALAPPDATA%\Diffasaurus`.
- macOS application data is stored under
  `~/Library/Application Support/Diffasaurus`.
