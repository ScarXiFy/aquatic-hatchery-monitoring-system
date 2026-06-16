const METRICS = {
  temperature: {
    label: "Temperature",
    shortLabel: "Temperature",
    unit: "°C",
    color: "#22d3ee",
    gaugeMin: 15,
    gaugeMax: 35,
    decimals: 1,
  },
  ph: {
    label: "pH Level",
    shortLabel: "pH",
    unit: "",
    color: "#8b5cf6",
    gaugeMin: 0,
    gaugeMax: 14,
    decimals: 1,
  },
  dissolved_oxygen: {
    label: "Dissolved Oxygen",
    shortLabel: "DO",
    unit: "mg/L",
    color: "#10b981",
    gaugeMin: 0,
    gaugeMax: 15,
    decimals: 1,
  },
  salinity: {
    label: "Salinity",
    shortLabel: "Salinity",
    unit: "g/L",
    color: "#f59e0b",
    gaugeMin: 0,
    gaugeMax: 40,
    decimals: 1,
  },
};

const hatcheryCharts = {};
let latestHistory = [];

function formatMetricValue(metric, value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  const metricConfig = METRICS[metric];
  return `${Number(value).toFixed(metricConfig.decimals)}${metricConfig.unit ? ` ${metricConfig.unit}` : ""}`;
}

function makeChart(canvas, metric) {
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
          pointRadius: 3,
          pointHoverRadius: 5,
          borderWidth: 3,
          tension: 0.28,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: {
        mode: "index",
        intersect: false,
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (context) => `${METRICS[metric].label}: ${formatMetricValue(metric, context.parsed.y)}`,
          },
        },
      },
      scales: {
        x: {
          title: { display: true, text: "Time", color: "#a6b2c1" },
          ticks: { color: "#a6b2c1", maxTicksLimit: 10 },
          grid: { color: "rgba(148, 163, 184, 0.08)" },
        },
        y: {
          title: {
            display: true,
            text: METRICS[metric].unit ? `${METRICS[metric].label} (${METRICS[metric].unit})` : METRICS[metric].label,
            color: "#a6b2c1",
          },
          ticks: { color: "#a6b2c1" },
          grid: { color: "rgba(148, 163, 184, 0.08)" },
        },
      },
    },
  });
}

function initializeCharts() {
  document.querySelectorAll("canvas[data-chart]").forEach((canvas) => {
    const metric = canvas.dataset.metric;
    hatcheryCharts[canvas.id] = makeChart(canvas, metric);
  });
}

function formatTimeLabel(timestamp) {
  return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function getMetricStats(readings, metric) {
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

function updateChartStats(readings) {
  Object.keys(METRICS).forEach((metric) => {
    const stats = getMetricStats(readings, metric);
    const statContainer = document.querySelector(`[data-chart-stats="${metric}"]`);
    if (statContainer) {
      statContainer.querySelector('[data-stat="current"]').textContent = formatMetricValue(metric, stats.current);
      statContainer.querySelector('[data-stat="min"]').textContent = formatMetricValue(metric, stats.min);
      statContainer.querySelector('[data-stat="max"]').textContent = formatMetricValue(metric, stats.max);
    }
    updateTrendRow(metric, stats);
  });
}

function updateTrendRow(metric, stats) {
  const row = document.querySelector(`[data-trend-metric="${metric}"]`);
  if (!row) {
    return;
  }

  let position = 50;
  if (stats.current !== null && stats.min !== null && stats.max !== null && stats.max !== stats.min) {
    position = ((stats.current - stats.min) / (stats.max - stats.min)) * 100;
  }

  row.style.setProperty("--trend-position", `${Math.max(0, Math.min(100, position))}%`);
  row.innerHTML = `
    <div class="trend-top">
      <span class="trend-name">${METRICS[metric].shortLabel}</span>
      <span class="trend-current">${formatMetricValue(metric, stats.current)}</span>
    </div>
    <div class="trend-track"><span class="trend-thumb"></span></div>
    <div class="trend-bottom">
      <span>${formatMetricValue(metric, stats.min)}</span>
      <span>${formatMetricValue(metric, stats.max)}</span>
    </div>
  `;
}

function setChartData(readings) {
  latestHistory = readings;
  Object.entries(hatcheryCharts).forEach(([id, chart]) => {
    const canvas = document.getElementById(id);
    const metric = canvas.dataset.metric;
    chart.data.labels = readings.map((reading) => formatTimeLabel(reading.timestamp));
    chart.data.datasets[0].data = readings.map((reading) => reading[metric]);
    chart.update();
  });
  updateChartStats(readings);
}

function appendReadingToCharts(reading) {
  latestHistory.push(reading);
  if (latestHistory.length > 240) {
    latestHistory.shift();
  }

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
  updateChartStats(latestHistory);
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
