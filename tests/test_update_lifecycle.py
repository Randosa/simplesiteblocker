from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ssb import cli, core as c, lifecycle, migration, settings, updater


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, relative in [('CONFIG_PATH', 'config/settings.json'),
                               ('STATE_PATH', 'state/state.json'),
                               ('INSTALL_STATE_PATH', 'state/install-state.json')]:
            self.stack.enter_context(patch.object(c, name, self.root / relative))
        self.stack.enter_context(patch.object(c, 'require_windows_admin'))
        self.stack.enter_context(patch.object(c, 'configure_logging'))

    def test_queued_task_cannot_reapply_rules_after_uninstall(self):
        c.save_json(c.INSTALL_STATE_PATH, {'installation_complete': False})
        with patch.object(c, 'enforce') as enforce:
            self.assertEqual(cli.main(['enforce']), 0)
            enforce.assert_not_called()

    def test_prepared_update_blocks_queued_enforcement(self):
        c.save_json(c.INSTALL_STATE_PATH, {'installation_complete': True})
        with patch.object(c, 'run'), patch.object(c, 'enforce') as enforce:
            lifecycle.prepare_update()
            self.assertEqual(cli.main(['enforce']), 0)
            enforce.assert_not_called()

    def test_failed_update_retains_maintenance_marker_for_repair(self):
        marker = c.INSTALL_STATE_PATH.parent / 'maintenance.json'
        settings.atomic_json(marker, {'updating': True})
        c.save_json(c.INSTALL_STATE_PATH, {'installation_complete': True})
        with patch.object(c, 'secure_install_directory'), \
             patch.object(lifecycle, 'apply_configuration', side_effect=RuntimeError('failure')), \
             patch.object(migration, 'mark_legacy_migrated') as mark:
            with self.assertRaisesRegex(RuntimeError, 'failure'):
                lifecycle.finish_update()
            self.assertTrue(marker.exists())
            mark.assert_not_called()

    def test_successful_update_clears_maintenance_after_integration(self):
        marker = c.INSTALL_STATE_PATH.parent / 'maintenance.json'
        settings.atomic_json(marker, {'updating': True})
        c.save_json(c.INSTALL_STATE_PATH, {'installation_complete': True})
        with patch.object(c, 'secure_install_directory'), \
             patch.object(lifecycle, 'apply_configuration') as apply, \
             patch.object(migration, 'mark_legacy_migrated'):
            lifecycle.finish_update()
            apply.assert_called_once()
            self.assertFalse(marker.exists())

    def test_public_migration_backs_up_before_import(self):
        old = self.root / 'legacy'
        config = c.migrate_config({**c.DEFAULT_CONFIG, 'block_start': '19:17', 'sync_firefox': False})
        install = {'app_version': '1.0.2', 'installation_complete': True,
                   'browser_block_policies': [], 'previous_network_protection': 2}
        settings.atomic_json(old / 'config.json', config)
        settings.atomic_json(old / 'install-state.json', install)
        settings.atomic_json(old / 'state.json', {'blocked': False})
        with ExitStack() as stack:
            stack.enter_context(patch.object(migration, 'legacy_directory', return_value=old))
            for name in ('delete_task', 'set_browser_policy_entries', 'remove_firewall_rules_only', 'remove_dynamic_keywords'):
                stack.enter_context(patch.object(c, name))
            migration.migrate_legacy()
            self.assertEqual(c.load_config(), config)
            self.assertEqual(c.load_json(c.INSTALL_STATE_PATH), install)
            backup = c.INSTALL_STATE_PATH.parent / 'legacy-backup/config.json'
            self.assertEqual(backup.read_bytes(), (old / 'config.json').read_bytes())
            self.assertTrue(settings.read_json(old / 'install-state.json')['installation_complete'])
            migration.mark_legacy_migrated()
            self.assertFalse(settings.read_json(old / 'install-state.json')['installation_complete'])


class UpdaterTests(unittest.TestCase):
    def test_save_blocks_shutdown_and_callback_only_signals_main_thread(self):
        dll = Mock()
        dll.win_sparkle_set_eddsa_public_key.return_value = 1
        busy = True
        with patch.object(updater.ctypes, 'CDLL', return_value=dll):
            instance = updater.Updater(lambda: not busy)
        allowed, shutdown = instance._callbacks
        self.assertEqual(allowed(), 0)
        busy = False
        self.assertEqual(allowed(), 1)
        shutdown()
        self.assertTrue(instance.shutdown_requested.is_set())
        instance.close()
        instance.close()
        dll.win_sparkle_cleanup.assert_called_once()

    def test_rejected_public_key_prevents_updater_initialization(self):
        dll = Mock()
        dll.win_sparkle_set_eddsa_public_key.return_value = 0
        with patch.object(updater.ctypes, 'CDLL', return_value=dll):
            with self.assertRaisesRegex(RuntimeError, 'rejected'):
                updater.Updater()
        dll.win_sparkle_init.assert_not_called()


if __name__ == '__main__':
    unittest.main()
