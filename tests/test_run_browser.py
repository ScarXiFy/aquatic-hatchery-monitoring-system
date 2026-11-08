import os
import sys
import unittest
from unittest.mock import MagicMock, call, patch

import run


class TestRunBrowser(unittest.TestCase):
    def setUp(self):
        self.original_argv = list(sys.argv)
        self.original_env = os.environ.copy()

    def tearDown(self):
        sys.argv = list(self.original_argv)
        os.environ.clear()
        os.environ.update(self.original_env)

    @patch("run.urllib.request.urlopen")
    def test_wait_for_server_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = run.wait_for_server("http://localhost:5050", timeout=2)
        self.assertTrue(result)
        mock_urlopen.assert_called_once()

    @patch("run.time.sleep")
    @patch("run.urllib.request.urlopen")
    def test_wait_for_server_retry_then_success(self, mock_urlopen, mock_sleep):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_enter = MagicMock(return_value=mock_resp)

        # First call raises an exception, second call succeeds
        mock_urlopen.side_effect = [Exception("Connection refused"), MagicMock(__enter__=mock_enter)]

        result = run.wait_for_server("http://localhost:5050", timeout=5)
        self.assertTrue(result)
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once_with(0.5)

    @patch("run.time.sleep")
    @patch("run.urllib.request.urlopen", side_effect=Exception("Connection refused"))
    def test_wait_for_server_timeout(self, mock_urlopen, mock_sleep):
        result = run.wait_for_server("http://localhost:5050", timeout=0.1)
        self.assertFalse(result)

    @patch("run.wait_for_server", return_value=True)
    @patch("run.shutil.which", return_value="/usr/bin/chromium-browser")
    @patch("run.sys.platform", "linux")
    @patch("run.subprocess.Popen")
    def test_open_browser_chromium_on_linux(self, mock_popen, mock_which, mock_wait):
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            run.open_browser("http://localhost:5050")

        mock_wait.assert_called_once_with("http://localhost:5050", timeout=15)
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        self.assertEqual(cmd[0], "/usr/bin/chromium-browser")
        self.assertIn("--start-maximized", cmd)
        self.assertIn("http://localhost:5050", cmd)
        self.assertTrue(mock_popen.call_args[1].get("start_new_session"))

    @patch("run.wait_for_server", return_value=True)
    @patch("run.shutil.which", return_value="/usr/bin/chromium")
    @patch("run.sys.platform", "linux")
    @patch("run.subprocess.Popen")
    def test_open_browser_kiosk_mode(self, mock_popen, mock_which, mock_wait):
        sys.argv.append("--kiosk")
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            run.open_browser("http://localhost:5050")

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        self.assertIn("--kiosk", cmd)
        self.assertNotIn("--start-maximized", cmd)

    @patch("run.wait_for_server", return_value=True)
    @patch("run.shutil.which", return_value="/usr/bin/chromium-browser")
    @patch("run.sys.platform", "linux")
    @patch("run.os.geteuid", return_value=0)
    @patch("run.subprocess.Popen")
    def test_open_browser_root_adds_no_sandbox(self, mock_popen, mock_geteuid, mock_which, mock_wait):
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            run.open_browser("http://localhost:5050")

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        self.assertIn("--no-sandbox", cmd)

    @patch("run.wait_for_server", return_value=True)
    @patch("run.shutil.which", return_value=None)
    @patch("run.webbrowser.open")
    def test_open_browser_fallback_webbrowser(self, mock_webbrowser_open, mock_which, mock_wait):
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            run.open_browser("http://localhost:5050")

        mock_webbrowser_open.assert_called_once_with("http://localhost:5050")

    @patch("run.threading.Thread")
    def test_start_browser_thread_enabled(self, mock_thread_cls):
        mock_instance = MagicMock()
        mock_thread_cls.return_value = mock_instance

        thread = run.start_browser_thread("http://localhost:5050")

        self.assertIsNotNone(thread)
        mock_thread_cls.assert_called_once_with(
            target=run.open_browser,
            args=("http://localhost:5050",),
            daemon=True,
        )
        mock_instance.start.assert_called_once()

    @patch("run.threading.Thread")
    def test_start_browser_thread_disabled_via_flag(self, mock_thread_cls):
        sys.argv.append("--no-browser")
        thread = run.start_browser_thread("http://localhost:5050")
        self.assertIsNone(thread)
        mock_thread_cls.assert_not_called()

    @patch("run.threading.Thread")
    def test_start_browser_thread_disabled_via_env(self, mock_thread_cls):
        with patch.dict(os.environ, {"NO_BROWSER": "1"}):
            thread = run.start_browser_thread("http://localhost:5050")
            self.assertIsNone(thread)
            mock_thread_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
