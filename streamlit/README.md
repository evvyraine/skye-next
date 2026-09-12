# Skye web — Streamlit demo

A standalone spike that previews the web chat and projects rebuilt on
Streamlit, themed after the landing page (`site/css/site.css`): Geist with
Cyrillic subsets, large radii, and the violet accent.

This is **variant A**: Streamlit is a thin client of the existing aiohttp API.
It reads the existing `skye_session` cookie and calls `/api/*` exactly like the
React client. It never touches SQLite and never duplicates the agent runtime,
so the bot process stays the single writer. `/ops` is untouched.

## Run

```bash
./streamlit/run.sh
```

This launches in an isolated `uv` environment (`--no-project`, Python 3.12) so
it does not change the bot's `pyproject.toml` or `.venv`. Extra arguments are
forwarded to `streamlit run`:

```bash
./streamlit/run.sh --server.port 8600
```

## Modes

- **Demo (default).** Without `SKYE_API_URL` the app uses an in-memory
  `DemoClient` with sample projects, tool steps, streamed replies, and a
  generated image. The login button simulates Telegram so you can see the whole
  shell immediately.
- **Live.** Set `SKYE_API_URL` to the running API and the app becomes a real
  client:

  ```bash
  SKYE_API_URL=http://127.0.0.1:8080 ./streamlit/run.sh
  ```

  The session is resolved from (in order): the `skye_session` cookie, the
  `?session=` query parameter, or the `SKYE_SESSION` environment variable.

  In production this only works **same-origin** behind Caddy, where `/` is
  Streamlit and `/api/*` + `/auth/*` reverse-proxy to the Python process, so
  the session cookie is shared. Locally the two servers are on different
  origins, so either put a small proxy in front or pass `SKYE_SESSION` / the
  `?session=` parameter for testing. Set `SKYE_AUTH_URL` to override the login
  link target.

## What this demonstrates

- A **Custom Component v2** (`project_list.py`) for the sidebar project list:
  it picks up the active theme through `--st-*` variables, renders each
  project's Material Symbols icon and pastel color, supports search, and
  emits select/new/pin/edit/delete events back to Python.
- `st.chat_message` history, `st.status(type="step")` tool timelines, a
  `st.status(type="compact")` "Thought" wrapper, generated images, and document
  attachments.
- `st.chat_input(accept_file="multiple", accept_audio=True, submit_mode="stop")`
  for attachments, dictation, and an in-flight stop affordance.
- `@st.dialog` for create / edit / delete project, with the backend's exact
  icon and color catalogs (`PROJECT_ICONS`, `PROJECT_COLORS`) mapped onto
  Material Symbols and the landing pastels.
- Theme parity with the landing via `.streamlit/config.toml` plus a small CSS
  block (`ui.inject_theme`). Fonts are self-hosted from `static/` so Cyrillic
  renders in Geist.

## Deploy (beta at `/beta`)

Mounted at `https://chat.skye-bot.com/beta` alongside the React app.

**Why a path, not `beta.chat.skye-bot.com`.** Variant A reuses the existing
`skye_session` cookie, which is host-only for `chat.skye-bot.com`. A subdomain
would not receive it (without widening the cookie to `.skye-bot.com`), so the
path mount is the correct shape.

Wiring (all in this repo):

- `streamlit/Dockerfile` — minimal image (`streamlit` + `httpx`, non-root).
- `compose.yaml` → `skye-beta` service, published on `127.0.0.1:18781`.
- `scripts/caddy-chat.skye-bot.com.caddy` → `handle /beta*` proxies to it
  **without** stripping the prefix (Streamlit runs with
  `--server.baseUrlPath=beta`).
- `scripts/deploy.sh` health-checks the container.

Environment:

| var | value | why |
| --- | --- | --- |
| `SKYE_API_URL` | `http://skye:8080` | server-side calls stay on the compose network |
| `SKYE_AUTH_URL` | `/auth/telegram` | the login button must hit the public path |
| `SKYE_BASE_PATH` | `/beta` | must match `--server.baseUrlPath` for asset URLs |

`SKYE_BASE_PATH` and `--server.baseUrlPath` are a pair: the former builds
`ui.asset()` URLs, the latter mounts Streamlit itself. Change one, change both.

Redeploy just the beta:

```bash
docker compose up -d --build skye-beta
```

Login caveat: the API's `/auth/callback` redirects to `/` (the React app), so a
first-time Telegram login lands there. The cookie is shared, so opening `/beta`
afterwards just works.

## Known gaps vs. the React client

- Sidebar rows show name + time; there is no preview line.
- No PWA, sound toggle, or motion — Material Symbols instead of Heroicons.
- One dialog at a time; no bottom sheets.
- `st.session_state` is per browser tab and resets on server restart; history is
  reloaded from the API each run.

## Notes for reviewers

- The folder is excluded from `ruff` (`[tool.ruff].extend-exclude` in
  `pyproject.toml`); `mypy` only checks `src/skye`, so nothing else changes for
  the shipped package.
- Files: `app.py` (shell + dialogs), `project_list.py` (CCv2 project list),
  `skye_api.py` (live + demo clients), `ui.py` (tokens, CSS, rendering),
  `static/` (Geist, logos), `Dockerfile` (beta image).
