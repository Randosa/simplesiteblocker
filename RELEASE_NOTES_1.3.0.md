# SSB v1.3.0 — Scheduled Internet cutoff

## Features

- **Daily Internet cutoff:** Turn on a separate schedule to block outbound traffic to Internet addresses, such as 22:30–06:00. It is off by default.
- **Automatic restoration:** SSB applies and removes the firewall rule at the chosen times. Temporary unlocks suspend it as well.
- **Preserved settings:** Updating keeps thy website list, blocking hours, and Firefox preference. Local network destinations remain outside the Internet cutoff rule.

Update through **Check for Updates**, or download **SSB-Setup-1.3.0.exe** below. Windows administrator approval is required. Firefox may need a full restart when website rules change. VPNs and proxies may affect which destinations Windows classifies as Internet addresses.

## Validation

Automated tests cover the new schedule, firewall rule command, and existing configuration behavior. A live Windows firewall and signed installer update test has not yet been completed for this version.
