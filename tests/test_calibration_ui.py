import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class TestCalibrationUI(unittest.TestCase):
    def setUp(self):
        self.base_html = (BASE_DIR / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        self.calibration_js = (BASE_DIR / "app" / "static" / "js" / "calibration.js").read_text(encoding="utf-8")
        self.dashboard_css = (BASE_DIR / "app" / "static" / "css" / "dashboard.css").read_text(encoding="utf-8")

    def test_confirmation_modal_in_base_html(self):
        """Verify the commission confirmation modal exists in base.html with required elements and warning text."""
        # Confirmation overlay exists and is hidden by default
        self.assertIn('id="commission-confirm-modal-overlay"', self.base_html)
        self.assertIn('class="commission-confirm-overlay"', self.base_html)

        # Exact prompt message required by user
        self.assertIn(
            "Are you sure you want to commission this equation? You cannot revert to the previous calibration equation.",
            self.base_html,
        )

        # Key elements
        self.assertIn('id="confirm-commission-title"', self.base_html)
        self.assertIn('id="confirm-sensor-name"', self.base_html)
        self.assertIn('id="confirm-equation-text"', self.base_html)
        self.assertIn('id="confirm-commission-close-btn"', self.base_html)
        self.assertIn('id="confirm-commission-cancel-btn"', self.base_html)
        self.assertIn('id="confirm-commission-proceed-btn"', self.base_html)

    def test_confirmation_modal_css_styling(self):
        """Verify confirmation modal styles in dashboard.css have proper stacking above calibration modal."""
        self.assertIn(".commission-confirm-overlay", self.dashboard_css)
        self.assertIn(".commission-confirm-card", self.dashboard_css)
        self.assertIn(".btn-confirm-cancel", self.dashboard_css)
        self.assertIn(".btn-confirm-proceed", self.dashboard_css)
        # z-index must be higher than calibration modal (9998)
        self.assertIn("z-index: 10001", self.dashboard_css)

    def test_actual_reading_is_typable_in_calibration_js(self):
        """Verify actual sensor/voltage reading input is typable (no disabled attribute, number type)."""
        # Ensure disabled attribute is not present on actual-input
        self.assertNotIn('class="point-input actual-input" placeholder="-- ${config.inputUnit}" value="${actualVal !== undefined && actualVal !== null && actualVal !== "" ? actualVal : ""}" disabled', self.calibration_js)
        # Ensure it is type="number" step="any" and has class point-input actual-input
        self.assertIn('<input type="number" step="any" class="point-input actual-input"', self.calibration_js)
        # Ensure it has an input event listener so user typing invalidates pending calculation
        self.assertIn('actualInput.addEventListener("input"', self.calibration_js)

    def test_commission_triggers_confirmation_popup(self):
        """Verify commission button triggers confirmation dialog and confirm button executes commission."""
        self.assertIn("function requestCommission()", self.calibration_js)
        self.assertIn("function closeConfirmCommissionModal()", self.calibration_js)
        self.assertIn("function onConfirmCommissionProceed()", self.calibration_js)
        # Commission button invokes requestCommission instead of directly executing
        self.assertIn('commissionBtn.addEventListener("click", requestCommission)', self.calibration_js)
        # Proceed button in confirmation dialog calls onConfirmCommissionProceed
        self.assertIn('confirmProceedBtn.addEventListener("click", onConfirmCommissionProceed)', self.calibration_js)


if __name__ == "__main__":
    unittest.main()
