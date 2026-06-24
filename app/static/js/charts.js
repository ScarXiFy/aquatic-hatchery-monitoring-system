(function () {
  // ── Metric definitions ───────────────────────────────────────────────
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

  // ── Threshold band plugin ────────────────────────────────────────────
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
      // Safe operating range — green band
      ctx.fillStyle = "rgba(16, 185, 129, 0.16)";
      ctx.fillRect(chartArea.left, safeTop, chartArea.right - chartArea.left, safeBottom - safeTop);
      // Out-of-range zones — amber tint
      ctx.fillStyle = "rgba(251, 191, 36, 0.07)";
      ctx.fillRect(chartArea.left, chartArea.top, chartArea.right - chartArea.left, safeTop - chartArea.top);
      ctx.fillRect(chartArea.left, safeBottom, chartArea.right - chartArea.left, chartArea.bottom - safeBottom);
      ctx.restore();
    },
  };

  // ── Threshold label plugin ───────────────────────────────────────────
  const thresholdLabelPlugin = {
    id: "thresholdLabelPlugin",
    afterDatasetsDraw(chart, args, pluginOptions) {
      const metric = pluginOptions && pluginOptions.metric;
      const limits = thresholds[metric];
      if (!limits) return;

      const { ctx, chartArea, scales } = chart;
      const yScale = scales.y;
      if (!chartArea || !yScale) return;

      ctx.save();

      function drawLabel(text, yPos) {
        if (yPos < chartArea.top || yPos > chartArea.bottom) return;

        ctx.font = "bold 11px Arial, sans-serif";
        const padding = { x: 6, y: 4 };
        const textWidth = ctx.measureText(text).width;
        const textHeight = 11;
        const pillWidth = textWidth + padding.x * 2;
        const pillHeight = textHeight + padding.y * 2;
        const chartWidth = chartArea.right - chartArea.left;
        const x = chartArea.left + chartWidth * 0.88 - pillWidth / 2;
        const y = yPos - pillHeight / 2;

        ctx.fillStyle = "rgba(0, 0, 0, 0.6)";
        ctx.beginPath();
        if (ctx.roundRect) {
          ctx.roundRect(x, y, pillWidth, pillHeight, 4);
        } else {
          ctx.rect(x, y, pillWidth, pillHeight);
        }
        ctx.fill();

        ctx.fillStyle = "#ffffff";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(text, x + pillWidth / 2, y + pillHeight / 2 + 1);
      }

      const minVal = Number(limits.min_value);
      if (!Number.isNaN(minVal)) drawLabel("Min Threshold", yScale.getPixelForValue(minVal));

      const maxVal = Number(limits.max_value);
      if (!Number.isNaN(maxVal)) drawLabel("Max Threshold", yScale.getPixelForValue(maxVal));

      ctx.restore();
    },
  };

  if (typeof Chart !== "undefined") {
    Chart.register(thresholdBandPlugin);
    Chart.register(thresholdLabelPlugin);
  }

  // ── Utility helpers ──────────────────────────────────────────────────
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

  // ── Status helpers ───────────────────────────────────────────────────
  /**
   * Returns "optimal" | "warning" | "critical" | "neutral"
   * Warning zone = within 15% of either threshold edge
   */
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

  // ── Data summarization ───────────────────────────────────────────────
  /**
   * Day view: return individual readings from the last 24 hours.
   * Avoids hourly bucketing which collapses sparse data into 1–2 points.
   * Sub-samples to MAX_POINTS when the dataset is very large.
   */
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

  /**
   * Week view: 4-hour buckets → up to 42 points over 7 days.
   * More granular than daily buckets, giving meaningful curves.
   */
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

  // ── Stats & KPI updates ──────────────────────────────────────────────
  function statsFor(metric, points) {
    const values = points.map((p) => Number(p[metric])).filter((v) => !Number.isNaN(v));
    if (!values.length) return { current: null, min: null, max: null };
    return {
      current: values[values.length - 1],
      min: Math.min(...values),
      max: Math.max(...values),
    };
  }

  /** Update Current / Min / Max text inside each chart card header */
  function updateStats(points) {
    Object.keys(metrics).forEach((metric) => {
      const stats = statsFor(metric, points);
      const container = document.querySelector(`[data-chart-stats="${metric}"]`);
      if (!container) return;
      container.querySelector('[data-stat="current"]').textContent = formatValue(metric, stats.current);
      container.querySelector('[data-stat="min"]').textContent = formatValue(metric, stats.min);
      container.querySelector('[data-stat="max"]').textContent = formatValue(metric, stats.max);
    });
  }

  /** Update the four KPI cards above the chart grid */
  function updateKpiCards(points) {
    const len = points.length;
    Object.keys(metrics).forEach((metric) => {
      const config = metrics[metric];
      const current = len > 0 ? Number(points[len - 1][metric]) : null;
      const prev = len > 1 ? Number(points[len - 2][metric]) : null;
      const condition = conditionFor(metric, current);

      // Numeric value
      const valueEl = document.querySelector(`[data-kpi-value="${metric}"]`);
      if (valueEl) {
        valueEl.textContent =
          current !== null && !Number.isNaN(current)
            ? Number(current).toFixed(config.decimals)
            : "--";
      }

      // Badge
      const badgeEl = document.querySelector(`[data-kpi-status="${metric}"]`);
      if (badgeEl) {
        badgeEl.textContent = conditionLabel(condition);
        badgeEl.dataset.condition = condition;
      }

      // Card border colour
      const card = document.querySelector(`[data-kpi="${metric}"]`);
      if (card) card.dataset.condition = condition;

      // Trend arrow
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

  /** Update the status badge embedded in each chart card header */
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

  // ── Chart creation ───────────────────────────────────────────────────
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
            pointRadius: 4,
            pointHoverRadius: 7,
            pointHitRadius: 16,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: config.color,
            pointBorderWidth: 2,
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
          thresholdLabelPlugin: { metric },
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
              // Show sensor label + full timestamp as tooltip title
              title: (items) => {
                if (!items.length) return config.label;
                const idx = items[0].dataIndex;
                const timestamps = chartTimestamps[canvas.id];
                if (!timestamps || !timestamps[idx]) return config.label;
                const d = new Date(timestamps[idx]);
                const timeStr = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
                // In week view also show the date
                const dateStr =
                  currentRange === "week"
                    ? d.toLocaleDateString([], { month: "short", day: "numeric" }) + " "
                    : "";
                return `${config.label}  ·  ${dateStr}${timeStr}`;
              },
              // Only render the primary dataset; skip threshold lines
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

  // ── Data update ──────────────────────────────────────────────────────
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
    });

    updateStats(points);
    updateKpiCards(points);
    updateChartStatusBadges(points);
  }

  // ── Threshold management ─────────────────────────────────────────────
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

  // ── History loading ──────────────────────────────────────────────────
  async function loadHistory(range = "day") {
    currentRange = range;
    const response = await fetch(`/api/readings/history?range=${range}`);
    const payload = await response.json();
    setChartData(payload.readings || []);
  }

  // ── Init ─────────────────────────────────────────────────────────────
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

  // ── Live sensor events ───────────────────────────────────────────────
  window.addEventListener("hatchery:reading", (event) => {
    if (!Object.keys(charts).length || !event.detail) return;
    currentReadings.push(event.detail);
    // Keep a rolling 500-reading buffer (generous for 24-hour day view)
    if (currentReadings.length > 500) currentReadings.shift();
    setChartData(currentReadings);
  });

  window.addEventListener("hatchery:thresholds", (event) => {
    setThresholds(event.detail || []);
    Object.values(charts).forEach((chart) => chart.update());
    // Re-evaluate badges with updated threshold values
    const points = summarizeHistory(currentReadings, currentRange);
    updateKpiCards(points);
    updateChartStatusBadges(points);
  });

  document.addEventListener("DOMContentLoaded", () => {
    initializeCharts();
    bindRangeButtons();
  });
})();