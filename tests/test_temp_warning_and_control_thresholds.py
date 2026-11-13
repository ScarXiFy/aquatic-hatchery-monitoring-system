import unittest
from pathlib import Path
import re

import app.api.piUtils as piUtils
import run


class TestTempWarningAndControlThresholds(unittest.TestCase):
    def test_temperature_control_trigger_setpoint_tolerance_kept_at_half_degree(self):
        """
        Verify that temperature control trigger setpoints remain strictly at 0.5 degrees
        as required by user specification.
        """
        self.assertEqual(piUtils.TEMP_TOLERANCE, 0.5)

    def test_dashboard_js_temperature_warning_limit_is_one_degree(self):
        """
        Verify that in dashboard.js, warning indicator appears at +-1.0 degree
        for temperature from setpoint, while keeping DO at +-0.5.
        """
        dashboard_js_path = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "dashboard.js"
        content = dashboard_js_path.read_text()
        
        # Check that warning_limit uses 1.0 for temperature and 0.5 for others (DO)
        pattern = re.compile(r"metric\s*===\s*[\"']temperature[\"']\s*\?\s*1\.0\s*:\s*0\.5")
        self.assertTrue(
            pattern.search(content),
            "dashboard.js must set warning_limit to 1.0 for temperature and 0.5 for DO"
        )

    def test_charts_js_temperature_warning_limit_is_one_degree(self):
        """
        Verify that in charts.js, warning bands and condition check appear at +-1.0 degree
        for temperature from setpoint.
        """
        charts_js_path = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "charts.js"
        content = charts_js_path.read_text()

        matches = re.findall(r"metric\s*===\s*[\"']temperature[\"']\s*\?\s*1\.0\s*:\s*0\.5", content)
        self.assertGreaterEqual(
            len(matches),
            2,
            "charts.js must set warning_limit to 1.0 for temperature in both threshold band and conditionFor"
        )

    def test_socket_js_temperature_warning_limit_is_one_degree(self):
        """
        Verify that in socket.js, warning check uses 1.0 for temperature.
        """
        socket_js_path = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "socket.js"
        content = socket_js_path.read_text()

        self.assertIn('metric === "temperature" ? 1.0 : 0.5', content)

    def test_run_py_fullscreen_and_maximized_flags(self):
        """
        Verify that run.py includes both --start-maximized and --start-fullscreen
        when launching the browser.
        """
        run_py_path = Path(__file__).resolve().parent.parent / "run.py"
        content = run_py_path.read_text()

        self.assertIn('cmd.append("--start-maximized")', content)
        self.assertIn('cmd.append("--start-fullscreen")', content)

    def test_salinity_freshwater_zero_threshold_in_dashboard_and_charts(self):
        """
        Verify that dashboard.js and charts.js treat minimum salinity of 0 (freshwater)
        as optimal near zero rather than triggering a lower buffer warning.
        """
        dashboard_js_path = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "dashboard.js"
        dash_content = dashboard_js_path.read_text()
        self.assertIn("metric === \"salinity\" && min <= 0", dash_content)

        charts_js_path = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "charts.js"
        charts_content = charts_js_path.read_text()
        self.assertIn("metric === \"salinity\" && min <= 0", charts_content)

    def test_base_html_has_responsive_brand_and_notification_btn(self):
        """
        Verify that base.html includes responsive brand titles and notification bell.
        """
        base_html_path = Path(__file__).resolve().parent.parent / "app" / "templates" / "base.html"
        content = base_html_path.read_text()

        self.assertIn('class="brand-full"', content)
        self.assertIn('class="brand-short"', content)
        self.assertIn('id="notification-bell-btn"', content)


if __name__ == "__main__":
    unittest.main()
