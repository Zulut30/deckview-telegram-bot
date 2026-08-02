import { gsap } from "https://esm.sh/gsap@3.12.5";
import anime from "https://esm.sh/animejs@3.2.2/lib/anime.es.js";

const THEME_KEY = "deckview-admin-theme";
const THEMES = ["light", "midnight", "hearth"];
const prefersLight = window.matchMedia?.("(prefers-color-scheme: light)")?.matches;

function cssVar(name, fallback) {
  return getComputedStyle(document.body).getPropertyValue(name).trim() || fallback;
}

function alpha(color, value) {
  if (!color) return `rgba(56, 189, 248, ${value})`;
  if (color.startsWith("#")) {
    const hex = color.length === 4
      ? color.slice(1).split("").map((ch) => ch + ch).join("")
      : color.slice(1);
    const num = parseInt(hex, 16);
    return `rgba(${(num >> 16) & 255}, ${(num >> 8) & 255}, ${num & 255}, ${value})`;
  }
  if (color.startsWith("rgb(")) return color.replace("rgb(", "rgba(").replace(")", `, ${value})`);
  return color;
}

function themeVars() {
  return {
    text: cssVar("--admin-text", "#eef2ff"),
    muted: cssVar("--admin-muted", "#9aa7bd"),
    surface: cssVar("--admin-surface-solid", "#121928"),
    border: cssVar("--admin-border", "rgba(148, 163, 184, 0.18)"),
    accent: cssVar("--admin-accent", "#38bdf8"),
    green: cssVar("--admin-accent-2", "#34d399"),
    warm: cssVar("--admin-warm", "#fbbf24"),
    red: cssVar("--admin-red", "#fb7185"),
    violet: cssVar("--admin-violet", "#a78bfa"),
  };
}

function tuneChartConfig(cfg) {
  if (!cfg || !cfg.options) return cfg;
  const v = themeVars();
  const palette = [v.accent, v.green, v.warm, v.red, v.violet, "#22d3ee", "#60a5fa"];

  cfg.options.plugins = cfg.options.plugins || {};
  cfg.options.plugins.legend = cfg.options.plugins.legend || {};
  cfg.options.plugins.legend.labels = {
    ...(cfg.options.plugins.legend.labels || {}),
    color: v.muted,
    boxWidth: 11,
    boxHeight: 11,
    usePointStyle: true,
  };
  cfg.options.plugins.tooltip = {
    ...(cfg.options.plugins.tooltip || {}),
    backgroundColor: v.surface,
    titleColor: v.text,
    bodyColor: v.muted,
    borderColor: v.border,
    borderWidth: 1,
    padding: 12,
  };

  if (cfg.options.scales) {
    for (const scale of Object.values(cfg.options.scales)) {
      scale.grid = { ...(scale.grid || {}), color: v.border };
      scale.ticks = { ...(scale.ticks || {}), color: v.muted };
    }
  }

  for (const dataset of cfg.data?.datasets || []) {
    if (cfg.type === "line") {
      dataset.borderColor = v.accent;
      dataset.backgroundColor = alpha(v.accent, 0.16);
      dataset.pointBackgroundColor = v.green;
      dataset.pointBorderColor = v.surface;
      dataset.borderWidth = 3;
      dataset.tension = dataset.tension ?? 0.34;
    } else if (cfg.type === "bar") {
      dataset.backgroundColor = alpha(v.accent, 0.24);
      dataset.borderColor = alpha(v.accent, 0.55);
      dataset.borderWidth = 1;
      dataset.borderRadius = 7;
    } else if (cfg.type === "doughnut") {
      dataset.backgroundColor = palette;
      dataset.borderColor = v.surface;
      dataset.borderWidth = 4;
      dataset.hoverOffset = 8;
    }
  }

  return cfg;
}

function wrapCharts() {
  if (typeof window.mkChart !== "function" || window.mkChart.__adminWrapped) return;
  const original = window.mkChart;
  window.mkChart = function adminMkChart(id, cfg) {
    return original(id, tuneChartConfig(cfg));
  };
  window.mkChart.__adminWrapped = true;
}

function refreshVisibleCharts() {
  const active = document.querySelector(".page.active")?.id;
  if (active === "p-overview" && typeof window.loadOverview === "function") window.loadOverview();
  if (active === "p-load" && typeof window.loadLoad === "function") window.loadLoad();
}

function setTheme(theme, shouldRefresh = true) {
  const next = THEMES.includes(theme) ? theme : "midnight";
  document.body.dataset.adminTheme = next;
  localStorage.setItem(THEME_KEY, next);
  document.querySelectorAll("[data-admin-theme-choice]").forEach((button) => {
    const active = button.dataset.adminThemeChoice === next;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });

  if (shouldRefresh) {
    anime({
      targets: ".topbar, .admin-hero, .sc, .cc, .sec",
      opacity: [0.92, 1],
      duration: 320,
      easing: "easeOutQuad",
    });
    refreshVisibleCharts();
  }
}

function animatePage() {
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) return;
  const page = document.querySelector(".page.active");
  if (!page) return;
  const items = page.querySelectorAll(".admin-hero, .sc, .cc, .sec, .erd-box, .st");
  gsap.fromTo(
    items,
    { y: 16, opacity: 0 },
    { y: 0, opacity: 1, duration: 0.45, stagger: 0.035, ease: "power2.out", clearProps: "transform,opacity" }
  );
}

function animateHero() {
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) return;
  gsap.fromTo(
    ".admin-stack span",
    { y: 18, rotate: -8, opacity: 0 },
    { y: 0, rotate: 0, opacity: 1, duration: 0.72, stagger: 0.08, ease: "back.out(1.4)" }
  );
  anime({
    targets: ".admin-signal-grid i",
    scaleY: [0.38, 1],
    opacity: [0.35, 0.82],
    delay: anime.stagger(70),
    duration: 760,
    easing: "easeOutElastic(1, .72)",
  });
}

function observeStats() {
  const sg = document.getElementById("sg");
  if (!sg) return;
  const observer = new MutationObserver(() => {
    anime({
      targets: "#sg .sc",
      translateY: [10, 0],
      opacity: [0, 1],
      delay: anime.stagger(35),
      duration: 420,
      easing: "easeOutCubic",
    });
  });
  observer.observe(sg, { childList: true });
}

function wrapNavigation() {
  if (typeof window.go !== "function" || window.go.__adminWrapped) return;
  const original = window.go;
  window.go = function adminGo(name, el) {
    original(name, el);
    requestAnimationFrame(animatePage);
  };
  window.go.__adminWrapped = true;
}

function init() {
  wrapCharts();
  wrapNavigation();
  observeStats();

  const urlTheme = new URLSearchParams(window.location.search).get("theme");
  const saved = localStorage.getItem(THEME_KEY);
  setTheme(urlTheme || saved || (prefersLight ? "light" : "midnight"), false);

  document.querySelectorAll("[data-admin-theme-choice]").forEach((button) => {
    button.addEventListener("click", () => setTheme(button.dataset.adminThemeChoice));
  });

  animateHero();
  animatePage();

  setTimeout(() => {
    wrapCharts();
    refreshVisibleCharts();
  }, 250);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
