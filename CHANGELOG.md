# Changelog

## Unreleased

### Added

- Separate background-scanned counts for Diffasaurus-isolated modules and
  modules installed in normal PowerShell user or machine locations.
- An installed-module inventory with explicit, background copying of one or
  all installed module versions into a runtime's independent environment.

### Fixed

- The runtime manager no longer reports `0` in a misleading “Private modules”
  column when a system PowerShell has native modules installed. The interface
  now clearly distinguishes **Isolated** from **Installed** modules.

## 0.2.0-preview.3

This private preview turns every detected PowerShell version into an
independent, testable environment.

### Added

- A private module directory and module-analysis cache for each PowerShell
  executable, version, and architecture.
- Per-runtime module inventory, arbitrary module/version installation, removal,
  folder access, and a one-click report-module installation preset.
- A persistent embedded PowerShell console that runs the exact selected
  executable with its isolated module environment.
- Private-module counts in the runtime manager and report generator.

### Fixed

- Portable runtime imports, removals, rescans, and version probes now run in
  background workers instead of freezing the Qt interface.
- Portable import selects the actual `pwsh` or `pwsh.exe` executable, removing
  ambiguity around nested extracted folders.
- Report generation no longer sees modules installed for another PowerShell
  version or in the normal user/system module locations.
- Runtime architecture now reports the PowerShell process architecture rather
  than the operating-system architecture.

### Migration note

- Existing user-wide modules are intentionally not copied. Install the desired
  report modules once in **Modules & console** for each runtime that should run
  Diffasaurus reports.

### Packaging notes

- macOS and Windows packages remain preview builds without production signing
  or notarization.

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
