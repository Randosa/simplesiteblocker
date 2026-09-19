import unittest
from unittest.mock import Mock

from ssb.ui import SSBWindow


class StatusRefreshTests(unittest.TestCase):
    def window(self, busy=False):
        window = SSBWindow.__new__(SSBWindow)
        window._busy = busy
        window.root = Mock()
        window.status_var = Mock()
        window._refresh_status = Mock()
        return window

    def test_refreshes_while_window_remains_open_and_schedules_next_tick(self):
        window = self.window()
        window._poll_status()
        window._refresh_status.assert_called_once()
        window.root.after.assert_called_once_with(1000, window._poll_status)

    def test_does_not_read_partially_saved_state_during_save(self):
        window = self.window(busy=True)
        window._poll_status()
        window._refresh_status.assert_not_called()
        window.root.after.assert_called_once_with(1000, window._poll_status)

    def test_transient_read_failure_does_not_stop_future_refreshes(self):
        window = self.window()
        window._refresh_status.side_effect = OSError('Temporarily unavailable')
        window._poll_status()
        self.assertIn('Status unavailable', window.status_var.set.call_args.args[0])
        window.root.after.assert_called_once_with(1000, window._poll_status)

    def test_click_refreshes_immediately_without_starting_another_timer(self):
        window = self.window()
        window._refresh_status_clicked()
        window._refresh_status.assert_called_once()
        window.root.after.assert_not_called()

    def test_click_during_save_does_not_read_partially_saved_state(self):
        window = self.window(busy=True)
        window._refresh_status_clicked()
        window._refresh_status.assert_not_called()


if __name__ == '__main__':
    unittest.main()
