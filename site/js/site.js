import { bind, play, setEnabled, setVolume } from "../vendor/cuelume/index.js";
import { applyI18n, currentLang } from "./i18n.js";

const THEME_KEY = "skye-theme";
const LANG_KEY = "skye-lang";
const SOUND_KEY = "skye-sound";

function storedTheme() {
  return localStorage.getItem(THEME_KEY);
}

function preferredTheme() {
  return storedTheme() ?? "light";
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_KEY, theme);
}

export function applySound(on) {
  document.documentElement.dataset.sound = on ? "on" : "off";
  localStorage.setItem(SOUND_KEY, on ? "on" : "off");
  setEnabled(on);
}

function bootPreferences() {
  applyTheme(preferredTheme());
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
    const open = nav?.classList.toggle("is-open");
    menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    play(open ? "bloom" : "droplet");
  });
}

function revealOnScroll() {
  const nodes = document.querySelectorAll(".reveal");
  if (!nodes.length) return;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
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
