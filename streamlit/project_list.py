# ruff: noqa: E501
"""Custom Component v2: the sidebar project list.

Renders project rows with the landing look, picks up the active Streamlit theme
through ``--st-*`` CSS variables, and uses each project's Material Symbols icon
plus its pastel color. Emits a single trigger, ``event``, as a dict:

    {"type": "new" | "select" | "pin" | "edit" | "delete", "id": <project id>}

See https://docs.streamlit.io/develop/concepts/custom-components/components-v2
"""

from __future__ import annotations

from typing import Any

import streamlit as st

import ui

_HTML = """
<link rel="stylesheet"
  href="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" />
<div class="bar">
  <button id="new" class="new" type="button">
    <span class="ms">add</span><span>New project</span>
  </button>
  <label class="search">
    <span class="ms">search</span>
    <input id="search" type="text" placeholder="Search projects" aria-label="Search projects" />
  </label>
</div>
<div id="root" class="list"></div>
"""

_CSS = """
:host {
  display: block;
  font-family: var(--st-font, sans-serif);
  font-size: var(--st-base-font-size, 15px);
  color: var(--st-text-color);
}
*, *::before, *::after { box-sizing: border-box; }

.ms {
  font-family: "Material Symbols Rounded";
  font-weight: normal; font-style: normal;
  font-size: 20px; line-height: 1; display: inline-block;
  letter-spacing: normal; text-transform: none; white-space: nowrap;
  word-wrap: normal; direction: ltr;
  -webkit-font-feature-settings: "liga"; font-feature-settings: "liga";
  -webkit-font-smoothing: antialiased;
  font-variation-settings: "FILL" 0, "wght" 400, "GRAD" 0, "opsz" 24;
}

.bar { display: flex; flex-direction: column; gap: 0.4rem; margin-bottom: 0.6rem; }

.new {
  display: flex; align-items: center; justify-content: center; gap: 0.4rem;
  width: 100%; border: none; cursor: pointer; padding: 0.5rem 0.75rem;
  border-radius: var(--st-button-radius, 1.5rem);
  background: var(--st-primary-color); color: var(--st-background-color);
  font: inherit; font-weight: 600; font-size: 0.88rem;
}
.new:hover { filter: brightness(1.06); }

.search {
  display: flex; align-items: center; gap: 0.4rem; padding: 0 0.6rem;
  border: 1px solid var(--st-border-color);
  border-radius: var(--st-button-radius, 1.5rem);
  background: var(--st-background-color);
}
.search > .ms { font-size: 18px; opacity: 0.55; }
.search input {
  flex: 1 1 auto; min-width: 0; padding: 0.45rem 0; border: none; outline: none;
  background: transparent; color: inherit; font: inherit; font-size: 0.86rem;
}
.search input::placeholder { color: inherit; opacity: 0.5; }

.list { display: flex; flex-direction: column; gap: 2px; }

.item {
  position: relative; display: flex; align-items: center;
  border-radius: var(--st-base-radius, 0.85rem);
}
.item:hover { background: var(--st-secondary-background-color); }
.item.selected { background: color-mix(in srgb, var(--st-primary-color) 13%, transparent); }

.row {
  flex: 1 1 auto; min-width: 0; display: flex; align-items: center; gap: 0.55rem;
  padding: 0.32rem 0.4rem; border: none; background: transparent;
  color: var(--st-text-color); font: inherit; text-align: left; cursor: pointer;
}

.badge {
  flex: 0 0 auto; width: 34px; height: 34px;
  border-radius: var(--st-base-radius, 0.85rem);
  display: flex; align-items: center; justify-content: center;
}
.badge .ms {
  font-size: 19px;
  font-variation-settings: "FILL" 1, "wght" 500, "GRAD" 0, "opsz" 24;
}
.name {
  flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; font-size: 0.9rem; font-weight: 500; letter-spacing: -0.01em;
}
.pin { flex: 0 0 auto; display: flex; opacity: 0.85; }
.pin .ms { font-size: 15px; }
.when { flex: 0 0 auto; font-size: 0.72rem; opacity: 0.6; font-variant-numeric: tabular-nums; }

.menu-wrap { position: relative; flex: 0 0 auto; margin-right: 0.25rem; opacity: 0; }
.item:hover .menu-wrap, .item.selected .menu-wrap, .menu-wrap[open] { opacity: 1; }
.menu-wrap > summary {
  list-style: none; cursor: pointer; display: flex; align-items: center;
  justify-content: center; width: 26px; height: 26px; border-radius: 999px; color: inherit;
}
.menu-wrap > summary::-webkit-details-marker { display: none; }
.menu-wrap > summary:hover { background: color-mix(in srgb, currentColor 14%, transparent); }

.menu {
  position: absolute; right: 0; top: 1.85rem; z-index: 30; min-width: 10.5rem;
  padding: 0.25rem; background: var(--st-background-color); color: var(--st-text-color);
  border: 1px solid var(--st-border-color);
  border-radius: var(--st-base-radius, 0.85rem);
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.16);
}
.menu button {
  display: flex; align-items: center; gap: 0.5rem; width: 100%;
  border: none; background: transparent; color: inherit; font: inherit;
  font-size: 0.85rem; text-align: left; padding: 0.4rem 0.5rem;
  border-radius: calc(var(--st-base-radius, 0.85rem) - 3px); cursor: pointer;
}
.menu button:hover { background: var(--st-secondary-background-color); }
.menu button.danger:hover { color: var(--st-red-color, #d33); }
.menu .ms { font-size: 17px; opacity: 0.8; }

.empty { padding: 0.6rem 0.4rem; font-size: 0.8rem; opacity: 0.6; }
"""

