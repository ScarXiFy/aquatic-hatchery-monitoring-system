(function () {
  const metrics = window.HATCHERY_METRICS || {};
  const ledLevels = [0, 100, 500, 1000, 3000];
  let thresholds = [];
  let latestReading = null;
  let dayHistory = [];

  function formatValue(metric, value, includeUnit = true) {
    const config = metrics[metric];
    if (!config || value === null || value === undefined || Number.isNaN(Number(value))) {
      return "--";
    }
    const formatted = Number(value).toFixed(config.decimals);
    return includeUnit && config.unit ? `${formatted} ${config.unit}` : formatted;
  }

  function thresholdFor(metric) {
    return thresholds.find((item) => item.metric === metric);
  }

  function statusFor(metric, value) {
    const threshold = thresholdFor(metric);
    if (!threshold || value === null || value === undefined || Number.isNaN(Number(value))) {
      return "neutral";
    }
    return value < threshold.min_value || value > threshold.max_value ? "warning" : "optimal";
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
    const state = statusFor(metric, value);
    valueElement.textContent = formatValue(metric, value, false);
    setBadgeState(statusElement, state);
    gaugeElement.style.setProperty("--gauge-progress", `${gaugeDegrees(metric, value)}deg`);
    gaugeElement.style.setProperty("--gauge-color", state === "warning" ? "#fbbf24" : "#10b981");
  }

  function updateGauges(reading) {
    latestReading = reading;
    Object.keys(metrics).forEach((metric) => updateGauge(metric, reading));
    renderThresholds();
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

  async function updateThreshold(metric, field, value) {
    const response = await fetch(`/api/threshold/${metric}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ field, value: Number(value) }),
    });
    if (!response.ok) {
      alert(`Failed to update ${metric} threshold`);
      return;
    }
    const entry = thresholds.find((item) => item.metric === metric);
    if (entry) {
      entry[field] = Number(value);
    }
  }

  function renderThresholds() {
    const body = document.getElementById("threshold-table-body");
    if (!body) {
      return;
    }
    const rows = thresholds
      .filter((item) => item.metric === "ph" || item.metric === "salinity")
      .map((item) => {
        const config = metrics[item.metric] || { label: item.metric, unit: "" };
        const value = latestReading ? Number(latestReading[item.metric]) : null;
        const state = statusFor(item.metric, value);
        const label = state === "warning" ? "Warning" : state === "optimal" ? "Optimal" : "Waiting";
        const step = item.metric === "ph" ? "1" : "0.1";
        const minAttr = item.metric === "ph" ? 'min="1" max="14"' : "";
        return `
          <tr>
            <td><strong>${config.label}</strong></td>
            <td><input class="threshold-input" type="number" step="${step}" ${minAttr} value="${item.min_value}" data-metric="${item.metric}" data-field="min_value"></td>
            <td><input class="threshold-input" type="number" step="${step}" ${minAttr} value="${item.max_value}" data-metric="${item.metric}" data-field="max_value"></td>
            <td>${config.unit}</td>
            <td><span class="status-badge ${state === "warning" ? "status-warning" : state === "neutral" ? "status-neutral" : ""}">${label}</span></td>
          </tr>
        `;
      })
      .join("") || `<tr><td colspan="5">No pH or salinity thresholds available.</td></tr>`;

    body.innerHTML = rows;

    body.querySelectorAll("input.threshold-input").forEach((input) => {
      input.addEventListener("change", () => {
        updateThreshold(input.dataset.metric, input.dataset.field, input.value);
      });
    });
  }

  async function loadThresholds() {
    const response = await fetch("/api/thresholds");
    const payload = await response.json();
    thresholds = payload.thresholds || [];
    renderThresholds();
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
    await fetch("/api/controls/sliders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  function bindControls() {
    document.querySelectorAll("[data-control-slider]").forEach((slider) => {
      updateSliderOutput(slider);
      slider.addEventListener("input", () => updateSliderOutput(slider));
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
        button.querySelector("strong").innerHTML = `<i></i> ${nextState ? "Open" : "Closed"}`;
      });
    });
  }

  function bindSocket() {
    if (typeof io !== "function") {
      return;
    }
    const socket = io();
    socket.on("sensor_update", (reading) => {
      updateGauges(reading);
      dayHistory.push(reading);
      if (dayHistory.length > 240) {
        dayHistory.shift();
      }
      renderTrends();
      window.dispatchEvent(new CustomEvent("hatchery:reading", { detail: reading }));
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    updateDateTime();
    setInterval(updateDateTime, 30000);
    bindControls();
    loadThresholds().then(loadLatestReading);
    loadDayHistory();
    bindSocket();
  });
})();
