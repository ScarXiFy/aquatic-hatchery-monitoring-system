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

  function updateGauges(reading) {
    latestReading = reading;
    Object.keys(metrics).forEach((metric) => updateGauge(metric, reading));
    updateNavStatus(reading);
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
        dayHistory.push(reading);
        const now = new Date();
        if (window.TRENDS_MODE === "yesterday") {
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
        }
        renderTrends();
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
    loadControls()
      .then(loadThresholds)
      .then(loadLatestReading);
    loadDayHistory().then(scheduleMidnightTrendRefresh);
    bindSocket();
  });
})();
