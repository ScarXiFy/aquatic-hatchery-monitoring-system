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

    def test_nav_status_ignores_adjusting_and_displays_other_readings(self):
        """
        Verify that when temp/do status is adjusting, updateNavStatus does NOT display
        adjusting in the main status indicator, but displays based on other readings
        (e.g., optimal when ph/salinity are stable, warning if one hits warning, etc.).
        """
        node_script = """
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const code = fs.readFileSync(process.argv[1], "utf8");

const navIndicator = {
  classList: {
    classes: new Set(),
    remove: function(...args) { args.forEach(c => this.classes.delete(c)); },
    add: function(c) { this.classes.add(c); }
  },
  lastChild: { textContent: "" }
};

const doc = {
  getElementById: (id) => {
    if (id === "status-indicator-nav") return navIndicator;
    return null;
  },
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {}
};

const win = {
  HATCHERY_METRICS: {
    temperature: { unit: "°C", min: 15, max: 35 },
    dissolved_oxygen: { unit: "mg/L", min: 0, max: 20 },
    ph: { unit: "", min: 0, max: 14 },
    salinity: { unit: "ppt", min: 0, max: 35 }
  },
  HatcherySound: { updateAlarmState: () => {}, stopAllAudio: () => {} },
  dispatchEvent: () => {},
  addEventListener: () => {},
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};

const ctx = vm.createContext({
  window: win,
  document: doc,
  sessionStorage: win.sessionStorage,
  localStorage: win.localStorage,
  CustomEvent: function() {},
  console: console,
  Date: Date,
  Math: Math,
  Number: Number,
  Array: Array,
  Map: Map,
  JSON: JSON,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout
});

vm.runInContext(code, ctx);

const { recordSetpointChange, isRateOfChangeAdjusting, updateNavStatus, setThresholds } = ctx.window.HatcheryRateOfChange;

// Set thresholds in context
setThresholds([
  { metric: "temperature", min_value: 28.0, max_value: 28.0 },
  { metric: "dissolved_oxygen", min_value: 8.0, max_value: 8.0 },
  { metric: "ph", min_value: 7.4, max_value: 8.4 },
  { metric: "salinity", min_value: 28.0, max_value: 35.0 }
]);

// Trigger adjusting on temperature (setpoint 28.0, reading 26.0)
recordSetpointChange("temperature", 28.0);
assert.strictEqual(isRateOfChangeAdjusting("temperature", 26.0), true, "Temp should be adjusting");

// 1. Temp is adjusting, DO/pH/Salinity are optimal -> Main indicator must display Optimal!
updateNavStatus({
  temperature: 26.0,
  dissolved_oxygen: 8.0,
  ph: 7.8,
  salinity: 32.0
});
assert.strictEqual(navIndicator.lastChild.textContent.trim(), "Optimal", "Indicator should be Optimal when temp is adjusting and others are optimal");

// 2. Temp is adjusting, DO/Salinity optimal, but pH hits Warning (e.g. 8.35 in warning buffer) -> Main indicator must display Warning!
updateNavStatus({
  temperature: 26.0,
  dissolved_oxygen: 8.0,
  ph: 8.35,
  salinity: 32.0
});
assert.strictEqual(navIndicator.lastChild.textContent.trim(), "Warning", "Indicator should be Warning when pH hits warning");

// 3. Temp is adjusting, DO/Salinity optimal, but pH hits Critical (e.g. 8.6 > 8.4) -> Main indicator must display Critical!
updateNavStatus({
  temperature: 26.0,
  dissolved_oxygen: 8.0,
  ph: 8.6,
  salinity: 32.0
});
assert.strictEqual(navIndicator.lastChild.textContent.trim(), "Critical", "Indicator should be Critical when pH hits critical");

console.log("NAV_STATUS_ADJUSTING_TEST_PASSED");
"""
        proc = subprocess.run(
            ["node", "-e", node_script, str(self.dashboard_js_path)],
            capture_output=True,
            text=True
        )
        self.assertEqual(proc.returncode, 0, f"Nav status test failed: {proc.stderr}\n{proc.stdout}")
        self.assertIn("NAV_STATUS_ADJUSTING_TEST_PASSED", proc.stdout)

    def test_alert_maintains_minimized_and_closed_state_when_readings_change(self):
        """
        Verify that when an alert is minimized or closed, it maintains that minimized
        or closed state even as readings change, preventing repeated popups.
        """
        node_script = """
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const code = fs.readFileSync(process.argv[1], "utf8");

const overlayEl = { hidden: true };
const dockedEl = { hidden: true };
const dockedTextEl = { textContent: "", innerHTML: "" };
const modalBodyEl = { innerHTML: "", appendChild: () => {} };

const doc = {
  getElementById: (id) => {
    if (id === "alert-modal-overlay") return overlayEl;
    if (id === "alert-docked-card") return dockedEl;
    if (id === "alert-docked-text") return dockedTextEl;
    if (id === "alert-modal-body") return modalBodyEl;
    return null;
  },
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {}
};

const win = {
  HATCHERY_METRICS: {
    temperature: { unit: "°C", min: 15, max: 35 },
    dissolved_oxygen: { unit: "mg/L", min: 0, max: 20 },
    ph: { unit: "", min: 0, max: 14 },
    salinity: { unit: "ppt", min: 0, max: 35 }
  },
  HatcherySound: { updateAlarmState: () => {}, stopAllAudio: () => {} },
  dispatchEvent: () => {},
  addEventListener: () => {},
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};

const ctx = vm.createContext({
  window: win,
  document: doc,
  sessionStorage: win.sessionStorage,
  localStorage: win.localStorage,
  CustomEvent: function() {},
  console: console,
  Date: Date,
  Math: Math,
  Number: Number,
  Array: Array,
  Map: Map,
  JSON: JSON,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout
});

vm.runInContext(code, ctx);

const { setThresholds, checkAlertTriggers, minimizeOverlay, dismissOverlay } = ctx.window.HatcheryRateOfChange;

setThresholds([
  { metric: "temperature", min_value: 26.0, max_value: 26.0 },
  { metric: "dissolved_oxygen", min_value: 7.2, max_value: 7.2 },
  { metric: "ph", min_value: 7.4, max_value: 8.4 },
  { metric: "salinity", min_value: 28.0, max_value: 35.0 }
]);

// 1. Initial trigger: temperature is 30.0 (> 26.0 setpoint critical)
checkAlertTriggers({
  timestamp: "2026-11-19T00:00:00Z",
  temperature: 30.0,
  dissolved_oxygen: 7.2,
  ph: 8.0,
  salinity: 32.0
});
assert.strictEqual(overlayEl.hidden, false, "Alert overlay should initially pop up on first trigger");

// 2. User MINIMIZES the alert
minimizeOverlay();
assert.strictEqual(overlayEl.hidden, true, "Overlay must be hidden when minimized");
assert.strictEqual(dockedEl.hidden, false, "Docked card must be visible when minimized");

// 3. Readings CHANGE while minimized (e.g. temp increases to 30.5, 31.0, 31.5, 30.2)
for (let temp of [30.5, 31.0, 31.5, 30.2, 30.8]) {
  checkAlertTriggers({
    timestamp: "2026-11-19T00:00:03Z",
    temperature: temp,
    dissolved_oxygen: 7.2,
    ph: 8.0,
    salinity: 32.0
  });
  assert.strictEqual(overlayEl.hidden, true, `Overlay must STAY hidden while minimized even as temp changes to ${temp}`);
  assert.strictEqual(dockedEl.hidden, false, "Docked card must STAY visible while minimized");
}

// 4. User CLOSES / DISMISSES the alert
dismissOverlay();
assert.strictEqual(overlayEl.hidden, true, "Overlay must be hidden when dismissed");
assert.strictEqual(dockedEl.hidden, true, "Docked card must be hidden when dismissed");

// 5. Readings CHANGE while closed (e.g. temp moves to 31.2, 31.8, 32.0, or another metric like pH changes)
for (let temp of [31.2, 31.8, 32.0, 31.5]) {
  checkAlertTriggers({
    timestamp: "2026-11-19T00:00:06Z",
    temperature: temp,
    dissolved_oxygen: 7.2,
    ph: 8.2,
    salinity: 32.0
  });
  assert.strictEqual(overlayEl.hidden, true, `Overlay must STAY hidden after closing even as temp changes to ${temp}`);
  assert.strictEqual(dockedEl.hidden, true, "Docked card must STAY hidden after closing");
}

// 6. Reading REACHES OPTIMAL STATE (temp returns to 26.0)
checkAlertTriggers({
  timestamp: "2026-11-19T00:00:09Z",
  temperature: 26.0,
  dissolved_oxygen: 7.2,
  ph: 8.0,
  salinity: 32.0
});
assert.strictEqual(overlayEl.hidden, true, "Overlay should be hidden when optimal");
assert.strictEqual(dockedEl.hidden, true, "Docked card should be hidden when optimal");

// 7. Reading becomes CRITICAL / WARNING AGAIN (e.g. temp reaches 30.0 again)
checkAlertTriggers({
  timestamp: "2026-11-19T00:00:12Z",
  temperature: 30.0,
  dissolved_oxygen: 7.2,
  ph: 8.0,
  salinity: 32.0
});
assert.strictEqual(overlayEl.hidden, false, "Alert overlay MUST POP UP AGAIN when warning/critical is detected after reaching optimal!");

console.log("MINIMIZED_AND_CLOSED_MAINTAINED_SUCCESS");
"""
        proc = subprocess.run(
            ["node", "-e", node_script, str(self.dashboard_js_path)],
            capture_output=True,
            text=True
        )
        self.assertEqual(proc.returncode, 0, f"Alert maintain state test failed: {proc.stderr}\n{proc.stdout}")
        self.assertIn("MINIMIZED_AND_CLOSED_MAINTAINED_SUCCESS", proc.stdout)

    def test_alert_pops_up_again_when_warning_critical_detected_after_reaching_optimal(self):
        """
        Verify that if an alert was previously exited/dismissed by the operator,
        once the parameter reading reaches optimal, any subsequent warning or critical
        reading triggers the alert modal overlay to pop up again.
        """
        node_script = """
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const code = fs.readFileSync(process.argv[1], "utf8");

const overlayEl = { hidden: true };
const dockedEl = { hidden: true };
const dockedTextEl = { textContent: "", innerHTML: "" };
const modalBodyEl = { innerHTML: "", appendChild: () => {} };

const storageMap = new Map();
const storage = {
  getItem: (k) => (storageMap.has(k) ? storageMap.get(k) : null),
  setItem: (k, v) => storageMap.set(k, String(v)),
  removeItem: (k) => storageMap.delete(k)
};

const doc = {
  getElementById: (id) => {
    if (id === "alert-modal-overlay") return overlayEl;
    if (id === "alert-docked-card") return dockedEl;
    if (id === "alert-docked-text") return dockedTextEl;
    if (id === "alert-modal-body") return modalBodyEl;
    return null;
  },
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {}
};

let alarmSoundCalls = 0;
let alarmSoundActive = false;

const win = {
  HATCHERY_METRICS: {
    temperature: { unit: "°C", min: 15, max: 35 },
    dissolved_oxygen: { unit: "mg/L", min: 0, max: 20 },
    ph: { unit: "", min: 0, max: 14 },
    salinity: { unit: "ppt", min: 0, max: 35 }
  },
  HatcherySound: {
    updateAlarmState: (map) => {
      alarmSoundCalls++;
      alarmSoundActive = map && map.size > 0;
    },
    stopAllAudio: () => {
      alarmSoundActive = false;
    }
  },
  dispatchEvent: () => {},
  addEventListener: () => {},
  sessionStorage: storage,
  localStorage: storage
};

const ctx = vm.createContext({
  window: win,
  document: doc,
  sessionStorage: storage,
  localStorage: storage,
  CustomEvent: function() {},
  console: console,
  Date: Date,
  Math: Math,
  Number: Number,
  Array: Array,
  Map: Map,
  JSON: JSON,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout
});

vm.runInContext(code, ctx);

const { setThresholds, checkAlertTriggers, dismissOverlay } = ctx.window.HatcheryRateOfChange;

setThresholds([
  { metric: "temperature", min_value: 26.0, max_value: 26.0 },
  { metric: "dissolved_oxygen", min_value: 7.2, max_value: 7.2 },
  { metric: "ph", min_value: 7.4, max_value: 8.4 },
  { metric: "salinity", min_value: 28.0, max_value: 35.0 }
]);

// Step 1: Trigger alert (pH = 6.8 is WARNING, DO = 7.2 is optimal, temp = 26.0 is optimal)
checkAlertTriggers({
  timestamp: "2026-11-19T00:00:00Z",
  temperature: 26.0,
  dissolved_oxygen: 7.2,
  ph: 6.8,
  salinity: 32.0
});
assert.strictEqual(overlayEl.hidden, false, "Alert overlay should pop up on warning");
assert.strictEqual(alarmSoundActive, true, "Alarm sound should be active");

// Step 2: Operator exits / dismisses the alert
dismissOverlay();
assert.strictEqual(overlayEl.hidden, true, "Alert overlay should be dismissed and hidden");
assert.strictEqual(alarmSoundActive, false, "Alarm sound should stop on dismissal");

// Step 3: Readings stay in warning state; overlay stays hidden
checkAlertTriggers({
  timestamp: "2026-11-19T00:00:03Z",
  temperature: 26.0,
  dissolved_oxygen: 7.2,
  ph: 6.9,
  salinity: 32.0
});
assert.strictEqual(overlayEl.hidden, true, "Alert must stay suppressed while warning persists");

// Step 4: Reading REACHES OPTIMAL STATE (pH returns to 8.0)
checkAlertTriggers({
  timestamp: "2026-11-19T00:00:06Z",
  temperature: 26.0,
  dissolved_oxygen: 7.2,
  ph: 8.0,
  salinity: 32.0
});
assert.strictEqual(overlayEl.hidden, true, "Alert remains hidden in optimal state");
assert.strictEqual(storage.getItem("hatchery:alert-state"), null, "Stored alert state must be cleaned up on optimal");

// Step 5: Reading detects warning / critical reading again (pH drops to 6.5)
checkAlertTriggers({
  timestamp: "2026-11-19T00:00:09Z",
  temperature: 26.0,
  dissolved_oxygen: 7.2,
  ph: 6.5,
  salinity: 32.0
});
assert.strictEqual(overlayEl.hidden, false, "Alert overlay MUST POP UP AGAIN after reaching optimal and detecting warning/critical!");
assert.strictEqual(alarmSoundActive, true, "Alarm sound must play again");

console.log("RE_TRIGGER_AFTER_OPTIMAL_PASSED");
"""
        proc = subprocess.run(
            ["node", "-e", node_script, str(self.dashboard_js_path)],
            capture_output=True,
            text=True
        )
        self.assertEqual(proc.returncode, 0, f"Re-trigger after optimal test failed: {proc.stderr}\n{proc.stdout}")
        self.assertIn("RE_TRIGGER_AFTER_OPTIMAL_PASSED", proc.stdout)

    def test_page_switch_from_home_to_view_graphs_preserves_minimized_alert_state(self):
        """
        Verify that after minimizing the warning on the main (home) page,
        navigating to the view graphs page keeps the alert state minimized
        so the user does not have to minimize it again.
        """
        node_script = """
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const code = fs.readFileSync(process.argv[1], "utf8");

// Shared browser storage across page navigation
const sharedSession = new Map();
const sharedLocal = new Map();

function makeStorage(map) {
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k)
  };
}

// -------------------------------------------------------------
// 1. User on Home Page (/): alert triggers and user minimizes it
// -------------------------------------------------------------
const homeOverlay = { hidden: true };
const homeDocked = { hidden: true };
const homeDockedText = { textContent: "", innerHTML: "" };
const homeModalBody = { innerHTML: "", appendChild: () => {} };

const homeDoc = {
  getElementById: (id) => {
    if (id === "alert-modal-overlay") return homeOverlay;
    if (id === "alert-docked-card") return homeDocked;
    if (id === "alert-docked-text") return homeDockedText;
    if (id === "alert-modal-body") return homeModalBody;
    return null;
  },
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {}
};

const homeWin = {
  HATCHERY_METRICS: {
    temperature: { unit: "°C", min: 15, max: 35 },
    dissolved_oxygen: { unit: "mg/L", min: 0, max: 20 },
    ph: { unit: "", min: 0, max: 14 },
    salinity: { unit: "ppt", min: 0, max: 35 }
  },
  HatcherySound: { updateAlarmState: () => {}, stopAllAudio: () => {} },
  dispatchEvent: () => {},
  addEventListener: () => {},
  sessionStorage: makeStorage(sharedSession),
  localStorage: makeStorage(sharedLocal)
};

const homeCtx = vm.createContext({
  window: homeWin,
  document: homeDoc,
  sessionStorage: homeWin.sessionStorage,
  localStorage: homeWin.localStorage,
  CustomEvent: function() {},
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
  console: console,
  Date: Date,
  Math: Math,
  Number: Number,
  Array: Array,
  Map: Map,
  JSON: JSON,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  setInterval: () => 1,
  clearInterval: () => {}
});

vm.runInContext(code, homeCtx);

const homeApi = homeCtx.window.HatcheryRateOfChange;

// Set thresholds on home page
homeApi.setThresholds([
  { metric: "temperature", min_value: 26.0, max_value: 26.0 },
  { metric: "dissolved_oxygen", min_value: 7.2, max_value: 7.2 },
  { metric: "ph", min_value: 7.4, max_value: 8.4 },
  { metric: "salinity", min_value: 28.0, max_value: 35.0 }
]);

// Alert triggers on Home page (temperature is 30.0 °C critical)
homeApi.checkAlertTriggers({
  timestamp: "2026-11-19T00:00:00Z",
  temperature: 30.0,
  dissolved_oxygen: 7.2,
  ph: 8.0,
  salinity: 32.0
});
assert.strictEqual(homeOverlay.hidden, false, "Overlay should initially pop up on Home page");

// User minimizes the alert on Home page
homeApi.minimizeOverlay();
assert.strictEqual(homeOverlay.hidden, true, "Overlay should be hidden on Home page after minimizing");
assert.strictEqual(homeDocked.hidden, false, "Docked card should be visible on Home page");
assert.strictEqual(sharedLocal.get("hatchery:alert-state"), "minimized");
assert.strictEqual(sharedLocal.get("hatchery:alert-minimized"), "true");

// -------------------------------------------------------------
// 2. User navigates to View Graphs Page (/graph)
// -------------------------------------------------------------
const graphOverlay = { hidden: true };
const graphDocked = { hidden: true };
const graphDockedText = { textContent: "", innerHTML: "" };
const graphModalBody = { innerHTML: "", appendChild: () => {} };

let domListeners = [];
const graphDoc = {
  getElementById: (id) => {
    if (id === "alert-modal-overlay") return graphOverlay;
    if (id === "alert-docked-card") return graphDocked;
    if (id === "alert-docked-text") return graphDockedText;
    if (id === "alert-modal-body") return graphModalBody;
    return null;
  },
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: (event, handler) => {
    if (event === "DOMContentLoaded") {
      domListeners.push(handler);
    }
  }
};

const graphWin = {
  HATCHERY_METRICS: {
    temperature: { unit: "°C", min: 15, max: 35 },
    dissolved_oxygen: { unit: "mg/L", min: 0, max: 20 },
    ph: { unit: "", min: 0, max: 14 },
    salinity: { unit: "ppt", min: 0, max: 35 }
  },
  HatcherySound: { updateAlarmState: () => {}, stopAllAudio: () => {} },
  dispatchEvent: () => {},
  addEventListener: () => {},
  sessionStorage: makeStorage(sharedSession),
  localStorage: makeStorage(sharedLocal)
};

const graphCtx = vm.createContext({
  window: graphWin,
  document: graphDoc,
  sessionStorage: graphWin.sessionStorage,
  localStorage: graphWin.localStorage,
  CustomEvent: function() {},
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
  console: console,
  Date: Date,
  Math: Math,
  Number: Number,
  Array: Array,
  Map: Map,
  JSON: JSON,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  setInterval: () => 1,
  clearInterval: () => {}
});

vm.runInContext(code, graphCtx);

// Fire DOMContentLoaded on View Graphs page
for (let handler of domListeners) {
  handler();
}

assert.strictEqual(graphOverlay.hidden, true, "Overlay MUST stay hidden on View Graphs page");
assert.strictEqual(graphDocked.hidden, false, "Docked card MUST be visible on View Graphs page");

const graphApi = graphCtx.window.HatcheryRateOfChange;

// Live reading arrives on View Graphs page (temp is still 30.0 °C critical)
graphApi.checkAlertTriggers({
  timestamp: "2026-11-19T00:00:03Z",
  temperature: 30.0,
  dissolved_oxygen: 7.2,
  ph: 8.0,
  salinity: 32.0
});

// The alert MUST REMAIN MINIMIZED — overlay stays hidden, docked stays visible!
assert.strictEqual(graphOverlay.hidden, true, "Overlay must REMAIN hidden on View Graphs page without needing re-minimizing!");
assert.strictEqual(graphDocked.hidden, false, "Docked card must REMAIN visible on View Graphs page");

// -------------------------------------------------------------
// 3. User navigates back to Home Page (/) from View Graph
// -------------------------------------------------------------
const backHomeOverlay = { hidden: true };
const backHomeDocked = { hidden: true };
const backHomeDockedText = { textContent: "", innerHTML: "" };
let backHomeDomListeners = [];

const backHomeDoc = {
  getElementById: (id) => {
    if (id === "alert-modal-overlay") return backHomeOverlay;
    if (id === "alert-docked-card") return backHomeDocked;
    if (id === "alert-docked-text") return backHomeDockedText;
    return null;
  },
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: (event, handler) => {
    if (event === "DOMContentLoaded") {
      backHomeDomListeners.push(handler);
    }
  }
};

const backHomeWin = {
  HATCHERY_METRICS: {
    temperature: { unit: "°C", min: 15, max: 35 },
    dissolved_oxygen: { unit: "mg/L", min: 0, max: 20 },
    ph: { unit: "", min: 0, max: 14 },
    salinity: { unit: "ppt", min: 0, max: 35 }
  },
  HatcherySound: { updateAlarmState: () => {}, stopAllAudio: () => {} },
  dispatchEvent: () => {},
  addEventListener: () => {},
  sessionStorage: makeStorage(sharedSession),
  localStorage: makeStorage(sharedLocal)
};

const backHomeCtx = vm.createContext({
  window: backHomeWin,
  document: backHomeDoc,
  sessionStorage: backHomeWin.sessionStorage,
  localStorage: backHomeWin.localStorage,
  CustomEvent: function() {},
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
  console: console,
  Date: Date,
  Math: Math,
  Number: Number,
  Array: Array,
  Map: Map,
  JSON: JSON,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  setInterval: () => 1,
  clearInterval: () => {}
});

vm.runInContext(code, backHomeCtx);

for (let handler of backHomeDomListeners) {
  handler();
}

assert.strictEqual(backHomeOverlay.hidden, true, "Overlay must remain hidden when returning to Home page");
assert.strictEqual(backHomeDocked.hidden, false, "Docked card must remain visible when returning to Home page");

console.log("MINIMIZED_SHARED_ACROSS_PAGES_PASSED");
process.exit(0);
"""
        proc = subprocess.run(
            ["node", "-e", node_script, str(self.dashboard_js_path)],
            capture_output=True,
            text=True
        )
        self.assertEqual(proc.returncode, 0, f"Page switch minimize test failed: {proc.stderr}\n{proc.stdout}")
        self.assertIn("MINIMIZED_SHARED_ACROSS_PAGES_PASSED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
