#!/usr/bin/env python3
"""SSB (Simple Site Blocker): a simple, locally run website blocker for Windows.

Double-click this file to install, configure, repair, or remove SSB through its
graphical interface.  The same file is copied into ProgramData at install time
and invoked briefly by Windows Task Scheduler to enforce the chosen schedule.

Command-line maintenance:
  python site_blocker.py                 Open the graphical manager (UAC required)
  python site_blocker.py enforce         Reconcile the present blocking state
  python site_blocker.py status          Print status as JSON
  python site_blocker.py unlock --minutes 30 --reason "Specific purpose"
  python site_blocker.py uninstall       Remove SSB and restore changed settings

SSB is visible software.  It does not hide its scheduled task or Python process,
and a Windows administrator can alter or remove it.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import uuid
import xml.etree.ElementTree as ET


APP_NAME = "SSB (Simple Site Blocker)"
APP_VERSION = "1.0.0"
TASK_NAME = r"\Simple Site Blocker\Reconcile"
LEGACY_TASK_NAME = r"\Scheduled Site Blocker\Reconcile"
FIREWALL_GROUP = "Simple Site Blocker"
LEGACY_FIREWALL_GROUP = "Scheduled Site Blocker"
DEFAULT_INSTALL_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "CodexSiteBlocker"
INSTALL_DIR = Path(os.environ.get("SSB_HOME", str(DEFAULT_INSTALL_DIR)))
INSTALLED_SCRIPT = INSTALL_DIR / "site_blocker.py"
CONFIG_PATH = INSTALL_DIR / "config.json"
STATE_PATH = INSTALL_DIR / "state.json"
INSTALL_STATE_PATH = INSTALL_DIR / "install-state.json"
LOG_PATH = INSTALL_DIR / "ssb.log"
POWERSHELL = os.environ.get("SystemRoot", r"C:\Windows") + r"\System32\WindowsPowerShell\v1.0\powershell.exe"

DEFAULT_CONFIG = {
    "version": 1,
    "block_start": "21:00",
    "block_end": "03:00",
    "default_unlock_minutes": 30,
    "maximum_unlock_minutes": 120,
    "sites": [
        {"hostname": "youtube.com", "include_subdomains": True},
        {"hostname": "youtu.be", "include_subdomains": True},
        {"hostname": "youtube-nocookie.com", "include_subdomains": True},
        {"hostname": "ytimg.com", "include_subdomains": True},
        {"hostname": "googlevideo.com", "include_subdomains": True},
        {"hostname": "instagram.com", "include_subdomains": True},
        {"hostname": "cdninstagram.com", "include_subdomains": True},
    ],
}

LEGACY_DEFAULT_DOMAINS = [
    "youtube.com", "*.youtube.com", "youtu.be", "*.youtu.be",
    "youtube-nocookie.com", "*.youtube-nocookie.com", "ytimg.com",
    "*.ytimg.com", "googlevideo.com", "*.googlevideo.com",
    "instagram.com", "*.instagram.com", "cdninstagram.com",
    "*.cdninstagram.com",
]

HELP_TEXT = """INSTALLATION

1. Install Python 3.10 or newer for Windows from python.org. During setup, enable
   “Add Python to PATH.”
2. Download site_blocker.py.
3. Double-click site_blocker.py and approve the Windows administrator prompt.
4. Add the websites thou wishest to govern. For each site, choose whether all
   subdomains shall also be blocked.
5. Choose the daily start and end times, review the summary, and select Install.

If SSB is already installed, double-click the same file to edit its settings.
When a change is saved during the configured blocked period, SSB presents an
additional confirmation window before applying it.

TEMPORARY ACCESS

The graphical manager intentionally hath no Unlock button. On this computer,
ask in the Codex task that installed SSB for a temporary exception, stating a
duration and purpose. An administrator may also use:

  python "C:\\ProgramData\\CodexSiteBlocker\\site_blocker.py" unlock \
      --minutes 30 --reason "Specific purpose"

The exception expires automatically and cannot extend beyond the scheduled end.

REMOVAL

Double-click site_blocker.py, approve the administrator prompt, and choose
Uninstall SSB. Confirm the removal. SSB removes its scheduled task, firewall
rules, browser-policy entries, configuration, log, and installed script, and
restores the Defender Network Protection setting it found before installation.

LIMITS

