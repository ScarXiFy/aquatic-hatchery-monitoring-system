(function () {
  const metrics = window.HATCHERY_METRICS || {};
  const ledLevels = [0, 100, 500, 1000, 3000];
  let thresholds = [];
  let latestReading = null;
  let dayHistory = [];
  let socketBound = false;
  let hasSocketConnected = false;
  let trendRefreshTimeout = null;
  const thresholdSaveTimers = new Map();
  const controlState = {
    temperature_setpoint: 26,
    heating_tank_setpoint: 40,
    dissolved_oxygen_setpoint: 7.2,
    led_intensity: 1000,
  };

  const regulatedThresholds = {
    temperature: {
      control: "temperature_setpoint",
      tolerance: 2,
    },
    dissolved_oxygen: {
      control: "dissolved_oxygen_setpoint",
      tolerance: 1,
    },
  };

  const STORAGE_KEY_SETPOINT_TRACKERS = "hatchery:setpoint-trackers";
  const STORAGE_KEY_THRESHOLDS = "hatchery:thresholds";
  const ALLOWED_ADJUSTING_TIME_MS = null; // No timer to stop adjusting status; adjusting persists until setpoint is reached
  const MAX_TRANSITION_MS = null;

  function getStoredItem(key) {
    try {
      const sess = sessionStorage.getItem(key);
      if (sess !== null) return sess;
    } catch (e) {}
    try {
      return localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }

  function setStoredItem(key, value) {
    try {
      sessionStorage.setItem(key, value);
    } catch (e) {}
    try {
      localStorage.setItem(key, value);
    } catch (e) {}
  }

  function removeStoredItem(key) {
    try {
      sessionStorage.removeItem(key);
    } catch (e) {}
    try {
      localStorage.removeItem(key);
    } catch (e) {}
  }

  try {
    const savedThresholds = getStoredItem(STORAGE_KEY_THRESHOLDS);
    if (savedThresholds) {
      const parsed = JSON.parse(savedThresholds);
      if (Array.isArray(parsed) && parsed.length) {
        thresholds = parsed;
      }
    }
  } catch (e) {}

  const setpointTrackers = {
    temperature: {
      targetSetpoint: 26.0,
      active: false,
      hasReachedSetpoint: false,
      isSystemStart: false,
      initialValue: null,
      startTime: null,
      readings: [],
      stalledCount: 0,
      wrongDirectionCount: 0,
      lastRate: 0,
    },
    dissolved_oxygen: {
      targetSetpoint: 7.2,
      active: false,
      hasReachedSetpoint: false,
      isSystemStart: false,
      initialValue: null,
      startTime: null,
      readings: [],
      stalledCount: 0,
      wrongDirectionCount: 0,
      lastRate: 0,
    },
  };

  function saveSetpointTrackers() {
    try {
      setStoredItem(STORAGE_KEY_SETPOINT_TRACKERS, JSON.stringify(setpointTrackers));
    } catch (e) {}
  }

  function loadSetpointTrackers() {
    try {
      const raw = getStoredItem(STORAGE_KEY_SETPOINT_TRACKERS);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") {
        ["temperature", "dissolved_oxygen"].forEach((metric) => {
          if (parsed[metric] && typeof parsed[metric] === "object") {
            const saved = parsed[metric];
            const tracker = setpointTrackers[metric];
            tracker.targetSetpoint = saved.targetSetpoint !== undefined ? Number(saved.targetSetpoint) : tracker.targetSetpoint;
            tracker.active = Boolean(saved.active);
            tracker.hasReachedSetpoint = Boolean(saved.hasReachedSetpoint);
            tracker.isSystemStart = Boolean(saved.isSystemStart);
            tracker.initialValue = saved.initialValue !== null && saved.initialValue !== undefined ? Number(saved.initialValue) : null;
            tracker.startTime = saved.startTime ? Number(saved.startTime) : null;
            tracker.readings = Array.isArray(saved.readings) ? saved.readings : [];
            tracker.stalledCount = Number(saved.stalledCount) || 0;
            tracker.wrongDirectionCount = Number(saved.wrongDirectionCount) || 0;
            tracker.lastRate = Number(saved.lastRate) || 0;
          }
        });
      }
    } catch (e) {}
  }

  loadSetpointTrackers();

  /**
   * Synchronize setpoint timestamps and adjusting mode directly from the server.
   * Ensures the RPi and remotely connected devices are in sync.
   * There is no timer to stop adjusting: temperature and DO remain in adjusting status
   * until the reading reaches within the setpoint. Once reached, adjusting ends,
   * and any subsequent deviation will trigger an alarm.
   */
  function syncSetpointTimes(serverSetpointTimes, serverIsSystemStart = null, serverSliders = null, serverAdjustingState = null) {
    if (!serverSetpointTimes) return;
    const sliderKeys = {
      temperature: "temperature_setpoint",
      dissolved_oxygen: "dissolved_oxygen_setpoint",
    };
    ["temperature", "dissolved_oxygen"].forEach((metric) => {
      const tracker = setpointTrackers[metric];
      if (!tracker) return;

      const sliderKey = sliderKeys[metric];
      const prevTarget = tracker.targetSetpoint;
      let targetChanged = false;
      if (serverSliders && serverSliders[sliderKey] !== undefined) {
        const numTarget = Number(serverSliders[sliderKey]);
        if (!Number.isNaN(numTarget)) {
          targetChanged = prevTarget !== numTarget;
          tracker.targetSetpoint = numTarget;
        }
      }

      const serverTime = Number(serverSetpointTimes[metric]);
      if (!serverTime || Number.isNaN(serverTime)) return;

      const isSystemStart = Boolean(serverIsSystemStart && serverIsSystemStart[metric]);
      const isNewSetpointTime = tracker.startTime !== serverTime;
      tracker.startTime = serverTime;

      const currentVal = latestReading && latestReading[metric] !== undefined && !Number.isNaN(Number(latestReading[metric]))
        ? Number(latestReading[metric])
        : null;
      const warning_limit = metric === "temperature" ? 1.0 : 0.5;
      const hasServerAdjusting =
        serverAdjustingState && typeof serverAdjustingState[metric] === "boolean";

      if (currentVal !== null && Math.abs(currentVal - tracker.targetSetpoint) < warning_limit) {
        tracker.active = false;
        tracker.isSystemStart = false;
        tracker.hasReachedSetpoint = true;
        tracker.readings = [];
        tracker.initialValue = null;
      } else if (hasServerAdjusting) {
        tracker.active = serverAdjustingState[metric];
        tracker.isSystemStart = tracker.active ? isSystemStart : false;
        tracker.hasReachedSetpoint = !tracker.active;
        if (tracker.active) {
          if (isNewSetpointTime || targetChanged) {
            tracker.initialValue = currentVal;
            tracker.readings = currentVal !== null ? [{ value: currentVal, timestamp: serverTime }] : [];
            tracker.stalledCount = 0;
            tracker.wrongDirectionCount = 0;
            tracker.lastRate = 0;
          }
          activeAlertsMap.delete(metric);
          dismissedAlertKeys.delete(`${metric}_WARNING`);
          dismissedAlertKeys.delete(`${metric}_CRITICAL`);
          dismissedAlertKeys.delete(`${metric}_Warning`);
          dismissedAlertKeys.delete(`${metric}_Critical`);
          if (window.HatcherySound) {
            window.HatcherySound.updateAlarmState(activeAlertsMap);
          }
        } else {
          tracker.readings = [];
          tracker.initialValue = null;
        }
      } else if (isNewSetpointTime || targetChanged) {
        tracker.active = true;
        tracker.isSystemStart = isSystemStart;
        tracker.hasReachedSetpoint = false;
        tracker.initialValue = currentVal;
        tracker.readings = currentVal !== null ? [{ value: currentVal, timestamp: serverTime }] : [];
        tracker.stalledCount = 0;
        tracker.wrongDirectionCount = 0;
        tracker.lastRate = 0;

        activeAlertsMap.delete(metric);
        dismissedAlertKeys.delete(`${metric}_WARNING`);
        dismissedAlertKeys.delete(`${metric}_CRITICAL`);
        dismissedAlertKeys.delete(`${metric}_Warning`);
        dismissedAlertKeys.delete(`${metric}_Critical`);
        if (window.HatcherySound) {
          window.HatcherySound.updateAlarmState(activeAlertsMap);
        }
      }
    });
    saveSetpointTrackers();
  }

  /**
   * Record when temperature or DO setpoint is changed.
   * If current reading differs from new setpoint beyond normal tolerance,
   * activates adjusting mode and suppresses immediate alarms until
   * the parameter reaches within its setpoint.
   */
  function recordSetpointChange(metric, newSetpoint, explicitStartTime = null, isSystemStart = false) {
    const tracker = setpointTrackers[metric];
    if (!tracker) return;

    const numTarget = Number(newSetpoint);
    if (Number.isNaN(numTarget)) return;

    const currentVal = latestReading && latestReading[metric] !== undefined && !Number.isNaN(Number(latestReading[metric]))
      ? Number(latestReading[metric])
      : null;

    const warning_limit = metric === "temperature" ? 1.0 : 0.5;

    tracker.targetSetpoint = numTarget;
    tracker.startTime = explicitStartTime ? Number(explicitStartTime) : Date.now();
    tracker.isSystemStart = isSystemStart;
    tracker.stalledCount = 0;
    tracker.wrongDirectionCount = 0;
    tracker.lastRate = 0;

    if (currentVal === null || Math.abs(currentVal - numTarget) >= warning_limit) {
      tracker.active = true;
      tracker.hasReachedSetpoint = false;
      tracker.initialValue = currentVal;
      tracker.readings = currentVal !== null ? [{ value: currentVal, timestamp: tracker.startTime }] : [];

      // Suppress/clear any active alarm for this metric right away when setpoint is changed
      activeAlertsMap.delete(metric);
      dismissedAlertKeys.delete(`${metric}_WARNING`);
      dismissedAlertKeys.delete(`${metric}_CRITICAL`);
      dismissedAlertKeys.delete(`${metric}_Warning`);
      dismissedAlertKeys.delete(`${metric}_Critical`);
      if (window.HatcherySound) {
        window.HatcherySound.updateAlarmState(activeAlertsMap);
      }
    } else {
      tracker.active = false;
      tracker.hasReachedSetpoint = true;
      tracker.isSystemStart = false;
      tracker.initialValue = null;
      tracker.readings = [];
    }
    saveSetpointTrackers();
  }

  /**
   * Check adjusting status for temperature and DO following a setpoint change.
   * Critical/warning alerts are not triggered by the parameter until it has reached its setpoint;
   * there is no timer to stop the adjusting status. Once the parameter has reached within its setpoint,
   * adjusting ends, and any subsequent deviation will trigger an alarm.
   */
  function isRateOfChangeAdjusting(metric, currentValue) {
    const tracker = setpointTrackers[metric];
    if (!tracker || !tracker.active) {
      return false;
    }

    const val = Number(currentValue);
    if (Number.isNaN(val)) {
      return false;
    }

    const warning_limit = metric === "temperature" ? 1.0 : 0.5;
    const diff = Math.abs(val - tracker.targetSetpoint);

    // 1. If within warning limit of setpoint, target optimal range has been reached
    if (diff < warning_limit) {
      tracker.active = false;
      tracker.hasReachedSetpoint = true;
      tracker.isSystemStart = false;
      tracker.readings = [];
      tracker.initialValue = null;
      tracker.stalledCount = 0;
      tracker.wrongDirectionCount = 0;
      saveSetpointTrackers();
      return false;
    }

    const now = Date.now();

    // If initialValue was not known at slider change time, set it on first reading
    if (tracker.initialValue === null) {
      tracker.initialValue = val;
      tracker.startTime = tracker.startTime || now;
      tracker.readings = [{ value: val, timestamp: now }];
      saveSetpointTrackers();
      return true;
    }

    // Record reading in rolling history (avoid rapid duplicates within same cycle)
    const lastReading = tracker.readings.length ? tracker.readings[tracker.readings.length - 1] : null;
    const shouldAddReading = !lastReading || (now - lastReading.timestamp >= 1500);
    if (shouldAddReading) {
      tracker.readings.push({ value: val, timestamp: now });
      if (tracker.readings.length > 10) {
        tracker.readings.shift();
      }
      saveSetpointTrackers();
    }

    // Rate calculation for tracker.lastRate (informational)
    if (tracker.readings.length >= 2) {
      const windowSlice = tracker.readings.slice(-5);
      const firstInWindow = windowSlice[0];
      const latestInWindow = windowSlice[windowSlice.length - 1];
      const dtSeconds = Math.max(1, (latestInWindow.timestamp - firstInWindow.timestamp) / 1000);
      const dVal = latestInWindow.value - firstInWindow.value;
      tracker.lastRate = dVal / dtSeconds;
    }

    // Remains adjusting without any timeout until target setpoint is reached
    return true;
  }

  function truncateToOneDecimal(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "--";
    }
    const num = Number(value);
    const sign = num < 0 ? -1 : 1;
    const absNum = Math.abs(num);
    const truncated = Math.floor(absNum * 10 + 1e-9) / 10;
    return (sign * truncated).toFixed(1);
  }

  function formatValue(metric, value, includeUnit = true) {
    const config = metrics[metric];
    if (!config || value === null || value === undefined || Number.isNaN(Number(value))) {
      return "--";
    }
    const formatted = truncateToOneDecimal(value);
    return includeUnit && config.unit ? `${formatted} ${config.unit}` : formatted;
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function controlThresholdFor(metric) {
    const rule = regulatedThresholds[metric];
    if (!rule) {
      return null;
    }

    const slider = document.querySelector(`[data-control-slider="${rule.control}"]`);
    const config = metrics[metric];
    const setpoint = Number(controlState[rule.control]);
    const min = slider ? Number(slider.min) : config.min;
    const max = slider ? Number(slider.max) : config.max;

    return {
      metric,
      min_value: clamp(setpoint - rule.tolerance, min, max),
      max_value: clamp(setpoint + rule.tolerance, min, max),
    };
  }

  function thresholdFor(metric) {
    return controlThresholdFor(metric) || thresholds.find((item) => item.metric === metric);
  }

  function conditionFor(metric, value) {
    const threshold = thresholds.find((item) => item.metric === metric);
    if (!threshold || value === null || value === undefined || Number.isNaN(Number(value))) {
      return "neutral";
    }

    const v = Number(value);
    const min = Number(threshold.min_value);
    const max = Number(threshold.max_value);
    if (Number.isNaN(min) || Number.isNaN(max)) {
      return "neutral";
    }

    if (min === max) {
      const limit = max;
      const warning_limit = metric === "temperature" ? 1.0 : 0.5; // Warning alert appears at +-1.0 from setpoint for temperature and +-0.5 for DO
      const critical_limit = metric === "temperature" ? 2.0 : 1.0;
      const diff = Math.abs(v - limit);
      if (diff >= critical_limit) {
        return "critical";
      }
      if (diff >= warning_limit) {
        return "warning";
      }
      return "optimal";
    } else {
      const range = max - min;
      const buffer = range * 0.10;
      if (v < min || v > max) {
        return "critical";
      }
      const isSalinityZeroMin = metric === "salinity" && min <= 0;
      const lowerWarning = !isSalinityZeroMin && v <= min + buffer;
      const upperWarning = v >= max - buffer;
      if (lowerWarning || upperWarning) {
        return "warning";
      }
      return "optimal";
    }
  }

  function updateDateTime() {
    const dateTime = document.getElementById("current-date-time");
    if (!dateTime) {
      return;
    }
    const now = new Date();
    dateTime.dateTime = now.toISOString();
    dateTime.textContent = now.toLocaleString([], {
      month: "short",
      day: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function updateSocketStatus(isConnected) {
    const status = document.getElementById("socket-status");
    if (!status) {
      return;
    }

    const label = status.querySelector("[data-socket-status-label]");
    status.classList.toggle("status-rpi-disconnected", !isConnected);
    if (label) {
      label.textContent = isConnected ? status.dataset.connectedLabel : status.dataset.disconnectedLabel;
    }
  }

  function setBadgeState(element, state) {
    if (!element) {
      return;
    }
    element.classList.remove("status-neutral", "status-warning", "status-critical", "status-adjusting");
    if (state === "warning") {
      element.textContent = "Warning";
      element.classList.add("status-warning");
      return;
    }
    if (state === "critical") {
      element.textContent = "Critical";
      element.classList.add("status-critical");
      return;
    }
    if (state === "adjusting") {
      element.textContent = "Adjusting";
      element.classList.add("status-adjusting");
      return;
    }
    if (state === "optimal") {
      element.textContent = "Optimal";
      return;
    }
    element.textContent = "Waiting";
    element.classList.add("status-neutral");
  }
  function gaugeDegrees(metric, value) {
    const config = metrics[metric];
    if (!config) {
      return 0;
    }

    const threshold = thresholdFor(metric);
    const min = threshold ? Number(threshold.min_value) : Number(config.min);
    const max = threshold ? Number(threshold.max_value) : Number(config.max);
    const numericValue = Number(value);

    if (
      Number.isNaN(numericValue) ||
      Number.isNaN(min) ||
      Number.isNaN(max) ||
      max <= min
    ) {
      return 0;
    }

    const buffer = metric === "ph" ? 2 : 5;

    let gaugeMin = min - buffer;
    let gaugeMax = max + buffer;

    if (metric === "ph" && gaugeMax > 14) {
      gaugeMax = 14;
    }
    if (metric === "ph" && gaugeMin < 1) {
      gaugeMin = 1;
    } else if (metric !== "ph" && gaugeMin < 0) {
      gaugeMin = 0;
    }

    const ratio = (numericValue - gaugeMin) / (gaugeMax - gaugeMin);

    return clamp(ratio, 0, 1) * 180;
  }

  function updateGauge(metric, reading) {
    const config = metrics[metric];
    const valueElement = document.getElementById(`${metric}-value`);
    const statusElement = document.getElementById(`${metric}-status`);
    const gaugeElement = document.querySelector(`[data-gauge="${metric}"]`);
    if (!config || !valueElement || !statusElement || !gaugeElement || !reading) {
      return;
    }

    const value = Number(reading[metric]);
    let state = conditionFor(metric, value);
    if ((metric === "temperature" || metric === "dissolved_oxygen") && isRateOfChangeAdjusting(metric, value)) {
      state = "adjusting";
    }
    valueElement.textContent = formatValue(metric, value, false);
    setBadgeState(statusElement, state);
    gaugeElement.style.setProperty("--gauge-progress", `${gaugeDegrees(metric, value)}deg`);
    const gaugeColor = state === "critical" ? "#ef4444" : state === "warning" ? "#fbbf24" : state === "adjusting" ? "#3b82f6" : "#10b981";
    gaugeElement.style.setProperty("--gauge-color", gaugeColor);
  }

  function updateNavStatus(reading) {
    const indicator = document.getElementById("status-indicator-nav");
    if (!indicator) {
      return;
    }

    // When status for temp/do is adjusting, do not display adjusting in the main status indicator,
    // but display based on the other readings (e.g. if pH and salinity are stable, display optimal;
    // if one hits warning, display warning; etc.).
    const nonAdjustingStates = Object.keys(metrics)
      .filter((metric) => {
        const val = Number(reading[metric]);
        const isAdjusting =
          (metric === "temperature" || metric === "dissolved_oxygen") &&
          isRateOfChangeAdjusting(metric, val);
        return !isAdjusting;
      })
      .map((metric) => {
        const val = Number(reading[metric]);
        return conditionFor(metric, val);
      });

    const overallState = nonAdjustingStates.includes("critical")
      ? "critical"
      : nonAdjustingStates.includes("warning")
      ? "warning"
      : nonAdjustingStates.length > 0 && nonAdjustingStates.every((s) => s === "neutral")
      ? "neutral"
      : "optimal";

    indicator.classList.remove("status-warning", "status-critical", "status-neutral");

    if (overallState === "critical") {
      indicator.classList.add("status-critical");
      indicator.lastChild.textContent = " Critical";
    } else if (overallState === "warning") {
      indicator.classList.add("status-warning");
      indicator.lastChild.textContent = " Warning";
    } else if (overallState === "adjusting") {
      indicator.classList.add("status-neutral");
      indicator.lastChild.textContent = " Adjusting";
    } else if (overallState === "neutral") {
      indicator.classList.add("status-neutral");
      indicator.lastChild.textContent = " Waiting";
    } else {
      indicator.lastChild.textContent = " Optimal";
    }
  }

  const metricDisplayNames = {
    temperature: "Temperature",
    dissolved_oxygen: "Dissolved Oxygen",
    ph: "pH Level",
    salinity: "Salinity",
  };

  const STORAGE_KEY_ALERT_STATE = "hatchery:alert-state";
  const STORAGE_KEY_ALERT_MINIMIZED = "hatchery:alert-minimized";
  const STORAGE_KEY_METRIC_STATES = "hatchery:metric-states";
  const STORAGE_KEY_DISMISSED_KEYS = "hatchery:dismissed-keys";
  const STORAGE_KEY_DOCKED_TEXT = "hatchery:docked-text";

  const previousMetricStates = {};
  const storedMetricStates = getStoredItem(STORAGE_KEY_METRIC_STATES);
  if (storedMetricStates) {
    try {
      Object.assign(previousMetricStates, JSON.parse(storedMetricStates));
    } catch (e) {}
  }

  const activeAlertsMap = new Map();
  const dismissedAlertKeys = new Set();
  const storedDismissedKeys = getStoredItem(STORAGE_KEY_DISMISSED_KEYS);
  if (storedDismissedKeys) {
    try {
      const parsed = JSON.parse(storedDismissedKeys);
      if (Array.isArray(parsed)) {
        parsed.forEach((k) => dismissedAlertKeys.add(k));
      }
    } catch (e) {}
  }

  let notificationHistory = [];
  let unreadCount = 0;

  let notificationSystemState = "idle"; // "idle", "overlay", "minimized", "dismissed"
  const storedAlertState = getStoredItem(STORAGE_KEY_ALERT_STATE);
  const storedMinimized = getStoredItem(STORAGE_KEY_ALERT_MINIMIZED);
  if (storedMinimized === "true" || storedAlertState === "minimized") {
    notificationSystemState = "minimized";
  } else if (storedAlertState === "overlay") {
    notificationSystemState = "overlay";
  } else if (storedAlertState === "dismissed") {
    notificationSystemState = "dismissed";
  } else {
    notificationSystemState = "idle";
  }

  function formatTimestamp(isoOrDate) {
    const d = isoOrDate ? new Date(isoOrDate) : new Date();
    if (Number.isNaN(d.getTime())) return new Date().toLocaleString();
    return d.toLocaleString([], {
      month: "short",
      day: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatTimeOnly(isoOrDate) {
    const d = isoOrDate ? new Date(isoOrDate) : new Date();
    if (Number.isNaN(d.getTime())) return new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  function getThresholdDescription(metric) {
    if (metric === "temperature") {
      const setpoint = controlState.temperature_setpoint !== undefined ? controlState.temperature_setpoint : 26;
      return `Setpoint ${formatValue("temperature", setpoint)} (±2.0 °C)`;
    }
    if (metric === "dissolved_oxygen") {
      const setpoint = controlState.dissolved_oxygen_setpoint !== undefined ? controlState.dissolved_oxygen_setpoint : 7.2;
      return `Setpoint ${formatValue("dissolved_oxygen", setpoint)} (±1.0 mg/L)`;
    }
    const t = thresholdFor(metric);
    if (!t) return "--";
    return `Min ${formatValue(metric, t.min_value)} — Max ${formatValue(metric, t.max_value)}`;
  }

  async function loadNotificationsFromDB() {
    try {
      const res = await fetch("/api/notifications");
      if (res.ok) {
        const data = await res.json();
        notificationHistory = data.notifications || [];
        unreadCount = data.unread_count || 0;
        updateNotificationBellUI();
      }
    } catch (e) {
      console.error("Failed to load notifications from DB", e);
    }
  }

  async function persistNotificationToDB(notificationData) {
    try {
      const res = await fetch("/api/notifications", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(notificationData),
      });
      if (res.ok) {
        await loadNotificationsFromDB();
      }
    } catch (e) {
      console.error("Failed to persist notification to DB", e);
    }
  }

  async function markNotificationsAsReadDB() {
    try {
      const res = await fetch("/api/notifications/read", { method: "PUT" });
      if (res.ok) {
        const data = await res.json();
        notificationHistory = data.notifications || [];
        unreadCount = 0;
        updateNotificationBellUI();
      }
    } catch (e) {
      console.error("Failed to mark notifications as read", e);
    }
  }

  async function clearNotificationsDB() {
    try {
      const res = await fetch("/api/notifications", { method: "DELETE" });
      if (res.ok) {
        notificationHistory = [];
        unreadCount = 0;
        updateNotificationBellUI();
      }
    } catch (e) {
      console.error("Failed to clear notifications", e);
    }
  }

  function renderAlertModalBody() {
    const card = document.querySelector(".alert-modal-card");
    const body = document.getElementById("alert-modal-body");
    if (!body) return;

    const items = Array.from(activeAlertsMap.values());
    if (!items.length) {
      body.innerHTML = "<div class='alert-modal-item'>No active alerts.</div>";
      return;
    }

    const hasCritical = items.some((i) => i.status === "CRITICAL" || i.status === "Critical");
    if (card) {
      card.setAttribute("data-level", hasCritical ? "CRITICAL" : "WARNING");
    }

    body.innerHTML = items
      .map(
        (item) => `
        <div class="alert-modal-item" data-status="${item.status}">
          <div class="alert-item-status-wrap">
            <span class="alert-item-badge ${item.status === "CRITICAL" ? "badge-critical" : "badge-warning"}">
              [ ${item.status} ]
            </span>
          </div>
          <h3 class="alert-item-parameter">${item.parameter}</h3>
          <div class="alert-item-value">${item.currentValueFormatted}</div>
          <div class="alert-item-message">${item.message}</div>
          <div class="alert-item-time">${item.timeOnly}</div>
        </div>
      `
      )
      .join("");
  }

  function showOverlay() {
    renderAlertModalBody();
    const overlay = document.getElementById("alert-modal-overlay");
    const docked = document.getElementById("alert-docked-card");
    if (overlay) overlay.hidden = false;
    if (docked) docked.hidden = true;
    notificationSystemState = "overlay";
    setStoredItem(STORAGE_KEY_ALERT_STATE, "overlay");
    setStoredItem(STORAGE_KEY_ALERT_MINIMIZED, "false");
  }

  function minimizeOverlay() {
    const overlay = document.getElementById("alert-modal-overlay");
    const docked = document.getElementById("alert-docked-card");
    const dockedText = document.getElementById("alert-docked-text");
    if (overlay) overlay.hidden = true;
    if (docked) docked.hidden = false;

    const items = Array.from(activeAlertsMap.values());
    if (dockedText) {
      if (items.length === 1) {
        dockedText.innerHTML = `<div><strong>${items[0].icon} ${items[0].status}:</strong> ${items[0].parameter} (${items[0].currentValueFormatted})</div><div><small>${items[0].timeOnly}</small></div>`;
      } else if (items.length > 1) {
        dockedText.innerHTML = `<div><strong>⚠️ ${items.length} Active Alerts:</strong> ${items.map((i) => i.parameter).join(", ")}</div>`;
      } else {
        dockedText.textContent = "System Alert";
      }
      setStoredItem(STORAGE_KEY_DOCKED_TEXT, dockedText.innerHTML);
    }
    notificationSystemState = "minimized";
    setStoredItem(STORAGE_KEY_ALERT_STATE, "minimized");
    setStoredItem(STORAGE_KEY_ALERT_MINIMIZED, "true");
  }

  function updateFloatingWidget() {
    if (notificationSystemState === "minimized") {
      minimizeOverlay();
    }
  }

  function dismissOverlay() {
    const overlay = document.getElementById("alert-modal-overlay");
    const docked = document.getElementById("alert-docked-card");
    if (overlay) overlay.hidden = true;
    if (docked) docked.hidden = true;

    const items = Array.from(activeAlertsMap.values());
    items.forEach((item) => {
      dismissedAlertKeys.add(item.key);
    });

    if (window.HatcherySound && typeof window.HatcherySound.stopAllAudio === "function") {
      window.HatcherySound.stopAllAudio();
    }

    notificationSystemState = "dismissed";
    setStoredItem(STORAGE_KEY_ALERT_STATE, "dismissed");
    setStoredItem(STORAGE_KEY_ALERT_MINIMIZED, "false");
    removeStoredItem(STORAGE_KEY_DOCKED_TEXT);
    setStoredItem(STORAGE_KEY_DISMISSED_KEYS, JSON.stringify(Array.from(dismissedAlertKeys)));
    loadNotificationsFromDB();
  }

  function hideAllAlertUI() {
    const overlay = document.getElementById("alert-modal-overlay");
    const docked = document.getElementById("alert-docked-card");
    if (overlay) overlay.hidden = true;
    if (docked) docked.hidden = true;
  }

  function updateNotificationBellUI() {
    const badge = document.getElementById("notification-badge");
    const list = document.getElementById("notification-list");
    if (badge) {
      if (unreadCount > 0) {
        badge.textContent = unreadCount > 99 ? "99+" : unreadCount;
        badge.hidden = false;
      } else {
        badge.hidden = true;
      }
    }

    if (list) {
      if (!notificationHistory.length) {
        list.innerHTML = '<p class="notification-empty">No notifications yet.</p>';
      } else {
        list.innerHTML = notificationHistory
          .map(
            (n) => `
          <div class="notification-item" data-status="${n.status}">
            <div style="margin-bottom: 4px; display: flex; justify-content: space-between;">
              <strong style="color: ${n.status === 'CRITICAL' || n.status === 'Critical' ? '#dc2626' : '#d97706'};">${n.status === "CRITICAL" || n.status === "Critical" ? "🚨" : "⚠️"} [${n.status}] ${n.parameter}</strong>
              <small style="color: #64748b;">${formatTimestamp(n.created_at)}</small>
            </div>
            <div style="font-size: 13px; color: #334155;">
              <div>Value: <strong>${n.current_value}</strong></div>
              <p style="margin: 4px 0 0; font-weight: 700; color: #475569;">${n.message}</p>
            </div>
          </div>
        `
          )
          .join("");
      }
    }
  }

  function checkAlertTriggers(reading) {
    if (!reading) return;
    if (!thresholds || !thresholds.length) return;
    const timeFormatted = formatTimestamp(reading.timestamp);
    const timeOnlyFormatted = formatTimeOnly(reading.timestamp);
    let newlyTriggered = false;

    Object.keys(metricDisplayNames).forEach((metric) => {
      const val = Number(reading[metric]);
      const state = conditionFor(metric, val);
      const isAdjusting = (metric === "temperature" || metric === "dissolved_oxygen") && isRateOfChangeAdjusting(metric, val);
      const prevState = previousMetricStates[metric];
      const paramName = metricDisplayNames[metric];

      if (isAdjusting) {
        // Temperature or DO is adjusting toward setpoint: alarm is suppressed until setpoint is reached
        activeAlertsMap.delete(metric);
        dismissedAlertKeys.delete(`${metric}_WARNING`);
        dismissedAlertKeys.delete(`${metric}_CRITICAL`);
        dismissedAlertKeys.delete(`${metric}_Warning`);
        dismissedAlertKeys.delete(`${metric}_Critical`);
        previousMetricStates[metric] = "adjusting";
        return;
      }

      if (state === "warning" || state === "critical") {
        const statusText = state === "critical" ? "CRITICAL" : "WARNING";
        const iconStr = state === "critical" ? "🚨" : "⚠️";
        const messageStr =
          state === "critical"
            ? "Immediate action required."
            : "Outside normal range.";
        const currValStr = formatValue(metric, val);
        const threshStr = getThresholdDescription(metric);
        const alertKey = `${metric}_${statusText}`;

        activeAlertsMap.set(metric, {
          metric,
          status: statusText,
          parameter: paramName,
          currentValue: val,
          currentValueFormatted: currValStr,
          thresholdValue: threshStr,
          message: messageStr,
          icon: iconStr,
          time: timeFormatted,
          timeOnly: timeOnlyFormatted,
          key: alertKey,
        });

        // Only persist new notification to SQLite DB when state actually changes
        if (prevState !== state) {
          persistNotificationToDB({
            parameter: paramName,
            status: statusText,
            current_value: val,
            threshold_value: threshStr,
            message: messageStr,
            created_at: reading.timestamp,
          });
          newlyTriggered = true;
        }
      } else if (state === "optimal") {
        activeAlertsMap.delete(metric);
        dismissedAlertKeys.delete(`${metric}_WARNING`);
        dismissedAlertKeys.delete(`${metric}_CRITICAL`);
        dismissedAlertKeys.delete(`${metric}_Warning`);
        dismissedAlertKeys.delete(`${metric}_Critical`);
        setStoredItem(STORAGE_KEY_DISMISSED_KEYS, JSON.stringify(Array.from(dismissedAlertKeys)));
      }
      previousMetricStates[metric] = state;
    });

    setStoredItem(STORAGE_KEY_METRIC_STATES, JSON.stringify(previousMetricStates));

    if (activeAlertsMap.size > 0) {
      if (notificationSystemState === "minimized") {
        // When alert is minimized, maintain minimized state even though readings are changing:
        // keep overlay hidden and update floating widget in place
        updateFloatingWidget();
      } else if (notificationSystemState === "dismissed") {
        const hasUndismissedAlert = Array.from(activeAlertsMap.values()).some(
          (item) => !dismissedAlertKeys.has(item.key)
        );
        if (hasUndismissedAlert) {
          // If a parameter reached optimal previously and has now triggered a warning/critical reading again,
          // or a new parameter reading is detected, pop up the overlay alert
          showOverlay();
        } else {
          // When alert is closed, maintain closed state while the same alert condition persists:
          // keep both overlay and docked widget hidden, do not pop up on screen
          hideAllAlertUI();
        }
      } else {
        // Active overlay or newly triggered initial alert: display/update overlay
        showOverlay();
      }
    } else {
      hideAllAlertUI();
      notificationSystemState = "idle";
      dismissedAlertKeys.clear();
      removeStoredItem(STORAGE_KEY_ALERT_STATE);
      removeStoredItem(STORAGE_KEY_ALERT_MINIMIZED);
      removeStoredItem(STORAGE_KEY_DOCKED_TEXT);
      removeStoredItem(STORAGE_KEY_DISMISSED_KEYS);
    }

    if (window.HatcherySound) {
      if (notificationSystemState !== "dismissed" && activeAlertsMap.size > 0) {
        window.HatcherySound.updateAlarmState(activeAlertsMap);
      } else {
        window.HatcherySound.stopAllAudio();
      }
    }
  }

  function bindNotificationUI() {
    const bellBtn = document.getElementById("notification-bell-btn");
    const dropdown = document.getElementById("notification-dropdown");
    const clearBtn = document.getElementById("clear-notifications-btn");

    if (bellBtn && dropdown) {
      bellBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        dropdown.hidden = !dropdown.hidden;
        if (!dropdown.hidden) {
          markNotificationsAsReadDB();
        }
      });

      document.addEventListener("click", (e) => {
        if (!dropdown.contains(e.target) && !bellBtn.contains(e.target)) {
          dropdown.hidden = true;
        }
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        clearNotificationsDB();
      });
    }

    const minBtn = document.getElementById("alert-modal-minimize-btn");
    const closeBtn = document.getElementById("alert-modal-close-btn");
    const expandTrigger = document.getElementById("alert-docked-expand-trigger");
    const dockedCloseBtn = document.getElementById("alert-docked-close-btn");

    if (minBtn) {
      minBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        minimizeOverlay();
      });
    }

    if (closeBtn) {
      closeBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        dismissOverlay();
      });
    }

    if (expandTrigger) {
      expandTrigger.addEventListener("click", (e) => {
        e.stopPropagation();
        showOverlay();
      });
    }

    if (dockedCloseBtn) {
      dockedCloseBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        dismissOverlay();
      });
    }

    if (notificationSystemState === "minimized") {
      const overlay = document.getElementById("alert-modal-overlay");
      const docked = document.getElementById("alert-docked-card");
      const dockedText = document.getElementById("alert-docked-text");
      const savedDockedText = getStoredItem(STORAGE_KEY_DOCKED_TEXT);
      if (overlay) {
        overlay.hidden = true;
      }
      if (docked) {
        if (dockedText) {
          if (savedDockedText) {
            dockedText.innerHTML = savedDockedText;
          } else {
            dockedText.textContent = "System Alert (Minimized)";
          }
        }
        docked.hidden = false;
      }
    }

    loadNotificationsFromDB();
  }

  function updateGauges(reading) {
    latestReading = reading;
    Object.keys(metrics).forEach((metric) => updateGauge(metric, reading));
    updateNavStatus(reading);
    checkAlertTriggers(reading);
  }

  function metricStats(metric, readings) {
    const values = readings.map((reading) => Number(reading[metric])).filter((value) => !Number.isNaN(value));
    if (!values.length) {
      return { current: null, min: null, max: null, average: null };
    }
    const average = values.reduce((sum, v) => sum + v, 0) / values.length;
    return {
      current: values[values.length - 1],
      min: Math.min(...values),
      max: Math.max(...values),
      average: average,
    };
  }

  function renderTrend(metric, stats) {
    const row = document.querySelector(`[data-trend-metric="${metric}"]`);
    const config = metrics[metric];
    if (!row || !config) {
      return;
    }

    if (stats.average === null || stats.min === null || stats.max === null) {
      row.className = "trend-row is-empty";
      row.textContent = `${config.shortLabel}: waiting for logged readings`;
      return;
    }

    const range = stats.max - stats.min;
    const position = range === 0 ? 50 : ((stats.average - stats.min) / range) * 100;
    row.className = "trend-row";
    row.style.setProperty("--trend-position", `${Math.max(0, Math.min(100, position))}%`);
    row.innerHTML = `
      <div class="trend-head">
        <span class="trend-label">${config.shortLabel}</span>
        <span class="trend-value">${formatValue(metric, stats.average)}</span>
      </div>
      <div class="trend-track">
        <span class="trend-fill"></span>
        <span class="trend-thumb" title="avg ${formatValue(metric, stats.average)}"></span>
      </div>
      <div class="trend-foot">
        <span>${formatValue(metric, stats.min)}</span>
        <span>${formatValue(metric, stats.max)}</span>
      </div>
    `;
  }

  function renderTrends() {
    Object.keys(metrics).forEach((metric) => renderTrend(metric, metricStats(metric, dayHistory)));
  }

  function renderThresholds() {
    const body = document.getElementById("threshold-table-body");
    if (!body) {
      return;
    }
    body.innerHTML = thresholds
      .filter((item) => item.metric === "ph" || item.metric === "salinity")
      .map((item) => {
        const config = metrics[item.metric] || { label: item.metric, unit: "" };
        const step = item.metric === "ph" ? "1" : "0.1";
        const minAttr = item.metric === "ph" ? 'min="1" max="14"' : "";
        return `
          <tr data-threshold-row="${item.metric}">
            <td><strong>${config.label}</strong></td>
            <td><input class="threshold-input" data-threshold-field="min_value" type="number" step="${step}" ${minAttr} value="${formatValue(item.metric, item.min_value, false)}" aria-label="${config.label} minimum value"></td>
            <td><input class="threshold-input" data-threshold-field="max_value" type="number" step="${step}" ${minAttr} value="${formatValue(item.metric, item.max_value, false)}" aria-label="${config.label} maximum value"></td>
            <td>${config.unit}</td>
          </tr>
        `;
      })
      .join("") || `<tr><td colspan="4">No pH or salinity thresholds available.</td></tr>`;
    bindThresholdInputs();
  }

  function setThresholds(next) {
    thresholds = next || [];
    setStoredItem(STORAGE_KEY_THRESHOLDS, JSON.stringify(thresholds));
  }

  async function loadThresholds() {
    try {
      const response = await fetch("/api/thresholds");
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      thresholds = payload.thresholds || [];
      setStoredItem(STORAGE_KEY_THRESHOLDS, JSON.stringify(thresholds));
      thresholds.forEach((item) => {
        if (item.metric === "temperature" && item.min_value !== undefined) {
          controlState.temperature_setpoint = Number(item.min_value);
        } else if (item.metric === "dissolved_oxygen" && item.min_value !== undefined) {
          controlState.dissolved_oxygen_setpoint = Number(item.min_value);
        }
      });
      renderThresholds();
      if (latestReading) {
        checkAlertTriggers(latestReading);
      }
      window.dispatchEvent(new CustomEvent("hatchery:thresholds", { detail: thresholds }));
    } catch (e) {
      console.warn("[DASHBOARD] Failed to load thresholds:", e);
    }
  }

  function isEditingThresholds() {
    return Boolean(
      thresholdSaveTimers.size ||
        (document.activeElement && document.activeElement.classList.contains("threshold-input"))
    );
  }

  const HEATING_TANK_MIN = 27;
  const HEATING_TANK_MAX = 40;

  function syncHeatingTankSliderBounds(mainTempValue) {
    const heatingTankSlider = document.querySelector('[data-control-slider="heating_tank_setpoint"]');
    if (!heatingTankSlider) return;
    const mainTemp = Number(mainTempValue);
    const minAllowed = Math.min(
      HEATING_TANK_MAX,
      Math.max(HEATING_TANK_MIN, Number.isNaN(mainTemp) ? HEATING_TANK_MIN : Math.floor(mainTemp) + 1)
    );
    heatingTankSlider.min = String(minAllowed);
    heatingTankSlider.max = String(HEATING_TANK_MAX);
    const minLabel = document.getElementById("heating_tank_setpoint-min");
    if (minLabel) {
      minLabel.textContent = `${minAllowed}°C`;
    }
    const currentVal = Number(heatingTankSlider.value);
    if (Number.isNaN(currentVal) || currentVal < minAllowed) {
      heatingTankSlider.value = String(minAllowed);
      controlState.heating_tank_setpoint = minAllowed;
      updateSliderOutput(heatingTankSlider);
    } else if (currentVal > HEATING_TANK_MAX) {
      heatingTankSlider.value = String(HEATING_TANK_MAX);
      controlState.heating_tank_setpoint = HEATING_TANK_MAX;
      updateSliderOutput(heatingTankSlider);
    }
  }

  function updateSliders(sliders, serverSetpointTimes = null, serverIsSystemStart = null, serverAdjustingState = null) {
    if (!sliders) return;
    if (sliders.temperature_setpoint !== undefined && !Number.isNaN(Number(sliders.temperature_setpoint))) {
      controlState.temperature_setpoint = Number(sliders.temperature_setpoint);
      if (!serverSetpointTimes) {
        setpointTrackers.temperature.targetSetpoint = Number(sliders.temperature_setpoint);
      }
    }
    if (sliders.dissolved_oxygen_setpoint !== undefined && !Number.isNaN(Number(sliders.dissolved_oxygen_setpoint))) {
      controlState.dissolved_oxygen_setpoint = Number(sliders.dissolved_oxygen_setpoint);
      if (!serverSetpointTimes) {
        setpointTrackers.dissolved_oxygen.targetSetpoint = Number(sliders.dissolved_oxygen_setpoint);
      }
    }
    if (serverSetpointTimes) {
      syncSetpointTimes(serverSetpointTimes, serverIsSystemStart, sliders, serverAdjustingState);
    }
    const tempSlider = document.querySelector('[data-control-slider="temperature_setpoint"]');
    if (tempSlider && sliders.temperature_setpoint !== undefined) {
      tempSlider.value = sliders.temperature_setpoint;
      updateControlState(tempSlider, false);
      updateSliderOutput(tempSlider);
      syncHeatingTankSliderBounds(sliders.temperature_setpoint);
    }
    const heatingTankSlider = document.querySelector('[data-control-slider="heating_tank_setpoint"]');
    if (heatingTankSlider && sliders.heating_tank_setpoint !== undefined) {
      heatingTankSlider.value = sliders.heating_tank_setpoint;
      syncHeatingTankSliderBounds(controlState.temperature_setpoint);
      updateControlState(heatingTankSlider, false);
      updateSliderOutput(heatingTankSlider);
    }
    const doSlider = document.querySelector('[data-control-slider="dissolved_oxygen_setpoint"]');
    if (doSlider && sliders.dissolved_oxygen_setpoint !== undefined) {
      doSlider.value = sliders.dissolved_oxygen_setpoint;
      updateControlState(doSlider, false);
      updateSliderOutput(doSlider);
    }
    const ledSlider = document.querySelector('[data-control-slider="led_intensity"]');
    if (ledSlider && sliders.led_intensity !== undefined) {
      const index = ledLevels.indexOf(sliders.led_intensity);
      if (index !== -1) {
        ledSlider.value = index;
      }
      updateControlState(ledSlider, false);
      updateSliderOutput(ledSlider);
    }
  }

  async function loadControls() {
    try {
      const response = await fetch("/api/controls");
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      if (payload.sliders) {
        Object.entries(payload.sliders).forEach(([key, value]) => {
          controlState[key] = Number(value);
        });
        updateSliders(payload.sliders, payload.setpoint_times, payload.is_system_start, payload.adjusting_state);
      }
      if (payload.valves) {
        document.querySelectorAll("[data-valve]").forEach((button) => {
          const v = button.dataset.valve;
          if (v in payload.valves) {
            const isOpen = Boolean(payload.valves[v]);
            button.setAttribute("aria-pressed", String(isOpen));
            button.innerHTML = `<i></i> ${isOpen ? "Open" : "Closed"}`;
          }
        });
      }
    } catch (e) {
      console.warn("[DASHBOARD] Failed to load controls:", e);
    }
  }

  async function loadLatestReading() {
    try {
      const response = await fetch("/api/readings/latest");
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      if (payload.reading) {
        updateGauges(payload.reading);
      }
    } catch (e) {
      console.warn("[DASHBOARD] Failed to load latest reading:", e);
    }
  }

  async function loadDayHistory() {
    const trendHeading = document.getElementById("trend-date-label");
    try {
      const response = await fetch("/api/readings/trend");
      if (!response.ok) {
        if (trendHeading) {
          trendHeading.textContent = "No previous readings found";
        }
        return;
      }
      const payload = await response.json();
      dayHistory = payload.readings || [];
      const trendDate = payload.date;

      // Update trend section label to show which day the averages are from
      if (trendHeading) {
        if (trendDate) {
          const d = new Date(trendDate + "T00:00:00");
          const yesterday = new Date();
          yesterday.setDate(yesterday.getDate() - 1);
          const isYesterday = d.toDateString() === yesterday.toDateString();
          trendHeading.textContent = isYesterday
            ? "Yesterday's averages"
            : `Averages from ${d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}`;
        } else {
          trendHeading.textContent = "No previous readings found";
        }
      }

      renderTrends();
    } catch (e) {
      if (trendHeading) {
        trendHeading.textContent = "No previous readings found";
      }
      console.warn("[DASHBOARD] Failed to load day history:", e);
    }
  }

  function millisecondsUntilNextMidnight() {
    const now = new Date();
    const nextMidnight = new Date(now);
    nextMidnight.setHours(24, 0, 0, 0);
    return nextMidnight.getTime() - now.getTime();
  }

  function scheduleMidnightTrendRefresh() {
    if (trendRefreshTimeout) {
      clearTimeout(trendRefreshTimeout);
    }

    trendRefreshTimeout = setTimeout(async () => {
      await loadDayHistory();
      scheduleMidnightTrendRefresh();
    }, millisecondsUntilNextMidnight());
  }

  async function saveThreshold(metric) {
    const row = document.querySelector(`[data-threshold-row="${metric}"]`);
    if (!row) {
      return;
    }

    const minInput = row.querySelector('[data-threshold-field="min_value"]');
    const maxInput = row.querySelector('[data-threshold-field="max_value"]');
    const minValue = Number(minInput.value);
    const maxValue = Number(maxInput.value);
    if (Number.isNaN(minValue) || Number.isNaN(maxValue) || minValue >= maxValue) {
      row.classList.add("threshold-error");
      return;
    }

    row.classList.remove("threshold-error");
    const response = await fetch("/api/thresholds", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        [metric]: {
          min_value: minValue,
          max_value: maxValue,
        },
      }),
    });
    if (!response.ok) {
      row.classList.add("threshold-error");
      return;
    }

    const payload = await response.json();
    thresholds = payload.thresholds || [];
    setStoredItem(STORAGE_KEY_THRESHOLDS, JSON.stringify(thresholds));
    if (latestReading) {
      updateGauges(latestReading);
    }
    window.dispatchEvent(new CustomEvent("hatchery:thresholds", { detail: thresholds }));
  }

  function scheduleThresholdSave(metric) {
    if (thresholdSaveTimers.has(metric)) {
      clearTimeout(thresholdSaveTimers.get(metric));
    }
    thresholdSaveTimers.set(
      metric,
      setTimeout(() => {
        thresholdSaveTimers.delete(metric);
        saveThreshold(metric);
      }, 500)
    );
  }

  function bindThresholdInputs() {
    document.querySelectorAll("[data-threshold-row] .threshold-input").forEach((input) => {
      input.addEventListener("input", () => {
        const row = input.closest("[data-threshold-row]");
        if (!row) {
          return;
        }
        row.classList.remove("threshold-error");
        scheduleThresholdSave(row.dataset.thresholdRow);
      });

      input.addEventListener("change", () => {
        const row = input.closest("[data-threshold-row]");
        if (!row) {
          return;
        }
        if (thresholdSaveTimers.has(row.dataset.thresholdRow)) {
          clearTimeout(thresholdSaveTimers.get(row.dataset.thresholdRow));
          thresholdSaveTimers.delete(row.dataset.thresholdRow);
        }
        saveThreshold(row.dataset.thresholdRow);
      });
    });
  }

  function sliderDisplay(slider) {
    const key = slider.dataset.controlSlider;
    if (key === "temperature_setpoint" || key === "heating_tank_setpoint") {
      const val = Number(slider.value);
      return val % 1 === 0 ? `${val}°C` : `${val.toFixed(1)}°C`;
    }
    if (key === "dissolved_oxygen_setpoint") {
      const val = Number(slider.value);
      return `${val.toFixed(1)} mg/L`;
    }
    if (key === "led_intensity") {
      return `${ledLevels[Number(slider.value)]} lx`;
    }
    return slider.value;
  }

  function sliderPayloadValue(slider) {
    if (slider.dataset.controlSlider === "led_intensity") {
      return ledLevels[Number(slider.value)];
    }
    return Number(slider.value);
  }

  function updateControlState(slider, recordChange = true) {
    const key = slider.dataset.controlSlider;
    const value = sliderPayloadValue(slider);
    controlState[key] = value;

    if (key === "temperature_setpoint") {
      syncHeatingTankSliderBounds(value);
      if (recordChange) {
        recordSetpointChange("temperature", value);
      } else if (!Number.isNaN(Number(value))) {
        setpointTrackers.temperature.targetSetpoint = Number(value);
      }
      const item = thresholds.find((t) => t.metric === "temperature");
      if (item) {
        item.min_value = value;
        item.max_value = value;
        window.dispatchEvent(new CustomEvent("hatchery:thresholds", { detail: thresholds }));
      }
      if (latestReading) {
        updateGauge("temperature", latestReading);
        updateNavStatus(latestReading);
        checkAlertTriggers(latestReading);
      }
    } else if (key === "dissolved_oxygen_setpoint") {
      if (recordChange) {
        recordSetpointChange("dissolved_oxygen", value);
      } else if (!Number.isNaN(Number(value))) {
        setpointTrackers.dissolved_oxygen.targetSetpoint = Number(value);
      }
      const item = thresholds.find((t) => t.metric === "dissolved_oxygen");
      if (item) {
        item.min_value = value;
        item.max_value = value;
        window.dispatchEvent(new CustomEvent("hatchery:thresholds", { detail: thresholds }));
      }
      if (latestReading) {
        updateGauge("dissolved_oxygen", latestReading);
        updateNavStatus(latestReading);
        checkAlertTriggers(latestReading);
      }
    }
  }

  function updateSliderOutput(slider) {
    const output = document.getElementById(`${slider.dataset.controlSlider}-output`);
    if (output) {
      output.textContent = sliderDisplay(slider);
    }
  }

  async function postSliderState() {
    const payload = {};
    document.querySelectorAll("[data-control-slider]").forEach((slider) => {
      payload[slider.dataset.controlSlider] = sliderPayloadValue(slider);
    });
    const response = await fetch("/api/controls/sliders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (response.ok) {
      const data = await response.json();
      if (data.sliders) {
        updateSliders(data.sliders, data.setpoint_times, data.is_system_start, data.adjusting_state);
      }
    }
  }

  function bindControls() {
    document.querySelectorAll("[data-control-slider]").forEach((slider) => {
      updateControlState(slider, false);
      updateSliderOutput(slider);
      slider.addEventListener("input", () => {
        updateControlState(slider, true);
        updateSliderOutput(slider);
        if (latestReading) {
          updateGauges(latestReading);
        }
      });
      slider.addEventListener("change", postSliderState);
    });

    document.querySelectorAll("[data-valve]").forEach((button) => {
      button.addEventListener("click", async () => {
        const nextState = button.getAttribute("aria-pressed") !== "true";
        const response = await fetch(`/api/controls/valves/${button.dataset.valve}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ open: nextState }),
        });
        if (!response.ok) {
          return;
        }
        button.setAttribute("aria-pressed", String(nextState));
        button.innerHTML = `<i></i> ${nextState ? "Open" : "Closed"}`;
      });
    });
  }

  function bindSocket() {
    if (typeof io !== "function") {
      updateSocketStatus(false);
      return;
    }

    const socket =
      window.hatcherySocket ||
      io({
        reconnection: true,
        reconnectionAttempts: Infinity,
        reconnectionDelay: 500,
        reconnectionDelayMax: 1500,
        randomizationFactor: 0.1,
      });
    window.hatcherySocket = socket;
    if (socketBound) {
      return;
    }

    socketBound = true;
    socket.on("server_ready", (data) => {
      const prevBoot = sessionStorage.getItem("hatchery:server_boot");
      if (data && data.boot_id) {
        const newBoot = String(data.boot_id);
        if (prevBoot && prevBoot !== newBoot) {
          sessionStorage.setItem("hatchery:server_boot", newBoot);
          window.location.reload();
          return;
        }
        sessionStorage.setItem("hatchery:server_boot", newBoot);
      }
    });
    socket.on("connect", async () => {
      updateSocketStatus(true);
      window.dispatchEvent(new CustomEvent("hatchery:socket-connected"));
      try {
        if (!isEditingThresholds()) {
          await loadThresholds();
        }
        await loadControls();
        await loadLatestReading();
      } catch (e) {
        console.warn("[DASHBOARD] Socket connect sync error:", e);
      }
      loadDayHistory();
      loadNotificationsFromDB();
      hasSocketConnected = true;
    });
    socket.on("disconnect", () => updateSocketStatus(false));
    socket.on("connect_error", () => updateSocketStatus(false));
    updateSocketStatus(Boolean(socket.connected));

    socket.on("controls_updated", (data) => {
      if (!data) return;
      if (data.sliders) {
        Object.entries(data.sliders).forEach(([key, value]) => {
          controlState[key] = Number(value);
        });
        updateSliders(data.sliders, data.setpoint_times, data.is_system_start, data.adjusting_state);
        if (data.sliders.temperature_setpoint !== undefined) {
          const tItem = thresholds.find((t) => t.metric === "temperature");
          if (tItem) {
            tItem.min_value = Number(data.sliders.temperature_setpoint);
            tItem.max_value = Number(data.sliders.temperature_setpoint);
          }
        }
        if (data.sliders.dissolved_oxygen_setpoint !== undefined) {
          const doItem = thresholds.find((t) => t.metric === "dissolved_oxygen");
          if (doItem) {
            doItem.min_value = Number(data.sliders.dissolved_oxygen_setpoint);
            doItem.max_value = Number(data.sliders.dissolved_oxygen_setpoint);
          }
        }
        window.dispatchEvent(new CustomEvent("hatchery:thresholds", { detail: thresholds }));
        if (latestReading) {
          checkAlertTriggers(latestReading);
          updateGauges(latestReading);
        }
      }
      if (data.valves) {
        document.querySelectorAll("[data-valve]").forEach((button) => {
          const v = button.dataset.valve;
          if (v in data.valves) {
            const isOpen = Boolean(data.valves[v]);
            button.setAttribute("aria-pressed", String(isOpen));
            button.innerHTML = `<i></i> ${isOpen ? "Open" : "Closed"}`;
          }
        });
      }
    });

    socket.on("thresholds_updated", (data) => {
      if (!data || !data.thresholds) return;
      thresholds = data.thresholds;
      setStoredItem(STORAGE_KEY_THRESHOLDS, JSON.stringify(thresholds));
      thresholds.forEach((item) => {
        if (item.metric === "temperature" && item.min_value !== undefined) {
          controlState.temperature_setpoint = Number(item.min_value);
          setpointTrackers.temperature.targetSetpoint = Number(item.min_value);
        } else if (item.metric === "dissolved_oxygen" && item.min_value !== undefined) {
          controlState.dissolved_oxygen_setpoint = Number(item.min_value);
          setpointTrackers.dissolved_oxygen.targetSetpoint = Number(item.min_value);
        }
      });
      if (!isEditingThresholds()) {
        renderThresholds();
      }
      if (latestReading) {
        checkAlertTriggers(latestReading);
        updateGauges(latestReading);
      }
      window.dispatchEvent(new CustomEvent("hatchery:thresholds", { detail: thresholds }));
    });

    socket.on("sensor_update", (reading) => {
      updateGauges(reading);
      
      if (reading && reading.id !== undefined && reading.timestamp) {
        //dayHistory.push(reading);
        //const now = new Date();
        /*if (window.TRENDS_MODE === "yesterday") {
          const yesterdayStart = new Date(now);
          yesterdayStart.setDate(yesterdayStart.getDate() - 1);
          yesterdayStart.setHours(0, 0, 0, 0);
          
          const yesterdayEnd = new Date(now);
          yesterdayEnd.setDate(yesterdayEnd.getDate() - 1);
          yesterdayEnd.setHours(23, 59, 59, 999);
          
          dayHistory = dayHistory.filter((r) => {
            const t = new Date(r.timestamp);
            return t >= yesterdayStart && t <= yesterdayEnd;
          });
        } else {
          const limit = now.getTime() - 24 * 60 * 60 * 1000;
          dayHistory = dayHistory.filter((r) => new Date(r.timestamp).getTime() >= limit);
        }*/
        //renderTrends();
      } else {
        loadDayHistory();
      }
      
      if (document.querySelector('[data-page="graph"]')) {
        window.dispatchEvent(new CustomEvent("hatchery:reading", { detail: reading }));
      }
    });
  }

  function initializeHomeSlider() {
    const viewport = document.getElementById("home-viewport");
    if (!viewport) return;

    const slides = viewport.querySelectorAll(".screen-slide");
    const pills = document.querySelectorAll("#home-screen-nav .screen-nav-pill");
    const prevBtn = document.getElementById("home-prev-btn");
    const nextBtn = document.getElementById("home-next-btn");
    const counter = document.getElementById("home-screen-counter");

    let currentIdx = 0;
    const totalSlides = slides.length;

    function updateNav(idx) {
      currentIdx = idx;
      pills.forEach((pill, i) => {
        if (i === idx) {
          pill.classList.add("active");
          pill.setAttribute("aria-current", "true");
        } else {
          pill.classList.remove("active");
          pill.removeAttribute("aria-current");
        }
      });

      if (counter) {
        counter.textContent = `${idx + 1} / ${totalSlides}`;
      }

      if (prevBtn) {
        prevBtn.style.opacity = idx === 0 ? "0.35" : "1";
        prevBtn.style.pointerEvents = idx === 0 ? "none" : "auto";
      }
      if (nextBtn) {
        nextBtn.style.opacity = idx === totalSlides - 1 ? "0.35" : "1";
        nextBtn.style.pointerEvents = idx === totalSlides - 1 ? "none" : "auto";
      }
    }

    function scrollToSlide(idx, smooth = true) {
      if (idx < 0) idx = 0;
      if (idx >= totalSlides) idx = totalSlides - 1;
      currentIdx = idx;
      const targetSlide = slides[idx];
      if (targetSlide) {
        viewport.scrollTo({
          left: targetSlide.offsetLeft,
          behavior: smooth ? "smooth" : "auto"
        });
      }
      updateNav(idx);
    }

    pills.forEach((pill, i) => {
      pill.addEventListener("click", () => scrollToSlide(i, true));
    });

    if (prevBtn) {
      prevBtn.addEventListener("click", () => scrollToSlide(currentIdx - 1, true));
    }
    if (nextBtn) {
      nextBtn.addEventListener("click", () => scrollToSlide(currentIdx + 1, true));
    }

    let scrollRaf = null;
    viewport.addEventListener("scroll", () => {
      if (scrollRaf) cancelAnimationFrame(scrollRaf);
      scrollRaf = requestAnimationFrame(() => {
        const width = viewport.clientWidth;
        if (width > 0) {
          const newIdx = Math.round(viewport.scrollLeft / width);
          if (newIdx !== currentIdx && newIdx >= 0 && newIdx < totalSlides) {
            updateNav(newIdx);
          }
        }
      });
    }, { passive: true });

    // Touch swipe gesture handling
    let touchStartX = 0;
    let touchStartY = 0;
    let isSwiping = false;

    viewport.addEventListener("touchstart", (e) => {
      if (e.target.closest("input[type='range']") || e.target.closest("button") || e.target.closest("input")) {
        isSwiping = false;
        return;
      }
      touchStartX = e.touches[0].clientX;
      touchStartY = e.touches[0].clientY;
      isSwiping = true;
    }, { passive: true });

    viewport.addEventListener("touchend", (e) => {
      if (!isSwiping) return;
      isSwiping = false;
      const touchEndX = e.changedTouches[0].clientX;
      const touchEndY = e.changedTouches[0].clientY;
      const diffX = touchEndX - touchStartX;
      const diffY = touchEndY - touchStartY;

      if (Math.abs(diffX) > 40 && Math.abs(diffX) > Math.abs(diffY) * 1.3) {
        if (diffX < 0) {
          // Swiped left -> scroll to next slide to the right
          scrollToSlide(currentIdx + 1, true);
        } else {
          // Swiped right -> scroll to prev slide to the left
          scrollToSlide(currentIdx - 1, true);
        }
      }
    }, { passive: true });

    updateNav(0);
  }

  function bindFullscreenUI() {
    const btn = document.getElementById("fullscreen-toggle-btn");
    if (!btn) return;

    function updateFullscreenIcon() {
      const isFullscreen = Boolean(document.fullscreenElement);
      btn.title = isFullscreen ? "Exit fullscreen" : "Toggle fullscreen";
      btn.setAttribute("aria-label", isFullscreen ? "Exit fullscreen" : "Toggle fullscreen");
      const wrap = btn.querySelector(".fullscreen-icon-wrap");
      if (wrap) {
        wrap.innerHTML = isFullscreen
          ? `<svg viewBox="0 0 24 24" aria-hidden="true" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3"/>
            </svg>`
          : `<svg viewBox="0 0 24 24" aria-hidden="true" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
            </svg>`;
      }
    }

    btn.addEventListener("click", () => {
      if (!document.fullscreenElement) {
        if (document.documentElement.requestFullscreen) {
          document.documentElement.requestFullscreen().catch((err) => {
            console.warn("[FULLSCREEN] Error entering fullscreen:", err);
          });
        }
      } else {
        if (document.exitFullscreen) {
          document.exitFullscreen().catch((err) => {
            console.warn("[FULLSCREEN] Error exiting fullscreen:", err);
          });
        }
      }
    });

    document.addEventListener("fullscreenchange", updateFullscreenIcon);
  }

  document.addEventListener("DOMContentLoaded", async () => {
    updateDateTime();
    setInterval(updateDateTime, 30000);
    bindControls();
    initializeHomeSlider();
    bindNotificationUI();
    bindFullscreenUI();
    bindSocket();
    try {
      await Promise.allSettled([loadControls(), loadThresholds()]);
      await loadLatestReading();
    } catch (e) {
      console.warn("[DASHBOARD] Init sync error:", e);
    }
    Promise.allSettled([
      loadDayHistory(),
      loadNotificationsFromDB(),
    ]).then(scheduleMidnightTrendRefresh);
  });
  if (typeof window !== "undefined") {
    window.HatcheryRateOfChange = {
      ALLOWED_ADJUSTING_TIME_MS,
      setpointTrackers,
      recordSetpointChange,
      isRateOfChangeAdjusting,
      syncSetpointTimes,
      updateNavStatus,
      setThresholds,
      checkAlertTriggers,
      minimizeOverlay,
      dismissOverlay,
      showOverlay,
    };
  }
})();
