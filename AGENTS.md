# AGENTS.md

Operating brief for anyone writing code or copy in this repository.

## Language

English only. No exceptions.

PRs, issues, commit messages, reviews, user communication, README, docs, comments that ship, and every string the bot shows or speaks are English. Do not mix languages in product text.

## What this is

Skye Next is a private, allowlisted OpenAI agent host. Users talk to Skye in Telegram or at `chat.skye-bot.com`; Skye talks to OpenAI.

The product should expose OpenAI's native capabilities with as little application code as possible.

Skye's voice is calm, short, warm, and grounded. She is female. Identity and tone live in `BASE_PROMPT.md` — keep that file limited to stable identity, tone, and the product-wide authorization boundary.

## What we own

- Telegram identity, permissions, updates, buttons, streaming, and files
- Web chat at `chat.skye-bot.com`: Telegram Login, private projects, streaming, and files
- Durable product data: settings, allowlist, memories, custom agents, connectors, skills, web projects, Telegram Stars entitlements
- Safe composition of the active agent and its tools
- Reliability, observability, and lifecycle

We do not own a model loop, a provider abstraction, a billing provider, an admin web panel, or a plugin framework. Telegram owns Stars payments; Skye stores the resulting entitlement.

## Core decisions

- **One provider.** OpenAI Responses API through `openai-agents`. Every normal chat turn is `Agent` + `Runner.run_streamed()`. Use the official `openai` client only for resource lifecycle (conversations, files, skills). No Chat Completions fallback. No second-provider interface until a second real provider exists.
- **One hosted model.** Chat turns use `gpt-5.6-luna`. There is no user-facing model picker. Public copy never names Luna, Terra, Sol, GPT, or other model IDs. If asked, Skye says she works on Sun Engine.
- **No Mini App.** Telegram settings stay inline-keyboard messages edited in place. Callback data is a short action plus an opaque id — never JSON, never trusted client state. The web app at `chat.skye-bot.com` is a second transport for private project chats, not a Mini App and not an admin panel.
- **Connectors are per user.** Hosted apps connect through Composio; custom HTTPS MCP is stored locally. A group run receives a connector only after the owner explicitly shares that one item with that group. The owner or a group admin can revoke the share.
- **Explicit composition.** One typed app container, one startup function. Features expose services; they do not register themselves.
- **Two memories, never mixed.** OpenAI `conversation_id` is the working context for a Telegram thread or a web project — send only the new turn; `/reset` (or web reset) starts a new conversation. Skye memories are small, inspectable, scoped facts (`remember` / `recall` / `forget`). Private memories are `scope=user`. Group memories are `scope=chat`. A group run never sees a participant's private memories. Never combine an Agents SDK `Session` with `conversation_id` on the same run. Telegram stores the OpenAI conversation id, not a duplicate transcript. The web UI may keep a display/search event log; that log is never sent back as model history. Web projects never share a conversation id with a Telegram DM.
- **Hosted execution only.** Shell never runs on the bot host. `ShellTool` uses `container_auto` with a request-level domain allowlist (`SKYE_SANDBOX_ALLOWED_DOMAINS`; must be a subset of the org allow list). Never pass bot secrets into that container. Web search and image generation/editing are native hosted tools; users ask in natural language.
- **Skills are data.** Users upload a zip bundle or a `SKILL.md` file; every file in the zip is stored locally and sent together to OpenAI `/v1/skills`. Mount them on hosted shell as `skill_reference`. Delete removes both the local copy and the OpenAI skill. Skills are scoped like memories: private skills stay `scope=user`, group skills stay `scope=chat`.
- **Custom agents are data**, not Python: name, description, instructions, optional model, hosted capabilities, visibility. Users cannot upload function tools. Sharing pins an immutable published version; later edits never change an imported install. Default orchestration is manager-style: Skye is the root, specialists are `Agent.as_tool()`. Selecting an agent as active makes that profile the root.
- **Access is deny-by-default.** Allowed if the sender is the owner, the private user is allowlisted, the current group is allowlisted, or the private user has an active Telegram Stars entitlement. A ban beats every allow except the immutable owner. Allowlisting a group grants access only inside that group. Stars plans grant private and web access, not group access.
- **Telegram Stars plans** are code-owned: Skye Plus (449 Stars / 30 days). Monthly plans use Bot API `createInvoiceLink` with `subscription_period=2592000`. Cancel renewal with `editUserStarSubscription` from `/account`. Usage is metered silently per Telegram user; user-facing copy talks about daily and monthly message allowance, never remaining counts.
- **Groups stay quiet** unless the bot is mentioned, replied to, invoked by command, or named in the text. At most the latest 20 *new* messages per topic since the last Skye turn are attached as untrusted user content — never a repeat of what OpenAI `conversation_id` already holds. Forum topics get their own conversation state but share the group's settings and group memory. Ignore Telegram reply-only thread ids.
- **Photos** go to vision only when they are on the current message or the user replies to a photo while addressing Skye. Voice, video notes, and audio are transcribed; only the text from video notes enters the model. Videos attached or replied to become a short text placeholder; the model never receives the video file. Documents are native OpenAI file inputs. Captions stay part of the request. Generated images are artifacts sent as Telegram photos, not embedded in text.

