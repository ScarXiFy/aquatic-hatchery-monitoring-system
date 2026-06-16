function updateSliderOutput(slider) {
  const output = document.getElementById(`${slider.dataset.controlSlider}-output`);
  if (output) {
    output.textContent = `${slider.value}%`;
  }
}

async function sendSliderState() {
  const payload = {};
  document.querySelectorAll("[data-control-slider]").forEach((slider) => {
    payload[slider.dataset.controlSlider] = Number(slider.value);
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
    slider.addEventListener("change", sendSliderState);
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
      button.querySelector("strong").textContent = nextState ? "Open" : "Closed";
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initializeSliders();
  initializeValves();
});
