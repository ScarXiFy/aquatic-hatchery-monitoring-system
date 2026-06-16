(function () {
  const metrics = window.HATCHERY_METRICS || {};
  const ledLevels = [0, 100, 500, 1000, 3000];
  let thresholds = [];
  let latestReading = null;
  let dayHistory = [];
  let socketBound = false;
  let trendRefreshTimeout = null;
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
    const formatted = Number(value).toFixed(config.decimals);
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
    const threshold = thresholdFor(metric);
    if (!threshold || value === null || value === undefined || Number.isNaN(Number(value))) {
      return "neutral";
    }

    const numericValue = Number(value);
    const min = Number(threshold.min_value);
    const max = Number(threshold.max_value);
    if (numericValue < min || numericValue > max) {
      return "critical";
    }

    const range = max - min;
    if (range <= 0) {
      return numericValue === min ? "optimal" : "critical";
    }

    const warningSize = range * 0.2;
    const inLowerWarningZone = numericValue >= min && numericValue <= min + warningSize;
    const inUpperWarningZone = numericValue <= max && numericValue >= max - warningSize;
    return inLowerWarningZone || inUpperWarningZone ? "warning" : "optimal";
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
    const ratio = (value - config.min) / (config.max - config.min);
    return Math.max(0, Math.min(180, ratio * 180));
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

  function updateGauges(reading) {
    latestReading = reading;
    Object.keys(metrics).forEach((metric) => updateGauge(metric, reading));
  }

  function metricStats(metric, readings) {
    const values = readings.map((reading) => Number(reading[metric])).filter((value) => !Number.isNaN(value));
    if (!values.length) {
      return { current: null, min: null, max: null };
    }
    return {
      current: values[values.length - 1],
      min: Math.min(...values),
      max: Math.max(...values),
    };
  }

  function renderTrend(metric, stats) {
    const row = document.querySelector(`[data-trend-metric="${metric}"]`);
    const config = metrics[metric];
    if (!row || !config) {
      return;
    }

    if (stats.current === null || stats.min === null || stats.max === null) {
      row.className = "trend-row is-empty";
      row.textContent = `${config.shortLabel}: waiting for logged readings`;
      return;
    }

    const range = stats.max - stats.min;
    const position = range === 0 ? 50 : ((stats.current - stats.min) / range) * 100;
    row.className = "trend-row";
    row.style.setProperty("--trend-position", `${Math.max(0, Math.min(100, position))}%`);
    row.innerHTML = `
      <div class="trend-head">
        <span class="trend-label">${config.shortLabel}</span>
        <span class="trend-value">${formatValue(metric, stats.current)}</span>
      </div>
      <div class="trend-track">
        <span class="trend-fill"></span>
        <span class="trend-thumb"></span>
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
        const value = latestReading ? Number(latestReading[item.metric]) : null;
        const state = conditionFor(item.metric, value);
        const label = state === "critical" ? "Critical" : state === "warning" ? "Warning" : state === "optimal" ? "Optimal" : "Waiting";
        const stateClass = state === "critical" ? "status-critical" : state === "warning" ? "status-warning" : state === "neutral" ? "status-neutral" : "";
        return `
          <tr data-threshold-row="${item.metric}">
            <td><strong>${config.label}</strong></td>
            <td><input class="threshold-input" data-threshold-field="min_value" type="number" step="0.1" value="${formatValue(item.metric, item.min_value, false)}" aria-label="${config.label} minimum value"></td>
            <td><input class="threshold-input" data-threshold-field="max_value" type="number" step="0.1" value="${formatValue(item.metric, item.max_value, false)}" aria-label="${config.label} maximum value"></td>
            <td>${config.unit}</td>
            <td>
              <span class="status-badge ${stateClass}">${label}</span>
              <button class="threshold-save" data-threshold-save="${item.metric}" type="button">Save</button>
            </td>
          </tr>
        `;
      })
      .join("") || `<tr><td colspan="5">No pH or salinity thresholds available.</td></tr>`;
    bindThresholdSaves();
  }

  async function loadThresholds() {
    const response = await fetch("/api/thresholds");
    const payload = await response.json();
    thresholds = payload.thresholds || [];
    renderThresholds();
    window.dispatchEvent(new CustomEvent("hatchery:thresholds", { detail: thresholds }));
  }

  async function loadLatestReading() {
    const response = await fetch("/api/readings/latest");
    const payload = await response.json();
    if (payload.reading) {
      updateGauges(payload.reading);
    }
  }

  async function loadDayHistory() {
    const response = await fetch("/api/readings/history?range=day");
    const payload = await response.json();
    dayHistory = payload.readings || [];
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
    renderThresholds();
    if (latestReading) {
      updateGauges(latestReading);
    }
    window.dispatchEvent(new CustomEvent("hatchery:thresholds", { detail: thresholds }));
  }

  function bindThresholdSaves() {
    document.querySelectorAll("[data-threshold-save]").forEach((button) => {
      button.addEventListener("click", () => saveThreshold(button.dataset.thresholdSave));
    });
  }

  function sliderDisplay(slider) {
    const key = slider.dataset.controlSlider;
    if (key === "temperature_setpoint") {
      return `${Number(slider.value).toFixed(0)}°C`;
    }
    if (key === "dissolved_oxygen_setpoint") {
      return `${Number(slider.value).toFixed(1)} mg/L`;
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
    controlState[slider.dataset.controlSlider] = sliderPayloadValue(slider);
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
      updateControlState(slider);
      payload[slider.dataset.controlSlider] = sliderPayloadValue(slider);
    });
    await fetch("/api/controls/sliders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
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
    socket.on("connect", () => updateSocketStatus(true));
    socket.on("disconnect", () => updateSocketStatus(false));
    socket.on("connect_error", () => updateSocketStatus(false));
    updateSocketStatus(Boolean(socket.connected));
    socket.on("sensor_update", (reading) => {
      updateGauges(reading);
      if (document.querySelector('[data-page="graph"]')) {
        window.dispatchEvent(new CustomEvent("hatchery:reading", { detail: reading }));
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    updateDateTime();
    setInterval(updateDateTime, 30000);
    bindControls();
    loadThresholds().then(loadLatestReading);
    loadDayHistory().then(scheduleMidnightTrendRefresh);
    bindSocket();
  });
})();
