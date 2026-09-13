# SSB — Simple Site Blocker

SSB (Simple Site Blocker) is a simple, locally run website blocker for Windows. Choose the websites and daily hours in a small graphical window; SSB then uses Windows' own scheduling, firewall, and browser-policy facilities to enforce them.

SSB has no account, cloud service, analytics, advertisements, or third-party Python packages. Its configuration and logs remain on the computer.

## Requirements

- Windows 10 or Windows 11
- Python 3.10 or newer from [python.org](https://www.python.org/downloads/windows/)
- A Windows account able to approve administrator prompts
- Microsoft Defender Antivirus and Network Protection for the firewall FQDN layer

## Install

1. Download [`site_blocker.py`](site_blocker.py).
2. Double-click it.
3. Approve the Windows administrator prompt.
4. Add each website thou wishest to block.
5. For each website, choose whether all its subdomains shall also be included.
6. Select the daily start and end times. Periods crossing midnight are supported.
7. Review the exact configuration and select **Install SSB**.

No separate installer or package manager is required. The program copies itself to `C:\ProgramData\CodexSiteBlocker` and registers a visible Windows scheduled task that runs briefly every five minutes, at startup and sign-in, and at the configured boundaries.

## Edit the configuration

Double-click `site_blocker.py` again and approve the administrator prompt. The graphical manager loads the installed configuration and permits thee to:

- Add, update, or remove websites.
- Include or exclude all subdomains for each site.
- Change the daily blocking hours.
- Repair the installed rules and scheduled task by saving again.

If a change is saved while the current time falls within either the existing or proposed blocked period, SSB displays an additional warning and requires a second confirmation. This makes an impulsive blocked-period edit more deliberate without falsely claiming that local administrator software is impossible to bypass.

## Temporary access

The graphical manager intentionally has no Unlock button. An administrator may grant a temporary exception from a terminal:

```powershell
python "C:\ProgramData\CodexSiteBlocker\site_blocker.py" unlock --minutes 30 --reason "Watch a particular lecture"
```

The exception expires automatically, cannot extend beyond the scheduled end time, and records its stated reason locally.

On the computer for which SSB was first prepared, the user may instead ask in the original Codex task for this temporary command to be invoked.

## Remove SSB

1. Double-click `site_blocker.py`.
2. Approve the administrator prompt.
3. Select **Uninstall SSB**.
4. Confirm removal.

SSB removes its scheduled task, firewall rules, browser-policy entries, configuration, log, and installed copy. It also restores the Microsoft Defender Network Protection setting it found before installation.

Instructions are also embedded in the program. Open **Instructions** in the graphical manager or run:

```powershell
python site_blocker.py instructions
```

## How blocking works

SSB combines two local mechanisms:

1. Windows Firewall dynamic FQDN rules provide the system-wide layer and support wildcard hostnames such as `*.example.com`.
2. URL-blocking policies for Chrome, Edge, and Firefox provide a second browser layer.

SSB does **not** disable encrypted DNS and does not hide itself from Task Manager or Windows administrative tools.

## Limitations

This is deliberate friction, not parental-control or endpoint-security software. A Windows administrator can change or remove it. VPNs, proxies, nonstandard browsers, cached addresses, and applications using independent encrypted DNS may evade Windows FQDN filtering. Firefox may require a restart before a newly changed enterprise URL policy is observed.

Windows FQDN rules resolve names to destination IP addresses. A service that shares an IP address with a blocked domain may therefore be affected. Review the domain list before installation.

## Privacy and security

- No network requests are made by SSB itself.
- No browsing history is collected.
- Temporary-access reasons remain in the local log.
- The installed directory is restricted to Administrators and SYSTEM.
- The scheduled process is visible and plainly named **Simple Site Blocker**.
- Existing numbered browser-policy values are preserved rather than overwritten.

## Development

Run the standard-library test suite:

```powershell
python -m unittest discover -s tests -v
```

SSB is distributed under the MIT License.
