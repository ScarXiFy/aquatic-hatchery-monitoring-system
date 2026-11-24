(function () {
  let maintenanceState = {
    active: false,
    components: {
      heater: false,
      heating_valve: false,
      cooling_valve: true,
      peltiers: false,
      aerator_valve_1: true,
      aerator_valve_2: true,
      bleed_valve: 0,
      heating_pump: false,
      cooling_pump: false,
      mixing_fan: false,
    },
  };

  let requestInFlight = false;
  let socketListenerBound = false;

  const VALVE_COMPONENTS = new Set([
    "heating_valve",
    "cooling_valve",
    "aerator_valve_1",
    "aerator_valve_2",
  ]);

  const ALL_COMPONENTS = [
    "heater",
    "peltiers",
    "mixing_fan",
    "heating_valve",
    "cooling_valve",
    "heating_pump",
    "cooling_pump",
    "aerator_valve_1",
    "aerator_valve_2",
  ];

  function getEl(id) {
    return document.getElementById(id);
  }

  function renderState() {
    const isActive = Boolean(maintenanceState.active);
    const components = maintenanceState.components || {};

    // Topbar button & pulsing dot
    const topbarBtn = getEl("open-maintenance-btn");
    const topbarDot = getEl("maintenance-topbar-dot");
    if (topbarBtn) {
      topbarBtn.classList.toggle("is-active", isActive);
      topbarBtn.setAttribute("aria-pressed", String(isActive));
    }
    if (topbarDot) {
      topbarDot.hidden = !isActive;
    }

    // Mode banner & master button
    const banner = getEl("maintenance-mode-banner");
    const bannerHeading = getEl("maintenance-banner-heading");
    const masterBtn = getEl("maintenance-mode-toggle-btn");
    if (banner) {
      banner.classList.toggle("is-active", isActive);
    }
    if (bannerHeading) {
      bannerHeading.textContent = isActive
        ? "Maintenance Mode Active"
        : "Normal Mode Active";
    }
    if (masterBtn) {
      masterBtn.textContent = isActive
        ? "Return to Normal Mode"
        : "Enable Maintenance Mode";
      masterBtn.classList.toggle("is-exit", isActive);
      masterBtn.disabled = requestInFlight;
    }

    // Controls grid lock state
    const grid = getEl("maintenance-controls-grid");
    if (grid) {
      grid.classList.toggle("is-locked", !isActive);
    }

    // Update all component toggle cards
    ALL_COMPONENTS.forEach((key) => {
      const val = Boolean(components[key]);
      const isValve = VALVE_COMPONENTS.has(key);
      const pill = getEl(`maint-status-${key}`);
      if (pill) {
        pill.textContent = isValve ? (val ? "OPEN" : "CLOSED") : val ? "ON" : "OFF";
        pill.classList.toggle("is-on", val);
      }

      const btn = document.querySelector(`[data-maint-component="${key}"]`);
      if (btn) {
        btn.disabled = !isActive || requestInFlight;
        btn.setAttribute("aria-pressed", String(val));
        btn.classList.toggle("is-on", val);
        if (isValve) {
          btn.textContent = val ? "Close Valve" : "Open Valve";
        } else {
          btn.textContent = val ? "Turn OFF" : "Turn ON";
        }
      }
    });

    // Update Bleed Valve panel (0%, 33%, 66%, 100%)
    const bleedVal = Number.isFinite(Number(components.bleed_valve))
      ? Math.round(Number(components.bleed_valve))
      : 0;
    const bleedPill = getEl("maint-status-bleed_valve");
    if (bleedPill) {
      bleedPill.textContent = `${bleedVal}%`;
      bleedPill.classList.toggle("is-on", bleedVal > 0);
    }
    document.querySelectorAll("[data-maint-bleed-value]").forEach((btn) => {
      const btnVal = Number(btn.getAttribute("data-maint-bleed-value"));
      const isSelected = btnVal === bleedVal;
      btn.disabled = !isActive || requestInFlight;
      btn.setAttribute("aria-pressed", String(isSelected));
      btn.classList.toggle("is-on", isSelected);
    });
  }

  function applyState(nextState) {
    if (!nextState || typeof nextState !== "object") return;
    maintenanceState = {
      active: Boolean(nextState.active),
      components: Object.assign({}, maintenanceState.components, nextState.components || {}),
    };
    renderState();
  }

  async function fetchMaintenanceState() {
    try {
      const res = await fetch("/api/maintenance");
      if (!res.ok) return;
      const data = await res.json();
      applyState(data);
    } catch (err) {
      console.error("Failed to fetch maintenance state:", err);
    }
  }

  async function toggleMaintenanceMode() {
    if (requestInFlight) return;
    const nextActive = !maintenanceState.active;
    requestInFlight = true;
    renderState();

    try {
      const res = await fetch("/api/maintenance/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: nextActive }),
      });
      if (res.ok) {
        const data = await res.json();
        applyState(data);
      }
    } catch (err) {
      console.error("Error toggling maintenance mode:", err);
    } finally {
      requestInFlight = false;
      renderState();
    }
  }

  async function setComponentState(component, state) {
    if (requestInFlight || !maintenanceState.active) return;
    requestInFlight = true;
    renderState();

    const payloadState = component === "bleed_valve" ? Number(state) : Boolean(state);

    try {
      const res = await fetch("/api/maintenance/component", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ component, state: payloadState }),
      });
      if (res.ok) {
        const data = await res.json();
        applyState(data);
      }
    } catch (err) {
      console.error("Error updating maintenance component:", err);
    } finally {
      requestInFlight = false;
      renderState();
    }
  }

  function openModal() {
    const overlay = getEl("maintenance-modal-overlay");
    if (!overlay) return;
    overlay.hidden = false;
    fetchMaintenanceState();
  }

  function closeModal() {
    const overlay = getEl("maintenance-modal-overlay");
    if (!overlay) return;
    overlay.hidden = true;
  }

  function bindSocketEvents() {
    if (socketListenerBound) return;
    if (window.hatcherySocket) {
      window.hatcherySocket.on("maintenance_updated", (data) => {
        applyState(data);
      });
      socketListenerBound = true;
    }
  }

  function init() {
    const openBtn = getEl("open-maintenance-btn");
    const closeBtn = getEl("maintenance-modal-close-btn");
    const doneBtn = getEl("maintenance-modal-done-btn");
    const overlay = getEl("maintenance-modal-overlay");
    const masterBtn = getEl("maintenance-mode-toggle-btn");

    if (openBtn) {
      openBtn.addEventListener("click", openModal);
    }
    if (closeBtn) {
      closeBtn.addEventListener("click", closeModal);
    }
    if (doneBtn) {
      doneBtn.addEventListener("click", closeModal);
    }
    if (overlay) {
      overlay.addEventListener("click", (e) => {
        if (e.target === overlay) {
          closeModal();
        }
      });
    }
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && overlay && !overlay.hidden) {
        closeModal();
      }
    });

    if (masterBtn) {
      masterBtn.addEventListener("click", toggleMaintenanceMode);
    }

    document.querySelectorAll("[data-maint-component]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const component = btn.getAttribute("data-maint-component");
        if (!component || !maintenanceState.active) return;
        const current = Boolean(maintenanceState.components[component]);
        setComponentState(component, !current);
      });
    });

    document.querySelectorAll("[data-maint-bleed-value]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (!maintenanceState.active) return;
        const opening = Number(btn.getAttribute("data-maint-bleed-value"));
        if (!Number.isFinite(opening)) return;
        setComponentState("bleed_valve", opening);
      });
    });

    bindSocketEvents();
    window.addEventListener("hatchery:socket-connected", () => {
      bindSocketEvents();
      fetchMaintenanceState();
    });

    renderState();
    fetchMaintenanceState();
  }

  window.HatcheryMaintenance = {
    openModal,
    closeModal,
    applyState,
    fetchMaintenanceState,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