SSB runs locally and sends no browsing information anywhere. It adds Windows
Firewall FQDN rules plus URL-blocking entries for Chrome, Edge, and Firefox.
VPNs, proxies, unusual browsers, cached addresses, or applications with their
own encrypted DNS may evade Windows FQDN filtering. A Windows administrator can
always alter or remove locally installed software.
"""


def configure_logging() -> None:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def require_windows_admin() -> None:
    if os.name != "nt":
        raise RuntimeError("SSB supports Windows only.")
    if not is_admin():
        raise PermissionError("Windows administrator approval is required.")


def parse_clock(value: str) -> dt.time:
    try:
        return dt.datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"Invalid time {value!r}; use 24-hour HH:MM format.") from exc


def in_block_window(now: dt.datetime, start: dt.time, end: dt.time) -> bool:
    current = now.time().replace(second=0, microsecond=0)
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def next_block_end(now: dt.datetime, start: dt.time, end: dt.time) -> dt.datetime:
    candidate = dt.datetime.combine(now.date(), end, tzinfo=now.tzinfo)
    if start > end and now.time() >= start:
        candidate += dt.timedelta(days=1)
    elif candidate <= now:
        candidate += dt.timedelta(days=1)
    return candidate


def requires_blocked_period_confirmation(
    now: dt.datetime, old_config: dict, new_config: dict, changed: bool
) -> bool:
    if not changed:
        return False
    old_active = in_block_window(
        now,
        parse_clock(old_config["block_start"]),
        parse_clock(old_config["block_end"]),
    )
    new_active = in_block_window(
        now,
        parse_clock(new_config["block_start"]),
        parse_clock(new_config["block_end"]),
    )
    return old_active or new_active


def normalize_hostname(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("Enter a website hostname or URL.")
    parsed = urlsplit(text if "://" in text else "//" + text)
    if parsed.username or parsed.password:
        raise ValueError("Usernames and passwords are not valid website entries.")
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if hostname.startswith("*."):
        hostname = hostname[2:]
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("This international hostname is not valid.") from exc
    if not hostname or len(hostname) > 253:
        raise ValueError("The hostname is missing or too long.")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("Enter a hostname, not an IP address.")
    if "." not in hostname or hostname == "localhost":
        raise ValueError("Enter a public hostname such as example.com.")
    label_pattern = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
    if any(not label_pattern.fullmatch(label) for label in hostname.split(".")):
        raise ValueError("The hostname contains an invalid label.")
    return hostname


def normalized_sites(sites: list[dict]) -> list[dict]:
    combined: dict[str, bool] = {}
    for site in sites:
        hostname = normalize_hostname(str(site.get("hostname", "")))
        combined[hostname] = combined.get(hostname, False) or bool(site.get("include_subdomains", False))
    if not combined:
        raise ValueError("Add at least one website.")
    return [
        {"hostname": hostname, "include_subdomains": combined[hostname]}
        for hostname in sorted(combined)
    ]


def domain_patterns(config: dict) -> list[str]:
    patterns: list[str] = []
    for site in normalized_sites(config["sites"]):
        hostname = site["hostname"]
        patterns.append(hostname)
        if site["include_subdomains"]:
            patterns.append("*." + hostname)
    return patterns


def browser_policy_values(config: dict) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    sites = normalized_sites(config["sites"])
    chromium_paths = (
        r"SOFTWARE\Policies\Google\Chrome\URLBlocklist",
        r"SOFTWARE\Policies\Microsoft\Edge\URLBlocklist",
    )
    for path in chromium_paths:
        for site in sites:
            prefix = "[*.]" if site["include_subdomains"] else ""
            values.append((path, prefix + site["hostname"]))
    firefox_path = r"SOFTWARE\Policies\Mozilla\Firefox\WebsiteFilter\Block"
    for site in sites:
        values.append((firefox_path, f"*://{site['hostname']}/*"))
        if site["include_subdomains"]:
            values.append((firefox_path, f"*://*.{site['hostname']}/*"))
    return values


def migrate_config(raw: dict | None) -> dict:
    if not raw:
        return json.loads(json.dumps(DEFAULT_CONFIG))
    if "sites" in raw:
        result = dict(raw)
        result["sites"] = normalized_sites(raw["sites"])
    elif "domains" in raw:
        flags: dict[str, bool] = {}
        for domain in raw["domains"]:
            value = str(domain).strip().lower()
            wildcard = value.startswith("*.")
            hostname = normalize_hostname(value[2:] if wildcard else value)
            flags[hostname] = flags.get(hostname, False) or wildcard
        result = {
            "version": 1,
            "block_start": raw.get("block_start", "21:00"),
            "block_end": raw.get("block_end", "03:00"),
            "default_unlock_minutes": raw.get("default_unlock_minutes", 30),
            "maximum_unlock_minutes": raw.get("maximum_unlock_minutes", 120),
            "sites": [
                {"hostname": host, "include_subdomains": flags[host]}
                for host in sorted(flags)
            ],
        }
    else:
        raise ValueError("The installed configuration format is not recognized.")
    result["version"] = 1
    parse_clock(str(result.get("block_start", "")))
    parse_clock(str(result.get("block_end", "")))
    if int(result.get("maximum_unlock_minutes", 120)) < 1:
        raise ValueError("Maximum unlock duration must be positive.")
    return result


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False, suffix=".tmp"
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def load_config() -> dict:
    return migrate_config(load_json(CONFIG_PATH, None))


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"Command failed with exit code {result.returncode}.")
    return result


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def powershell(script: str, *, check: bool = True) -> subprocess.CompletedProcess:
    return run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=check,
    )


def keyword_id(domain: str) -> str:
    return "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, "scheduled-site-blocker:" + domain)) + "}"


def remove_firewall_rules_only() -> None:
    for group_name in (FIREWALL_GROUP, LEGACY_FIREWALL_GROUP):
        group = ps_quote(group_name)
        powershell(
            f"Get-NetFirewallRule -Group {group} -ErrorAction SilentlyContinue | "
            "Remove-NetFirewallRule -ErrorAction SilentlyContinue",
            check=False,
        )


def remove_dynamic_keywords(domains: list[str]) -> None:
    for domain in domains:
        powershell(
            f"Remove-NetFirewallDynamicKeywordAddress -Id {ps_quote(keyword_id(domain))} "
            "-ErrorAction SilentlyContinue",
            check=False,
        )


def create_firewall_rules(config: dict) -> None:
    for domain in domain_patterns(config):
        ident = keyword_id(domain)
        display = f"SSB: {domain}"
        script = (
            f"$id = {ps_quote(ident)}; $domain = {ps_quote(domain)}; "
            "$item = Get-NetFirewallDynamicKeywordAddress -Id $id -ErrorAction SilentlyContinue; "
            "if (-not $item) { New-NetFirewallDynamicKeywordAddress -Id $id -Keyword $domain "
            "-AutoResolve $true -ErrorAction Stop | Out-Null }; "
            f"$rule = Get-NetFirewallRule -DisplayName {ps_quote(display)} -ErrorAction SilentlyContinue; "
            "if (-not $rule) { "
            f"New-NetFirewallRule -DisplayName {ps_quote(display)} -Group {ps_quote(FIREWALL_GROUP)} "
            "-Action Block -Direction Outbound -RemoteDynamicKeywordAddresses $id "
            "-Enabled True -ErrorAction Stop | Out-Null }"
        )
        powershell(script)


def firewall_rule_count() -> int | None:
    result = powershell(
        f"@(Get-NetFirewallRule -Group {ps_quote(FIREWALL_GROUP)} -ErrorAction SilentlyContinue).Count",
        check=False,
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def registry_get(root, path: str, name: str) -> dict:
    import winreg

    try:
        with winreg.OpenKey(root, path) as key:
            value, kind = winreg.QueryValueEx(key, name)
            return {"exists": True, "value": value, "kind": kind}
    except FileNotFoundError:
        return {"exists": False}


def registry_set(root, path: str, name: str, value, kind: int) -> None:
    import winreg

    with winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, kind, value)


def registry_restore(root, path: str, name: str, previous: dict) -> None:
    import winreg

    if previous.get("exists"):
        registry_set(root, path, name, previous["value"], int(previous["kind"]))
        return
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


def prepare_browser_policy_entries(config: dict) -> list[dict]:
    import winreg

    used_by_path: dict[str, set[str]] = {}
    entries: list[dict] = []
    for path, value in browser_policy_values(config):
        if path not in used_by_path:
            names: set[str] = set()
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                    index = 0
                    while True:
                        try:
                            names.add(winreg.EnumValue(key, index)[0])
                            index += 1
                        except OSError:
                            break
            except FileNotFoundError:
                pass
            used_by_path[path] = names
        number = 9000
        while str(number) in used_by_path[path]:
            number += 1
        name = str(number)
        used_by_path[path].add(name)
        entries.append({
            "path": path,
            "name": name,
            "value": value,
            "previous": registry_get(winreg.HKEY_LOCAL_MACHINE, path, name),
        })
    return entries


def set_browser_policy_entries(enabled: bool, entries: list[dict] | None = None) -> None:
    import winreg

    if entries is None:
        entries = load_json(INSTALL_STATE_PATH, {}).get("browser_block_policies", [])
    for item in entries:
        if enabled:
            registry_set(winreg.HKEY_LOCAL_MACHINE, item["path"], item["name"], item["value"], winreg.REG_SZ)
        else:
            registry_restore(winreg.HKEY_LOCAL_MACHINE, item["path"], item["name"], item["previous"])


def defender_network_protection() -> int | None:
    result = powershell("(Get-MpPreference -ErrorAction Stop).EnableNetworkProtection", check=False)
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def set_defender_network_protection(mode: str) -> None:
    powershell(f"Set-MpPreference -EnableNetworkProtection {mode} -ErrorAction Stop")


def restore_defender_network_protection(previous: int | None) -> None:
    modes = {0: "Disabled", 1: "Enabled", 2: "AuditMode"}
    if previous in modes:
        set_defender_network_protection(modes[previous])


def set_blocking_enabled(enabled: bool, config: dict) -> None:
    if enabled:
        set_browser_policy_entries(True)
        create_firewall_rules(config)
    else:
        remove_firewall_rules_only()
        set_browser_policy_entries(False)


def active_exception(now: dt.datetime, state: dict) -> dt.datetime | None:
    raw = state.get("unlock_until")
    if not raw:
        return None
    try:
        expiry = dt.datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=dt.timezone.utc)
    return expiry if expiry > now.astimezone(dt.timezone.utc) else None


def enforce(now: dt.datetime | None = None) -> dict:
    require_windows_admin()
    config = load_config()
    state = load_json(STATE_PATH, {}) or {}
    now = now or dt.datetime.now().astimezone()
    start = parse_clock(config["block_start"])
    end = parse_clock(config["block_end"])
    scheduled = in_block_window(now, start, end)
    exception_until = active_exception(now, state)
    blocked = scheduled and exception_until is None
    set_blocking_enabled(blocked, config)
    if not exception_until:
        state.pop("unlock_until", None)
        state.pop("unlock_reason", None)
    state.update({
        "last_enforced": now.astimezone(dt.timezone.utc).isoformat(),
        "blocked": blocked,
    })
    save_json(STATE_PATH, state)
    logging.info("Enforced blocked=%s scheduled=%s exception_until=%s", blocked, scheduled, exception_until)
    return {"blocked": blocked, "scheduled": scheduled, "exception_until": exception_until}


def unlock(minutes: int, reason: str) -> dt.datetime:
    require_windows_admin()
    config = load_config()
    maximum = int(config.get("maximum_unlock_minutes", 120))
    if minutes < 1 or minutes > maximum:
        raise ValueError(f"Unlock duration must be between 1 and {maximum} minutes.")
    now = dt.datetime.now().astimezone()
    start = parse_clock(config["block_start"])
    end = parse_clock(config["block_end"])
    if not in_block_window(now, start, end):
        set_blocking_enabled(False, config)
        logging.info("Unlock requested outside blocking hours. Reason=%s", reason)
        return now
    expiry = min(now + dt.timedelta(minutes=minutes), next_block_end(now, start, end))
    state = load_json(STATE_PATH, {}) or {}
    state.update({
        "unlock_until": expiry.astimezone(dt.timezone.utc).isoformat(),
        "unlock_reason": reason.strip() or "Temporary exception",
        "last_enforced": now.astimezone(dt.timezone.utc).isoformat(),
        "blocked": False,
    })
    save_json(STATE_PATH, state)
    set_blocking_enabled(False, config)
    logging.warning("Temporary unlock until %s. Reason=%s", expiry.isoformat(), state["unlock_reason"])
    return expiry


def task_xml(pythonw: Path, config: dict) -> str:
    def xml_escape(value: str) -> str:
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    executable = xml_escape(str(pythonw))
    script = xml_escape(str(INSTALLED_SCRIPT))
    start = parse_clock(config["block_start"]).strftime("%H:%M")
    end = parse_clock(config["block_end"]).strftime("%H:%M")
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Reconciles the locally configured Simple Site Blocker schedule.</Description></RegistrationInfo>
  <Triggers>
    <BootTrigger><Enabled>true</Enabled><Delay>PT30S</Delay></BootTrigger>
    <LogonTrigger><Enabled>true</Enabled><Delay>PT30S</Delay></LogonTrigger>
    <CalendarTrigger><StartBoundary>2020-01-01T00:00:00</StartBoundary><Enabled>true</Enabled><Repetition><Interval>PT5M</Interval><Duration>P1D</Duration><StopAtDurationEnd>false</StopAtDurationEnd></Repetition><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>
    <CalendarTrigger><StartBoundary>2020-01-01T{start}:00</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>
    <CalendarTrigger><StartBoundary>2020-01-01T{end}:00</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable><IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings><AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>false</Hidden><RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>false</WakeToRun><ExecutionTimeLimit>PT2M</ExecutionTimeLimit><Priority>7</Priority></Settings>
  <Actions Context="Author"><Exec><Command>{executable}</Command><Arguments>&quot;{script}&quot; enforce</Arguments></Exec></Actions>
</Task>'''


def create_task(pythonw: Path, config: dict) -> None:
    ET.fromstring(task_xml(pythonw, config))
    with tempfile.NamedTemporaryFile("w", encoding="utf-16", delete=False, suffix=".xml") as handle:
        handle.write(task_xml(pythonw, config))
        xml_path = Path(handle.name)
    try:
        run(["schtasks.exe", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"])
    finally:
        xml_path.unlink(missing_ok=True)


def delete_task(name: str) -> None:
    run(["schtasks.exe", "/Delete", "/TN", name, "/F"], check=False)


def secure_install_directory() -> None:
    run([
        "icacls.exe", str(INSTALL_DIR), "/inheritance:r", "/grant:r",
        "SYSTEM:(OI)(CI)F", "Administrators:(OI)(CI)F",
    ])


def installation_complete() -> bool:
    return bool((load_json(INSTALL_STATE_PATH, {}) or {}).get("installation_complete"))


def install_or_update(config: dict) -> None:
    require_windows_admin()
    config = migrate_config(config)
    source = Path(__file__).resolve()
    python = Path(sys.executable).resolve()
    pythonw = python.with_name("pythonw.exe")
    if not pythonw.exists():
        raise FileNotFoundError(f"pythonw.exe was not found beside {python}.")

    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    configure_logging()
    previous_config_raw = load_json(CONFIG_PATH, None)
    previous_config = migrate_config(previous_config_raw) if previous_config_raw else None
    previous_install = load_json(INSTALL_STATE_PATH, {}) or {}
    previous_state = load_json(STATE_PATH, {}) or {}
    previously_complete = bool(previous_install.get("installation_complete"))
    previous_entries = previous_install.get("browser_block_policies", [])
    previous_network = previous_install.get("previous_network_protection")
    rollback_script = INSTALL_DIR / ".site_blocker.py.rollback"
    had_installed_script = INSTALLED_SCRIPT.exists()
    if had_installed_script:
        shutil.copy2(INSTALLED_SCRIPT, rollback_script)
    if previous_network is None:
        previous_network = defender_network_protection()

    if previous_entries:
        set_browser_policy_entries(False, previous_entries)
    remove_firewall_rules_only()
    if previous_config:
        remove_dynamic_keywords(domain_patterns(previous_config))
    remove_dynamic_keywords(LEGACY_DEFAULT_DOMAINS)

    entries: list[dict] = []
    try:
        shutil.copy2(source, INSTALLED_SCRIPT)
        entries = prepare_browser_policy_entries(config)
        install_state = {
            "app_version": APP_VERSION,
            "pythonw": str(pythonw),
            "previous_network_protection": previous_network,
            "browser_block_policies": entries,
            "installation_complete": False,
        }
        save_json(CONFIG_PATH, config)
        save_json(INSTALL_STATE_PATH, install_state)
        if defender_network_protection() != 1:
            set_defender_network_protection("Enabled")
        create_task(pythonw, config)
        install_state["installation_complete"] = True
        save_json(INSTALL_STATE_PATH, install_state)
        enforce()
        secure_install_directory()
        delete_task(LEGACY_TASK_NAME)
        legacy_script = INSTALL_DIR / "blocker.py"
        if legacy_script.exists() and legacy_script.resolve() != source:
            legacy_script.unlink(missing_ok=True)
        rollback_script.unlink(missing_ok=True)
        logging.info("Installed or updated SSB %s using %s", APP_VERSION, pythonw)
    except Exception:
        logging.exception("Installation or update failed")
        delete_task(TASK_NAME)
        set_browser_policy_entries(False, entries)
        remove_firewall_rules_only()
        remove_dynamic_keywords(domain_patterns(config))
        if previously_complete and previous_config:
            save_json(CONFIG_PATH, previous_config_raw)
            save_json(INSTALL_STATE_PATH, previous_install)
            save_json(STATE_PATH, previous_state)
            if had_installed_script and rollback_script.exists():
                shutil.copy2(rollback_script, INSTALLED_SCRIPT)
            if previous_install.get("app_version"):
                old_pythonw = Path(previous_install.get("pythonw", str(pythonw)))
                create_task(old_pythonw, previous_config)
            if previous_state.get("blocked"):
                set_browser_policy_entries(True, previous_entries)
                create_firewall_rules(previous_config)
            else:
                set_browser_policy_entries(False, previous_entries)
        else:
            restore_defender_network_protection(previous_network)
            for path in (INSTALLED_SCRIPT, CONFIG_PATH, STATE_PATH, INSTALL_STATE_PATH):
                path.unlink(missing_ok=True)
        rollback_script.unlink(missing_ok=True)
        raise


def uninstall() -> None:
    require_windows_admin()
    config = load_config()
    install_state = load_json(INSTALL_STATE_PATH, {}) or {}
    delete_task(TASK_NAME)
    delete_task(LEGACY_TASK_NAME)
    set_browser_policy_entries(False, install_state.get("browser_block_policies", []))
    remove_firewall_rules_only()
    remove_dynamic_keywords(domain_patterns(config) + LEGACY_DEFAULT_DOMAINS)
    restore_defender_network_protection(install_state.get("previous_network_protection"))
    logging.info("SSB uninstalled")
    logging.shutdown()
    expected = DEFAULT_INSTALL_DIR.resolve()
    actual = INSTALL_DIR.resolve()
    if actual != expected:
        raise RuntimeError(f"Refusing to remove unexpected installation directory: {actual}")
    shutil.rmtree(actual, ignore_errors=False)


def status_payload() -> dict:
    require_windows_admin()
    config = load_config()
    now = dt.datetime.now().astimezone()
    state = load_json(STATE_PATH, {}) or {}
    start = parse_clock(config["block_start"])
    end = parse_clock(config["block_end"])
    exception = active_exception(now, state)
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "installed": installation_complete(),
        "now": now.isoformat(),
        "schedule": f"{config['block_start']}-{config['block_end']}",
        "scheduled_block_window": in_block_window(now, start, end),
        "blocked": bool(state.get("blocked")),
        "firewall_rule_count": firewall_rule_count(),
        "temporary_unlock_until": exception.astimezone().isoformat() if exception else None,
        "sites": config["sites"],
    }


def relaunch_elevated_gui() -> None:
    if os.name != "nt":
        raise RuntimeError("SSB supports Windows only.")
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    executable = pythonw if pythonw.exists() else python
    parameters = subprocess.list2cmdline([str(Path(__file__).resolve()), "--gui"])
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(executable), parameters, None, 1)
    if result <= 32:
        raise PermissionError("Administrator approval was cancelled or unavailable.")


class SSBWindow:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("760x620")
        self.root.minsize(700, 560)
        self.root.option_add("*Font", ("Segoe UI", 10))
        self.current_config = load_config()
        self.sites = [dict(site) for site in self.current_config["sites"]]
        self.hostname_var = tk.StringVar()
        self.subdomains_var = tk.BooleanVar(value=True)
        self.start_var = tk.StringVar(value=self.current_config["block_start"])
        self.end_var = tk.StringVar(value=self.current_config["block_end"])
        self.status_var = tk.StringVar()
        self._build()
        self._refresh_table()
        self._refresh_status()

    def _build(self) -> None:
        from tkinter import ttk

        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="SSB", font=("Segoe UI Semibold", 24)).pack(anchor="w")
        ttk.Label(outer, text="Simple Site Blocker — local, scheduled, and under thy control.").pack(anchor="w", pady=(0, 12))
        status = ttk.Label(outer, textvariable=self.status_var, padding=10, relief="solid")
        status.pack(fill="x", pady=(0, 14))

        site_box = ttk.LabelFrame(outer, text="Websites", padding=10)
        site_box.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(site_box, columns=("hostname", "subdomains"), show="headings", height=9)
        self.tree.heading("hostname", text="Website")
        self.tree.heading("subdomains", text="All subdomains")
        self.tree.column("hostname", width=440, anchor="w")
        self.tree.column("subdomains", width=130, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._load_selected)

        entry_row = ttk.Frame(site_box)
        entry_row.pack(fill="x", pady=(9, 0))
        ttk.Entry(entry_row, textvariable=self.hostname_var).pack(side="left", fill="x", expand=True)
        ttk.Checkbutton(entry_row, text="Include all subdomains", variable=self.subdomains_var).pack(side="left", padx=10)
        ttk.Button(entry_row, text="Add / Update", command=self._add_or_update).pack(side="left")
        ttk.Button(entry_row, text="Remove", command=self._remove_selected).pack(side="left", padx=(8, 0))

        schedule = ttk.LabelFrame(outer, text="Daily blocking period", padding=10)
        schedule.pack(fill="x", pady=14)
        times = [f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 15, 30, 45)]
        ttk.Label(schedule, text="Block from").pack(side="left")
        ttk.Combobox(schedule, textvariable=self.start_var, values=times, width=8).pack(side="left", padx=8)
        ttk.Label(schedule, text="until").pack(side="left")
        ttk.Combobox(schedule, textvariable=self.end_var, values=times, width=8).pack(side="left", padx=8)
        ttk.Label(schedule, text="(local system time; midnight crossover is supported)").pack(side="left", padx=8)

        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        self.save_button = ttk.Button(
            actions,
            text="Save Changes" if installation_complete() else "Install SSB",
            command=self._save,
        )
        self.save_button.pack(side="left")
        ttk.Button(actions, text="Instructions", command=self._show_help).pack(side="left", padx=8)
        if installation_complete():
            ttk.Button(actions, text="Uninstall SSB", command=self._uninstall).pack(side="left")
        ttk.Button(actions, text="Close", command=self.root.destroy).pack(side="right")

    def _refresh_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for site in sorted(self.sites, key=lambda item: item["hostname"]):
            self.tree.insert("", "end", iid=site["hostname"], values=(site["hostname"], "Yes" if site["include_subdomains"] else "No"))

    def _refresh_status(self) -> None:
        now = dt.datetime.now().astimezone()
        start = parse_clock(self.current_config["block_start"])
        end = parse_clock(self.current_config["block_end"])
        state = load_json(STATE_PATH, {}) or {}
        exception = active_exception(now, state)
        if not installation_complete():
            text = "Not installed. Review the website list and schedule, then select Install SSB."
        elif in_block_window(now, start, end) and exception:
            text = f"Temporarily open until {exception.astimezone().strftime('%H:%M')}. Scheduled period: {self.current_config['block_start']}–{self.current_config['block_end']}."
        elif in_block_window(now, start, end):
            text = f"Blocking is active. Scheduled period: {self.current_config['block_start']}–{self.current_config['block_end']}."
        else:
            text = f"Sites are open. Scheduled period: {self.current_config['block_start']}–{self.current_config['block_end']}."
        self.status_var.set(text)

    def _load_selected(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        hostname = selected[0]
        site = next(item for item in self.sites if item["hostname"] == hostname)
        self.hostname_var.set(hostname)
        self.subdomains_var.set(site["include_subdomains"])

    def _add_or_update(self) -> None:
        from tkinter import messagebox

        try:
            hostname = normalize_hostname(self.hostname_var.get())
        except ValueError as exc:
            messagebox.showerror("Invalid website", str(exc), parent=self.root)
            return
        updated = False
        for site in self.sites:
            if site["hostname"] == hostname:
                site["include_subdomains"] = self.subdomains_var.get()
                updated = True
                break
        if not updated:
            self.sites.append({"hostname": hostname, "include_subdomains": self.subdomains_var.get()})
        self.hostname_var.set("")
        self.subdomains_var.set(True)
        self._refresh_table()

    def _remove_selected(self) -> None:
        selected = set(self.tree.selection())
        if not selected:
            return
        self.sites = [site for site in self.sites if site["hostname"] not in selected]
        self._refresh_table()

    def _proposed_config(self) -> dict:
        return migrate_config({
            "version": 1,
            "block_start": self.start_var.get().strip(),
            "block_end": self.end_var.get().strip(),
            "default_unlock_minutes": self.current_config.get("default_unlock_minutes", 30),
            "maximum_unlock_minutes": self.current_config.get("maximum_unlock_minutes", 120),
            "sites": self.sites,
        })

    def _save(self) -> None:
        from tkinter import messagebox

        try:
            proposed = self._proposed_config()
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Invalid configuration", str(exc), parent=self.root)
            return
        site_lines = "\n".join(
            f"• {site['hostname']}{' and all subdomains' if site['include_subdomains'] else ''}"
            for site in proposed["sites"]
        )
        summary = f"Block daily from {proposed['block_start']} until {proposed['block_end']}:\n\n{site_lines}"
        if not messagebox.askyesno("Confirm SSB configuration", summary, parent=self.root):
            return

        changed = proposed != self.current_config
        now = dt.datetime.now().astimezone()
        if installation_complete() and requires_blocked_period_confirmation(
            now, self.current_config, proposed, changed
        ):
            warning = (
                "The present time falleth within the existing or proposed blocked period.\n\n"
                "Changing websites or hours now may immediately grant or remove access. "
                "Dost thou truly wish to apply this change?"
            )
            if not messagebox.askyesno("Blocked-period confirmation", warning, icon="warning", parent=self.root):
                return
        try:
            install_or_update(proposed)
        except Exception as exc:
            messagebox.showerror("SSB could not save", str(exc), parent=self.root)
            return
        self.current_config = proposed
        self.save_button.configure(text="Save Changes")
        self._refresh_status()
        messagebox.showinfo("SSB is ready", "The configuration was installed and the present state was reconciled.", parent=self.root)

    def _show_help(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        window = tk.Toplevel(self.root)
        window.title("SSB Instructions")
        window.geometry("720x570")
        window.transient(self.root)
        text = tk.Text(window, wrap="word", padx=16, pady=16, font=("Segoe UI", 10))
        text.insert("1.0", HELP_TEXT)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)
        ttk.Button(window, text="Close", command=window.destroy).pack(pady=10)

    def _uninstall(self) -> None:
        from tkinter import messagebox

        if not messagebox.askyesno(
            "Uninstall SSB",
            "Remove the scheduled task, firewall rules, browser-policy entries, settings, and logs?",
            icon="warning",
            parent=self.root,
        ):
            return
        try:
            uninstall()
        except Exception as exc:
            messagebox.showerror("Uninstallation failed", str(exc), parent=self.root)
            return
        messagebox.showinfo("SSB removed", "Simple Site Blocker hath been removed from this computer.", parent=self.root)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_gui() -> None:
    require_windows_admin()
    SSBWindow().run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gui", action="store_true", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("enforce", help="Apply the state required by the schedule.")
    commands.add_parser("status", help="Print installation and blocking status as JSON.")
    commands.add_parser("repair", help="Reinstall the saved configuration, rules, and scheduled task.")
    unlock_parser = commands.add_parser("unlock", help="Grant a temporary exception during blocking hours.")
    unlock_parser.add_argument("--minutes", type=int, default=None, help="Exception duration; default is configured locally.")
    unlock_parser.add_argument("--reason", default="Temporary exception", help="Purpose recorded in the local log.")
    commands.add_parser("uninstall", help="Remove SSB and restore settings changed at installation.")
    commands.add_parser("instructions", help="Print installation and removal instructions.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.gui:
        try:
            run_gui()
            return 0
        except Exception as exc:
            try:
                from tkinter import messagebox
                messagebox.showerror(APP_NAME, str(exc))
            except Exception:
                pass
            return 1
    if args.command is None:
        try:
            if is_admin():
                run_gui()
            else:
                relaunch_elevated_gui()
            return 0
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    if args.command == "instructions":
        print(HELP_TEXT)
        return 0

    configure_logging()
    try:
        if args.command == "enforce":
            enforce()
        elif args.command == "repair":
            install_or_update(load_config())
            print("SSB installation was repaired.")
        elif args.command == "status":
            print(json.dumps(status_payload(), indent=2))
        elif args.command == "unlock":
            config = load_config()
            minutes = args.minutes if args.minutes is not None else int(config.get("default_unlock_minutes", 30))
            expiry = unlock(minutes, args.reason)
            print(f"Temporary unlock expires at {expiry.strftime('%Y-%m-%d %H:%M:%S %Z')}.")
        elif args.command == "uninstall":
            uninstall()
            print("SSB was removed.")
        return 0
    except Exception as exc:
        logging.exception("Command %s failed", args.command)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
