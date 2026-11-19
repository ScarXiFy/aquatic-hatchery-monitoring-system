import unittest
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import app.api.piUtils as piUtils
import app.models as models


class TestMotorValveStateAndDirections(unittest.TestCase):
    def setUp(self):
        # Create an in-memory SQLite DB for models testing
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._orig_get_db = models.get_db
        models.get_db = lambda: self.conn

    def tearDown(self):
        models.get_db = self._orig_get_db
        self.conn.close()

    def test_init_motor_state_defaults(self):
        """
        Verify that init_motor_state stores initial defaults:
        - closed (0) for heating_valve
        - open (1) for cooling_valve
        - 0 for bleed_valve
        """
        models.init_motor_state()
        bleed_pos = models.load_motor_state("bleed_valve")
        heat_pos = models.load_motor_state("heating_valve")
        cool_pos = models.load_motor_state("cooling_valve")

        self.assertEqual(bleed_pos, 0)
        self.assertEqual(heat_pos, 0, "Heating valve must initially be closed (0)")
        self.assertEqual(cool_pos, 1, "Cooling valve must initially be open (1)")

    def test_initialize_valve_motor_states_restores_without_movement(self):
        """
        Verify initializeValveMotorStates restores saved states from DB.
        """
        models.init_motor_state()
        # Verify initial defaults are restored
        piUtils.initializeValveMotorStates()
        self.assertTrue(piUtils.coolingValveOpen, "Cooling valve should be initialized to OPEN (1)")
        self.assertFalse(piUtils.heatingValveOpen, "Heating valve should be initialized to CLOSED (0)")

        # Change DB states to opposite
        models.save_motor_state("cooling_valve", 0)
        models.save_motor_state("heating_valve", 1)
        piUtils.initializeValveMotorStates()
        self.assertFalse(piUtils.coolingValveOpen)
        self.assertTrue(piUtils.heatingValveOpen)

    def test_valve_movement_directions_cw_closes_ccw_opens(self):
        """
        Verify that:
        - clockwise (CW) is used to close
        - counterclockwise (CCW) is used to open
        - Redundant movement is skipped when already in desired state
        """
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        # Start with cooling open and heating closed
        piUtils.coolingValveOpen = True
        piUtils.heatingValveOpen = False

        with patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "GPIO", mock_gpio), \
             patch.object(piUtils, "CW", 1), \
             patch.object(piUtils, "CCW", 0), \
             patch("time.sleep", return_value=None):

            # Calling coolingValve(True) when already open does NOT step motor
            piUtils.setRpiCoolingValve(True)
            self.assertEqual(len(mock_gpio.output.call_args_list), 0)

            # Calling coolingValve(False) steps CW (close)
            piUtils.setRpiCoolingValve(False)
            self.assertFalse(piUtils.coolingValveOpen)
            mock_gpio.output.assert_any_call(piUtils.coolValveDirPin, 1)  # CW to close

            # Calling heatingValve(False) when already closed does NOT step motor
            mock_gpio.reset_mock()
            piUtils.setRpiHeatingValve(False)
            self.assertEqual(len(mock_gpio.output.call_args_list), 0)

            # Calling heatingValve(True) steps CCW (open)
            piUtils.setRpiHeatingValve(True)
            self.assertTrue(piUtils.heatingValveOpen)
            mock_gpio.output.assert_any_call(piUtils.heatValveDirPin, 0)  # CCW to open


class TestWeekGraphNoReadingDisplay(unittest.TestCase):
    def test_week_graph_missing_days_return_null(self):
        """
        Verify that buildWeekPoints returns null for days with no readings,
        and does NOT repeat current day's reading on previous days.
        """
        charts_js_path = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "charts.js"
        node_script = """
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const code = fs.readFileSync(process.argv[1], "utf8");

const mockDoc = {
  querySelectorAll: () => [],
  querySelector: () => null,
  getElementById: () => null,
  addEventListener: () => {}
};

const mockWindow = {
  HATCHERY_METRICS: {
    temperature: { label: "Temperature", unit: "°C", decimals: 1 },
    dissolved_oxygen: { label: "Dissolved Oxygen", unit: "mg/L", decimals: 1 },
    salinity: { label: "Salinity", unit: "ppt", decimals: 1 },
    ph: { label: "pH", unit: "pH", decimals: 2 }
  },
  addEventListener: () => {}
};

const context = vm.createContext({
  window: mockWindow,
  document: mockDoc,
  Chart: { register: () => {} },
  console: console,
  Date: Date,
  Math: Math,
  Number: Number,
  Array: Array,
  Map: Map,
  String: String
});

vm.runInContext(code, context);

// Test buildWeekPoints behavior when only TODAY has readings:
// 6 previous days should have null, today should have the reading.
const now = new Date();
const todayReading = {
  id: 1,
  timestamp: now.toISOString(),
  temperature: 26.5,
  dissolved_oxygen: 7.4,
  salinity: 30.0,
  ph: 8.1
};

const points = context.window.HatcheryCharts.buildWeekPoints([todayReading]);
assert.strictEqual(points.length, 7, "Week points must have exactly 7 daily buckets");

// The first 6 days must be null
for (let i = 0; i < 6; i++) {
  assert.strictEqual(points[i].temperature, null, `Day index ${i} must have null temperature`);
  assert.strictEqual(points[i].dissolved_oxygen, null, `Day index ${i} must have null DO`);
  assert.strictEqual(points[i].salinity, null, `Day index ${i} must have null salinity`);
  assert.strictEqual(points[i].ph, null, `Day index ${i} must have null pH`);
}

// Today (last day) must have the reading
assert.strictEqual(points[6].temperature, 26.5, "Today must match today's temperature reading");
assert.strictEqual(points[6].dissolved_oxygen, 7.4, "Today must match today's DO reading");

console.log("WEEK_GRAPH_NULL_TEST_PASSED");
"""
        proc = subprocess.run(
            ["node", "-e", node_script, str(charts_js_path)],
            capture_output=True,
            text=True
        )
        self.assertEqual(proc.returncode, 0, f"Node test failed: {proc.stderr}\n{proc.stdout}")
        self.assertIn("WEEK_GRAPH_NULL_TEST_PASSED", proc.stdout)


