# Building and releasing SSB

Build on Windows x64 with Python 3.11. The source is based on public v1.0.2,
not the locally installed experimental v1.1.0 variant.

## Build

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r build-requirements.txt
.venv\Scripts\python tools/bootstrap.py
.venv\Scripts\python -m unittest discover -s tests -v
.venv\Scripts\python tools/build.py --winsparkle .tools/winsparkle/WinSparkle-0.9.4 --iscc .tools/InnoSetup/ISCC.exe
.venv\Scripts\python tools/test_installer.py --iscc .tools/InnoSetup/ISCC.exe
```

Bootstrap verifies pinned SHA-256 hashes before extracting WinSparkle 0.9.4 and
installing Inno Setup 6.7.3 in `.tools`. The build compiles the supplied PNGs into
Windows icon sizes, bundles the runtime and notices, and produces
`release/SSB-Setup-1.2.0.exe`. Generated files are ignored by Git.

`tools/test_installer.py` compiles the same Inno script with a separate test app
ID and no production integration hooks. It checks installation, Start menu and
Installed Apps registration, replacement by a higher installer version, byte-for-
byte configuration preservation, the frozen runtime without Python on PATH,
uninstallation, and retained configuration. It does not change real browser
policies, firewall rules, or SSB tasks. It is not a substitute for a Windows VM
integration test. Never distribute a PackagingTest build.

GitHub Actions runs the unit and packaging tests and uploads an unsigned candidate.
It does not automatically publish releases or receive the private signing key.

## Update signing

The application embeds `updates/public-key.txt`. Keep the matching WinSparkle
private key outside this repository and backed up securely. Losing it prevents
existing installations from trusting future updates. Do not regenerate a key for
each release. WinSparkle owns the key-file format; use its companion signing tool.

After completing validation, sign the final, unmodified installer:

```powershell
python tools/sign_release.py --key C:\secure\ssb-private.key --tool .tools/winsparkle/WinSparkle-0.9.4/bin/winsparkle-tool.exe --installer release/SSB-Setup-1.2.0.exe --version 1.2.0 --output release/appcast.xml
```

The script checks the signature against the embedded public key and writes a
SHA-256 file and appcast. Do not modify the executable after signing. If using
an Authenticode certificate, apply Authenticode before EdDSA signing.

## Before publishing

Validate on a disposable Windows 10/11 VM with Defender available:

1. Fresh install in Program Files, administrator prompt, Start search, and UI.
2. Public v1.0.2 migration preserving the list, schedule, and prior policy values.
3. A scheduled start/end, overnight hours, and an exception expiring.
4. Firefox sync on/off, browser restart, and built-in VPN blocking.
5. A real WinSparkle download/verify/install handoff from an older build to the
   new installer; interrupted download and a modified installer must not install.
6. Save during an update, installer failure/repair, and uninstall keep/remove.
7. Confirm unrelated browser policies and the prior Defender setting survive.

Publish the matching version tag and installer asset first. Download the asset
back and compare its SHA-256, then replace `updates/appcast.xml` on `main` with
the generated feed. Publishing the feed last avoids advertising a missing asset.
Keep release notes explicit about Firefox's restart requirement and the validation
actually performed. Do not label a candidate fully tested based only on unit tests.

Publish the signed feed only after the matching release asset is available.
Future versions must increase both
`ssb/core.py:APP_VERSION` and the installer/feed version.
