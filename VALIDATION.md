# SSB validation

## Version 1.2.1 — 2026-09-19

- All 57 automated tests passed, including status colors, hover label visibility,
  click refresh, automatic refresh, and skipping reads during a save.
- The signed 1.2.1 installer upgraded the real administrator-level installation
  successfully, preserving both the website list and settings.
- The installed 1.2.1 application passed its packaged runtime smoke test and was
  opened after installation. Its status light uses the last applied state.
- Signature verification passed; a one-byte modification was rejected.
- The blocking engine and Windows integration are unchanged except for version
  metadata; the administrator-level lifecycle coverage below still applies.

## Version 1.2.0 — 2026-09-19

Base: public v1.0.2, commit `1fe80354ee31bf38d7e8c8a83e112a7b063e11c9`.
Build/test host: Windows 10 Home x64; Python 3.11; PyInstaller 6.20.0;
Inno Setup 6.7.3; WinSparkle 0.9.4.

## Automated and packaging checks

- All 49 automated tests pass: schedule boundaries, Firefox list format/cleanup,
  quiet batched commands, responsive saving, opt-out persistence, configuration
  recovery, concurrent writes, migration, failure recovery, and updater callbacks.
- Production executable and installer compile successfully.
- Packaged smoke test loads Tk, icons, WinSparkle, and the public verification key.
- Isolated packaging tests pass install, Start menu/Installed Apps registration,
  upgrade, configuration preservation, runtime without Python on PATH, uninstall,
  and retained configuration.
- GitHub Windows CI independently passed unit, build, and packaging tests:
  https://github.com/Randosa/simplesiteblocker/actions/runs/35461620296
- The final installer's EdDSA signature verifies; changing one byte causes rejection.
  Appcast length, filename, and SHA-256 agree with the installer.

## Real administrator-level test

Performed on Windows 10 Home with no previous SSB installation, SSB firewall
rules, or SSB scheduled task. The targeted browser block lists were initially
empty; Defender Network Protection was initially Disabled.

Passed:

1. Fresh production installation into Program Files, Start menu shortcut,
   Installed Apps registration, and packaged runtime smoke test.
2. Actual SYSTEM task points at SSB.exe and completes with result zero.
3. An all-day example.com test creates the real firewall rule and Firefox entries.
4. Firefox opt-out removes its entries while retaining firewall blocking.
5. Changing to hours outside the present time clears actual firewall/browser blocks.
6. WinSparkle downloads the signed production installer, verifies it, launches it,
   invokes the shutdown callback, and completes installation successfully.
7. That upgrade preserves websites, schedule, and Firefox preference.
8. Production uninstall removes executable, shortcut, task, and blocking rules;
   restores browser values and the original Defender setting; retains configuration.
9. Final production reinstall succeeds with the original default list and hours.

The updater test used a loopback HTTP feed and a small host reporting version
1.1.9, loading the installed WinSparkle DLL and public key. It downloaded the exact
signed production 1.2.0 installer. This tests real download/verification/installer
handoff, not an older released SSB application's user interface. The production
feed and release assets use GitHub HTTPS. The test did not weaken signature checks.
The script is provided as `tools/test_admin_install.py`; use only on a clean test
machine, elevated, with the signed installer and generated release/appcast.xml.

## Remaining coverage limits

Public v1.0.x migration and failure recovery have automated coverage, but a live
old-version migration was not performed in the administrator test. Firefox VPN
blocking was confirmed by the user for v1.0.2; that engine is retained, but a new
browser/VPN session was not independently exercised in this test. Firefox must be
fully restarted to refresh policy changes, including the end of blocking hours.

Native screenshot capture failed with an unsupported interface error. Icons were
inspected and Tk resource loading was verified; full visual window review remains
outstanding. The installer is EdDSA update-signed, not Authenticode publisher-signed.
