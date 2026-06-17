(function () {
  const metrics = {
    temperature: {
      label: "Temperature",
      shortLabel: "Temperature",
      unit: "°C",
      color: "#06b6d4",
      min: 15,
      max: 35,
      decimals: 0,
    },
    ph: {
      label: "pH Level",
      shortLabel: "pH",
      unit: "",
      color: "#8b5cf6",
      min: 0,
      max: 14,
      decimals: 0,
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
  let thresholds = {};

  const thresholdBandPlugin = {
    id: "thresholdBandPlugin",
    beforeDatasetsDraw(chart, args, pluginOptions) {
      const metric = pluginOptions && pluginOptions.metric;
      const limits = thresholds[metric];
      const config = metrics[metric];
      if (!limits || !config) {
        return;
      }

      const { ctx, chartArea, scales } = chart;
      const yScale = scales.y;
      if (!chartArea || !yScale) {
        return;
      }

      const safeMin = Math.max(config.min, Number(limits.min_value));
      const safeMax = Math.min(config.max, Number(limits.max_value));
      if (Number.isNaN(safeMin) || Number.isNaN(safeMax) || safeMin >= safeMax) {
        return;
      }

      const safeTop = yScale.getPixelForValue(safeMax);
      const safeBottom = yScale.getPixelForValue(safeMin);

      ctx.save();
      ctx.fillStyle = "rgba(16, 185, 129, 0.10)";
      ctx.fillRect(chartArea.left, safeTop, chartArea.right - chartArea.left, safeBottom - safeTop);

      ctx.fillStyle = "rgba(251, 191, 36, 0.08)";
      ctx.fillRect(chartArea.left, chartArea.top, chartArea.right - chartArea.left, safeTop - chartArea.top);
      ctx.fillRect(chartArea.left, safeBottom, chartArea.right - chartArea.left, chartArea.bottom - safeBottom);
      ctx.restore();
    },
  };

  if (typeof Chart !== "undefined") {
    Chart.register(thresholdBandPlugin);
  }

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

  function dayLabel(timestamp) {
    return new Date(timestamp).toLocaleDateString([], { month: "short", day: "numeric" });
  }

  function sortedReadings(readings) {
    return [...readings].sort((first, second) => new Date(first.timestamp) - new Date(second.timestamp));
  }

  function bucketKey(reading, range) {
    const date = new Date(reading.timestamp);
    if (Number.isNaN(date.getTime())) {
      return null;
    }
    if (range === "week") {
      return date.toISOString().slice(0, 10);
    }
    date.setMinutes(0, 0, 0);
    return date.toISOString();
  }

  function bucketLabel(timestamp, range) {
    return range === "week" ? dayLabel(timestamp) : timeLabel(timestamp);
  }

  function average(values) {
    if (!values.length) {
      return null;
    }
    return values.reduce((total, value) => total + value, 0) / values.length;
  }

  function summarizeHistory(readings, range) {
    const buckets = new Map();
    sortedReadings(readings).forEach((reading) => {
      const key = bucketKey(reading, range);
      if (!key) {
        return;
      }
      if (!buckets.has(key)) {
        const values = {};
        Object.keys(metrics).forEach((metric) => {
          values[metric] = [];
        });
        buckets.set(key, { timestamp: reading.timestamp, values });
      }

      const bucket = buckets.get(key);
      bucket.timestamp = reading.timestamp;
      Object.keys(metrics).forEach((metric) => {
        const value = Number(reading[metric]);
        if (!Number.isNaN(value)) {
          bucket.values[metric].push(value);
        }
      });
    });

    const limit = range === "week" ? 7 : 24;
    return Array.from(buckets.values())
      .slice(-limit)
      .map((bucket) => {
        const point = {
          timestamp: bucket.timestamp,
          label: bucketLabel(bucket.timestamp, range),
        };
        Object.keys(metrics).forEach((metric) => {
          const value = average(bucket.values[metric]);
          point[metric] = value === null ? null : Number(value.toFixed(metrics[metric].decimals + 1));
        });
        return point;
      });
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
          thresholdBandPlugin: { metric },
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
            min: config.min,
            max: config.max,
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
    const summarizedReadings = summarizeHistory(readings, currentRange);
    Object.entries(charts).forEach(([id, chart]) => {
      const canvas = document.getElementById(id);
      const metric = canvas.dataset.metric;
      chart.data.labels = summarizedReadings.map((reading) => reading.label);
      chart.data.datasets[0].data = summarizedReadings.map((reading) => reading[metric]);
      chart.options.scales.x.ticks.maxTicksLimit = currentRange === "week" ? 7 : 12;
      chart.update();
    });
    updateStats(summarizedReadings);
  }

  async function loadThresholds() {
    try {
      const response = await fetch("/api/thresholds");
      const payload = await response.json();
      setThresholds(payload.thresholds || []);
      Object.values(charts).forEach((chart) => chart.update());
    } catch (error) {
      thresholds = {};
    }
  }

  function setThresholds(nextThresholds) {
    thresholds = (nextThresholds || []).reduce((items, threshold) => {
      items[threshold.metric] = threshold;
      return items;
    }, {});
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
      loadThresholds();
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

  window.addEventListener("hatchery:thresholds", (event) => {
    setThresholds(event.detail || []);
    Object.values(charts).forEach((chart) => chart.update());
  });

  document.addEventListener("DOMContentLoaded", () => {
    initializeCharts();
    bindRangeButtons();
  });
})();