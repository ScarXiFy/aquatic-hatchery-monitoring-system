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

  function formatValue(metric, value, includeUnit = true) {
    const config = metrics[metric];
    if (!config || value === null || value === undefined || Number.isNaN(Number(value))) {
      return "--";
    }
    let formatted;
    const num = Number(value);
    if (metric === "temperature") {
      formatted = num % 1 === 0 ? num.toFixed(0) : num.toFixed(1);
    } else {
      formatted = num.toFixed(config.decimals);
    }
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
      const warning_limit = metric === "temperature" ? 0.5 : 0.3;
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
      if (v <= min + buffer || v >= max - buffer) {
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
    element.classList.remove("status-neutral", "status-warning", "status-critical");
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
    const state = conditionFor(metric, value);
    valueElement.textContent = formatValue(metric, value, false);
    setBadgeState(statusElement, state);
    gaugeElement.style.setProperty("--gauge-progress", `${gaugeDegrees(metric, value)}deg`);
    const gaugeColor = state === "critical" ? "#ef4444" : state === "warning" ? "#fbbf24" : "#10b981";
    gaugeElement.style.setProperty("--gauge-color", gaugeColor);
  }

  function updateNavStatus(reading) {
    const indicator = document.getElementById("status-indicator-nav");
    if (!indicator) {
      return;
    }

    const states = Object.keys(metrics).map((metric) => conditionFor(metric, Number(reading[metric])));
    const overallState = states.includes("critical")
      ? "critical"
      : states.includes("warning")
      ? "warning"
      : states.every((s) => s === "neutral")
      ? "neutral"
      : "optimal";

    indicator.classList.remove("status-warning", "status-critical", "status-neutral");

    if (overallState === "critical") {
      indicator.classList.add("status-critical");
      indicator.lastChild.textContent = " Critical";
    } else if (overallState === "warning") {
      indicator.classList.add("status-warning");
      indicator.lastChild.textContent = " Warning";
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

  const previousMetricStates = {};
  const activeAlertsMap = new Map();
  let notificationHistory = [];
  let unreadCount = 0;
  let isAlertMinimized = false;
  let currentAlertModalState = "closed";

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

  function renderAlertModalBody() {
    const body = document.getElementById("alert-modal-body");
    if (!body) return;
    const items = Array.from(activeAlertsMap.values());
    if (!items.length) {
      body.innerHTML = "<p class='alert-modal-item'>No active alerts.</p>";
      return;
    }
    body.innerHTML = items
      .map(
        (item) => `
        <div class="alert-modal-item" data-status="${item.status}">
          ${item.text}
        </div>
      `
      )
      .join("");
  }

  function showAlertModal() {
    renderAlertModalBody();
    const overlay = document.getElementById("alert-modal-overlay");
    const docked = document.getElementById("alert-docked-card");
    if (overlay) overlay.hidden = false;
    if (docked) docked.hidden = true;
    isAlertMinimized = false;
    currentAlertModalState = "modal";
  }

  function minimizeAlertModal() {
    const overlay = document.getElementById("alert-modal-overlay");
    const docked = document.getElementById("alert-docked-card");
    const dockedText = document.getElementById("alert-docked-text");
    if (overlay) overlay.hidden = true;
    if (docked) docked.hidden = false;

    const items = Array.from(activeAlertsMap.values());
    if (dockedText) {
      if (items.length === 1) {
        dockedText.textContent = items[0].text;
      } else if (items.length > 1) {
        dockedText.textContent = `${items.length} Active Alerts: ${items.map((i) => i.parameter).join(", ")}`;
      } else {
        dockedText.textContent = "System Alert";
      }
    }
    isAlertMinimized = true;
    currentAlertModalState = "docked";
  }

  function updateDockedCard() {
    if (currentAlertModalState === "docked") {
      minimizeAlertModal();
    }
  }

  function dismissAlertModal() {
    const overlay = document.getElementById("alert-modal-overlay");
    const docked = document.getElementById("alert-docked-card");
    if (overlay) overlay.hidden = true;
    if (docked) docked.hidden = true;

    const items = Array.from(activeAlertsMap.values());
    items.forEach((item) => {
      notificationHistory.unshift({
        id: Date.now() + Math.random(),
        text: item.text,
        status: item.status,
        parameter: item.parameter,
        time: item.time,
      });
      unreadCount++;
    });

    activeAlertsMap.clear();
    isAlertMinimized = false;
    currentAlertModalState = "closed";
    updateNotificationBellUI();
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
          <div class="notification-item">
            <span class="notification-item-text">${n.text}</span>
          </div>
        `
          )
          .join("");
      }
    }
  }

  function checkAlertTriggers(reading) {
    if (!reading) return;
    const timeFormatted = formatTimestamp(reading.timestamp);
    let hasNewAlertState = false;

    Object.keys(metricDisplayNames).forEach((metric) => {
      const val = Number(reading[metric]);
      const state = conditionFor(metric, val);
      const prevState = previousMetricStates[metric];

      if (state === "warning" || state === "critical") {
        const statusText = state === "critical" ? "Critical" : "Warning";
        const paramName = metricDisplayNames[metric];
        const formattedText = `Status: ${statusText} | Parameter: ${paramName} | Time: ${timeFormatted}`;

        if (!prevState || prevState === "optimal" || prevState === "neutral" || prevState !== state) {
          hasNewAlertState = true;
        }

        activeAlertsMap.set(metric, {
          metric,
          status: statusText,
          parameter: paramName,
          time: timeFormatted,
          text: formattedText,
        });
      } else if (state === "optimal") {
        activeAlertsMap.delete(metric);
      }
      previousMetricStates[metric] = state;
    });

    if (activeAlertsMap.size > 0) {
      if (hasNewAlertState) {
        if (!isAlertMinimized) {
          showAlertModal();
        } else {
          updateDockedCard();
        }
      } else if (currentAlertModalState === "modal") {
        renderAlertModalBody();
      } else if (currentAlertModalState === "docked") {
        updateDockedCard();
      }
    } else {
      if (currentAlertModalState === "modal" || currentAlertModalState === "docked") {
        const overlay = document.getElementById("alert-modal-overlay");
        const docked = document.getElementById("alert-docked-card");
        if (overlay) overlay.hidden = true;
        if (docked) docked.hidden = true;
        currentAlertModalState = "closed";
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
          unreadCount = 0;
          updateNotificationBellUI();
        }
      });

      document.addEventListener("click", (e) => {
        if (!dropdown.contains(e.target) && !bellBtn.contains(e.target)) {
          dropdown.hidden = true;
        }
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        notificationHistory = [];
        unreadCount = 0;
        updateNotificationBellUI();
      });
    }

    const minBtn = document.getElementById("alert-modal-minimize-btn");
    const closeBtn = document.getElementById("alert-modal-close-btn");
    const expandTrigger = document.getElementById("alert-docked-expand-trigger");
    const dockedCloseBtn = document.getElementById("alert-docked-close-btn");

    if (minBtn) minBtn.addEventListener("click", minimizeAlertModal);
    if (closeBtn) closeBtn.addEventListener("click", dismissAlertModal);
    if (expandTrigger) expandTrigger.addEventListener("click", showAlertModal);
    if (dockedCloseBtn) dockedCloseBtn.addEventListener("click", dismissAlertModal);
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

  async function loadThresholds() {
    const response = await fetch("/api/thresholds");
    const payload = await response.json();
    thresholds = payload.thresholds || [];
    renderThresholds();
    window.dispatchEvent(new CustomEvent("hatchery:thresholds", { detail: thresholds }));
  }

  function isEditingThresholds() {
    return Boolean(
      thresholdSaveTimers.size ||
        (document.activeElement && document.activeElement.classList.contains("threshold-input"))
    );
  }

  function updateSliders(sliders) {
    if (!sliders) return;
    const tempSlider = document.querySelector('[data-control-slider="temperature_setpoint"]');
    if (tempSlider && sliders.temperature_setpoint !== undefined) {
      tempSlider.value = sliders.temperature_setpoint;
      updateControlState(tempSlider);
      updateSliderOutput(tempSlider);
    }
    const doSlider = document.querySelector('[data-control-slider="dissolved_oxygen_setpoint"]');
    if (doSlider && sliders.dissolved_oxygen_setpoint !== undefined) {
      doSlider.value = sliders.dissolved_oxygen_setpoint;
      updateControlState(doSlider);
      updateSliderOutput(doSlider);
    }
    const ledSlider = document.querySelector('[data-control-slider="led_intensity"]');
    if (ledSlider && sliders.led_intensity !== undefined) {
      const index = ledLevels.indexOf(sliders.led_intensity);
      if (index !== -1) {
        ledSlider.value = index;
      }
      updateControlState(ledSlider);
      updateSliderOutput(ledSlider);
    }
  }

  async function loadControls() {
    const response = await fetch("/api/controls");
    const payload = await response.json();
    if (payload.sliders) {
      updateSliders(payload.sliders);
    }
  }

  async function loadLatestReading() {
    const response = await fetch("/api/readings/latest");
    const payload = await response.json();
    if (payload.reading) {
      updateGauges(payload.reading);
    }
  }

  async function loadDayHistory() {
    const response = await fetch("/api/readings/trend");
    const payload = await response.json();
    dayHistory = payload.readings || [];
    const trendDate = payload.date;

    // Update trend section label to show which day the averages are from
    const trendHeading = document.getElementById("trend-date-label");
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
    if (key === "temperature_setpoint") {
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

  function updateControlState(slider) {
    const key = slider.dataset.controlSlider;
    const value = sliderPayloadValue(slider);
    controlState[key] = value;

    if (key === "temperature_setpoint") {
      const item = thresholds.find((t) => t.metric === "temperature");
      if (item) {
        item.min_value = value;
        item.max_value = value;
        window.dispatchEvent(new CustomEvent("hatchery:thresholds", { detail: thresholds }));
      }
    } else if (key === "dissolved_oxygen_setpoint") {
      const item = thresholds.find((t) => t.metric === "dissolved_oxygen");
      if (item) {
        item.min_value = value;
        item.max_value = value;
        window.dispatchEvent(new CustomEvent("hatchery:thresholds", { detail: thresholds }));
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
        updateSliders(data.sliders);
      }
    }
  }

  function bindControls() {
    document.querySelectorAll("[data-control-slider]").forEach((slider) => {
      updateControlState(slider);
      updateSliderOutput(slider);
      slider.addEventListener("input", () => {
        updateControlState(slider);
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

    const socket = window.hatcherySocket || io();
    window.hatcherySocket = socket;
    if (socketBound) {
      return;
    }

    socketBound = true;
    socket.on("connect", () => {
      updateSocketStatus(true);
      if (hasSocketConnected && !isEditingThresholds()) {
        loadThresholds();
      }
      hasSocketConnected = true;
    });
    socket.on("disconnect", () => updateSocketStatus(false));
    socket.on("connect_error", () => updateSocketStatus(false));
    updateSocketStatus(Boolean(socket.connected));
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

  document.addEventListener("DOMContentLoaded", () => {
    updateDateTime();
    setInterval(updateDateTime, 30000);
    bindControls();
    bindNotificationUI();
    loadControls()
      .then(loadThresholds)
      .then(loadLatestReading);
    loadDayHistory().then(scheduleMidnightTrendRefresh);
    bindSocket();
  });
})();
