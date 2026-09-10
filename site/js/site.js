import { bind, play, setEnabled, setVolume } from "../vendor/cuelume/index.js";
import { applyI18n, currentLang } from "./i18n.js";

const THEME_KEY = "skye-theme";
const LANG_KEY = "skye-lang";
const SOUND_KEY = "skye-sound";

function storedTheme() {
  const value = localStorage.getItem(THEME_KEY);
  return value === "dark" || value === "light" ? value : null;
}

function systemTheme() {
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function preferredTheme() {
  return storedTheme() ?? systemTheme();
}

function withoutTransitions(apply) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
    apply();
    return;
  }
  const style = document.createElement("style");
  style.textContent = "*,*::before,*::after{transition:none !important}";
  document.head.append(style);
  apply();
  // Read a layout value so the new theme commits while the override is
  // still active, then drop it on the next frame.
  void document.body.offsetHeight;
  requestAnimationFrame(() => requestAnimationFrame(() => style.remove()));
}

export function applyTheme(theme, persist = true) {
  withoutTransitions(() => {
    document.documentElement.dataset.theme = theme;
  });
  if (persist) localStorage.setItem(THEME_KEY, theme);
}

export function applySound(on) {
  document.documentElement.dataset.sound = on ? "on" : "off";
  localStorage.setItem(SOUND_KEY, on ? "on" : "off");
  setEnabled(on);
}

function bootPreferences() {
  applyTheme(preferredTheme(), false);
  document.documentElement.lang = currentLang() === "en" ? "en" : "ru";
  const soundOn = localStorage.getItem(SOUND_KEY) !== "off";
  applySound(soundOn);
  setVolume(0.62);
}

function wireHeader() {
  const langBtn = document.querySelector("[data-action='lang']");
  const themeBtn = document.querySelector("[data-action='theme']");
  const soundBtn = document.querySelector("[data-action='sound']");
  const menuBtn = document.querySelector("[data-action='menu']");
  const nav = document.querySelector(".nav-links");

  const setMenu = (open) => {
    nav?.classList.toggle("is-open", open);
    menuBtn?.setAttribute("aria-expanded", open ? "true" : "false");
  };

  langBtn?.addEventListener("click", () => {
    const next = currentLang() === "ru" ? "en" : "ru";
    localStorage.setItem(LANG_KEY, next);
    applyI18n();
    window.dispatchEvent(new Event("skye-i18n"));
  });

  themeBtn?.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
  });

  soundBtn?.addEventListener("click", () => {
    const on = document.documentElement.dataset.sound !== "on";
    applySound(on);
    if (on) play("ready");
  });

  menuBtn?.addEventListener("click", () => {
    const open = !nav?.classList.contains("is-open");
    setMenu(open);
    play(open ? "bloom" : "droplet");
  });

  nav?.addEventListener("click", (event) => {
    if (event.target instanceof Element && event.target.closest("a")) setMenu(false);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !nav?.classList.contains("is-open")) return;
    setMenu(false);
    menuBtn?.focus();
  });

  document.addEventListener("click", (event) => {
    if (!nav?.classList.contains("is-open")) return;
    const target = event.target;
    const insideNav = target instanceof Node && nav.contains(target);
    const insideButton = menuBtn?.contains(target);
    if (insideNav || insideButton) return;
    setMenu(false);
  });
}

function revealOnScroll() {
  const nodes = document.querySelectorAll(".reveal");
  if (!nodes.length) return;
  if (
    matchMedia("(prefers-reduced-motion: reduce)").matches ||
    !("IntersectionObserver" in window)
  ) {
    nodes.forEach((el) => el.classList.add("is-in"));
    return;
  }
  const io = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.classList.add("is-in");
        io.unobserve(entry.target);
      }
    },
    { threshold: 0.16, rootMargin: "0px 0px -8% 0px" },
  );
  nodes.forEach((el) => io.observe(el));
}

bootPreferences();
applyI18n();
document.documentElement.dataset.ready = "1";
bind();
wireHeader();
revealOnScroll();

document.addEventListener("pointerdown", () => {
  if (sessionStorage.getItem("skye-arrived")) return;
  sessionStorage.setItem("skye-arrived", "1");
  play("arrival");
}, { once: true });

export { play };
