let thresholds = [];

function updateLiveCards(reading) {
  Object.keys(METRICS).forEach((metric) => {
    const value = document.getElementById(`${metric}-value`);
    const status = document.getElementById(`${metric}-status`);
    const card = document.querySelector(`[data-metric-card="${metric}"]`);
    if (!value || !status || !card) {
      return;
    }

    value.textContent = Number(reading[metric]).toFixed(metric === "ph" ? 2 : 1);
    const threshold = thresholds.find((item) => item.metric === metric);
    const alerting = threshold && (reading[metric] < threshold.min_value || reading[metric] > threshold.max_value);
    status.textContent = alerting ? "Outside threshold" : "Within threshold";
    card.classList.toggle("alert", Boolean(alerting));
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
        const label = METRICS[item.metric]?.label || item.metric;
        return `<tr><td>${label}</td><td>${item.min_value}</td><td>${item.max_value}</td></tr>`;
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
  loadThresholds();
  loadLatestReading();

  const socket = io();
  socket.on("sensor_update", (reading) => {
    updateLiveCards(reading);
    appendReadingToCharts(reading);
  });
});
