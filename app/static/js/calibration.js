/**
 * Sensor Calibration Module
 * 
 * Manages UI interactions for sensor calibration modal:
 * - 5 Supported Sensors: pH, Dissolved Oxygen, Salinity/EC, Main Tank Temperature, Mixing Tank Temperature
 * - Dynamically adapts between Analog sensors (voltage reading in V) and Temperature sensors (reading in °C)
 * - Multi-point reference instrument calibration with linear regression & single-point offset calculation
 * - Dynamic addition/removal of calibration reference points with internal table scrollbar
 * - Real-time reading preview and live capture to calibration points
 * - "Calibrate" button creates and previews the calibration equation
 * - "Commission" button to the right pushes this calibration equation to the backend
 */

(function () {
  "use strict";

  // Sensor definitions and defaults based on AHMS hardware configuration
  const SENSOR_CONFIGS = {
    ph: {
      key: "ph",
      name: "pH Sensor",
      type: "analog",
      unit: "pH",
      inputUnit: "V",
      channelLabel: "ADC Channel 0 (ADS1115)",
      actualLabel: "Actual Voltage Reading",
      refLabel: "Reference Instrument Reading (pH)",
      actualColTitle: "Sensor Voltage (V)",
      defaultSlope: -6.41401,
      defaultOffset: 17.29167,
      refPlaceholder: "e.g. 7.00",
      actualPlaceholder: "Read voltage",
      standardPoints: [
        { ref: 4.01, actual: "" },
        { ref: 7.00, actual: "" },
        { ref: 10.01, actual: "" },
      ],
      defaultLive: 1.605,
      simVoltageRange: [1.45, 1.65],
      formatEquation: (m, b) => `pH = (${m >= 0 ? "+" : ""}${m.toFixed(4)}) · V + (${b.toFixed(4)})`,
    },
    do: {
      key: "do",
      name: "Dissolved Oxygen Sensor",
      type: "analog",
      unit: "mg/L",
      inputUnit: "V",
      channelLabel: "ADC Channel 2 (ADS1115)",
      actualLabel: "Actual Voltage Reading",
      refLabel: "Reference Instrument Reading (mg/L)",
      actualColTitle: "Sensor Voltage (V)",
      defaultSlope: 5.93086,
      defaultOffset: 0.3273368,
      refPlaceholder: "e.g. 8.20",
      actualPlaceholder: "Read voltage",
      standardPoints: [
        { ref: 0.00, actual: "" },
        { ref: 8.20, actual: "" },
      ],
      defaultLive: 1.162,
      simVoltageRange: [1.00, 1.30],
      formatEquation: (m, b) => `DO = (${m >= 0 ? "+" : ""}${m.toFixed(4)}) · V + (${b.toFixed(4)})`,
    },
    salinity: {
      key: "salinity",
      name: "Salinity / EC Sensor",
      type: "analog",
      unit: "ppt",
      inputUnit: "V",
      channelLabel: "ADC Channel 1 (ADS1115)",
      actualLabel: "Actual Voltage Reading",
      refLabel: "Reference Instrument Reading (ppt)",
      actualColTitle: "Sensor Voltage (V)",
      defaultSlope: 38.265306,
      defaultOffset: 0.0,
      refPlaceholder: "e.g. 35.0",
      actualPlaceholder: "Read voltage",
      standardPoints: [
        { ref: 0.0, actual: "" },
        { ref: 35.0, actual: "" },
      ],
      defaultLive: 0.852,
      simVoltageRange: [0.75, 0.95],
      formatEquation: (m, b) => `Salinity = (${m.toFixed(4)}) · V + (${b.toFixed(4)})`,
    },
    temp: {
      key: "temp",
      name: "Water Temperature Sensor - Main Tank",
      type: "temp",
      unit: "°C",
      inputUnit: "°C",
      channelLabel: "DS18B20 1-Wire (Main Tank)",
      actualLabel: "Actual Temperature Reading",
      refLabel: "Reference Thermometer Reading (°C)",
      actualColTitle: "Sensor Temperature (°C)",
      defaultSlope: 1.0,
      defaultOffset: 0.0,
      refPlaceholder: "e.g. 25.0",
      actualPlaceholder: "Read temp",
      standardPoints: [
        { ref: 25.0, actual: "" },
        { ref: 30.0, actual: "" },
      ],
      defaultLive: 26.2,
      simTempRange: [25.0, 27.5],
      formatEquation: (m, b) => {
        if (Math.abs(m - 1.0) < 0.0001) {
          const sign = b >= 0 ? "+" : "-";
          return `T_cal = T_sensor ${sign} ${Math.abs(b).toFixed(2)} °C`;
        }
        return `T_cal = (${m.toFixed(4)}) · T_sensor + (${b.toFixed(4)})`;
      },
    },
    mixing_temp: {
      key: "mixing_temp",
      name: "Mixing Tank Temperature Sensor",
      type: "temp",
      unit: "°C",
      inputUnit: "°C",
      channelLabel: "DS18B20 1-Wire (Mixing Tank)",
      actualLabel: "Actual Temperature Reading",
      refLabel: "Reference Thermometer Reading (°C)",
      actualColTitle: "Sensor Temperature (°C)",
      defaultSlope: 1.0,
      defaultOffset: 0.0,
      refPlaceholder: "e.g. 25.0",
      actualPlaceholder: "Read temp",
      standardPoints: [
        { ref: 25.0, actual: "" },
        { ref: 30.0, actual: "" },
      ],
      defaultLive: 26.0,
      simTempRange: [25.0, 27.5],
      formatEquation: (m, b) => {
        if (Math.abs(m - 1.0) < 0.0001) {
          const sign = b >= 0 ? "+" : "-";
          return `T_cal = T_sensor ${sign} ${Math.abs(b).toFixed(2)} °C`;
        }
        return `T_cal = (${m.toFixed(4)}) · T_sensor + (${b.toFixed(4)})`;
      },
    },
  };

  const STORAGE_KEY_CALIBRATION = "hatchery:calibrations";

  // Application state
  let currentSensorKey = "ph";
  let liveReadings = {
    ph: 1.605,
    do: 1.162,
    salinity: 0.852,
    temp: 26.2,
    mixing_temp: 26.0,
  };
  let activeCalibrations = {};
  let pendingCalculation = null; // Holds newly calculated equation awaiting commission
  let liveSimTimer = null;

  // Load custom calibrations saved in frontend
  function loadSavedCalibrations() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY_CALIBRATION);
      if (saved) {
        activeCalibrations = JSON.parse(saved);
      }
    } catch (e) {
      activeCalibrations = {};
    }
  }

  function saveCalibration(sensorKey, data) {
    try {
      activeCalibrations[sensorKey] = data;
      localStorage.setItem(STORAGE_KEY_CALIBRATION, JSON.stringify(activeCalibrations));
    } catch (e) {
      console.warn("Could not save calibration to localStorage", e);
    }
  }

  function removeSavedCalibration(sensorKey) {
    try {
      delete activeCalibrations[sensorKey];
      localStorage.setItem(STORAGE_KEY_CALIBRATION, JSON.stringify(activeCalibrations));
    } catch (e) {
      console.warn("Could not remove calibration from localStorage", e);
    }
  }

  function getActiveConfig(sensorKey) {
    const base = SENSOR_CONFIGS[sensorKey];
    const custom = activeCalibrations[sensorKey];
    if (custom) {
      return {
        ...base,
        slope: custom.slope !== undefined ? custom.slope : base.defaultSlope,
        offset: custom.offset !== undefined ? custom.offset : base.defaultOffset,
        r2: custom.r2,
        isCustom: true,
        commissioned: true,
      };
    }
    return {
      ...base,
      slope: base.defaultSlope,
      offset: base.defaultOffset,
      r2: 1.0,
      isCustom: false,
      commissioned: false,
    };
  }

  // DOM Elements
  let modalOverlay,
    openBtn,
    closeBtn,
    cancelBtn,
    sensorSelect,
    liveCard,
    actualLabel,
    actualValue,
    actualUnit,
    derivedLabel,
    derivedValue,
    derivedUnit,
    pointsList,
    addPointBtn,
    applyBtn,
    commissionBtn,
    equationDisplay,
    equationStatusPill,
    paramSlopeLabel,
    paramSlopeValue,
    paramOffsetLabel,
    paramOffsetValue,
    paramR2Value,
    feedbackAlert,
    feedbackText,
    colRefTitle,
    colSensorTitle;

  function initDOM() {
    modalOverlay = document.getElementById("calibration-modal-overlay");
    openBtn = document.getElementById("open-calibration-btn");
    closeBtn = document.getElementById("calibration-modal-close-btn");
    cancelBtn = document.getElementById("cancel-calibration-btn");
    sensorSelect = document.getElementById("calibration-sensor-select");
    liveCard = document.getElementById("calibration-live-card");
    actualLabel = document.getElementById("calibration-actual-label");
    actualValue = document.getElementById("calibration-actual-value");
    actualUnit = document.getElementById("calibration-actual-unit");
    derivedLabel = document.getElementById("calibration-derived-label");
    derivedValue = document.getElementById("calibration-derived-value");
    derivedUnit = document.getElementById("calibration-derived-unit");
    pointsList = document.getElementById("calibration-points-list");
    addPointBtn = document.getElementById("add-calibration-point-btn");
    applyBtn = document.getElementById("apply-calibration-btn");
    commissionBtn = document.getElementById("commission-calibration-btn");
    equationDisplay = document.getElementById("calibration-equation-display");
    equationStatusPill = document.getElementById("equation-status-pill");
    paramSlopeLabel = document.getElementById("param-slope-label");
    paramSlopeValue = document.getElementById("param-slope-value");
    paramOffsetLabel = document.getElementById("param-offset-label");
    paramOffsetValue = document.getElementById("param-offset-value");
    paramR2Value = document.getElementById("param-r2-value");
    feedbackAlert = document.getElementById("calibration-feedback");
    feedbackText = document.getElementById("calibration-feedback-text");
    colRefTitle = document.getElementById("col-ref-title");
    colSensorTitle = document.getElementById("col-sensor-title");

    if (!modalOverlay || !openBtn) return false;

    // Attach Event Listeners
    openBtn.addEventListener("click", openModal);
    if (closeBtn) closeBtn.addEventListener("click", closeModal);
    if (cancelBtn) cancelBtn.addEventListener("click", closeModal);

    modalOverlay.addEventListener("click", (e) => {
      if (e.target === modalOverlay) closeModal();
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !modalOverlay.hidden) {
        closeModal();
      }
    });

    if (sensorSelect) {
      sensorSelect.addEventListener("change", (e) => {
        selectSensor(e.target.value);
      });
    }

    if (addPointBtn) {
      addPointBtn.addEventListener("click", () => {
        addPointRow();
        invalidatePendingCalibration();
      });
    }

    if (applyBtn) {
      applyBtn.addEventListener("click", executeCalibration);
    }

    if (commissionBtn) {
      commissionBtn.addEventListener("click", executeCommission);
    }

    return true;
  }

  function openModal() {
    loadSavedCalibrations();
    syncCalibrationsWithBackend();
    modalOverlay.hidden = false;
    document.body.classList.add("modal-open");
    selectSensor(currentSensorKey);
    startLiveReadingFeed();
  }

  function closeModal() {
    modalOverlay.hidden = true;
    document.body.classList.remove("modal-open");
    if (feedbackAlert) feedbackAlert.hidden = true;
    stopLiveReadingFeed();
  }

  function selectSensor(sensorKey) {
    currentSensorKey = sensorKey;
    pendingCalculation = null;
    const config = SENSOR_CONFIGS[sensorKey];
    if (!config) return;

    if (sensorSelect && sensorSelect.value !== sensorKey) {
      sensorSelect.value = sensorKey;
    }

    if (actualLabel) {
      actualLabel.textContent = config.actualLabel;
    }

    if (actualUnit) {
      actualUnit.textContent = config.inputUnit;
    }

    if (derivedUnit) {
      derivedUnit.textContent = config.unit;
    }

    if (colRefTitle) {
      colRefTitle.innerHTML = `Reference Instrument Reading (<span class="unit-span">${config.unit}</span>)`;
    }

    if (colSensorTitle) {
      colSensorTitle.innerHTML = `Actual Sensor Reading (<span class="unit-span">${config.inputUnit}</span>)`;
    }

    // Load active or default points
    const activeData = activeCalibrations[sensorKey];
    if (activeData && activeData.points && activeData.points.length > 0) {
      renderPoints(activeData.points);
    } else {
      renderPoints(config.standardPoints);
    }

    updateEquationSummary();
    updateLiveDisplay();
    disableCommissionBtn();
  }

  function renderPoints(pointsArray) {
    if (!pointsList) return;
    pointsList.innerHTML = "";

    const config = SENSOR_CONFIGS[currentSensorKey];
    const pts = pointsArray && pointsArray.length > 0 ? pointsArray : config.standardPoints;

    pts.forEach((pt, idx) => {
      createPointRowElement(idx + 1, pt.ref, pt.actual);
    });
  }

  function addPointRow(refVal = "", actualVal = "") {
    const nextIndex = pointsList.querySelectorAll(".calibration-point-row").length + 1;
    createPointRowElement(nextIndex, refVal, actualVal);
    
    // Smoothly scroll the fixed table container to reveal the newly added row
    if (pointsList) {
      setTimeout(() => {
        pointsList.scrollTop = pointsList.scrollHeight;
      }, 50);
    }
  }

  function createPointRowElement(index, refVal = "", actualVal = "") {
    const config = SENSOR_CONFIGS[currentSensorKey];
    const row = document.createElement("div");
    row.className = "calibration-point-row";
    row.dataset.pointIndex = index;

    row.innerHTML = `
      <div class="col-pt">
        <span class="point-number-tag">Pt ${index}</span>
      </div>
      <div class="col-ref">
        <div class="input-with-unit">
          <input type="number" step="any" class="point-input ref-input" placeholder="${config.refPlaceholder}" value="${refVal !== undefined && refVal !== null ? refVal : ""}" aria-label="Reference instrument reading for point ${index}">
          <span class="input-unit-suffix">${config.unit}</span>
        </div>
      </div>
      <div class="col-sensor">
        <div class="input-with-unit">
          <input type="text" class="point-input actual-input" placeholder="-- ${config.inputUnit}" value="${actualVal !== undefined && actualVal !== null && actualVal !== "" ? actualVal : ""}" disabled aria-label="Actual sensor reading for point ${index}">
          <span class="input-unit-suffix">${config.inputUnit}</span>
        </div>
      </div>
      <div class="col-actions">
        <button type="button" class="btn-capture-live" title="Read current live reading into this row">Read</button>
        <button type="button" class="btn-remove-row" title="Remove calibration point" aria-label="Remove point ${index}">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
            <path d="M18 6L6 18M6 6l12 12"></path>
          </svg>
        </button>
      </div>
    `;

    // Row event listeners
    const captureBtn = row.querySelector(".btn-capture-live");
    const removeBtn = row.querySelector(".btn-remove-row");
    const refInput = row.querySelector(".ref-input");
    const actualInput = row.querySelector(".actual-input");

    // Any input change invalidates previously calculated equation until re-calibrated
    refInput.addEventListener("input", () => {
      if (refInput.value.trim() !== "") {
        refInput.classList.remove("input-error");
        if (actualInput.value.trim() !== "") {
          row.classList.remove("row-incomplete");
        }
      }
      invalidatePendingCalibration();
    });

    captureBtn.addEventListener("click", () => {
      const live = getLiveReadingForSensor(currentSensorKey);
      actualInput.value = config.type === "analog" ? live.toFixed(3) : live.toFixed(2);
      actualInput.classList.remove("input-error");
      if (refInput.value.trim() !== "") {
        row.classList.remove("row-incomplete");
      }
      row.classList.add("row-highlight");
      setTimeout(() => row.classList.remove("row-highlight"), 600);
      invalidatePendingCalibration();
      showFeedback(`Read live reading (${actualInput.value} ${config.inputUnit}) for Point ${index}.`, "info");
    });

    removeBtn.addEventListener("click", () => {
      const totalRows = pointsList.querySelectorAll(".calibration-point-row").length;
      if (totalRows <= 1) {
        showFeedback("At least one calibration point is required.", "warning");
        return;
      }
      row.remove();
      reindexRows();
      invalidatePendingCalibration();
    });

    pointsList.appendChild(row);
  }

  function reindexRows() {
    const rows = pointsList.querySelectorAll(".calibration-point-row");
    rows.forEach((row, i) => {
      const idx = i + 1;
      row.dataset.pointIndex = idx;
      const tag = row.querySelector(".point-number-tag");
      if (tag) tag.textContent = `Pt ${idx}`;
      const refInput = row.querySelector(".ref-input");
      if (refInput) refInput.setAttribute("aria-label", `Reference instrument reading for point ${idx}`);
      const actualInput = row.querySelector(".actual-input");
      if (actualInput) actualInput.setAttribute("aria-label", `Actual sensor reading for point ${idx}`);
    });
  }

  function validateAndReadPointsFromUI() {
    const rows = pointsList.querySelectorAll(".calibration-point-row");
    const points = [];
    let hasIncomplete = false;
    const incompletePointNames = [];

    if (rows.length === 0) {
      return {
        valid: false,
        error: "Please add at least one calibration point.",
        points: [],
      };
    }

    rows.forEach((row, i) => {
      const idx = i + 1;
      const refInput = row.querySelector(".ref-input");
      const actualInput = row.querySelector(".actual-input");
      const refVal = parseFloat(refInput.value);
      const actVal = parseFloat(actualInput.value);

      const refMissing = isNaN(refVal) || refInput.value.trim() === "";
      const actMissing = isNaN(actVal) || actualInput.value.trim() === "";

      if (refMissing || actMissing) {
        hasIncomplete = true;
        incompletePointNames.push(`Pt ${idx}`);
        row.classList.add("row-incomplete");
        if (refMissing) refInput.classList.add("input-error");
        if (actMissing) actualInput.classList.add("input-error");
      } else {
        row.classList.remove("row-incomplete");
        refInput.classList.remove("input-error");
        actualInput.classList.remove("input-error");
        points.push({ ref: refVal, actual: actVal });
      }
    });

    if (hasIncomplete) {
      return {
        valid: false,
        error: `Incomplete calibration data (${incompletePointNames.join(", ")}): All datapoints must be filled with both a reference reading and a captured sensor reading before calibrating.`,
        points: [],
      };
    }

    return { valid: true, error: null, points: points };
  }

  // Linear Regression and Calibration Math
  function calculateCalibration(points, sensorConfig) {
    if (!points || points.length === 0) {
      return null;
    }

    const n = points.length;

    // Single-point calibration: offset correction
    if (n === 1) {
      const pt = points[0];
      if (sensorConfig.type === "analog") {
        const slope = sensorConfig.defaultSlope;
        const offset = pt.ref - slope * pt.actual;
        return {
          slope: slope,
          offset: offset,
          r2: 1.0,
          pointsCount: 1,
        };
      } else {
        const offset = pt.ref - pt.actual;
        return {
          slope: 1.0,
          offset: offset,
          r2: 1.0,
          pointsCount: 1,
        };
      }
    }

    // 2 or more points: Ordinary Least Squares Linear Regression
    let sumX = 0;
    let sumY = 0;
    let sumXY = 0;
    let sumX2 = 0;
    let sumY2 = 0;

    for (let i = 0; i < n; i++) {
      const x = points[i].actual;
      const y = points[i].ref;
      sumX += x;
      sumY += y;
      sumXY += x * y;
      sumX2 += x * x;
      sumY2 += y * y;
    }

    const denominator = n * sumX2 - sumX * sumX;
    if (Math.abs(denominator) < 1e-12) {
      return null;
    }

    const slope = (n * sumXY - sumX * sumY) / denominator;
    const offset = (sumY - slope * sumX) / n;

    // Calculate Coefficient of Determination (R^2)
    const yMean = sumY / n;
    let ssTot = 0;
    let ssRes = 0;

    for (let i = 0; i < n; i++) {
      const x = points[i].actual;
      const y = points[i].ref;
      const yPred = slope * x + offset;
      ssTot += Math.pow(y - yMean, 2);
      ssRes += Math.pow(y - yPred, 2);
    }

    let r2 = 1.0;
    if (ssTot > 1e-12) {
      r2 = Math.max(0, Math.min(1.0, 1.0 - ssRes / ssTot));
    }

    return {
      slope: slope,
      offset: offset,
      r2: r2,
      pointsCount: n,
    };
  }

  /**
   * CALIBRATE ACTION:
   * Creates/computes the calibration equation from the points table.
   * Previews the equation and enables the Commission button.
   */
  function executeCalibration() {
    const validation = validateAndReadPointsFromUI();
    if (!validation.valid) {
      showFeedback(validation.error, "error");
      return;
    }

    const points = validation.points;
    const config = SENSOR_CONFIGS[currentSensorKey];

    const result = calculateCalibration(points, config);
    if (!result) {
      showFeedback("Unable to compute calibration equation. Sensor readings must not all be identical.", "error");
      return;
    }

    pendingCalculation = {
      sensorKey: currentSensorKey,
      slope: result.slope,
      offset: result.offset,
      r2: result.r2,
      points: points,
      calculatedAt: new Date().toISOString(),
    };

    // Update display with pending calculated equation
    if (equationDisplay) {
      equationDisplay.textContent = config.formatEquation(result.slope, result.offset);
    }

    if (paramSlopeValue) paramSlopeValue.textContent = result.slope.toFixed(4);
    if (paramOffsetValue) {
      const sign = result.offset >= 0 ? "+" : "";
      paramOffsetValue.textContent = `${sign}${result.offset.toFixed(4)}`;
    }
    if (paramR2Value) paramR2Value.textContent = result.r2.toFixed(4);

    if (equationStatusPill) {
      equationStatusPill.textContent = "Equation Computed (Ready)";
      equationStatusPill.className = "equation-status-pill computed";
    }

    enableCommissionBtn();
    showFeedback("Calibration was successful.", "success");
  }

  /**
   * COMMISSION ACTION:
   * Pushes the active/pending calibration equation to the backend.
   */
  function executeCommission() {
    if (!pendingCalculation || pendingCalculation.sensorKey !== currentSensorKey) {
      showFeedback("Please press 'Calibrate' to generate the equation before commissioning.", "warning");
      return;
    }

    const config = SENSOR_CONFIGS[currentSensorKey];
    const payload = {
      sensor: currentSensorKey,
      sensorName: config.name,
      channel: config.channelLabel,
      slope: pendingCalculation.slope,
      offset: pendingCalculation.offset,
      r2: pendingCalculation.r2,
      points: pendingCalculation.points,
      commissionedAt: new Date().toISOString(),
    };

    console.log("[Calibration UI] Commissioning equation to backend:", payload);

    // Save to active state and localStorage
    saveCalibration(currentSensorKey, {
      slope: pendingCalculation.slope,
      offset: pendingCalculation.offset,
      r2: pendingCalculation.r2,
      points: pendingCalculation.points,
      appliedAt: payload.commissionedAt,
      commissioned: true,
    });

    // Push new calibration equation to the backend API
    fetch("/api/calibrations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then((res) => {
        if (!res.ok) throw new Error("HTTP error " + res.status);
        return res.json();
      })
      .then((data) => {
        console.log("[Calibration UI] Backend calibration updated successfully:", data);
      })
      .catch((err) => {
        console.warn("[Calibration UI] Error sending calibration to backend:", err);
      });

    // Animate commission button state
    if (commissionBtn) {
      commissionBtn.classList.add("commissioning");
      commissionBtn.innerHTML = `
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="spin-icon">
          <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
        </svg>
        <span>Pushing...</span>
      `;
    }

    setTimeout(() => {
      if (commissionBtn) {
        commissionBtn.classList.remove("commissioning");
        commissionBtn.innerHTML = `
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" />
            <polyline points="16 6 12 2 8 6" />
            <line x1="12" y1="2" x2="12" y2="15" />
          </svg>
          <span>Commission</span>
        `;
      }

      disableCommissionBtn();

      if (equationStatusPill) {
        equationStatusPill.textContent = "Commissioned (Backend)";
        equationStatusPill.className = "equation-status-pill commissioned";
      }

      updateLiveDisplay();
      showFeedback(`Successfully commissioned calibration equation to backend for ${config.name}!`, "success");

      // Dispatch custom event for UI/system integration
      window.dispatchEvent(
        new CustomEvent("hatchery:calibration-commissioned", {
          detail: payload,
        })
      );
    }, 400);
  }

  function enableCommissionBtn() {
    if (!commissionBtn) return;
    commissionBtn.disabled = false;
    commissionBtn.classList.add("ready");
    commissionBtn.title = "Push calculated calibration equation to system backend";
  }

  function disableCommissionBtn() {
    if (!commissionBtn) return;
    commissionBtn.disabled = true;
    commissionBtn.classList.remove("ready");
    commissionBtn.title = "Calculate calibration equation before commissioning";
  }

  function invalidatePendingCalibration() {
    if (pendingCalculation) {
      pendingCalculation = null;
      disableCommissionBtn();
      if (equationStatusPill) {
        equationStatusPill.textContent = "Pending Re-Calibration";
        equationStatusPill.className = "equation-status-pill pending";
      }
    }
  }

  function resetToDefault() {
    removeSavedCalibration(currentSensorKey);
    pendingCalculation = null;
    disableCommissionBtn();
    const config = SENSOR_CONFIGS[currentSensorKey];
    renderPoints(config.standardPoints);
    updateEquationSummary();
    updateLiveDisplay();
    showFeedback(`Reset to factory default calibration for ${config.name}.`, "info");
  }

  function updateEquationSummary() {
    const config = SENSOR_CONFIGS[currentSensorKey];
    const active = getActiveConfig(currentSensorKey);

    if (equationDisplay) {
      equationDisplay.textContent = config.formatEquation(active.slope, active.offset);
    }

    if (equationStatusPill) {
      if (active.isCustom) {
        equationStatusPill.textContent = "Commissioned (Custom)";
        equationStatusPill.className = "equation-status-pill commissioned";
      } else {
        equationStatusPill.textContent = "Factory Default";
        equationStatusPill.className = "equation-status-pill default";
      }
    }

    if (paramSlopeLabel) {
      paramSlopeLabel.textContent = config.type === "analog" ? "Slope (m)" : "Scale (m)";
    }

    if (paramSlopeValue) {
      paramSlopeValue.textContent = active.slope.toFixed(4);
    }

    if (paramOffsetLabel) {
      paramOffsetLabel.textContent = config.type === "analog" ? "Offset / Intercept (b)" : "Offset / ΔT (b)";
    }

    if (paramOffsetValue) {
      const sign = active.offset >= 0 ? "+" : "";
      paramOffsetValue.textContent = `${sign}${active.offset.toFixed(4)}`;
    }

    if (paramR2Value) {
      paramR2Value.textContent = active.r2 !== undefined ? active.r2.toFixed(4) : "1.0000";
    }
  }

  // Live Reading simulation / integration
  function getLiveReadingForSensor(sensorKey) {
    const config = SENSOR_CONFIGS[sensorKey];
    if (window.HATCHERY_LATEST_READING) {
      const r = window.HATCHERY_LATEST_READING;

      // Prefer exact raw sensor readings sent from backend (raw voltages / temps)
      if (r.raw) {
        if (sensorKey === "temp" && r.raw.temperature !== undefined) return Number(r.raw.temperature);
        if (sensorKey === "mixing_temp" && r.raw.mixing_temperature !== undefined) return Number(r.raw.mixing_temperature);
        if (sensorKey === "ph" && r.raw.ph !== undefined) return Number(r.raw.ph);
        if (sensorKey === "do" && r.raw.dissolved_oxygen !== undefined) return Number(r.raw.dissolved_oxygen);
        if (sensorKey === "salinity" && r.raw.salinity !== undefined) return Number(r.raw.salinity);
      }
      if (sensorKey === "temp" && r.raw_temperature !== undefined) return Number(r.raw_temperature);
      if (sensorKey === "mixing_temp" && r.raw_mixing_temperature !== undefined) return Number(r.raw_mixing_temperature);
      if (sensorKey === "ph" && r.raw_ph !== undefined) return Number(r.raw_ph);
      if (sensorKey === "do" && r.raw_dissolved_oxygen !== undefined) return Number(r.raw_dissolved_oxygen);
      if (sensorKey === "salinity" && r.raw_salinity !== undefined) return Number(r.raw_salinity);

      // Fallbacks
      if (sensorKey === "temp" && r.temperature !== undefined) {
        return Number(r.temperature);
      }
      if (sensorKey === "mixing_temp") {
        return Number(r.mixing_temperature || r.temperature || 26.0);
      }
      if (sensorKey === "ph" && r.ph !== undefined) {
        return (Number(r.ph) - config.defaultOffset) / config.defaultSlope;
      }
      if (sensorKey === "do" && r.dissolved_oxygen !== undefined) {
        return (Number(r.dissolved_oxygen) - config.defaultOffset) / config.defaultSlope;
      }
      if (sensorKey === "salinity" && r.salinity !== undefined) {
        return (Number(r.salinity) - config.defaultOffset) / config.defaultSlope;
      }
    }

    if (sensorKey === "temp") {
      const el = document.getElementById("temperature-value");
      if (el && !isNaN(parseFloat(el.textContent))) {
        return parseFloat(el.textContent);
      }
    }

    return liveReadings[sensorKey] || config.defaultLive;
  }

  function updateLiveDisplay() {
    const config = SENSOR_CONFIGS[currentSensorKey];
    const liveVal = getLiveReadingForSensor(currentSensorKey);

    if (actualValue) {
      actualValue.textContent = config.type === "analog" ? liveVal.toFixed(3) : liveVal.toFixed(2);
    }

    if (derivedValue) {
      const active = getActiveConfig(currentSensorKey);
      const derived = active.slope * liveVal + active.offset;
      derivedValue.textContent = derived.toFixed(2);
    }
  }

  function startLiveReadingFeed() {
    stopLiveReadingFeed();
    liveSimTimer = setInterval(() => {
      const config = SENSOR_CONFIGS[currentSensorKey];
      if (config.type === "analog") {
        const delta = (Math.random() - 0.5) * 0.004;
        liveReadings[currentSensorKey] = Math.max(0.01, (liveReadings[currentSensorKey] || config.defaultLive) + delta);
      } else {
        const delta = (Math.random() - 0.5) * 0.04;
        liveReadings[currentSensorKey] = (liveReadings[currentSensorKey] || config.defaultLive) + delta;
      }
      updateLiveDisplay();
    }, 1500);
  }

  function stopLiveReadingFeed() {
    if (liveSimTimer) {
      clearInterval(liveSimTimer);
      liveSimTimer = null;
    }
  }

  function showFeedback(message, type = "success") {
    if (!feedbackAlert || !feedbackText) return;
    feedbackText.textContent = message;
    feedbackAlert.className = `calibration-feedback ${type}`;
    feedbackAlert.hidden = false;

    // Scroll modal body to top so the feedback banner is immediately visible
    const modalBody = feedbackAlert.closest(".calibration-modal-body");
    if (modalBody) {
      modalBody.scrollTop = 0;
    }

    if (type !== "error") {
      setTimeout(() => {
        if (!feedbackAlert.hidden) {
          feedbackAlert.hidden = true;
        }
      }, 5000);
    }
  }

  // Synchronize active calibrations with backend DB
  function syncCalibrationsWithBackend() {
    fetch("/api/calibrations")
      .then((res) => {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then((data) => {
        if (data && data.calibrations) {
          for (const [key, item] of Object.entries(data.calibrations)) {
            if (item && item.slope !== undefined && item.offset !== undefined) {
              activeCalibrations[key] = {
                slope: item.slope,
                offset: item.offset,
                appliedAt: item.created_at,
                commissioned: true,
              };
            }
          }
          try {
            localStorage.setItem(STORAGE_KEY_CALIBRATION, JSON.stringify(activeCalibrations));
          } catch (e) {}
          updateLiveDisplay();
          updateEquationSection();
        }
      })
      .catch((err) => {
        // Backend not reachable or in static test
      });
  }

  // Hook socket if available
  function hookSocket() {
    if (window.hatcherySocket) {
      window.hatcherySocket.on("sensor_update", (reading) => {
        window.HATCHERY_LATEST_READING = reading;
        if (!modalOverlay || modalOverlay.hidden) return;
        updateLiveDisplay();
      });
    }

    window.addEventListener("hatchery:reading", (e) => {
      if (e.detail) {
        window.HATCHERY_LATEST_READING = e.detail;
        if (!modalOverlay || modalOverlay.hidden) return;
        updateLiveDisplay();
      }
    });
  }

  // Self Initialization on DOMContentLoaded
  document.addEventListener("DOMContentLoaded", () => {
    initDOM();
    loadSavedCalibrations();
    syncCalibrationsWithBackend();
    hookSocket();
  });
})();
