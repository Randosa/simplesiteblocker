"""Install, repair and remove the Windows integration without self-copying."""
import logging
from . import core as c
from .settings import file_lock


def operation_lock():
    return file_lock(c.INSTALL_STATE_PATH.parent / '.operation.lock')


def apply_configuration(config, progress=None):
    report = progress or (lambda message: None)
    c.require_windows_admin()
    config = c.migrate_config(config)
    with operation_lock():
        report('Preparing changes...')
        c.configure_logging()
        c.secure_install_directory()
        previous_config = c.load_json(c.CONFIG_PATH)
        previous_install = c.load_json(c.INSTALL_STATE_PATH, {}) or {}
        previous_state = c.load_json(c.STATE_PATH, {}) or {}
        complete = previous_install.get('installation_complete', False)
        previous_entries = previous_install.get('browser_block_policies', [])
        previous_network = previous_install.get('previous_network_protection')
        if previous_network is None:
            previous_network = c.defender_network_protection()
        entries = []
        try:
            report('Updating website blocks...')
            c.set_browser_policy_entries(False, previous_entries)
            c.remove_firewall_rules_only()
            c.remove_dynamic_keywords(c.domain_patterns(previous_config) if previous_config else [])
            entries = c.prepare_browser_policy_entries(config)
            c.save_json(c.CONFIG_PATH, config)
            install = {'app_version': c.APP_VERSION, 'runner': str(c.runner()),
                       'previous_network_protection': previous_network,
                       'browser_block_policies': entries, 'installation_complete': False}
            c.save_json(c.INSTALL_STATE_PATH, install)
            if c.defender_network_protection() != 1:
                c.set_defender_network_protection('Enabled')
            report('Updating the schedule...')
            c.create_task(c.runner(), config)
            install['installation_complete'] = True
            c.save_json(c.INSTALL_STATE_PATH, install)
            report('Applying the current blocking state...')
            c.enforce()
            c.delete_task(c.LEGACY_TASK_NAME)
            report('Finishing...')
        except Exception:
            report('Save failed; restoring previous settings...')
            logging.exception('Applying SSB settings failed')
            c.set_browser_policy_entries(False, entries)
            c.remove_firewall_rules_only()
            c.remove_dynamic_keywords(c.domain_patterns(config))
            if previous_config is not None:
                c.save_json(c.CONFIG_PATH, previous_config)
            c.save_json(c.INSTALL_STATE_PATH, previous_install)
            c.save_json(c.STATE_PATH, previous_state)
            if complete and previous_config:
                c.create_task(c.runner(), previous_config)
                if previous_state.get('blocked'):
                    c.set_browser_policy_entries(True, previous_entries)
                    c.create_firewall_rules(previous_config)
                if previous_state.get('internet_blocked'):
                    c.set_internet_cutoff(True)
            else:
                c.delete_task(c.TASK_NAME)
                c.restore_defender_network_protection(previous_network)
            raise


def remove_integration():
    c.require_windows_admin()
    with operation_lock():
        config = c.load_config()
        install = c.load_json(c.INSTALL_STATE_PATH, {}) or {}
        c.delete_task(c.TASK_NAME)
        c.delete_task(c.LEGACY_TASK_NAME)
        c.set_browser_policy_entries(False, install.get('browser_block_policies', []))
        c.remove_firewall_rules_only()
        c.remove_dynamic_keywords(c.domain_patterns(config) + c.LEGACY_DEFAULT_DOMAINS)
        c.restore_defender_network_protection(install.get('previous_network_protection'))
        c.save_json(c.INSTALL_STATE_PATH, {'installation_complete': False})
        c.save_json(c.STATE_PATH, {})


def prepare_update():
    c.require_windows_admin()
    with operation_lock():
        c.save_json(c.INSTALL_STATE_PATH.parent / 'maintenance.json', {'updating': True})
        c.run(['schtasks.exe', '/Change', '/TN', c.TASK_NAME, '/DISABLE'], check=False)


def finish_update():
    from .migration import migrate_legacy, mark_legacy_migrated
    c.require_windows_admin()
    c.secure_install_directory()
    if not c.installation_complete():
        migrate_legacy()
    apply_configuration(c.load_config())
    mark_legacy_migrated()
    (c.INSTALL_STATE_PATH.parent / 'maintenance.json').unlink(missing_ok=True)