_JS = r"""
export default function (component) {
  const { data, parentElement, setStateValue, setTriggerValue } = component
  const root = parentElement.querySelector("#root")
  const search = parentElement.querySelector("#search")
  const newBtn = parentElement.querySelector("#new")
  if (!root || !search || !newBtn) return

  const projects = data.projects || []
  const icons = data.icons || {}
  const inks = data.inks || {}
  const palette = data.palette || {}
  const selected = data.selected

  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ESC[c])
  const ms = (name) => `<span class="ms">${escapeHtml(name)}</span>`

  // Hydrate the search box without fighting the cursor; emit after a pause.
  const query = data.query ?? ""
  if (search.value !== query) search.value = query
  let timer = null
  search.oninput = () => {
    clearTimeout(timer)
    timer = setTimeout(() => setStateValue("query", search.value), 200)
  }

  newBtn.onclick = () => setTriggerValue("event", { type: "new" })

  // Close open row menus when clicking anywhere outside this component.
  const closeMenus = () => {
    parentElement.querySelectorAll("details.menu-wrap[open]").forEach((node) => {
      node.removeAttribute("open")
    })
  }
  window.__skyeCloseMenus = closeMenus
  const host = parentElement.host || parentElement
  if (host && !host.__skyeMenuBound) {
    host.__skyeMenuBound = true
    document.addEventListener("click", (event) => {
      const path = event.composedPath ? event.composedPath() : []
      if (path.includes(host)) return
      if (window.__skyeCloseMenus) window.__skyeCloseMenus()
    })
  }

  const actionsFor = (p) => {
    const actions = []
    if (!p.is_inbox) actions.push(["pin", "push_pin", p.pinned ? "Unpin" : "Pin", false])
    actions.push(["edit", "edit", "Edit project", false])
    if (p.deletable) actions.push(["delete", "delete", "Delete project", true])
    return actions
  }

  root.innerHTML = ""
  if (!projects.length) {
    const empty = document.createElement("div")
    empty.className = "empty"
    empty.textContent = data.empty || "Nothing here yet."
    root.appendChild(empty)
    return
  }

  for (const p of projects) {
    const item = document.createElement("div")
    item.className = "item" + (p.id === selected ? " selected" : "")
    item.innerHTML =
      '<div class="row" role="button" tabindex="0">' +
        '<span class="badge" style="background:' +
          escapeHtml(palette[p.color] || "#C9C5D8") + '">' +
          '<span class="ms" style="color:' +
            escapeHtml(inks[p.color] || "#333333") + '">' +
            escapeHtml(icons[p.icon] || "folder") +
          "</span>" +
        "</span>" +
        '<span class="name">' + escapeHtml(p.name) + "</span>" +
        (p.pinned ? '<span class="pin">' + ms("push_pin") + "</span>" : "") +
        '<span class="when">' + escapeHtml(p.when || "") + "</span>" +
      "</div>" +
      '<details class="menu-wrap">' +
        '<summary aria-label="Project actions">' + ms("more_horiz") + "</summary>" +
        '<div class="menu"></div>' +
      "</details>"

    const row = item.querySelector(".row")
    row.onclick = () => setTriggerValue("event", { type: "select", id: p.id })
    row.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); row.onclick() }
    }

    const menu = item.querySelector(".menu")
    for (const [type, icon, label, danger] of actionsFor(p)) {
      const button = document.createElement("button")
      button.type = "button"
      if (danger) button.className = "danger"
      button.innerHTML = ms(icon) + "<span>" + escapeHtml(label) + "</span>"
      button.onclick = (e) => {
        e.stopPropagation()
        setTriggerValue("event", { type: type, id: p.id })
      }
      menu.appendChild(button)
    }
    root.appendChild(item)
  }

  // Only one row menu open at a time.
  root.querySelectorAll("details.menu-wrap").forEach((details) => {
    details.addEventListener("toggle", () => {
      if (!details.open) return
      root.querySelectorAll("details.menu-wrap[open]").forEach((other) => {
        if (other !== details) other.removeAttribute("open")
      })
    })
  })
}
"""

_PROJECT_LIST = st.components.v2.component(
    "skye_project_list",
    html=_HTML,
    css=_CSS,
    js=_JS,
)


def project_list(
    *,
    projects: list[dict[str, Any]],
    selected: str | None,
    query: str,
    empty: str,
    key: str = "skye_projects",
) -> dict[str, Any] | None:
    """Mount the list and return the triggered event for this run, if any."""
    result = _PROJECT_LIST(
        key=key,
        data={
            "projects": projects,
            "selected": selected,
            "query": query,
            "empty": empty,
            "icons": ui.MATERIAL_ICONS,
            "inks": ui.PROJECT_INKS,
            "palette": ui.PROJECT_PASTELS,
        },
        on_event_change=lambda: None,
    )
    event = result.event
    return dict(event) if isinstance(event, dict) else None
