const METRICS = {
  temperature: { label: "Temperature", unit: "C", color: "#2dd4bf" },
  dissolved_oxygen: { label: "Dissolved Oxygen", unit: "mg/L", color: "#7dd3fc" },
  salinity: { label: "Salinity", unit: "ppt", color: "#86efac" },
  ph: { label: "pH", unit: "", color: "#facc15" },
};

const hatcheryCharts = {};

function makeChart(canvas, metric, compact) {
  const color = METRICS[metric].color;
  return new Chart(canvas, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: METRICS[metric].label,
          data: [],
          borderColor: color,
          backgroundColor: color + "22",
          fill: true,
          pointRadius: compact ? 0 : 2,
          borderWidth: compact ? 2 : 3,
          tension: 0.35,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: !compact, labels: { color: "#d1fae5" } },
      },
      scales: {
        x: {
          ticks: { color: "#94a3b8", maxTicksLimit: compact ? 4 : 8 },
          grid: { color: "rgba(148, 163, 184, 0.12)" },
        },
        y: {
          ticks: { color: "#94a3b8" },
          grid: { color: "rgba(148, 163, 184, 0.12)" },
        },
      },
    },
  });
}

function initializeCharts() {
  document.querySelectorAll("canvas[data-chart]").forEach((canvas) => {
    const metric = canvas.dataset.metric;
    hatcheryCharts[canvas.id] = makeChart(canvas, metric, canvas.dataset.chart === "mini");
  });
}

function formatTimeLabel(timestamp) {
  return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function setChartData(readings) {
  Object.entries(hatcheryCharts).forEach(([id, chart]) => {
    const canvas = document.getElementById(id);
    const metric = canvas.dataset.metric;
    chart.data.labels = readings.map((reading) => formatTimeLabel(reading.timestamp));
    chart.data.datasets[0].data = readings.map((reading) => reading[metric]);
    chart.update();
  });
}

function appendReadingToCharts(reading) {
  Object.entries(hatcheryCharts).forEach(([id, chart]) => {
    const canvas = document.getElementById(id);
    const metric = canvas.dataset.metric;
    chart.data.labels.push(formatTimeLabel(reading.timestamp));
    chart.data.datasets[0].data.push(reading[metric]);
    if (chart.data.labels.length > 80) {
      chart.data.labels.shift();
      chart.data.datasets[0].data.shift();
    }
    chart.update();
  });
}

async function loadHistory(range = "day") {
  const response = await fetch(`/api/readings/history?range=${range}`);
  const payload = await response.json();
  setChartData(payload.readings || []);
}

document.addEventListener("DOMContentLoaded", () => {
  initializeCharts();
  loadHistory("day");

  document.querySelectorAll("[data-history-range]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-history-range]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      loadHistory(button.dataset.historyRange);
    });
  });
});
