/**
 * Aquatic Hatchery Monitoring System - Sound Alert Handler
 * Web Audio API synthesizer for WARNING and CRITICAL alarm states.
 */
(function (global) {
  const STORAGE_KEY_MUTED = "hatchery_sound_muted";

  // Volume configuration (Values between 0.0 for muted to 1.0 for 100% max volume)
  const VOLUME_CONFIG = {
    WARNING_MASTER: 0.80,  // Master gain for WARNING alarms
    WARNING_TONE: 0.80,    // Per-tone gain for WARNING alarms
    CRITICAL_MASTER: 1.0, // Master gain for CRITICAL alarms
    CRITICAL_TONE: 1.0,   // Per-tone gain for CRITICAL alarms
  };

  let audioCtx = null;
  let activeGainNode = null;
  let activeOscillators = [];
  let pulseTimer = null;

  let isMuted = localStorage.getItem(STORAGE_KEY_MUTED) === "true";
  let currentHighestSeverity = null;
  let activeAlarmKeys = new Set();
  let isPlaying = false;
  let userInteracted = false;

  function getAudioContext() {
    if (!audioCtx) {
      const AudioCtxClass = global.AudioContext || global.webkitAudioContext;
      if (AudioCtxClass) {
        audioCtx = new AudioCtxClass();
      }
    }
    return audioCtx;
  }

  function unlockAudioContext() {
    const ctx = getAudioContext();
    if (!ctx) return;
    userInteracted = true;
    if (ctx.state === "suspended") {
      ctx.resume().then(() => {
        updateToggleButtonsUI();
        if (!isMuted && activeAlarmKeys.size > 0 && !isPlaying) {
          startAlarmSequence(currentHighestSeverity);
        }
      }).catch((err) => {
        console.warn("Could not resume AudioContext:", err);
      });
    } else {
      updateToggleButtonsUI();
    }
  }

  function stopActiveTone() {
    if (pulseTimer) {
      clearInterval(pulseTimer);
      pulseTimer = null;
    }

    if (activeGainNode && audioCtx) {
      try {
        activeGainNode.gain.cancelScheduledValues(audioCtx.currentTime);
        activeGainNode.gain.setValueAtTime(activeGainNode.gain.value, audioCtx.currentTime);
        activeGainNode.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.08);
      } catch (e) {
        // Gain ramp fallback
      }
    }

    setTimeout(() => {
      activeOscillators.forEach((osc) => {
        try {
          osc.stop();
          osc.disconnect();
        } catch (e) {}
      });
      activeOscillators = [];

      if (activeGainNode) {
        try {
          activeGainNode.disconnect();
        } catch (e) {}
        activeGainNode = null;
      }
    }, 100);

    isPlaying = false;
  }

  function playPulse(severity) {
    const ctx = getAudioContext();
    if (!ctx || ctx.state === "suspended") return;

    const now = ctx.currentTime;
    const masterGain = ctx.createGain();
    masterGain.connect(ctx.destination);

    if (severity === "CRITICAL") {
      // Urgent, high-pitch 3-burst siren pulse (880 Hz / 1046 Hz / 1318 Hz)
      const masterVol = VOLUME_CONFIG.CRITICAL_MASTER;
      const toneVol = VOLUME_CONFIG.CRITICAL_TONE;

      masterGain.gain.setValueAtTime(0, now);
      masterGain.gain.linearRampToValueAtTime(masterVol, now + 0.02);

      const freqPattern = [880, 1046, 1318];
      freqPattern.forEach((freq, idx) => {
        const osc = ctx.createOscillator();
        const toneGain = ctx.createGain();
        osc.type = "square";
        osc.frequency.setValueAtTime(freq, now + idx * 0.12);

        toneGain.gain.setValueAtTime(0, now + idx * 0.12);
        toneGain.gain.linearRampToValueAtTime(toneVol, now + idx * 0.12 + 0.01);
        toneGain.gain.exponentialRampToValueAtTime(0.001, now + idx * 0.12 + 0.09);

        osc.connect(toneGain);
        toneGain.connect(masterGain);

        osc.start(now + idx * 0.12);
        osc.stop(now + idx * 0.12 + 0.1);
      });

      masterGain.gain.setValueAtTime(masterVol, now + 0.36);
      masterGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.42);
    } else {
      // WARNING: Moderate tempo dual-tone pulse (520 Hz / 650 Hz)
      const masterVol = VOLUME_CONFIG.WARNING_MASTER;
      const toneVol = VOLUME_CONFIG.WARNING_TONE;

      masterGain.gain.setValueAtTime(0, now);
      masterGain.gain.linearRampToValueAtTime(masterVol, now + 0.03);

      const freqPattern = [520, 650];
      freqPattern.forEach((freq, idx) => {
        const osc = ctx.createOscillator();
        const toneGain = ctx.createGain();
        osc.type = "square";
        osc.frequency.setValueAtTime(freq, now + idx * 0.15);

        toneGain.gain.setValueAtTime(0, now + idx * 0.15);
        toneGain.gain.linearRampToValueAtTime(toneVol, now + idx * 0.15 + 0.02);
        toneGain.gain.exponentialRampToValueAtTime(0.001, now + idx * 0.15 + 0.14);

        osc.connect(toneGain);
        toneGain.connect(masterGain);

        osc.start(now + idx * 0.15);
        osc.stop(now + idx * 0.15 + 0.15);
      });

      masterGain.gain.setValueAtTime(masterVol, now + 0.32);
      masterGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.38);
    }
  }

  function startAlarmSequence(severity) {
    stopActiveTone();

    if (isMuted) {
      updateToggleButtonsUI();
      return;
    }

    const ctx = getAudioContext();
    if (!ctx) return;

    if (ctx.state === "suspended") {
      updateToggleButtonsUI();
      return;
    }

    isPlaying = true;
    currentHighestSeverity = severity;

    // Play first immediate pulse
    playPulse(severity);

    // Schedule repeating pulses while alarm remains active
    const intervalMs = severity === "CRITICAL" ? 700 : 1500;
    pulseTimer = setInterval(() => {
      if (!isPlaying || isMuted) {
        clearInterval(pulseTimer);
        pulseTimer = null;
        return;
      }
      playPulse(severity);
    }, intervalMs);

    updateToggleButtonsUI();
  }

  function updateToggleButtonsUI() {
    const buttons = document.querySelectorAll(".sound-toggle-btn");
    const ctx = getAudioContext();
    const isSuspended = Boolean(ctx && ctx.state === "suspended" && !userInteracted && activeAlarmKeys.size > 0);

    buttons.forEach((btn) => {
      btn.classList.toggle("sound-muted", isMuted);
      btn.classList.toggle("sound-active", !isMuted && isPlaying);
      btn.classList.toggle("sound-pending-unlock", !isMuted && isSuspended);

      const label = isMuted
        ? "Unmute sound alerts"
        : isSuspended
        ? "Click to enable sound alerts"
        : "Mute sound alerts";
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
      } else if (isPlaying) {
        iconWrap.innerHTML = `
          <svg viewBox="0 0 24 24" aria-hidden="true" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="sound-wave-anim">
            <path d="M11 5L6 9H2v6h4l5 4V5z"/>
            <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
            <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
          </svg>`;
      } else {
        iconWrap.innerHTML = `
          <svg viewBox="0 0 24 24" aria-hidden="true" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M11 5L6 9H2v6h4l5 4V5z"/>
            <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
          </svg>`;
      }
    });
  }

  function setMuted(muted) {
    isMuted = Boolean(muted);
    localStorage.setItem(STORAGE_KEY_MUTED, String(isMuted));

    if (isMuted) {
      stopActiveTone();
    } else {
      unlockAudioContext();
      if (activeAlarmKeys.size > 0 && currentHighestSeverity) {
        startAlarmSequence(currentHighestSeverity);
      }
    }
    updateToggleButtonsUI();
  }

  function toggleMute() {
    unlockAudioContext();
    setMuted(!isMuted);
  }

  /**
   * Main integration method called when alarm states are evaluated.
   * @param {Map|Object} activeAlertsMap Map of active metric alerts e.g. { metric: { status: "WARNING"|"CRITICAL", key: string } }
   */
  function updateAlarmState(activeAlertsMap) {
    const items = activeAlertsMap instanceof Map ? Array.from(activeAlertsMap.values()) : Object.values(activeAlertsMap || {});

    if (items.length === 0) {
      // Alarm recovery - clear all active alarm sounds
      if (activeAlarmKeys.size > 0 || isPlaying) {
        stopActiveTone();
        activeAlarmKeys.clear();
        currentHighestSeverity = null;
        updateToggleButtonsUI();
      }
      return;
    }

    // Determine current active alarm keys and highest severity
    const newAlarmKeys = new Set();
    let hasCritical = false;
    let hasWarning = false;

    items.forEach((item) => {
      const status = (item.status || "").toUpperCase();
      const key = item.key || `${item.metric}_${status}`;
      newAlarmKeys.add(key);

      if (status === "CRITICAL") {
        hasCritical = true;
      } else if (status === "WARNING") {
        hasWarning = true;
      }
    });

    const newHighestSeverity = hasCritical ? "CRITICAL" : hasWarning ? "WARNING" : null;

    // Check if new alarm keys were added or severity escalated
    let keysAdded = false;
    newAlarmKeys.forEach((k) => {
      if (!activeAlarmKeys.has(k)) {
        keysAdded = true;
      }
    });

    const severityEscalated =
      (currentHighestSeverity === "WARNING" && newHighestSeverity === "CRITICAL") ||
      (currentHighestSeverity === null && newHighestSeverity !== null);

    // Update tracked keys and severity
    activeAlarmKeys = newAlarmKeys;

    if (!newHighestSeverity) {
      stopActiveTone();
      currentHighestSeverity = null;
      return;
    }

    // Trigger sound ONLY if:
    // 1. A new alarm condition key is added, OR
    // 2. Severity escalated (e.g. WARNING -> CRITICAL), OR
    // 3. System was stopped and an alarm is active.
    if (keysAdded || severityEscalated || !isPlaying) {
      currentHighestSeverity = newHighestSeverity;
      startAlarmSequence(newHighestSeverity);
    } else {
      // Same alarm state continuing; keep playing existing tone loop without restarting
      currentHighestSeverity = newHighestSeverity;
    }
  }

  function bindSoundToggle(buttonElement) {
    if (!buttonElement) return;
    buttonElement.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleMute();
    });
    updateToggleButtonsUI();
  }

  function init() {
    // Unlock Web Audio API on first user interaction
    const unlockEvents = ["click", "keydown", "touchstart"];
    const handleFirstInteraction = () => {
      unlockAudioContext();
      unlockEvents.forEach((evt) => document.removeEventListener(evt, handleFirstInteraction, true));
    };
    unlockEvents.forEach((evt) => document.addEventListener(evt, handleFirstInteraction, true));

    // Bind existing toggle buttons in DOM
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
    unlockAudioContext,
    stopActiveTone,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(window);
