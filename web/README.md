# Skye web chat

The web transport for Skye: a private project chat at `chat.skye-bot.com`.
Telegram login, streaming replies, files, and per-project settings. Built with
React 19, Vite, Tailwind CSS v4, and [sunkit-ui](https://github.com/evvyraine/sunkit).

## Local preview

Run the app against an in-memory mock API:

```bash
npm run dev:mock
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). Project edits, messages,
search, reset, delete, dictation, and logout are mocked and reset when the
process stops.

Point it at a real backend instead:

```bash
npm run dev   # proxies /api and /auth to http://127.0.0.1:8080
```

Both ports are configurable when something else already holds `8080`
(for example, `SKYE_MOCK_PORT=8081 npm run dev:mock`, or
`SKYE_API_PORT=8090 npm run dev`). The mock tells Vite which port to proxy to.

## Design system

The interface is assembled from **sunkit-ui** components, not local shadcn
primitives. The app-level pieces that remain here are feature components
(`chat-view`, `project-list`, `settings-panel`, …), a markdown renderer, and
attachment previews — all composed from sunkit building blocks.

- **Type**: Geist (UI) and Geist Mono (code), bundled locally via Fontsource.
- **Color**: the eight sunkit pastel tones with a violet/lavender accent
  (`#7c6cdc`). Project badges use a pastel sticker palette plus geometric
  `Shape`s for a playful, non-rectangular feel.
- **Themes**: light and dark, following the OS by default. Switch from the
  sun/moon menu in the project-list header.
- **Motion & sound**: Motion springs and sunkit's Web Audio cues. Both respect
  `prefers-reduced-motion`.
- **Responsive**: mobile-first. One pane below `md`, a persistent sidebar and
  chat above it, and a third settings column at `72rem`. Bottom sheets on
  phones, side sheets on tablets.

## sunkit-ui dependency

`sunkit-ui` is published to npm under the `alpha` dist-tag and pinned by exact
version in `package.json`, so `npm ci` resolves it from the registry on CI and
on the server.

To pull in new or changed sunkit components, release them from the sunkit repo
(merge the Changesets version PR; the Release workflow publishes via npm Trusted
Publishing), then bump the pin here and reinstall:

```bash
cd web
npm install sunkit-ui@alpha
```

Pin the exact published version (e.g. `0.2.0-alpha.7`); prerelease ranges are
easy to misread.

## Scripts

```bash
npm run dev        # Vite dev server (proxies /api, /auth)
npm run dev:mock   # Vite + in-memory mock API
npm run build      # tsc -b && vite build
npm run typecheck
npm run lint
npm run preview
```

## Layout

```
src/
├── App.tsx                 # auth gates, project state, responsive shell
├── components/
│   ├── chat-view.tsx       # message list, streaming, composer, dictation
│   ├── project-list.tsx    # search, rows, brand header
│   ├── create-project.tsx  # dialog (desktop) / sheet (mobile)
│   ├── settings-panel.tsx  # name, instructions, appearance
│   ├── attachment-card.tsx # attachment deck + preview
│   ├── profile-menu.tsx    # profile dropdown / mobile sheet
│   ├── theme-toggle.tsx    # light / dark / system
│   ├── sound-toggle.tsx    # sunkit sound on/off
│   └── markdown.tsx        # react-markdown + remark-gfm
├── hooks/
│   ├── use-mobile.ts
│   └── use-stick-to-bottom.ts
└── lib/
    ├── api.ts              # fetch wrappers + SSE stream parsing
    ├── icons.tsx           # project icons, pastels, shapes
    └── types.ts
```
