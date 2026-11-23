"use strict";

const fullscreenButton = document.getElementById("fullscreen");
const tiltButton = document.getElementById("tilt");
let tiltEnabled = false;
let tiltStarting = false;
let tiltReference;

function lampAt(x, y) {
  const percent = value => Math.max(0, Math.min(100, value)).toFixed(1) + "%";
  document.documentElement.style.setProperty("--lamp-x", percent(x));
  document.documentElement.style.setProperty("--lamp-y", percent(y));
}

function handLight(event) {
  if (tiltEnabled && tg?.DeviceOrientation?.isStarted) return;
  const art = event.target.closest(".lamp-surface");
  if (!art || !art.clientWidth || !art.clientHeight) return;
  const bounds = art.getBoundingClientRect();
  lampAt(100 * (event.clientX - bounds.left) / bounds.width, 100 * (event.clientY - bounds.top) / bounds.height);
}

function readTilt() {
  const sensor = tg.DeviceOrientation;
  if (!tiltEnabled || !sensor.isStarted || document.hidden) return;
  const { beta, gamma } = sensor;
  if (!Number.isFinite(beta) || !Number.isFinite(gamma)) return;
  tiltReference ??= { beta, gamma };
  lampAt(50 + (gamma - tiltReference.gamma) * 145, 42 + (beta - tiltReference.beta) * 145);
}

function syncLamp() {
  const sensor = tg?.DeviceOrientation;
  if (!tg?.isVersionAtLeast?.("8.0") || !sensor?.start) return;
  const artVisible = !document.hidden && (main.querySelector(".lamp-surface") ||
    (dialog.open && modalBody.querySelector(".lamp-surface")));
  tiltButton.hidden = !artVisible;
  tiltButton.setAttribute("aria-pressed", String(tiltEnabled));
  if (!tiltEnabled || !artVisible) {
    tiltReference = undefined;
    if (sensor.isStarted) sensor.stop();
    return;
  }
  if (sensor.isStarted || tiltStarting) return;
  tiltStarting = true;
  sensor.start({ refresh_rate: 50, need_absolute: false }, started => {
    tiltStarting = false;
    if (!started) {
      tiltEnabled = false;
      tiltButton.setAttribute("aria-pressed", "false");
      say("Датчик наклона недоступен. Свет можно двигать касанием.");
    } else if (!tiltEnabled || tiltButton.hidden) sensor.stop();
  });
}

function updateFullscreen() {
  const enabled = telegramFullscreen() ? tg.isFullscreen : Boolean(document.fullscreenElement);
  const title = enabled ? "Выйти из полноэкранного режима" : "На весь экран";
  fullscreenButton.setAttribute("aria-pressed", String(Boolean(enabled)));
  fullscreenButton.setAttribute("aria-label", title);
  fullscreenButton.title = title;
}

function telegramFullscreen() {
  return tg?.isVersionAtLeast?.("8.0") && typeof tg.requestFullscreen === "function";
}

function feel(style) {
  if (tg?.isVersionAtLeast?.("6.1") && tg.HapticFeedback?.impactOccurred) {
    tg.HapticFeedback.impactOccurred(style);
  } else navigator.vibrate?.(12);
}

function initEffects() {
  fullscreenButton.hidden = !(telegramFullscreen() || document.fullscreenEnabled);
  fullscreenButton.onclick = () => run(async () => {
    if (telegramFullscreen()) {
      if (tg.isFullscreen) tg.exitFullscreen();
      else tg.requestFullscreen();
    } else if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  });
  updateFullscreen();
  document.addEventListener("fullscreenchange", updateFullscreen);
  tg?.onEvent?.("fullscreenChanged", updateFullscreen);
  tg?.onEvent?.("fullscreenFailed", () => say("Полноэкранный режим здесь недоступен."));

  if (tg?.isVersionAtLeast?.("8.0") && tg.DeviceOrientation?.start) {
    tiltButton.onclick = () => {
      tiltReference = undefined;
      tiltEnabled = !tiltEnabled;
      syncLamp();
    };
    tg.onEvent?.("deviceOrientationChanged", readTilt);
  }
  document.addEventListener("pointerdown", handLight, { passive: true });
  document.addEventListener("pointermove", handLight, { passive: true });
  document.addEventListener("visibilitychange", syncLamp);
  dialog.addEventListener("close", syncLamp);
  syncLamp();
}
