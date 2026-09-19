"""Import documented public SSB settings, without executing old installation code."""
import os
from pathlib import Path
import shutil
from . import core as c
from .settings import read_json


def legacy_directory():
    return Path(os.environ.get('ProgramData', r'C:\ProgramData')) / 'CodexSiteBlocker'


def inspect_legacy():
    home = legacy_directory()
    install = read_json(home / 'install-state.json', {}) or {}
    if not install.get('installation_complete'):
        return None
    version = install.get('app_version', '')
    if version not in ('1.0.0', '1.0.1', '1.0.2', '1.0.1-personal.1'):
        raise RuntimeError('An unsupported local SSB variant is installed. Uninstall it first so its network restrictions are restored. Then run this installer again. No settings have been imported.')
    config = read_json(home / 'config.json')
    if not isinstance(config, dict) or 'lan_only' in config:
        raise RuntimeError('This legacy installation requires separate cleanup before installing SSB.')
    c.migrate_config(config)
    for entry in install.get('browser_block_policies', []):
        if not isinstance(entry, dict) or entry.get('path') not in {
            r'SOFTWARE\Policies\Google\Chrome\URLBlocklist',
            r'SOFTWARE\Policies\Microsoft\Edge\URLBlocklist', c.FIREFOX_BLOCK_PATH
        } or not str(entry.get('name', '')).isdigit():
            raise ValueError('The old browser-policy restoration data is not recognized.')
    return home, config, install


def migrate_legacy():
    legacy = inspect_legacy()
    if not legacy:
        return
    home, config, install = legacy
    backup = c.INSTALL_STATE_PATH.parent / 'legacy-backup'
    backup.mkdir(parents=True, exist_ok=True)
    for name in ('config.json', 'state.json', 'install-state.json'):
        path = home / name
        if path.exists():
            shutil.copy2(path, backup / name)
    # Preserve an existing packaged config; import only for the first installation.
    if not c.installation_complete():
        c.save_json(c.CONFIG_PATH, c.migrate_config(config))
        c.save_json(c.STATE_PATH, read_json(home / 'state.json', {}) or {})
        c.save_json(c.INSTALL_STATE_PATH, install)
    c.delete_task(c.TASK_NAME)
    c.delete_task(c.LEGACY_TASK_NAME)
    c.set_browser_policy_entries(False, install.get('browser_block_policies', []))
    c.remove_firewall_rules_only()
    c.remove_dynamic_keywords(c.domain_patterns(config))
    # Keep the old restoration information until the new installation succeeds.


def mark_legacy_migrated():
    home = legacy_directory()
    path = home / 'install-state.json'
    old = read_json(path, {}) or {}
    if old.get('installation_complete'):
        old['installation_complete'] = False
        old['migrated_to'] = str(c.APP_DIR)
        c.save_json(path, old)
