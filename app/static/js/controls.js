const LED_LEVELS = [0, 100, 500, 1000, 3000];

function formatControlValue(slider) {
  const key = slider.dataset.controlSlider;
  if (key === "temperature_setpoint") {
    return `${Number(slider.value).toFixed(0)}°C`;
  }
  if (key === "dissolved_oxygen_setpoint") {
    return `${Number(slider.value).toFixed(1)} mg/L`;
  }
  if (key === "led_intensity") {
    return `${LED_LEVELS[Number(slider.value)]} lx`;
  }
  return slider.value;
}

function getControlPayloadValue(slider) {
  if (slider.dataset.controlSlider === "led_intensity") {
    return LED_LEVELS[Number(slider.value)];
  }
  return Number(slider.value);
}

function updateSliderOutput(slider) {
  const output = document.getElementById(`${slider.dataset.controlSlider}-output`);
  if (output) {
    output.textContent = formatControlValue(slider);
  }
}

async function sendSliderState() {
  const payload = {};
  document.querySelectorAll("[data-control-slider]").forEach((slider) => {
    payload[slider.dataset.controlSlider] = getControlPayloadValue(slider);
  });

  await fetch("/api/controls/sliders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function initializeSliders() {
  document.querySelectorAll("[data-control-slider]").forEach((slider) => {
    updateSliderOutput(slider);
    slider.addEventListener("input", () => updateSliderOutput(slider));
    slider.addEventListener("change", () => {
      updateSliderOutput(slider);
      sendSliderState();
    });
  });
}

function initializeValves() {
  document.querySelectorAll("[data-valve]").forEach((button) => {
    button.addEventListener("click", async () => {
      const nextState = button.getAttribute("aria-pressed") !== "true";
      const response = await fetch(`/api/controls/valves/${button.dataset.valve}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ open: nextState }),
      });
      if (!response.ok) {
        return;
      }
      button.setAttribute("aria-pressed", String(nextState));
      button.querySelector("strong").innerHTML = `<i></i> ${nextState ? "Open" : "Closed"}`;
    });
  });
}

function updateDateTime() {
  const dateTime = document.getElementById("current-date-time");
  if (!dateTime) {
    return;
  }
  const now = new Date();
  dateTime.dateTime = now.toISOString();
  dateTime.textContent = now.toLocaleString([], {
    month: "short",
    day: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initializeSliders();
  initializeValves();
  updateDateTime();
  setInterval(updateDateTime, 30000);
});
