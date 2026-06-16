(function () {
  const metrics = {
    temperature: {
      label: "Temperature",
      shortLabel: "Temperature",
      unit: "°C",
      color: "#06b6d4",
      min: 15,
      max: 35,
      decimals: 1,
    },
    ph: {
      label: "pH Level",
      shortLabel: "pH",
      unit: "",
      color: "#8b5cf6",
      min: 0,
      max: 14,
      decimals: 1,
    },
    dissolved_oxygen: {
      label: "Dissolved Oxygen",
      shortLabel: "DO",
      unit: "mg/L",
      color: "#10b981",
      min: 0,
      max: 15,
      decimals: 1,
    },
    salinity: {
      label: "Salinity",
      shortLabel: "Salinity",
      unit: "g/L",
      color: "#f59e0b",
      min: 0,
      max: 50,
      decimals: 1,
    },
  };

  window.HATCHERY_METRICS = metrics;

  const charts = {};
  let currentRange = "day";
  let currentReadings = [];

  function formatValue(metric, value) {
    const config = metrics[metric];
    if (!config || value === null || value === undefined || Number.isNaN(Number(value))) {
      return "--";
    }
    const formatted = Number(value).toFixed(config.decimals);
    return config.unit ? `${formatted} ${config.unit}` : formatted;
  }

  function timeLabel(timestamp) {
    return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function statsFor(metric, readings) {
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

  function updateStats(readings) {
    Object.keys(metrics).forEach((metric) => {
      const stats = statsFor(metric, readings);
      const container = document.querySelector(`[data-chart-stats="${metric}"]`);
      if (!container) {
        return;
      }
      container.querySelector('[data-stat="current"]').textContent = formatValue(metric, stats.current);
      container.querySelector('[data-stat="min"]').textContent = formatValue(metric, stats.min);
      container.querySelector('[data-stat="max"]').textContent = formatValue(metric, stats.max);
    });
  }

  function createChart(canvas) {
    const metric = canvas.dataset.metric;
    const config = metrics[metric];
    return new Chart(canvas, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: config.label,
            data: [],
            borderColor: config.color,
            backgroundColor: `${config.color}22`,
            fill: true,
            borderWidth: 3,
            pointRadius: 3,
            pointHoverRadius: 5,
            tension: 0.3,
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
              label: (context) => `${config.label}: ${formatValue(metric, context.parsed.y)}`,
            },
          },
        },
        scales: {
          x: {
            title: { display: true, text: "Time", color: "#9ca3af" },
            ticks: { color: "#9ca3af", maxTicksLimit: currentRange === "week" ? 7 : 12 },
            grid: { color: "rgba(55, 65, 81, 0.28)", borderDash: [3, 3] },
          },
          y: {
            title: {
              display: true,
              text: config.unit ? `${config.label} (${config.unit})` : config.label,
              color: "#9ca3af",
            },
            ticks: { color: "#9ca3af" },
            grid: { color: "rgba(55, 65, 81, 0.28)", borderDash: [3, 3] },
          },
        },
      },
    });
  }

  function setChartData(readings) {
    currentReadings = readings;
    Object.entries(charts).forEach(([id, chart]) => {
      const canvas = document.getElementById(id);
      const metric = canvas.dataset.metric;
      chart.data.labels = readings.map((reading) => timeLabel(reading.timestamp));
      chart.data.datasets[0].data = readings.map((reading) => reading[metric]);
      chart.options.scales.x.ticks.maxTicksLimit = currentRange === "week" ? 7 : 12;
      chart.update();
    });
    updateStats(readings);
  }

  async function loadHistory(range = "day") {
    currentRange = range;
    const response = await fetch(`/api/readings/history?range=${range}`);
    const payload = await response.json();
    setChartData(payload.readings || []);
  }

  function initializeCharts() {
    document.querySelectorAll("canvas[data-chart='history']").forEach((canvas) => {
      charts[canvas.id] = createChart(canvas);
    });
    if (Object.keys(charts).length) {
      loadHistory(currentRange);
    }
  }

  function bindRangeButtons() {
    document.querySelectorAll("[data-history-range]").forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelectorAll("[data-history-range]").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
        loadHistory(button.dataset.historyRange);
      });
    });
  }

  window.addEventListener("hatchery:reading", (event) => {
    if (!Object.keys(charts).length || !event.detail) {
      return;
    }
    currentReadings.push(event.detail);
    if (currentReadings.length > 240) {
      currentReadings.shift();
    }
    setChartData(currentReadings);
  });

  document.addEventListener("DOMContentLoaded", () => {
    initializeCharts();
    bindRangeButtons();
  });
})();
