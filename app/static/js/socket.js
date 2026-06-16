let thresholds = [];

function getThreshold(metric) {
  return thresholds.find((item) => item.metric === metric);
}

function isWarning(metric, value) {
  const threshold = getThreshold(metric);
  return Boolean(threshold && (value < threshold.min_value || value > threshold.max_value));
}

function getGaugeDegrees(metric, value) {
  const metricConfig = METRICS[metric];
  const span = metricConfig.gaugeMax - metricConfig.gaugeMin;
  const ratio = span > 0 ? (value - metricConfig.gaugeMin) / span : 0;
  return Math.max(0, Math.min(260, ratio * 260));
}

function updateLiveCards(reading) {
  Object.keys(METRICS).forEach((metric) => {
    const value = document.getElementById(`${metric}-value`);
    const status = document.getElementById(`${metric}-status`);
    const card = document.querySelector(`[data-metric-card="${metric}"]`);
    const gauge = card?.querySelector(".gauge");
    if (!value || !status || !card || !gauge) {
      return;
    }

    const numericValue = Number(reading[metric]);
    const warning = isWarning(metric, numericValue);
    value.textContent = Number(numericValue).toFixed(METRICS[metric].decimals);
    status.textContent = warning ? "Warning" : "Optimal";
    status.classList.toggle("warning", warning);
    status.classList.toggle("neutral", false);
    gauge.style.setProperty("--gauge-progress", `${getGaugeDegrees(metric, numericValue)}deg`);
    gauge.style.setProperty("--gauge-color", warning ? "var(--amber)" : "var(--green)");
  });
}

async function loadThresholds() {
  const response = await fetch("/api/thresholds");
  const payload = await response.json();
  thresholds = payload.thresholds || [];

  const tbody = document.getElementById("threshold-table-body");
  if (tbody) {
    tbody.innerHTML = thresholds
      .map((item) => {
        const metricConfig = METRICS[item.metric];
        const latest = latestHistory.length ? latestHistory[latestHistory.length - 1] : null;
        const warning = latest ? isWarning(item.metric, Number(latest[item.metric])) : false;
        return `
          <tr>
            <td><strong>${metricConfig?.label || item.metric}</strong></td>
            <td><input value="${item.min_value}" aria-label="${metricConfig?.label || item.metric} minimum threshold" readonly></td>
            <td><input value="${item.max_value}" aria-label="${metricConfig?.label || item.metric} maximum threshold" readonly></td>
            <td>${metricConfig?.unit || ""}</td>
            <td><span class="status-badge ${warning ? "warning" : ""}">${warning ? "Warning" : "Optimal"}</span></td>
          </tr>
        `;
      })
      .join("");
  }
}

async function loadLatestReading() {
  const response = await fetch("/api/readings/latest");
  const payload = await response.json();
  if (payload.reading) {
    updateLiveCards(payload.reading);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadThresholds().then(loadLatestReading);

  const socket = io();
  socket.on("sensor_update", (reading) => {
    updateLiveCards(reading);
    appendReadingToCharts(reading);
    loadThresholds();
  });
});
