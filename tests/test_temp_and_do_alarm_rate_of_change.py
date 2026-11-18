import unittest
import subprocess
from pathlib import Path


class TestTempAndDoAlarmRateOfChange(unittest.TestCase):
    def setUp(self):
        self.dashboard_js_path = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "dashboard.js"
        self.content = self.dashboard_js_path.read_text()

    def test_dashboard_js_defines_rate_of_change_functions(self):
        """
        Verify that dashboard.js defines setpoint rate of change tracking:
        setpointTrackers, recordSetpointChange, and isRateOfChangeAdjusting.
        """
        self.assertIn("setpointTrackers", self.content)
        self.assertIn("function recordSetpointChange", self.content)
        self.assertIn("function isRateOfChangeAdjusting", self.content)
        self.assertIn("window.HatcheryRateOfChange", self.content)

    def test_check_alert_triggers_uses_rate_of_change_adjusting(self):
        """
        Verify that checkAlertTriggers uses isRateOfChangeAdjusting to suppress
        temperature and DO alarms right away when setpoint is changed.
        """
        self.assertIn("isRateOfChangeAdjusting(metric, val)", self.content)
        self.assertIn('previousMetricStates[metric] = "adjusting"', self.content)

    def test_update_gauge_and_nav_status_handle_adjusting_state(self):
        """
        Verify that updateGauge and updateNavStatus recognize the adjusting state
        and set badge to 'Adjusting' with calm blue color rather than sounding alarm.
        """
        self.assertIn('state = "adjusting"', self.content)
        self.assertIn('element.textContent = "Adjusting"', self.content)
        self.assertIn('indicator.lastChild.textContent = " Adjusting"', self.content)

    def test_node_rate_of_change_behavior_suite(self):
        """
        Execute full behavioral test suite using Node.js to test:
        1. Alarm is not triggered right away when temperature setpoint changes.
        2. Progress towards setpoint keeps alarm suppressed.
        3. Target reached completes transition and restores optimal state.
        4. Wrong direction triggers alarm after observation.
        5. Stalled condition (no progress) triggers alarm.
        6. Same behavior applies to Dissolved Oxygen (DO).
        """
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
const { setpointTrackers, recordSetpointChange, isRateOfChangeAdjusting } = context.window.HatcheryRateOfChange;

// 1. Initial setpoint change for Temperature (26 -> 28 °C)
recordSetpointChange("temperature", 28.0);
assert.strictEqual(setpointTrackers.temperature.active, true, "Temperature tracker should be active");
assert.strictEqual(setpointTrackers.temperature.targetSetpoint, 28.0, "Target setpoint should be 28.0");

// 2. Initial reading does NOT trigger alarm right away
assert.strictEqual(isRateOfChangeAdjusting("temperature", 26.0), true, "Initial reading must suppress alarm");

// 3. Positive rate of change towards setpoint continues suppressing alarm
for (let val of [26.15, 26.30, 26.48, 26.65, 26.85]) {
  simulatedNow += 3000;
  assert.strictEqual(isRateOfChangeAdjusting("temperature", val), true, `Alarm must stay suppressed at ${val}°C`);
}

// 4. Target reached (< 1.0 diff from 28.0, e.g. 27.05°C)
simulatedNow += 3000;
let reachedResult = isRateOfChangeAdjusting("temperature", 27.05);
assert.strictEqual(reachedResult, false, "Should complete adjusting once target band is reached");
assert.strictEqual(setpointTrackers.temperature.active, false, "Tracker should be inactive once reached");

// 5. Wrong direction triggers alarm
recordSetpointChange("temperature", 29.0);
isRateOfChangeAdjusting("temperature", 27.2);
simulatedNow += 16000;
for (let val of [27.0, 26.7, 26.4, 26.1, 25.8, 25.5]) {
  simulatedNow += 3000;
  isRateOfChangeAdjusting("temperature", val);
}
assert.strictEqual(setpointTrackers.temperature.active, false, "Tracker must fail on reverse drift");

// 6. Stalled progress triggers alarm
recordSetpointChange("temperature", 29.0);
isRateOfChangeAdjusting("temperature", 26.0);
simulatedNow += 16000;
for (let i = 0; i < 15; i++) {
  simulatedNow += 3000;
  isRateOfChangeAdjusting("temperature", 26.0);
}
assert.strictEqual(setpointTrackers.temperature.active, false, "Tracker must fail on stalled progress");

// 7. Dissolved Oxygen (DO) setpoint change (7.2 -> 8.5 mg/L)
recordSetpointChange("dissolved_oxygen", 8.5);
assert.strictEqual(setpointTrackers.dissolved_oxygen.active, true, "DO tracker should be active");
assert.strictEqual(isRateOfChangeAdjusting("dissolved_oxygen", 7.2), true, "DO alarm must not trigger right away");
for (let val of [7.35, 7.55, 7.75, 7.95]) {
  simulatedNow += 3000;
  assert.strictEqual(isRateOfChangeAdjusting("dissolved_oxygen", val), true, `DO alarm must stay suppressed at ${val} mg/L`);
}
// Target reached (< 0.5 diff from 8.5)
simulatedNow += 3000;
let doReached = isRateOfChangeAdjusting("dissolved_oxygen", 8.1);
assert.strictEqual(doReached, false, "DO should complete adjusting once within 0.5 mg/L");
assert.strictEqual(setpointTrackers.dissolved_oxygen.active, false, "DO tracker should be inactive once reached");

