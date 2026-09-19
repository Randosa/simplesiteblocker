# Changelog

## 1.2.0 — installer revision (release candidate)

Based on the public v1.0.2 tag.

- Add an Inno Setup installer for Program Files, Start menu/search, and Installed Apps.
- Bundle Python so users no longer need to install it separately.
- Store websites in config/list.json and preferences in config/settings.json;
  preserve both across upgrades with atomic saves and recovery records.
- Add manual WinSparkle updates with signature verification and installer handoff.
- Add the supplied transparent header logo and rounded Windows application icon.
- Remove the window subtitle and omit the experimental allow-only/LAN feature.
- Add a saved Sync changes to Firefox checkbox and explain the restart requirement.
- Retain v1.0.2 Firefox numbering/empty-key fixes and v1.0.1 quiet saving/progress.
- Separate the UI, settings, updater, migration, and Windows integration modules.
- Add build automation, packaging lifecycle tests, and release-signing tooling.


## 1.0.2 — 2026-09-14

SSB v1.0.2 fixes Firefox website-policy numbering so Firefox accepts and enforces the blocked website list.

### Changes

- Firefox WebsiteFilter entries now begin at **1**, instead of **9000**. The previous numbering caused Firefox to reject Block as an object rather than the required array, leaving the browser policy inactive.
- Empty Firefox Block/WebsiteFilter registry keys are removed when blocking is disabled, preventing the same schema error outside blocking hours.
- Existing policy values are preserved. An unrelated malformed Firefox list produces a clear error instead of being overwritten. Chrome and Edge retain their existing numbering.
- The quiet helper commands, responsive saving, and progress bar introduced in v1.0.1 are retained.

### Firefox and VPNs

The repaired WebsiteFilter is enforced inside Firefox rather than relying solely on DNS or Windows destination-address filtering. After applying the repair and restarting Firefox, the user confirmed that a blocked website **remained blocked with Firefox's built-in VPN enabled during blocking hours**. This validates that use case on the tested computer; it does not claim universal coverage for every VPN or proxy. No Firefox extension is installed or required by this fix.

### Validation

All **33 automated tests** passed on Windows with Python 3.11, including five new Firefox policy tests. The equivalent repair was also applied locally and the user confirmed that WebsiteFilter appeared under Active after restarting Firefox. Existing SSB configuration and DNS settings were preserved during that local repair.


## 1.0.1 — 2026-09-14

SSB v1.0.1 makes saving website blocks quiet and keeps the window responsive while changes are applied.

## Changes

- PowerShell and other helper commands run without opening console windows. The normal Windows administrator approval prompt remains when required.
- Website and subdomain rules are processed in batches, replacing the repeated PowerShell launches for individual entries.
- A small animated progress bar shows the current save stage: preparing changes, updating website blocks, updating the schedule, applying the current blocking state, and finishing. It indicates activity rather than an estimated percentage.
- Saving runs in the background. Editing, duplicate saves, and closing are prevented until the save finishes; completion and errors appear inside SSB.
- Saving from the installed copy no longer tries to copy the program onto itself.
- Cleanup during an update is covered by the existing recovery path, and large website batches use temporary script files to avoid Windows command-line length limits.

## Validation

All 28 tests passed on Windows with Python 3.11, covering schedule boundaries, website patterns, quiet command execution, batches of 100 websites, responsive saving, duplicate-save prevention, error handling, and installation recovery. The installed update was also checked against the existing configuration, firewall state, and browser policies during an unblocked period.

