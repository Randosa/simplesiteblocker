import copy
from contextlib import ExitStack
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


import sys
sys.path.insert(0, str(Path(__file__).parents[1]))
from ssb import core as ssb
from ssb.ui import SSBWindow
ssb.SSBWindow = SSBWindow


class QuietCommandTests(unittest.TestCase):
    def test_child_process_has_no_console_and_keeps_errors(self):
        result = subprocess.CompletedProcess([], 1, "", "Permission denied")
        with patch.object(ssb.subprocess, "run", return_value=result) as child:
            with self.assertRaisesRegex(RuntimeError, "Permission denied"):
                ssb.run(["example.exe"])
        self.assertEqual(child.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertTrue(child.call_args.kwargs["capture_output"])

    def test_large_batch_uses_file_and_cleans_it_after_error(self):
        script = "# large script\n" * 5000
        paths = []
        def failed_run(command, **kwargs):
            path = Path(command[-1])
            paths.append(path)
            self.assertIn("-File", command)
            self.assertEqual(path.read_text(encoding="utf-8-sig"), script)
            raise RuntimeError("test failure")
        with patch.object(ssb, "run", side_effect=failed_run):
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                ssb.powershell(script)
        self.assertFalse(paths[0].exists())

    def test_many_sites_launch_one_creation_batch(self):
        config = ssb.migrate_config({**ssb.DEFAULT_CONFIG, "sites": [
            {"hostname": f"site{i}.example.com", "include_subdomains": True} for i in range(100)
        ]})
        with patch.object(ssb, "powershell") as command:
            ssb.create_firewall_rules(config)
        command.assert_called_once()
        script = command.call_args.args[0]
        self.assertEqual(script.count("New-NetFirewallRule "), 200)
        for domain in ssb.domain_patterns(config):
            self.assertIn(ssb.ps_quote(domain), script)

    def test_cleanup_batches_and_deduplicates_domains(self):
        with patch.object(ssb, "powershell") as command:
            ssb.remove_dynamic_keywords(["example.com", "*.example.com", "example.com"])
        command.assert_called_once()
        self.assertEqual(command.call_args.args[0].count("Remove-NetFirewallDynamicKeywordAddress"), 2)
        with patch.object(ssb, "powershell") as command:
            ssb.remove_firewall_rules_only()
        command.assert_called_once()
        self.assertIn(ssb.FIREWALL_GROUP, command.call_args.args[0])
        self.assertIn(ssb.LEGACY_FIREWALL_GROUP, command.call_args.args[0])

    def test_real_powershell_output_and_error_propagation(self):
        self.assertEqual(ssb.powershell("Write-Output 'quiet-success'").stdout.strip(), "quiet-success")
        with self.assertRaisesRegex(RuntimeError, "quiet-test-failure"):
            ssb.powershell("$ErrorActionPreference = 'Stop'; throw 'quiet-test-failure'")


class SaveWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = []
        for name in ("CONFIG_PATH", "STATE_PATH", "INSTALL_STATE_PATH"):
            mocked = patch.object(ssb, name, Path(self.temp.name) / (name + ".json"))
            mocked.start()
            self.paths.append(mocked)
        self.window = ssb.SSBWindow()
        self.window.root.withdraw()

    def tearDown(self):
        self.window.root.destroy()
        for mocked in self.paths:
            mocked.stop()
        self.temp.cleanup()

    def pump_until(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate() and time.monotonic() < deadline:
            self.window.root.update()
            time.sleep(0.01)
        self.assertTrue(predicate())

    def test_save_keeps_ui_alive_blocks_duplicates_and_restores_controls(self):
        release = threading.Event()
        started = threading.Event()
        worker_ids = []
        def save(config, progress):
            worker_ids.append(threading.get_ident())
            progress("Updating website blocks...")
            started.set()
            if not release.wait(3):
                raise RuntimeError("Test worker timed out")
        main_id = threading.get_ident()
        with patch.object(ssb, "install_or_update", side_effect=save) as operation, \
             patch("tkinter.messagebox.askyesno", return_value=True), \
             patch("tkinter.messagebox.showinfo") as success:
            self.window._save()
            try:
                self.pump_until(started.is_set)
                self.assertTrue(self.window.save_button.instate(["disabled"]))
                self.window._save()
                self.window._close()
                self.assertTrue(self.window.root.winfo_exists())
                heartbeat = []
                self.window.root.after(10, lambda: heartbeat.append(True))
                self.pump_until(lambda: bool(heartbeat) and self.window.progress_var.get() == "Updating website blocks...")
                self.assertNotEqual(worker_ids[0], main_id)
                self.assertTrue(self.window.progress_frame.winfo_manager())
            finally:
                release.set()
                self.pump_until(lambda: not self.window._busy)
            operation.assert_called_once()
            success.assert_called_once()
            self.assertFalse(self.window.save_button.instate(["disabled"]))
            self.assertFalse(self.window.progress_frame.winfo_manager())

    def test_failed_save_reports_error_and_allows_retry(self):
        original = copy.deepcopy(self.window.current_config)
        with patch.object(ssb, "install_or_update", side_effect=RuntimeError("Cannot update firewall")), \
             patch("tkinter.messagebox.askyesno", return_value=True), \
             patch("tkinter.messagebox.showerror") as error:
            self.window._save()
            self.pump_until(lambda: not self.window._busy)
        self.assertEqual(error.call_args.args[1], "Cannot update firewall")
        self.assertEqual(self.window.current_config, original)
        self.assertFalse(self.window.save_button.instate(["disabled"]))


class InstallRecoveryTests(unittest.TestCase):
    def exercise_install(self, fail=False):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            directory = Path(temporary)
            installed = directory / "site_blocker.py"
            installed.write_text("# installed copy", encoding="utf-8")
            for name, value in {
                "INSTALL_DIR": directory, "INSTALLED_SCRIPT": installed,
                "CONFIG_PATH": directory / "config.json", "STATE_PATH": directory / "state.json",
                "INSTALL_STATE_PATH": directory / "install-state.json", "__file__": str(installed),
            }.items():
                stack.enter_context(patch.object(ssb, name, value))
            config = ssb.migrate_config(ssb.DEFAULT_CONFIG)
            old_install = {"installation_complete": True, "app_version": "1.0.0",
                           "previous_network_protection": 1, "browser_block_policies": []}
            state = {"blocked": False, "unlock_until": "2026-09-14T22:00:00+00:00"}
            ssb.save_json(ssb.CONFIG_PATH, config)
            ssb.save_json(ssb.INSTALL_STATE_PATH, old_install)
            ssb.save_json(ssb.STATE_PATH, state)
            mocks = {}
            for name in ("require_windows_admin", "configure_logging", "set_browser_policy_entries",
                         "remove_firewall_rules_only", "remove_dynamic_keywords", "create_task",
                         "enforce", "secure_install_directory", "delete_task", "create_firewall_rules"):
                mocks[name] = stack.enter_context(patch.object(ssb, name))
            stack.enter_context(patch.object(ssb, "defender_network_protection", return_value=1))
            stack.enter_context(patch.object(ssb, "prepare_browser_policy_entries", return_value=[]))
            stack.enter_context(patch.object(ssb.logging, "exception"))
            messages = []
            if fail:
                mocks["enforce"].side_effect = RuntimeError("test enforcement failure")
                with self.assertRaisesRegex(RuntimeError, "test enforcement failure"):
                    ssb.install_or_update(config, messages.append)
                self.assertEqual(ssb.load_json(ssb.INSTALL_STATE_PATH, {}), old_install)
                self.assertIn("Save failed; restoring previous settings...", messages)
                self.assertEqual(mocks["create_task"].call_count, 2)
            else:
                ssb.install_or_update(config, messages.append)
                self.assertEqual(ssb.load_json(ssb.INSTALL_STATE_PATH, {})["app_version"], ssb.APP_VERSION)
                self.assertEqual(messages[-1], "Finishing...")
                mocks["enforce"].assert_called_once()
                mocks["remove_dynamic_keywords"].assert_called_once()
            self.assertEqual(ssb.load_json(ssb.CONFIG_PATH, {}), config)
            self.assertEqual(ssb.load_json(ssb.STATE_PATH, {}), state)
            self.assertEqual(installed.read_text(encoding="utf-8"), "# installed copy")
            self.assertFalse((directory / ".site_blocker.py.rollback").exists())

    def test_save_from_installed_copy_preserves_configuration(self):
        self.exercise_install()

    def test_failed_enforcement_restores_previous_installation(self):
        self.exercise_install(fail=True)


if __name__ == "__main__":
    unittest.main()