console.log("ALL_BEHAVIORAL_TESTS_PASSED");
        """
        proc = subprocess.run(
            ["node", "-e", node_script, str(self.dashboard_js_path)],
            capture_output=True,
            text=True
        )
        self.assertEqual(proc.returncode, 0, f"Node tests failed: {proc.stderr}\n{proc.stdout}")
        self.assertIn("ALL_BEHAVIORAL_TESTS_PASSED", proc.stdout)

    def test_page_switch_from_home_to_view_graphs_preserves_adjusting_state_and_suppresses_alert(self):
        """
        Verify that when temperature setpoint is changed on the home page and is adjusting,
        switching to the view graph page (/graph) preserves the rate of change observation
        in storage and does NOT pop up the alert modal or play the alarm sound.
        """
        node_script = """
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const code = fs.readFileSync(process.argv[1], "utf8");

// Shared storage simulating browser across page transitions
const sharedSessionStorage = new Map();
const sharedLocalStorage = new Map();

function createStorage(map) {
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k)
  };
}

let simulatedNow = 1000000;
class MockDate {
  constructor() { this.t = simulatedNow; }
  static now() { return simulatedNow; }
  getTime() { return this.t; }
}

// -------------------------------------------------------------
// 1. User on Home Page (/): changes temp setpoint 26 -> 28 °C
// -------------------------------------------------------------
const homeDoc = {
  getElementById: (id) => {
    if (id === "alert-modal-overlay") return { hidden: true };
    return null;
  },
  querySelector: (sel) => {
    if (sel === ".alert-modal-card") return { setAttribute: () => {} };
    return null;
  },
  querySelectorAll: () => [],
  addEventListener: () => {}
};

const homeWindow = {
  HATCHERY_METRICS: {
    temperature: { unit: "°C", min: 15, max: 35 },
    dissolved_oxygen: { unit: "mg/L", min: 0, max: 20 }
  },
  HatcherySound: { updateAlarmState: () => {}, stopAllAudio: () => {} },
  dispatchEvent: () => {},
  addEventListener: () => {},
  sessionStorage: createStorage(sharedSessionStorage),
  localStorage: createStorage(sharedLocalStorage)
};

const homeContext = vm.createContext({
  window: homeWindow,
  document: homeDoc,
  sessionStorage: homeWindow.sessionStorage,
  localStorage: homeWindow.localStorage,
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

vm.runInContext(code, homeContext);

// User changes temperature setpoint to 28.0 on Home page
const homeRate = homeContext.window.HatcheryRateOfChange;
homeRate.recordSetpointChange("temperature", 28.0);
assert.strictEqual(homeRate.setpointTrackers.temperature.active, true);

// Reading arrives on Home page (26.0°C)
assert.strictEqual(homeRate.isRateOfChangeAdjusting("temperature", 26.0), true);

simulatedNow += 3000;
assert.strictEqual(homeRate.isRateOfChangeAdjusting("temperature", 26.15), true);

// -------------------------------------------------------------
// 2. User navigates to View Graph Page (/graph)
// -------------------------------------------------------------
const graphOverlay = { hidden: true };
let alarmPlayedOnGraph = false;

const graphDoc = {
  getElementById: (id) => {
    if (id === "alert-modal-overlay") return graphOverlay;
    if (id === "alert-docked-card") return { hidden: true };
    return null;
  },
  querySelector: (sel) => {
    if (sel === ".alert-modal-card") return { setAttribute: () => {} };
    return null;
  },
  querySelectorAll: () => [],
  addEventListener: () => {}
};

const graphWindow = {
  HATCHERY_METRICS: {
    temperature: { unit: "°C", min: 15, max: 35 },
    dissolved_oxygen: { unit: "mg/L", min: 0, max: 20 }
  },
  HatcherySound: {
    updateAlarmState: (alerts) => {
      if (alerts && alerts.has && alerts.has("temperature")) {
        alarmPlayedOnGraph = true;
      }
    },
    stopAllAudio: () => {}
  },
  dispatchEvent: () => {},
  addEventListener: () => {},
  sessionStorage: createStorage(sharedSessionStorage),
  localStorage: createStorage(sharedLocalStorage)
};

const graphContext = vm.createContext({
  window: graphWindow,
  document: graphDoc,
  sessionStorage: graphWindow.sessionStorage,
  localStorage: graphWindow.localStorage,
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

// Load dashboard.js in fresh context simulating navigation to /graph
vm.runInContext(code, graphContext);

const graphRate = graphContext.window.HatcheryRateOfChange;
// Verify that the active adjusting state was restored from storage
assert.strictEqual(graphRate.setpointTrackers.temperature.active, true, "Tracker active state must be restored on /graph");
assert.strictEqual(graphRate.setpointTrackers.temperature.targetSetpoint, 28.0, "Target setpoint 28.0 must be restored on /graph");

// Next reading arrives while on View Graph page (26.35°C)
simulatedNow += 3000;
const isAdjustingOnGraph = graphRate.isRateOfChangeAdjusting("temperature", 26.35);
assert.strictEqual(isAdjustingOnGraph, true, "Must recognize adjusting state on /graph");

// Verify that alert modal overlay did NOT pop up and alarm sound did NOT trigger
assert.strictEqual(graphOverlay.hidden, true, "Alert modal overlay must remain hidden on /graph");
assert.strictEqual(alarmPlayedOnGraph, false, "Alarm sound must not play on /graph while adjusting");

console.log("PAGE_SWITCH_VERIFIED_SUCCESS");
        """
        proc = subprocess.run(
            ["node", "-e", node_script, str(self.dashboard_js_path)],
            capture_output=True,
            text=True
        )
        self.assertEqual(proc.returncode, 0, f"Page switch test failed: {proc.stderr}\n{proc.stdout}")
        self.assertIn("PAGE_SWITCH_VERIFIED_SUCCESS", proc.stdout)


if __name__ == "__main__":
    unittest.main()
