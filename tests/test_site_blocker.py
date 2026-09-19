import datetime as dt
import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


import sys
sys.path.insert(0, str(Path(__file__).parents[1]))
from ssb import core as ssb
from ssb.ui import SSBWindow
ssb.SSBWindow = SSBWindow


class TimeTests(unittest.TestCase):
    def setUp(self):
        self.start = dt.time(21, 0)
        self.end = dt.time(3, 0)

    def test_before_start_is_open(self):
        self.assertFalse(ssb.in_block_window(dt.datetime(2026, 9, 13, 20, 59), self.start, self.end))

    def test_start_is_blocked(self):
        self.assertTrue(ssb.in_block_window(dt.datetime(2026, 9, 13, 21, 0), self.start, self.end))

    def test_after_midnight_is_blocked(self):
        self.assertTrue(ssb.in_block_window(dt.datetime(2026, 9, 14, 2, 59), self.start, self.end))

    def test_end_is_open(self):
        self.assertFalse(ssb.in_block_window(dt.datetime(2026, 9, 14, 3, 0), self.start, self.end))

    def test_same_start_and_end_means_all_day(self):
        self.assertTrue(ssb.in_block_window(dt.datetime(2026, 9, 14, 12, 0), dt.time(9), dt.time(9)))

    def test_exception_never_extends_past_end(self):
        zone = dt.timezone(dt.timedelta(hours=-4))
        now = dt.datetime(2026, 9, 13, 22, 0, tzinfo=zone)
        self.assertEqual(ssb.next_block_end(now, self.start, self.end), dt.datetime(2026, 9, 14, 3, 0, tzinfo=zone))

    def test_changed_config_requires_confirmation_during_old_block(self):
        old = {"block_start": "21:00", "block_end": "03:00"}
        new = {"block_start": "22:00", "block_end": "02:00"}
        now = dt.datetime(2026, 9, 13, 21, 30)
        self.assertTrue(ssb.requires_blocked_period_confirmation(now, old, new, True))

    def test_proposed_block_also_requires_confirmation(self):
        old = {"block_start": "21:00", "block_end": "03:00"}
        new = {"block_start": "12:00", "block_end": "18:00"}
        now = dt.datetime(2026, 9, 13, 15, 0)
        self.assertTrue(ssb.requires_blocked_period_confirmation(now, old, new, True))

    def test_unchanged_config_needs_no_extra_confirmation(self):
        config = {"block_start": "21:00", "block_end": "03:00"}
        now = dt.datetime(2026, 9, 13, 22, 0)
        self.assertFalse(ssb.requires_blocked_period_confirmation(now, config, config, False))


class HostnameTests(unittest.TestCase):
    def test_normalizes_url(self):
        self.assertEqual(ssb.normalize_hostname("https://WWW.YouTube.com/watch?v=1"), "www.youtube.com")

    def test_normalizes_plain_hostname_and_path(self):
        self.assertEqual(ssb.normalize_hostname("instagram.com/explore"), "instagram.com")

    def test_rejects_ip_address(self):
        with self.assertRaises(ValueError):
            ssb.normalize_hostname("127.0.0.1")

    def test_rejects_local_name(self):
        with self.assertRaises(ValueError):
            ssb.normalize_hostname("localhost")

    def test_duplicate_prefers_subdomain_coverage(self):
        sites = ssb.normalized_sites([
            {"hostname": "example.com", "include_subdomains": False},
            {"hostname": "EXAMPLE.com", "include_subdomains": True},
        ])
        self.assertEqual(sites, [{"hostname": "example.com", "include_subdomains": True}])


class ConfigurationTests(unittest.TestCase):
    def test_migrates_legacy_domain_pairs(self):
        migrated = ssb.migrate_config({
            "block_start": "21:00",
            "block_end": "03:00",
            "domains": ["example.com", "*.example.com", "exact.test"],
        })
        self.assertEqual(migrated["sites"], [
            {"hostname": "exact.test", "include_subdomains": False},
            {"hostname": "example.com", "include_subdomains": True},
        ])

    def test_domain_patterns_honor_checkbox(self):
        config = ssb.migrate_config({
            **ssb.DEFAULT_CONFIG,
            "sites": [
                {"hostname": "example.com", "include_subdomains": True},
                {"hostname": "exact.test", "include_subdomains": False},
            ],
        })
        self.assertEqual(ssb.domain_patterns(config), ["exact.test", "example.com", "*.example.com"])

    def test_browser_patterns_cover_supported_browsers(self):
        config = ssb.migrate_config({
            **ssb.DEFAULT_CONFIG,
            "sites": [{"hostname": "example.com", "include_subdomains": True}],
        })
        values = ssb.browser_policy_values(config)
        patterns = {value for _, value in values}
        self.assertIn("[*.]example.com", patterns)
        self.assertIn("*://example.com/*", patterns)
        self.assertIn("*://*.example.com/*", patterns)

    def test_task_xml_contains_exact_boundaries(self):
        config = ssb.migrate_config(ssb.DEFAULT_CONFIG)
        xml = ssb.task_xml(Path(r"C:\Python\pythonw.exe"), config)
        ET.fromstring(xml)
        self.assertIn("T21:00:00", xml)
        self.assertIn("T03:00:00", xml)

    def test_firewall_ids_are_stable_and_unique(self):
        self.assertEqual(ssb.keyword_id("example.com"), ssb.keyword_id("example.com"))
        self.assertNotEqual(ssb.keyword_id("example.com"), ssb.keyword_id("other.test"))


if __name__ == "__main__":
    unittest.main()
