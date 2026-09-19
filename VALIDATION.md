# SSB 1.2.0 candidate validation — 2026-09-19

Base: public v1.0.2, commit `1fe80354ee31bf38d7e8c8a83e112a7b063e11c9`.
Build host: Windows 10 Home x64; Python 3.11; PyInstaller 6.20.0;
Inno Setup 6.7.3; WinSparkle 0.9.4.

## Passed

- 49 automated tests: schedule boundaries, Firefox list format/cleanup, quiet
  batched commands, responsive saving, opt-out persistence, configuration
  transaction recovery, concurrent-write exclusion, migration, failed-update
  recovery, updater shutdown callbacks, and queued-task behavior after uninstall.
- Production PyInstaller bundle and Inno Setup installer compile successfully.
- Packaged smoke test loads Tk, both icon resources, WinSparkle, and its public
  key, and checks temporary configuration storage and Firefox opt-out.
- Isolated Inno packaging test: fresh install, Start menu shortcut, Installed Apps
  registration, upgrade to a higher installer version, exact preservation of
  custom websites/hours/Firefox preference, executable launch without Python on
  PATH, uninstall, removal of registrations, and retention of saved configuration.
- EdDSA signature verification against the embedded public key succeeds for the
  final installer and fails after a single byte is changed. Appcast length,
  filename, and SHA-256 agree with the installer.
- Git whitespace validation.

## Boundaries of this validation

The packaging test uses a separate app ID and replaces Windows integration hooks
at compile time with a harmless packaged smoke test. Its higher installer version
uses the same application bundle. It verifies Inno's file lifecycle, not a complete
production WinSparkle upgrade or actual firewall/task changes.

The existing local experimental SSB installation was not altered or used as source.
Windows Sandbox is unavailable on this Windows 10 Home host. Full elevated
installation, actual public-version migration, update download/install handoff,
Firefox VPN behavior in this new package, and uninstall policy restoration still
need a disposable Windows 10/11 integration test. The earlier v1.0.2 Firefox VPN
result is retained as historical evidence, not a new test of this package.

The native desktop screenshot tool failed with an unsupported capture-interface
error. Icon artwork was inspected and Tk resource loading was verified, but a
full visual window review remains outstanding.

The candidate is update-signed with EdDSA, not Authenticode publisher-signed.
No stable release/feed item should be published until the remaining integration
checks in BUILDING.md pass. The repository feed is intentionally empty meanwhile.