class TestAllowedAdjustingTime(unittest.TestCase):
    def test_adjusting_persists_without_timer_until_setpoint_reached(self):
        """
        Verify that there is no timer to stop adjusting status:
        DO and temperature remain adjusting indefinitely without expiring after 15 minutes,
        and critical/warning alarms are suppressed until the setpoint is reached.
        Once the setpoint is reached, subsequent deviation can trigger an alarm.
        """
        dashboard_js_path = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "dashboard.js"
        node_script = """
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const code = fs.readFileSync(process.argv[1], "utf8");

let simulatedNow = 1000000;
class MockDate {
  constructor() { this.t = simulatedNow; }
  static now() { return simulatedNow; }
  getTime() { return this.t; }
}

const windowMock = {
  HATCHERY_METRICS: {
    temperature: { unit: "°C", min: 15, max: 35 },
    dissolved_oxygen: { unit: "mg/L", min: 0, max: 20 }
  },
  HatcherySound: { updateAlarmState: () => {}, stopAllAudio: () => {} },
  dispatchEvent: () => {},
  addEventListener: () => {},
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};

const documentMock = {
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {}
};

const context = vm.createContext({
  window: windowMock,
  document: documentMock,
  localStorage: windowMock.localStorage,
  CustomEvent: function() {},
  console: console,
  Date: MockDate,
  Math: Math,
  Number: Number,
  Array: Array,
  Map: Map,
  JSON: JSON,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout
});

vm.runInContext(code, context);

const { recordSetpointChange, isRateOfChangeAdjusting, setpointTrackers } = context.window.HatcheryRateOfChange;

// Test temperature adjusting: setpoint 28.0, reading 26.0
recordSetpointChange("temperature", 28.0);
isRateOfChangeAdjusting("temperature", 26.0);

// Advance 14 minutes: still adjusting
simulatedNow += 14 * 60 * 1000;
assert.strictEqual(isRateOfChangeAdjusting("temperature", 26.5), true, "At 14 mins, adjusting should still be active");

// Advance past 15 minutes (e.g. 20 minutes, 60 minutes): still adjusting because setpoint has not been reached!
simulatedNow += 6 * 60 * 1000; // 20 mins total
assert.strictEqual(isRateOfChangeAdjusting("temperature", 26.5), true, "At 20 mins, adjusting must NOT time out");

simulatedNow += 40 * 60 * 1000; // 60 mins total
assert.strictEqual(isRateOfChangeAdjusting("temperature", 26.5), true, "At 60 mins, adjusting must NOT time out");
assert.strictEqual(setpointTrackers.temperature.active, true, "Tracker active flag must remain true");

// Test DO adjusting: setpoint 8.5, reading 7.2
recordSetpointChange("dissolved_oxygen", 8.5);
isRateOfChangeAdjusting("dissolved_oxygen", 7.2);
simulatedNow += 30 * 60 * 1000; // 30 mins later
assert.strictEqual(isRateOfChangeAdjusting("dissolved_oxygen", 7.6), true, "DO adjusting must NOT time out after 30 mins");
assert.strictEqual(setpointTrackers.dissolved_oxygen.active, true, "DO tracker active flag must remain true");

// Test system startup mode: remains adjusting without timing out
const { syncSetpointTimes } = context.window.HatcheryRateOfChange;
const bootTime = simulatedNow;
syncSetpointTimes({ temperature: bootTime, dissolved_oxygen: bootTime }, { temperature: true, dissolved_oxygen: true });

assert.strictEqual(setpointTrackers.temperature.active, true, "Startup tracker must be active");
assert.strictEqual(setpointTrackers.temperature.isSystemStart, true, "Startup flag must be true");

// Feed identical readings: does not abort
for (let i = 0; i < 20; i++) {
  simulatedNow += 3000;
  assert.strictEqual(isRateOfChangeAdjusting("temperature", 24.0), true, "Startup adjusting must NOT abort for stalled readings");
}

// 60 minutes later: still within adjusting mode
simulatedNow = bootTime + (60 * 60 * 1000);
assert.strictEqual(isRateOfChangeAdjusting("temperature", 24.0), true, "Startup adjusting must remain active indefinitely until setpoint reached");

// Now reaching setpoint (< 1.0 diff, e.g. 27.5 for setpoint 28.0)
let tempReached = isRateOfChangeAdjusting("temperature", 27.5);
assert.strictEqual(tempReached, false, "Should complete adjusting once setpoint is reached");
assert.strictEqual(setpointTrackers.temperature.active, false, "Tracker should become inactive once reached");

// Once reached, a subsequent deviation from setpoint allows alarm to trigger
let tempDeviated = isRateOfChangeAdjusting("temperature", 24.0);
assert.strictEqual(tempDeviated, false, "Subsequent deviation must not be adjusting, allowing alarm to trigger");

console.log("ADJUSTING_PERSISTS_WITHOUT_TIMER_TEST_PASSED");
"""
        proc = subprocess.run(
            ["node", "-e", node_script, str(dashboard_js_path)],
            capture_output=True,
            text=True
        )
        self.assertEqual(proc.returncode, 0, f"Node test failed: {proc.stderr}\n{proc.stdout}")
        self.assertIn("ADJUSTING_PERSISTS_WITHOUT_TIMER_TEST_PASSED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
