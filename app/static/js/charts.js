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
  const chartTimestamps = {};
  let currentRange = "day";
  let currentReadings = [];
  let thresholds = {};
  const lastUpdateTime = {
    temperature: null,
    ph: null,
    dissolved_oxygen: null,
    salinity: null
  };

  const thresholdBandPlugin = {
    id: "thresholdBandPlugin",
    beforeDatasetsDraw(chart, args, pluginOptions) {
      const metric = pluginOptions && pluginOptions.metric;
      const limits = thresholds[metric];
      const config = metrics[metric];
      if (!limits || !config) return;

      const { ctx, chartArea, scales } = chart;
      const yScale = scales.y;
      if (!chartArea || !yScale) return;

      const safeMin = Math.max(config.min, Number(limits.min_value));
      const safeMax = Math.min(config.max, Number(limits.max_value));
      if (Number.isNaN(safeMin) || Number.isNaN(safeMax) || safeMin >= safeMax) return;

      const safeTop = yScale.getPixelForValue(safeMax);
      const safeBottom = yScale.getPixelForValue(safeMin);

      ctx.save();
      ctx.fillStyle = "rgba(16, 185, 129, 0.24)";
      ctx.fillRect(chartArea.left, safeTop, chartArea.right - chartArea.left, safeBottom - safeTop);
      ctx.fillStyle = "rgba(251, 191, 36, 0.07)";
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
    if (!config || value === null || value === undefined || Number.isNaN(Number(value))) return "--";
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
    return [...readings].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  }

  function average(values) {
    if (!values.length) return null;
    return values.reduce((sum, v) => sum + v, 0) / values.length;
  }

  function conditionFor(metric, value) {
    const limits = thresholds[metric];
    if (!limits || value === null || value === undefined || Number.isNaN(Number(value))) return "neutral";
    const v = Number(value);
    const min = Number(limits.min_value);
    const max = Number(limits.max_value);
    if (Number.isNaN(min) || Number.isNaN(max)) return "neutral";
    if (v < min || v > max) return "critical";
    const range = max - min;
    if (range <= 0) return "optimal";
    const edge = range * 0.15;
    return v <= min + edge || v >= max - edge ? "warning" : "optimal";
  }

  function conditionLabel(condition) {
    return { optimal: "Normal", warning: "Warning", critical: "Critical", neutral: "—" }[condition] || "—";
  }

  function buildDayPoints(readings) {
    const MAX_POINTS = 120;
    const now = Date.now();
    const cutoff = now - 24 * 60 * 60 * 1000;

    const recent = sortedReadings(readings).filter((r) => {
      const t = new Date(r.timestamp).getTime();
      return !Number.isNaN(t) && t >= cutoff;
    });

    const step = recent.length > MAX_POINTS ? Math.ceil(recent.length / MAX_POINTS) : 1;
    const sampled = step > 1 ? recent.filter((_, i) => i % step === 0) : recent;

    return sampled.map((r) => {
      const point = { timestamp: r.timestamp, label: timeLabel(r.timestamp) };
      Object.keys(metrics).forEach((m) => {
        const v = Number(r[m]);
        point[m] = Number.isNaN(v) ? null : v;
      });
      return point;
    });
  }

  function buildWeekPoints(readings) {
    const buckets = new Map();

    sortedReadings(readings).forEach((reading) => {
      const date = new Date(reading.timestamp);
      if (Number.isNaN(date.getTime())) return;

      const hour4 = Math.floor(date.getHours() / 4) * 4;
      const key = `${date.toISOString().slice(0, 10)}T${String(hour4).padStart(2, "0")}`;

      if (!buckets.has(key)) {
        const values = {};
        Object.keys(metrics).forEach((m) => { values[m] = []; });
        buckets.set(key, { timestamp: reading.timestamp, values });
      }
      const bucket = buckets.get(key);
      bucket.timestamp = reading.timestamp;
      Object.keys(metrics).forEach((m) => {
        const v = Number(reading[m]);
        if (!Number.isNaN(v)) bucket.values[m].push(v);
      });
    });

    return Array.from(buckets.values())
      .slice(-42)
      .map((bucket) => {
        const point = {
          timestamp: bucket.timestamp,
          label: `${dayLabel(bucket.timestamp)} ${timeLabel(bucket.timestamp)}`,
        };
        Object.keys(metrics).forEach((m) => {
          const avg = average(bucket.values[m]);
          point[m] = avg === null ? null : Number(avg.toFixed(metrics[m].decimals + 1));
        });
        return point;
      });
  }

  function summarizeHistory(readings, range) {
    return range === "week" ? buildWeekPoints(readings) : buildDayPoints(readings);
  }

  function statsFor(metric, points) {
    const values = points.map((p) => Number(p[metric])).filter((v) => !Number.isNaN(v));
    if (!values.length) return { current: null, min: null, max: null };
    return {
      current: values[values.length - 1],
      min: Math.min(...values),
      max: Math.max(...values),
    };
  }

  function updateStats(points) {
    Object.keys(metrics).forEach((metric) => {
      const stats = statsFor(metric, points);
      const container = document.querySelector(`[data-chart-stats="${metric}"]`);
      if (!container) return;
      container.querySelector('[data-stat="current"]').textContent = formatValue(metric, stats.current);
      container.querySelector('[data-stat="min"]').textContent = formatValue(metric, stats.min);
      container.querySelector('[data-stat="max"]').textContent = formatValue(metric, stats.max);

      const validPoints = points.map(p => p[metric]).filter(v => v !== null && !Number.isNaN(v));
      const trendEl = document.querySelector(`[data-chart-trend="${metric}"]`);
      if (trendEl) {
        if (validPoints.length > 1) {
          const currentVal = validPoints[validPoints.length - 1];
          const prevVal = validPoints[validPoints.length - 2];
          if (prevVal !== 0) {
            const pct = ((currentVal - prevVal) / prevVal) * 100;
            const sign = pct > 0 ? "+" : "";
            trendEl.textContent = `(${sign}${pct.toFixed(1)}%)`;
            trendEl.className = "chart-trend";
            if (pct > 0) {
              trendEl.classList.add("trend-up");
            } else if (pct < 0) {
              trendEl.classList.add("trend-down");
            } else {
              trendEl.classList.add("trend-stable");
            }
          } else {
            trendEl.textContent = "(0.0%)";
            trendEl.className = "chart-trend trend-stable";
          }
        } else {
          trendEl.textContent = "";
          trendEl.className = "chart-trend";
        }
      }
    });
  }

  function updateKpiCards(points) {
    const len = points.length;
    Object.keys(metrics).forEach((metric) => {
      const config = metrics[metric];
      const current = len > 0 ? Number(points[len - 1][metric]) : null;
      const prev = len > 1 ? Number(points[len - 2][metric]) : null;
      const condition = conditionFor(metric, current);

      const valueEl = document.querySelector(`[data-kpi-value="${metric}"]`);
      if (valueEl) {
        valueEl.textContent =
          current !== null && !Number.isNaN(current)
            ? Number(current).toFixed(config.decimals)
            : "--";
      }

      const badgeEl = document.querySelector(`[data-kpi-status="${metric}"]`);
      if (badgeEl) {
        badgeEl.textContent = conditionLabel(condition);
        badgeEl.dataset.condition = condition;
      }

      const card = document.querySelector(`[data-kpi="${metric}"]`);
      if (card) card.dataset.condition = condition;

      const trendEl = document.querySelector(`[data-kpi-trend="${metric}"]`);
      if (trendEl && current !== null && prev !== null && !Number.isNaN(current) && !Number.isNaN(prev)) {
        const diff = current - prev;
        const epsilon = Math.pow(10, -config.decimals) * 0.5;
        if (Math.abs(diff) < epsilon) {
          trendEl.textContent = "→";
          trendEl.dataset.trend = "stable";
        } else if (diff > 0) {
          trendEl.textContent = "↑";
          trendEl.dataset.trend = "up";
        } else {
          trendEl.textContent = "↓";
          trendEl.dataset.trend = "down";
        }
      }
    });
  }

  function updateChartStatusBadges(points) {
    const len = points.length;
    Object.keys(metrics).forEach((metric) => {
      const current = len > 0 ? Number(points[len - 1][metric]) : null;
      const condition = conditionFor(metric, current);
      const badge = document.querySelector(`[data-chart-status="${metric}"]`);
      if (badge) {
        badge.textContent = conditionLabel(condition);
        badge.dataset.condition = condition;
      }
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
            backgroundColor: `${config.color}1a`,
            fill: true,
            borderWidth: 2.5,
            pointRadius: (context) => {
              if (context.datasetIndex !== 0) return 0;
              const index = context.dataIndex;
              const count = context.dataset.data.length;
              if (count > 0 && index === count - 1) {
                return 6;
              }
              return 0;
            },
            pointHoverRadius: (context) => {
              if (context.datasetIndex !== 0) return 0;
              const index = context.dataIndex;
              const count = context.dataset.data.length;
              if (count > 0 && index === count - 1) {
                return 8;
              }
              return 6;
            },
            pointHitRadius: 16,
            pointBackgroundColor: (context) => {
              if (context.datasetIndex !== 0) return "#ffffff";
              const index = context.dataIndex;
              const count = context.dataset.data.length;
              if (count > 0 && index === count - 1) {
                return config.color;
              }
              return "#ffffff";
            },
            pointBorderColor: (context) => {
              if (context.datasetIndex !== 0) return config.color;
              const index = context.dataIndex;
              const count = context.dataset.data.length;
              if (count > 0 && index === count - 1) {
                return "#ffffff";
              }
              return config.color;
            },
            pointBorderWidth: (context) => {
              if (context.datasetIndex !== 0) return 0;
              const index = context.dataIndex;
              const count = context.dataset.data.length;
              if (count > 0 && index === count - 1) {
                return 3;
              }
              return 2;
            },
            pointHoverBackgroundColor: config.color,
            pointHoverBorderColor: "#ffffff",
            pointHoverBorderWidth: 2.5,
            tension: 0.35,
          },
          {
            label: "Min Threshold",
            data: [],
            borderColor: "rgba(255, 60, 60, 0.85)",
            borderDash: [6, 3],
            borderWidth: 1.5,
            pointRadius: 0,
            pointHoverRadius: 0,
            pointHitRadius: 0,
            fill: false,
            tension: 0,
          },
          {
            label: "Max Threshold",
            data: [],
            borderColor: "rgba(255, 60, 60, 0.85)",
            borderDash: [6, 3],
            borderWidth: 1.5,
            pointRadius: 0,
            pointHoverRadius: 0,
            pointHitRadius: 0,
            fill: false,
            tension: 0,
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
            backgroundColor: "rgba(15, 23, 42, 0.96)",
            borderColor: "rgba(15, 118, 110, 0.6)",
            borderWidth: 1,
            titleColor: "#22d3ee",
            titleFont: { size: 13, weight: "700" },
            bodyColor: "#e2e8f0",
            bodyFont: { size: 12 },
            padding: 12,
            cornerRadius: 10,
            displayColors: false,
            caretSize: 5,
            callbacks: {
              title: (items) => {
                if (!items.length) return config.label;
                const idx = items[0].dataIndex;
                const timestamps = chartTimestamps[canvas.id];
                if (!timestamps || !timestamps[idx]) return config.label;
                const d = new Date(timestamps[idx]);
                const timeStr = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
                const dateStr =
                  currentRange === "week"
                    ? d.toLocaleDateString([], { month: "short", day: "numeric" }) + " "
                    : "";
                return `${config.label}  ·  ${dateStr}${timeStr}`;
              },
              label: (context) => {
                if (context.datasetIndex !== 0) return null;
                const value = context.parsed.y;
                const condition = conditionFor(metric, value);
                const statusText = conditionLabel(condition);
                return [
                  `Value:  ${formatValue(metric, value)}`,
                  `Status: ${statusText}`,
                ];
              },
            },
          },
        },
        scales: {
          x: {
            title: {
              display: true,
              text: "Time",
              color: "#94a3b8",
              font: { size: 12, weight: "bold" },
            },
            ticks: {
              color: "#94a3b8",
              maxTicksLimit: currentRange === "week" ? 8 : 12,
              font: { size: 11 },
              maxRotation: 30,
            },
            grid: { color: "rgba(55, 65, 81, 0.25)", borderDash: [3, 3] },
          },
          y: {
            min: config.min,
            max: config.max,
            title: {
              display: true,
              text: config.unit ? `${config.label} (${config.unit})` : config.label,
              color: "#94a3b8",
              font: { size: 12, weight: "bold" },
            },
            ticks: {
              color: "#94a3b8",
              font: { size: 11 },
            },
            grid: { color: "rgba(55, 65, 81, 0.25)", borderDash: [3, 3] },
          },
        },
      },
    });
  }

  function setChartData(readings) {
    currentReadings = readings;
    const points = summarizeHistory(readings, currentRange);

    Object.entries(charts).forEach(([id, chart]) => {
      const canvas = document.getElementById(id);
      const metric = canvas.dataset.metric;
      const xLabels = points.map((p) => p.label);

      chartTimestamps[id] = points.map((p) => p.timestamp);
      chart.data.labels = xLabels;
      chart.data.datasets[0].data = points.map((p) => p[metric]);

      const limits = thresholds[metric];
      if (limits) {
        chart.data.datasets[1].data = Array(xLabels.length).fill(Number(limits.min_value));
        chart.data.datasets[2].data = Array(xLabels.length).fill(Number(limits.max_value));
      } else {
        chart.data.datasets[1].data = [];
        chart.data.datasets[2].data = [];
      }

      chart.options.scales.x.ticks.maxTicksLimit = currentRange === "week" ? 8 : 12;
      chart.update();

      if (points.length > 0) {
        lastUpdateTime[metric] = Date.now();
      }
    });

    updateStats(points);
    updateKpiCards(points);
    updateChartStatusBadges(points);
    updateElapsedTimes();
  }

  async function loadThresholds() {
    try {
      const response = await fetch("/api/thresholds");
      const payload = await response.json();
      setThresholds(payload.thresholds || []);
      Object.values(charts).forEach((chart) => chart.update());
    } catch {
      thresholds = {};
    }
  }

  function setThresholds(nextThresholds) {
    thresholds = (nextThresholds || []).reduce((acc, t) => {
      acc[t.metric] = t;
      return acc;
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
        document.querySelectorAll("[data-history-range]").forEach((btn) => btn.classList.remove("active"));
        button.classList.add("active");
        loadHistory(button.dataset.historyRange);
      });
    });
  }

  window.addEventListener("hatchery:reading", (event) => {
    if (!Object.keys(charts).length || !event.detail) return;
    currentReadings.push(event.detail);
    if (currentReadings.length > 500) currentReadings.shift();
    setChartData(currentReadings);
  });

  window.addEventListener("hatchery:thresholds", (event) => {
    setThresholds(event.detail || []);
    Object.values(charts).forEach((chart) => chart.update());
    const points = summarizeHistory(currentReadings, currentRange);
    updateKpiCards(points);
    updateChartStatusBadges(points);
  });

  function updateElapsedTimes() {
    const now = Date.now();
    Object.keys(metrics).forEach((metric) => {
      const el = document.querySelector(`[data-chart-update="${metric}"]`);
      if (!el) return;
      const t = lastUpdateTime[metric];
      if (!t) {
        el.textContent = "Waiting for data...";
        return;
      }
      const seconds = Math.round((now - t) / 1000);
      if (seconds < 5) {
        el.textContent = "Updated just now";
      } else {
        el.textContent = `Updated ${seconds}s ago`;
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initializeCharts();
    bindRangeButtons();
    setInterval(updateElapsedTimes, 1000);
  });
})();
