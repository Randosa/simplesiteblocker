# Changelog

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

