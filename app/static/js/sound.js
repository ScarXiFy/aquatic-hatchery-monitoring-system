/**
 * Aquatic Hatchery Monitoring System - Sound Alert Handler
 * HTML5 Audio element playback system for WARNING and CRITICAL alarm states.
 * Plays alert sound continuously in an indefinite loop until interrupted (muted, dismissed, or cleared).
 */
(function (global) {
  const STORAGE_KEY_MUTED = "hatchery:alert-mute";

  let isMuted = localStorage.getItem(STORAGE_KEY_MUTED) === "true";
  let currentHighestSeverity = null;

  /**
   * Ensure HTML5 <audio> elements for alert levels exist in the DOM with looping enabled.
   */
  function ensureAudioElements() {
    const levels = ["warning", "critical"];
    levels.forEach((level) => {
      let elem = document.querySelector(
        `audio[data-alert-type="${level}"], audio[data-alert-level="${level}"]`
      );
      if (!elem) {
        elem = document.createElement("audio");
        elem.id = `audio-alert-${level}`;
        elem.setAttribute("data-alert-type", level);
        elem.setAttribute("data-alert-level", level);
        elem.src = `/static/sounds/alert-${level}.mp3`;
        elem.preload = "auto";
        elem.loop = true;
        document.body.appendChild(elem);
      } else {
        elem.loop = true;
      }
    });
  }

  /**
   * Get the HTML5 <audio> element for a given alert level.
   * @param {string} level Alert level e.g. "warning" or "critical"
   * @returns {HTMLAudioElement|null}
   */
  function getAudioElement(level) {
    if (!level) return null;
    const normalized = String(level).toLowerCase();
    return document.querySelector(
      `audio[data-alert-type="${normalized}"], audio[data-alert-level="${normalized}"]`
    );
  }

  /**
   * Stop and reset all alert audio elements currently playing.
   */
  function stopAllAudio() {
    const levels = ["warning", "critical"];
    levels.forEach((level) => {
      const elem = getAudioElement(level);
      if (elem) {
        try {
          elem.pause();
          elem.currentTime = 0;
        } catch (e) {
          // Ignore pause/reset errors
        }
      }
    });
  }

  /**
   * Play the HTML5 audio element for a given alert level continuously in an indefinite loop.
   * Gracefully handles missing files or playback restrictions by catching play() errors.
   * @param {string} level Alert level e.g. "warning" or "critical"
   */
  function playAudioForLevel(level) {
    stopAllAudio();

    if (isMuted) {
      return;
    }

    const audioElement = getAudioElement(level);
    if (!audioElement) {
      console.warn(`[HatcherySound] Audio element for alert level '${level}' not found in DOM.`);
      return;
    }

    // Set audio to loop continuously until interrupted/muted/cleared
    audioElement.loop = true;

    try {
      audioElement.currentTime = 0;
    } catch (e) {
      // Ignore currentTime reset errors if media is uninitialized
    }

    try {
      const playPromise = audioElement.play();
      if (playPromise !== undefined && typeof playPromise.catch === "function") {
        playPromise.catch((err) => {
          console.warn(
            `[HatcherySound] Unable to play sound for alert level '${level}' (${audioElement.src}):`,
            err
          );
        });
      }
    } catch (err) {
      console.warn(
        `[HatcherySound] Synchronous error when playing sound for alert level '${level}' (${audioElement.src}):`,
        err
      );
    }
  }

  /**
   * Update UI state for all mute toggle buttons in topbar and alert modal header.
   */
  function updateToggleButtonsUI() {
    const buttons = document.querySelectorAll(".sound-toggle-btn");

    buttons.forEach((btn) => {
      btn.classList.toggle("sound-muted", isMuted);
      btn.classList.toggle("sound-active", !isMuted && currentHighestSeverity !== null);

      const label = isMuted ? "Unmute sound alerts" : "Mute sound alerts";
      btn.setAttribute("aria-label", label);
      btn.setAttribute("title", label);

      // SVG Icon replacement
      const iconWrap = btn.querySelector(".sound-icon-wrap") || btn;
      if (isMuted) {
        iconWrap.innerHTML = `
          <svg viewBox="0 0 24 24" aria-hidden="true" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M11 5L6 9H2v6h4l5 4V5z"/>
            <line x1="23" y1="9" x2="17" y2="15"/>
            <line x1="17" y1="9" x2="23" y2="15"/>
          </svg>`;
      } else {
        iconWrap.innerHTML = `
          <svg viewBox="0 0 24 24" aria-hidden="true" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M11 5L6 9H2v6h4l5 4V5z"/>
            <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
            <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
          </svg>`;
      }
    });
  }

  /**
   * Set muted state and store in localStorage under key "hatchery:alert-mute".
   * Interrupts/stops playback if muted, or resumes looping if unmuted while an alert is active.
   * @param {boolean} muted
   */
  function setMuted(muted) {
    isMuted = Boolean(muted);
    try {
      localStorage.setItem(STORAGE_KEY_MUTED, String(isMuted));
    } catch (e) {
      console.warn("[HatcherySound] Could not write to localStorage:", e);
    }

    if (isMuted) {
      stopAllAudio();
    } else if (currentHighestSeverity !== null) {
      playAudioForLevel(currentHighestSeverity.toLowerCase());
    }

    updateToggleButtonsUI();
  }

  /**
   * Toggle current mute state.
   */
  function toggleMute() {
    setMuted(!isMuted);
  }

  /**
   * Main integration method called when active metric alerts change.
   * Plays audio continuously on state transitions and stops when cleared.
   * @param {Map|Object} activeAlertsMap Map of active metric alerts e.g. { metric: { status: "WARNING"|"CRITICAL" } }
   */
  function updateAlarmState(activeAlertsMap) {
    const items = activeAlertsMap instanceof Map ? Array.from(activeAlertsMap.values()) : Object.values(activeAlertsMap || {});

    let hasCritical = false;
    let hasWarning = false;

    items.forEach((item) => {
      const status = String(item.status || "").toUpperCase();
      if (status === "CRITICAL") {
        hasCritical = true;
      } else if (status === "WARNING") {
        hasWarning = true;
      }
    });

    const newHighestSeverity = hasCritical ? "CRITICAL" : hasWarning ? "WARNING" : null;

    // Check for state transition (alert level change)
    const levelChanged = newHighestSeverity !== currentHighestSeverity;

    currentHighestSeverity = newHighestSeverity;
    updateToggleButtonsUI();

    if (newHighestSeverity === null) {
      stopAllAudio();
    } else if (levelChanged) {
      playAudioForLevel(newHighestSeverity.toLowerCase());
    }
  }

  /**
   * Bind event handler to a sound toggle button.
   * @param {HTMLElement} buttonElement
   */
  function bindSoundToggle(buttonElement) {
    if (!buttonElement) return;
    buttonElement.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleMute();
    });
    updateToggleButtonsUI();
  }

  /**
   * Initialize sound handler, DOM elements, and UI button bindings.
   */
  function init() {
    ensureAudioElements();
    document.querySelectorAll(".sound-toggle-btn").forEach((btn) => bindSoundToggle(btn));
    updateToggleButtonsUI();
  }

  // Export HatcherySound module to global scope
  global.HatcherySound = {
    init,
    toggleMute,
    setMuted,
    isMuted: () => isMuted,
    updateAlarmState,
    bindSoundToggle,
    playAudioForLevel,
    getAudioElement,
    stopAllAudio,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(window);
