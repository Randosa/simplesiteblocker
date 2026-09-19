#!/usr/bin/env python3
"""SSB (Simple Site Blocker): a simple, locally run website blocker for Windows.

Installed by Inno Setup in Program Files. The executable opens the graphical
manager or runs briefly under Task Scheduler to enforce the chosen schedule.

Command-line maintenance:
  python site_blocker.py                 Open the graphical manager (UAC required)
  python site_blocker.py enforce         Reconcile the present blocking state
  python site_blocker.py status          Print status as JSON
  python site_blocker.py unlock --minutes 30 --reason "Specific purpose"
  python site_blocker.py uninstall       Restore SSB's Windows integration only

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
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Callable
from urllib.parse import urlsplit
import uuid
import xml.etree.ElementTree as ET


APP_NAME = "SSB (Simple Site Blocker)"
APP_VERSION = "1.2.0"
TASK_NAME = r"\Simple Site Blocker\Reconcile"
LEGACY_TASK_NAME = r"\Scheduled Site Blocker\Reconcile"
FIREWALL_GROUP = "Simple Site Blocker"
LEGACY_FIREWALL_GROUP = "Scheduled Site Blocker"
from .paths import APP_DIR, resource
from . import settings
DEFAULT_INSTALL_DIR = APP_DIR
INSTALL_DIR = APP_DIR
INSTALLED_SCRIPT = APP_DIR / "SSB.exe"
CONFIG_PATH = APP_DIR / "config" / "settings.json"
STATE_PATH = APP_DIR / "state" / "state.json"
INSTALL_STATE_PATH = APP_DIR / "state" / "install-state.json"
LOG_PATH = APP_DIR / "logs" / "ssb.log"
POWERSHELL = os.environ.get("SystemRoot", r"C:\Windows") + r"\System32\WindowsPowerShell\v1.0\powershell.exe"

DEFAULT_CONFIG = {
    "version": 1,
    "sync_firefox": True,
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

HELP_TEXT = """SSB — Simple Site Blocker

Open SSB from the Start menu and approve the administrator prompt.
Edit the websites and daily hours, then select Save Changes.
Settings are stored in Program Files\\SSB\\config.

Sync changes to Firefox applies website rules to Firefox. Disabling it removes
SSB's Firefox entries only. Firefox requires a full restart to refresh policies,
including when blocking hours end. Chrome and Edge retain their own policies.

Check for Updates downloads and verifies a signed installer after confirmation.
Updates preserve the saved website list and schedule. No Python installation
is required. Updates and setup may need administrator approval.

