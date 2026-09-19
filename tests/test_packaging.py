import copy
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from ssb import core as c, settings, migration, lifecycle


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.json'
        self.config = c.migrate_config(copy.deepcopy(c.DEFAULT_CONFIG))

    def test_separate_website_list_and_backup(self):
        settings.save_config(self.path, self.config)
        self.assertNotIn('sites', settings.read_json(self.path))
        self.assertEqual(settings.read_json(self.path.parent / 'list.json')['sites'], self.config['sites'])
        updated = {**self.config, 'sync_firefox': False, 'block_start': '20:00'}
        settings.save_config(self.path, updated)
        self.assertEqual(settings.load_config(self.path), updated)
        self.assertEqual(settings.read_json(self.path.parent / 'backup.json'), self.config)

    def test_recovers_interrupted_two_file_commit(self):
        settings.save_config(self.path, self.config)
        updated = {**self.config, 'sites': [{'hostname': 'example.com', 'include_subdomains': False}]}
        original = settings.atomic_json
        failed = False
        def interrupted(path, value):
            nonlocal failed
            if Path(path).name == 'settings.json' and not failed:
                failed = True
                raise OSError('simulated interruption between files')
            original(path, value)
        with patch.object(settings, 'atomic_json', side_effect=interrupted):
            with self.assertRaises(OSError):
                settings.save_config(self.path, updated)
        self.assertEqual(settings.load_config(self.path), updated)
        self.assertFalse((self.path.parent / '.config-transaction.json').exists())

    def test_corrupt_or_missing_file_does_not_reset_defaults(self):
        settings.save_config(self.path, self.config)
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'damaged'):
            settings.load_config(self.path)
        self.path.unlink()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            settings.load_config(self.path)

    def test_concurrent_writer_is_not_allowed(self):
        lock = self.path.parent / 'test.lock'
        with settings.file_lock(lock):
            with self.assertRaisesRegex(RuntimeError, 'Another SSB operation'):
                with settings.file_lock(lock, timeout=0.1):
                    pass


class IntegrationTests(unittest.TestCase):
    def test_firefox_opt_out_preserves_other_browser_policies(self):
        config = c.migrate_config({**c.DEFAULT_CONFIG, 'sync_firefox': False})
        values = c.browser_policy_values(config)
        self.assertTrue(values)
        self.assertFalse(any('Firefox' in path for path, _ in values))
        self.assertTrue(any('Chrome' in path for path, _ in values))
        self.assertTrue(any('Edge' in path for path, _ in values))

    def test_firefox_preference_defaults_true_and_rejects_strings(self):
        self.assertTrue(c.migrate_config({'sites': c.DEFAULT_CONFIG['sites'], 'block_start': '21:00', 'block_end': '03:00'})['sync_firefox'])
        with self.assertRaises(ValueError):
            c.migrate_config({**c.DEFAULT_CONFIG, 'sync_firefox': 'false'})

    def test_packaged_task_runs_executable_without_python(self):
        with patch.object(sys, 'frozen', True, create=True):
            xml = c.task_xml(Path(r'C:\Program Files\SSB\SSB.exe'), c.DEFAULT_CONFIG)
        self.assertIn('<Arguments>enforce</Arguments>', xml)
        self.assertNotIn('python', xml.lower())
        self.assertNotIn('site_blocker.py', xml)

    def test_unsupported_local_variant_is_rejected_without_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings.atomic_json(root / 'install-state.json', {'app_version': '1.1.0', 'installation_complete': True})
            with patch.object(migration, 'legacy_directory', return_value=root):
                with self.assertRaisesRegex(RuntimeError, 'unsupported local'):
                    migration.inspect_legacy()

    def test_unchecking_removes_previously_owned_firefox_rules(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            for name, relative in [('CONFIG_PATH','config/settings.json'), ('STATE_PATH','state/state.json'), ('INSTALL_STATE_PATH','state/install-state.json')]:
                stack.enter_context(patch.object(c, name, root / relative))
            old = [{'path': c.FIREFOX_BLOCK_PATH, 'name': '1', 'value': '*://example.com/*', 'previous': {'exists': False}}]
            c.save_json(c.CONFIG_PATH, c.migrate_config(c.DEFAULT_CONFIG))
            c.save_json(c.INSTALL_STATE_PATH, {'installation_complete': True, 'browser_block_policies': old, 'previous_network_protection': 1})
            for name in ('require_windows_admin','configure_logging','secure_install_directory','remove_firewall_rules_only','remove_dynamic_keywords','create_task','enforce','delete_task'):
                stack.enter_context(patch.object(c, name))
            stack.enter_context(patch.object(c, 'defender_network_protection', return_value=1))
            stack.enter_context(patch.object(c, 'prepare_browser_policy_entries', return_value=[]))
            restore = stack.enter_context(patch.object(c, 'set_browser_policy_entries'))
            lifecycle.apply_configuration({**c.DEFAULT_CONFIG, 'sync_firefox': False})
            restore.assert_any_call(False, old)
            self.assertFalse(c.load_config()['sync_firefox'])


if __name__ == '__main__':
    unittest.main()
