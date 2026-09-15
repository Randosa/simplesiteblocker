import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
import winreg

spec = importlib.util.spec_from_file_location("personal_ssb", Path(__file__).parents[1] / "site_blocker.py")
ssb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ssb)


class FirefoxPolicyTests(unittest.TestCase):
    def test_fresh_firefox_list_is_array_with_no_changed_chromium_numbering(self):
        with patch.object(winreg, "OpenKey", side_effect=FileNotFoundError), \
             patch.object(ssb, "registry_get", return_value={"exists": False}):
            entries = ssb.prepare_browser_policy_entries(ssb.DEFAULT_CONFIG)
        firefox = [entry for entry in entries if entry["path"] == ssb.FIREFOX_BLOCK_PATH]
        self.assertEqual([entry["name"] for entry in firefox], [str(i) for i in range(1, 15)])
        self.assertTrue(all(int(entry["name"]) >= 9000 for entry in entries if entry not in firefox))

    def test_existing_valid_list_is_preserved_and_appended(self):
        key = MagicMock()
        with patch.object(winreg, "OpenKey", return_value=key), \
             patch.object(winreg, "EnumValue", side_effect=[("1", "https://other.example/*", 1), OSError()]), \
             patch.object(ssb, "browser_policy_values", return_value=[(ssb.FIREFOX_BLOCK_PATH, "*://example.com/*")]), \
             patch.object(ssb, "registry_get", return_value={"exists": False}):
            entries = ssb.prepare_browser_policy_entries(ssb.DEFAULT_CONFIG)
        self.assertEqual(entries[0]["name"], "2")

    def test_foreign_malformed_list_is_not_overwritten(self):
        with self.assertRaisesRegex(ValueError, "first registry value"):
            ssb.policy_entry_start(ssb.FIREFOX_BLOCK_PATH, ["9000", "1"])

    def test_disable_prunes_empty_keys_so_no_object_remains(self):
        with patch.object(winreg, "OpenKey", return_value=MagicMock()), \
             patch.object(winreg, "QueryInfoKey", return_value=(0, 0, 0)), \
             patch.object(winreg, "DeleteKey") as delete:
            ssb.set_browser_policy_entries(False, [])
        self.assertEqual([call.args[1] for call in delete.call_args_list],
                         [ssb.FIREFOX_BLOCK_PATH, ssb.FIREFOX_BLOCK_PATH.rsplit("\\", 1)[0]])

    def test_does_not_remove_other_policy_values_or_children(self):
        with patch.object(winreg, "OpenKey", return_value=MagicMock()), \
             patch.object(winreg, "QueryInfoKey", side_effect=[(0, 1, 0), (1, 0, 0)]), \
             patch.object(winreg, "DeleteKey") as delete:
            ssb.prune_empty_firefox_policy_keys()
        delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