Uninstall through Windows Installed Apps or the Uninstall SSB button.
The uninstaller removes SSB rules and tasks and offers to keep configuration.
"""


def configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    if not config.get("sync_firefox", True):
        return values
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
    result.setdefault("sync_firefox", True)
    if not isinstance(result["sync_firefox"], bool):
        raise ValueError("Sync changes to Firefox must be enabled or disabled.")
    parse_clock(str(result.get("block_start", "")))
    parse_clock(str(result.get("block_end", "")))
    if int(result.get("maximum_unlock_minutes", 120)) < 1:
        raise ValueError("Maximum unlock duration must be positive.")
    return result


def load_json(path: Path, default=None):
    if path == CONFIG_PATH:
        return settings.load_config(path, default)
    return settings.read_json(path, default)


def save_json(path: Path, value) -> None:
    if path == CONFIG_PATH:
        settings.save_config(path, value)
    else:
        settings.atomic_json(path, value)


def load_config() -> dict:
    return migrate_config(load_json(CONFIG_PATH, None))


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command, text=True, capture_output=True, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"Command failed with exit code {result.returncode}.")
    return result


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def powershell(script: str, *, check: bool = True) -> subprocess.CompletedProcess:
    # A file avoids Windows' command-line length limit for large website lists.
    with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", delete=False, suffix=".ps1") as handle:
        handle.write(script)
        script_path = Path(handle.name)
    try:
        return run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
            check=check,
        )
    finally:
        script_path.unlink(missing_ok=True)


def keyword_id(domain: str) -> str:
    return "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, "scheduled-site-blocker:" + domain)) + "}"


def remove_firewall_rules_only() -> None:
    scripts = []
    for group_name in (FIREWALL_GROUP, LEGACY_FIREWALL_GROUP):
        group = ps_quote(group_name)
        scripts.append(
            f"Get-NetFirewallRule -Group {group} -ErrorAction SilentlyContinue | "
            "Remove-NetFirewallRule -ErrorAction SilentlyContinue"
        )
    powershell("\n".join(scripts), check=False)


def remove_dynamic_keywords(domains: list[str]) -> None:
    scripts = []
    for domain in dict.fromkeys(domains):
        scripts.append(
            f"Remove-NetFirewallDynamicKeywordAddress -Id {ps_quote(keyword_id(domain))} "
            "-ErrorAction SilentlyContinue"
        )
    if scripts:
        powershell("\n".join(scripts), check=False)


def create_firewall_rules(config: dict) -> None:
    scripts = []
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
        scripts.append(script)
    if scripts:
        powershell("$ErrorActionPreference = 'Stop'\n" + "\n".join(scripts))


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


FIREFOX_BLOCK_PATH = r"SOFTWARE\Policies\Mozilla\Firefox\WebsiteFilter\Block"


def policy_entry_start(path: str, existing_names: list[str]) -> int:
    if path != FIREFOX_BLOCK_PATH:
        return 9000
    # Firefox identifies a registry array by the FIRST enumerated value being 1.
    if existing_names and existing_names[0] != "1":
        raise ValueError("An existing Firefox website list is not a valid array. Its first registry value must be named 1.")
    return 1


def prune_empty_firefox_policy_keys() -> None:
    import winreg

    for path in (FIREFOX_BLOCK_PATH, FIREFOX_BLOCK_PATH.rsplit("\\", 1)[0]):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                children, values, _modified = winreg.QueryInfoKey(key)
            if not children and not values:
                winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, path)
        except FileNotFoundError:
            pass


def prepare_browser_policy_entries(config: dict) -> list[dict]:
    import winreg

    used_by_path: dict[str, set[str]] = {}
    starts_by_path: dict[str, int] = {}
    entries: list[dict] = []
    for path, value in browser_policy_values(config):
        if path not in used_by_path:
            names: list[str] = []
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                    index = 0
                    while True:
                        try:
                            names.append(winreg.EnumValue(key, index)[0])
                            index += 1
                        except OSError:
                            break
            except FileNotFoundError:
                pass
            starts_by_path[path] = policy_entry_start(path, names)
            used_by_path[path] = set(names)
        number = starts_by_path[path]
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
    if not enabled:
        # Empty registry keys would otherwise become {} instead of an array.
        prune_empty_firefox_policy_keys()


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
    arguments = "enforce" if getattr(sys, "frozen", False) else xml_escape(subprocess.list2cmdline([str(Path(__file__).parents[1] / "site_blocker.py"), "enforce"]))
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
  <Actions Context="Author"><Exec><Command>{executable}</Command><Arguments>{arguments}</Arguments></Exec></Actions>
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
    # Keep program files readable/executable; restrict mutable state and settings.
    for directory in (CONFIG_PATH.parent, STATE_PATH.parent, LOG_PATH.parent):
        directory.mkdir(parents=True, exist_ok=True)
        run(["icacls.exe", str(directory), "/inheritance:r", "/grant:r",
             "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"])


def installation_complete() -> bool:
    return bool((load_json(INSTALL_STATE_PATH, {}) or {}).get("installation_complete"))


def runner() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(sys.executable).with_name("pythonw.exe")


def install_or_update(config: dict, progress: Callable[[str], None] | None = None) -> None:
    from .lifecycle import apply_configuration
    apply_configuration(config, progress)


def uninstall() -> None:
    from .lifecycle import remove_integration
    remove_integration()


def launch_uninstaller() -> None:
    path = APP_DIR / "unins000.exe"
    if not path.is_file():
        raise RuntimeError("Use Windows Installed Apps to uninstall the packaged SSB application.")
    subprocess.Popen([str(path)], creationflags=subprocess.CREATE_NO_WINDOW)


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
    parameters = "--gui" if getattr(sys, "frozen", False) else subprocess.list2cmdline([str(Path(__file__).parents[1] / "site_blocker.py"), "--gui"])
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(executable), parameters, None, 1)
    if result <= 32:
        raise PermissionError("Administrator approval was cancelled or unavailable.")



def run_gui() -> None:
    require_windows_admin()
    from .ui import SSBWindow
    SSBWindow().run()