## How to write code

- Python 3.14, `uv`, src layout. Type everything; `mypy --strict`. Prefer `from __future__ import annotations`.
- Frozen `@dataclass(..., slots=True)` for domain objects. `Scope` (`user` | `chat` + id) is the isolation key — never query private data with a chat id or vice versa.
- Services over frameworks. No ORM, no DI container, no FastAPI, no Redis, no Celery. SQLite + WAL via `aiosqlite` is enough. One process, one writer.
- Config is environment only (`pydantic-settings`). No YAML. No per-user API keys.
- Keep the dependency list short. Standard library for ids, HMAC, zip, paths, queues, and datetimes.
- One file per feature until the file has two reasons to change. Then split. Do not create empty packages ahead of behavior.
- All outbound Telegram text goes through the rich-message boundary. Stream drafts in private chats; placeholder + edit in groups. Throttle edits.
- Bot copy: calm, short, sentence case. No "oops", no cheerleading, no corporate filler.
- Assemble volatile context per run (identity, active agent, memories, tools that are actually attached). State each rule once. Never describe a tool that is not present. Keep the stable prompt prefix first.
- Persist every Telegram `update_id` before work; drop `pending` on startup. Serialize Telegram runs per `(chat_id, thread_id)` and web runs per `web:{project_id}`. OpenAI runs wait in one process-wide queue. Rate limits and conversation locks are retried with the official SDK and Tenacity exponential backoff. `/stop` (or the web stop endpoint) cancels the active run.
- Logs are structured JSON. Redact tokens, prompts, memory contents, and file bodies. Tracing is opt-in. Send a privacy-preserving `safety_identifier` derived with HMAC from the Telegram user id — never the raw id.
- `/reset` replaces working conversation state and keeps long-term memories. Memory deletion is a separate destructive action.

## Do not add

- A second model provider
- A Mini App or an admin web panel
- A second payment provider, or product tiers outside Telegram Stars
- Token numbers in user-facing copy
- Local shell, Daytona, or host-side code execution
- Dynamic module discovery
- Embeddings for memory until FTS is proven insufficient
- Per-member model overrides inside a group
- Handoffs, unless a workflow truly needs the specialist to take over
- Tool manuals pasted into the prompt
- Secrets, full prompts, or memory contents in logs

Before adding a subsystem, ask:

1. Can OpenAI or Telegram already own this?
2. Is it required by a real current user flow?
3. Can it be data, a hosted tool, or one small function tool?
4. Does it preserve user/chat isolation and immutable sharing?

If (1) is yes or (2) is no, do not add it.

## Commands

Keep the public command list small: `/start`, `/help`, `/account`, `/settings`, `/projects`, `/agents`, `/catchup`, `/reset`, `/stop`, `/admin`. `/paysupport` and `/terms` exist because Telegram Stars requires them; keep them off the command menu.

## Tooling

```bash
uv sync
uv run ruff check .
uv run mypy
uv run pytest
```

Line length 100. Ruff selects `E`, `F`, `I`, `UP`, `B`, `SIM`.

Test the boundaries: scope isolation, allowlist precedence, callback parsing, prompt/tool composition, Telegram rendering, SQLite repositories, and fake-runtime events. Prefer focused tests over mocks of the universe.

Required env: `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `SKYE_OWNER_IDS`.
Optional: `COMPOSIO_API_KEY` for hosted app connections. Web chat also needs `SKYE_WEB_ORIGIN`, `TELEGRAM_LOGIN_CLIENT_ID`, and `TELEGRAM_LOGIN_CLIENT_SECRET`.

`ARCHITECTURE.md` is the product and system design. This file is the short operating brief — follow both, and do not re-litigate settled decisions.
