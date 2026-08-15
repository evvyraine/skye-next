# Skye Next architecture

## Product thesis

Skye Next is a private, allowlisted Telegram interface to one OpenAI-powered
agent runtime. It should expose OpenAI's native capabilities with as little
application code as possible.

The application owns only what OpenAI and Telegram cannot own for us:

- Telegram identity, permissions, updates, buttons, streaming, and files;
- durable product data: settings, allowlist, memories, agents, skills, and
  connector selections;
- safe composition of the active agent and its capabilities;
- reliability, observability, and lifecycle management.

The application does not implement its own model loop, provider abstraction,
billing, subscription system, web panel, connector framework, or general
module/plugin framework.

## Decisions

### One provider and one runtime

- Use the OpenAI Responses API through `openai-agents`.
- Use `Agent` and `Runner.run_streamed()` for every normal chat turn.
- Use the official `openai` client only for resource lifecycle operations that
  are not owned by the Agents SDK: conversations, files, and uploaded skills.
- Do not preserve a fallback Chat Completions loop.
- Do not add a generic provider interface until a second real provider exists.

The initial model catalog is deliberately small:

| UI label | Model id | Role |
| --- | --- | --- |
| Luna | `gpt-5.6-luna` | Fast and economical default |
| Terra | `gpt-5.6-terra` | Balanced |
| Sol | `gpt-5.6-sol` | Highest capability |

The catalog is code-owned and records capabilities. A model can be shown in
settings only when the runtime supports the tools Skye promises for it.

### No Mini App

There is no web frontend and no Telegram menu web app. `/settings` renders an
inline-keyboard message and edits that same message as the user navigates.
Callback data contains only a short action and opaque id; no JSON and no
trusted state.

Private-chat settings belong to the Telegram user. Group settings belong to
the Telegram chat. Only a Telegram chat administrator or a Skye administrator
may change group settings.

### Explicit composition over a module framework

The old Skye's module host solved a real problem, but it also made startup
order, contribution collection, and service discovery part of the product.
Skye Next has one typed `App` container and one explicit startup function.
Feature packages may expose routers, repositories, services, or tools, but do
not register themselves dynamically.

### Two distinct kinds of memory

Conversation state and long-term memory are separate:

1. OpenAI `conversation_id` is the working context for a Telegram thread.
   Only the new turn is sent on each run. `/reset` creates a new conversation.
2. Skye memories are durable, small, inspectable facts stored locally and
   accessed through `remember`, `recall`, and `forget` function tools.

Never combine an Agents SDK `Session` with `conversation_id` for the same run.
The local database stores the OpenAI conversation id, not a duplicate model
transcript. A compact run log is retained for diagnostics and Telegram-facing
history, but is not replayed into the model.

Private memories have `scope=user`. Group memories have `scope=chat`. A group
run never receives a participant's private memories. Telegram forum topics get
separate conversation state but share the group's settings and group memory.

### Hosted execution only

Shell commands never run on the bot host. The root agent receives a hosted
`ShellTool` with `container_auto`, bounded memory, and networking disabled by
default. Telegram files needed by a task are uploaded and mounted into that
container. A future allowlisted-network mode may be added globally; it is not
a per-user setting in v1.

Web search and image generation/editing are native hosted tools. Users ask in
natural language; `/image` and tool toggles are unnecessary.

### Declarative, shareable agents

A custom agent is data, not executable Python:

- name, description, and instructions;
- optional model override;
- enabled hosted capabilities;
- attached skill versions;
- visibility: `private`, `unlisted`, or `public`;
- immutable published versions.

This makes agents safe to import, cache, clone, inspect, and share. Arbitrary
Python/function tools are not accepted from users.

The normal pattern is manager-style orchestration. Skye remains the root agent
and installed specialists are exposed with `Agent.as_tool()`, so Skye owns the
final answer and shared safety rules. Selecting a custom agent as the active
agent makes that profile the root for the conversation. Handoffs are reserved
for a later workflow that truly needs the specialist to take over.

An unlisted/public agent is shared as an immutable version through a Telegram
deep link such as `https://t.me/<bot>?start=agent_<token>`. Import creates an
installation referencing that version. Editing the source creates a new
version; it never silently changes someone else's installed agent.

### Skills are first-class from day one

A skill is a versioned bundle containing `SKILL.md` plus optional supporting
files. It has a name, description, owner, visibility, checksum, and immutable
versions. Accepted inputs are:

- one Markdown file, normalized to `SKILL.md`;
- a `.zip` containing one `SKILL.md` and safe relative files.

Uploads reject absolute paths, `..`, symlinks, nested archives, executables,
oversized files, excessive file counts, and duplicate paths. User bundles are
never imported or executed by the bot process.

At run time the agent receives a compact catalog of enabled skills and the
selected versions are mounted into the hosted shell as inline skill bundles.
The provider adapter may later cache them as OpenAI skill resources without
changing the domain model. Only skills attached to the active agent or enabled
for the current user/chat are mounted; the complete library is not placed in
every prompt.

Skills use the same immutable sharing model and deep-link import flow as
agents. Group skill installation and selection require group-admin rights.

## Telegram experience

All user-facing text is English. The permanent command list stays small:

| Command | Purpose |
| --- | --- |
| `/start` | Welcome, deep-link import, and access status |
| `/help` | Compact capabilities and usage |
| `/settings` | Inline model, agent, skill, memory, and connector settings |
| `/agents` | Create, edit, install, share, select, and remove agents |
| `/skills` | Add, inspect, enable, attach, share, and remove skills |
| `/reset` | Start a fresh OpenAI conversation for this thread |
| `/stop` | Cancel the active run in this thread |
| `/admin` | Admin-only allowlist controls |

`/settings` in a private chat:

```text
Settings

Model        Luna
Reasoning    Medium
Agent        Skye
Connectors   2 connected
Memory       On

[ Model ] [ Reasoning ]
[ Agent ] [ Connectors ]
[ Memory ]
```

Connectors are set up in a private chat. A group run never receives a
member's private connectors unless that person explicitly shared a specific
app or custom MCP with that group. Shared connectors stay references to the
owner's current connection; revoke, disconnect, or delete removes them from
the group. Hosted apps connect through Composio Connect Links; a custom
HTTPS MCP is stored locally and attached as a native hosted MCP tool.

`/settings` in a group shows one shared model and one shared default agent.
Non-admin members may inspect the settings but do not receive mutation
buttons. There are no per-member model overrides inside a group.

`/admin` is owner-only and uses the same in-place keyboard. There are no
admin subcommands.

In a private chat the owner sees the full allowlist:

```text
Access

Owner-only allowlist. A ban beats every allow except the owner.

[ Allow ] [ Ban ]
[ Remove ]
[ user 42 · allow ]
```

Allow, Ban, and Remove ask for a reply to that prompt with a numeric
Telegram id. Tapping an entry opens Allow / Ban / Remove for that id.

In a group the panel shows only this group's status. Members never see
other allow/ban entries. The group keyboard is Allow this group and/or
Remove this group, plus one-tap Allow / Ban if `/admin` is a reply to a
user. Manage the rest in a private chat.

Agent creation is a short Telegram wizard:

1. name;
2. what the agent is good at;
3. instructions (typed, pasted, or uploaded as Markdown);
4. optional skills;
5. preview and save.

Skill creation is similarly short: `/skills` -> `Add` -> upload Markdown/ZIP
-> validation preview -> save. Buttons handle management after creation; users
do not need to memorize subcommands.

In groups the bot responds only when mentioned, replied to, invoked by command,
or addressed inside a dedicated bot topic. This prevents accidental spending
and noise without introducing quotas.

## Access model

Access is deny-by-default.

An update is allowed when any of the following is true:

- its sender is the owner or a Skye administrator;
- a private chat's user id is allowlisted;
- the current group/supergroup chat id is allowlisted.

Allowlisting a group allows every current and future member to use Skye only
inside that group. It does not allow those members to use Skye in private or in
another group. An explicit ban wins over every allow entry except the immutable
owner.

The `/admin` keyboard supports:

- allow this group;
- allow a replied-to user;
- allow by numeric id;
- remove access;
- ban/unban;
- list entries.

The first owner comes from `SKYE_OWNER_IDS`; there is no public claim token in
the minimal version.

## Runtime assembly

For every accepted message, `AgentFactory` builds an agent from current data:

```text
base identity
+ autonomy and safety policy
+ Telegram/user/chat context
+ relevant durable memories
+ active agent instructions
+ compact enabled-skill catalog
+ hosted tools
+ memory function tools
+ installed specialists as tools
```

Hosted tools in v1:

- `WebSearchTool(search_context_size="medium")`;
- `ImageGenerationTool` configured for `gpt-image-2`;
- `ShellTool(environment={"type": "container_auto", ...})`;
- `ToolSearchTool()` only when deferred function namespaces or deferred MCP
  tools actually exist. It is not useful by itself.
- `HostedMCPTool` for the user's Composio session and each enabled custom
  HTTPS MCP server. Composio credentials never enter the bot database.

Application function tools in v1:

- `remember(content, category)`;
- `recall(query)`;
- `forget(memory_id)`.

Agent specialists are added dynamically from installations visible to the
current scope. Tool names use stable internal ids, not user-controlled names.

Run flow:

```text
Telegram update
  -> persist/idempotency check
  -> identity + allowlist middleware
  -> command or message router
  -> per-thread queue and cancellation scope
  -> download/validate attachments
  -> load settings, conversation, memories, agents, skills
  -> build root agent and tools
  -> Runner.run_streamed(..., conversation_id=...)
  -> throttle Telegram draft edits
  -> send text/images/files
  -> atomically persist run result and resource ids
```

Image output is treated as an artifact, not embedded into a text response.
Generated images are downloaded/decoded, validated, sent as Telegram photos or
documents, and recorded in `artifacts`. A user's current image and replied-to
images are passed as image inputs, enabling edits through the same tool.

## Prompt strategy

Keep `BASE_PROMPT.md` limited to Skye's stable identity, tone, and product-wide
authorization boundary. Build all volatile context through a dynamic
instructions function:

- current user/chat/topic identity;
- active agent instructions;
- relevant memories;
- enabled skill catalog;
- only the capabilities actually available in this run.

State every rule once. Do not paste long tool manuals into the prompt when the
tool description already explains the contract, and never describe a tool that
is not present. Keep the stable prefix first for prompt caching. Version prompt
composition in code and evaluate changes against a small, fixed conversation
set instead of growing the prompt after every isolated failure.

## Package layout

```text
skye-next/
├── src/skye/
│   ├── __init__.py
│   ├── __main__.py             # python -m skye
│   ├── app.py                  # explicit composition root and lifecycle
│   ├── config.py               # pydantic-settings, environment only
│   ├── logging.py              # structured logging and redaction
│   ├── context.py              # immutable RequestContext
│   ├── storage/
│   │   ├── db.py               # aiosqlite connection, WAL, transactions
│   │   ├── migrations.py       # tiny ordered PRAGMA user_version migrations
│   │   └── schema.py           # row/domain conversion helpers
│   ├── access/
│   │   ├── repository.py
│   │   ├── service.py
│   │   └── handlers.py
│   ├── settings/
│   │   ├── repository.py
│   │   ├── service.py
│   │   └── handlers.py         # /settings and callback keyboards
│   ├── conversations/
│   │   ├── repository.py
│   │   └── service.py          # OpenAI conversation lifecycle and reset
│   ├── memory/
│   │   ├── repository.py
│   │   ├── service.py
│   │   └── tools.py
│   ├── custom_agents/
│   │   ├── models.py
│   │   ├── repository.py
│   │   ├── service.py
│   │   ├── sharing.py
│   │   └── handlers.py
│   ├── skills/
│   │   ├── models.py
│   │   ├── repository.py
│   │   ├── service.py
│   │   ├── bundles.py          # safe validation and normalization
│   │   ├── provider.py         # compile/mount for OpenAI hosted shell
│   │   └── handlers.py
│   ├── runtime/
│   │   ├── factory.py          # construct Agent for one RequestContext
│   │   ├── runner.py           # streaming, cancellation, timeout, recovery
│   │   ├── hosted_tools.py
│   │   ├── events.py           # SDK event -> product event
│   │   └── artifacts.py
│   └── telegram/
│       ├── bot.py              # Dispatcher, startup, polling/webhook
│       ├── middleware.py       # context, access, idempotency
│       ├── chat.py             # text/media message path
│       ├── files.py
│       ├── rendering.py        # safe HTML, splitting, draft throttling
│       └── commands.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fakes/
├── data/                       # ignored runtime state
├── BASE_PROMPT.md
├── ARCHITECTURE.md
├── README.md
└── pyproject.toml
```

This is a map, not a requirement to create every file before it has behavior.
Start with one file per feature and split only when a file has two reasons to
change.

## Data model

SQLite in WAL mode is the default. One process and one writer are enough for
the intended allowlisted deployment. Repository interfaces are introduced only
at feature boundaries, making a later PostgreSQL move possible without making
v1 pay for it.

Core tables:

| Table | Important fields |
| --- | --- |
| `access_entries` | `kind`, `telegram_id`, `effect`, `created_by`, timestamps |
| `user_settings` | `user_id`, `model`, `reasoning`, `active_agent_id`, `memory_enabled` |
| `chat_settings` | `chat_id`, `model`, `reasoning`, `active_agent_id`, `memory_enabled` |
| `conversations` | `chat_id`, `thread_id`, `openai_conversation_id`, timestamps |
| `memories` | `id`, `scope_kind`, `scope_id`, `category`, `content`, timestamps |
| `agents` | `id`, `owner_id`, `visibility`, `current_version`, `share_token` |
| `agent_versions` | `agent_id`, `version`, declarative definition JSON, checksum |
| `agent_installs` | `scope_kind`, `scope_id`, `agent_id`, `version`, enabled |
| `skills` | `id`, `owner_id`, `visibility`, `current_version`, `share_token` |
| `skill_versions` | `skill_id`, `version`, bundle path, checksum, provider ids |
| `skill_installs` | `scope_kind`, `scope_id`, `skill_id`, `version`, enabled |
| `agent_skills` | `agent_id`, `agent_version`, `skill_id`, `skill_version` |
| `updates` | `update_id`, payload, state, attempts, timestamps, last_error |
| `runs` | scope, update id, model, status, OpenAI ids, usage, latency, error class |
| `artifacts` | run id, kind, OpenAI file id, Telegram file id, metadata |
| `custom_connectors` | `id`, `user_id`, name, HTTPS URL, headers, enabled |
| `user_toolkits` | `user_id`, Composio toolkit slug for no-auth apps |
| `composio_session_cache` | `user_id`, toolkit key, session id, MCP URL |
| `known_chats` | `chat_id`, title |
| `connector_shares` | `id`, `chat_id`, `owner_id`, kind, ref |

`scope_kind + scope_id` is always validated through a shared value object.
Repository methods accept that object so private data cannot accidentally be
queried with a group id or vice versa.

Memory starts with normalized SQLite FTS5 search plus always-included recent
preferences. Do not add embeddings until representative conversations show
that FTS retrieval is insufficient. The storage contract leaves room for an
embedding column later.

## Reliability and safety

- Persist every Telegram update before processing and make `update_id` unique.
  Replay `pending` updates after restart.
- Serialize work per `(chat_id, thread_id)`; different chats run concurrently.
- A new ordinary message queues behind the active turn. `/stop` cancels it.
- Bound model turns, total run time, attachment bytes, skill bundle bytes,
  Telegram edit frequency, and stored tool output. These are operational
  safety limits, not subscriptions or user quotas.
- Retry transient OpenAI and Telegram failures with exponential backoff and
  jitter only when replay is safe. Never retry a completed side effect blindly.
- Keep shell networking disabled. Never pass bot secrets into a hosted
  container or user skill.
- Send a stable, privacy-preserving `safety_identifier` derived with HMAC from
  the Telegram user id.
- Treat skill text, web results, files, group messages, and future connector
  results as untrusted content, never as higher-priority instructions.
- Redact tokens, full prompts, memory contents, file bodies, and skill bodies
  from normal logs. Tracing is opt-in and sensitive tracing is off.
- `/reset` deletes/replaces working conversation state but keeps long-term
  memories. A separate destructive action in settings deletes memories.
- User deletion removes owned private memories, settings, installations,
  unpublished agents/skills, and stored OpenAI resources where possible.

The absence of billing does not mean the absence of an emergency brake. Global
daily cost alerts, a maximum concurrent-run setting, and an operator kill
switch protect the deployment without creating product tiers.

## Configuration

Use environment variables through `pydantic-settings`; no runtime YAML and no
per-user API keys.

Required:

```text
TELEGRAM_BOT_TOKEN=
OPENAI_API_KEY=
SKYE_OWNER_IDS=123456789
```

Important defaults:

```text
SKYE_DATABASE_PATH=data/skye.db
SKYE_DEFAULT_MODEL=gpt-5.6-luna
SKYE_DEFAULT_REASONING=medium
SKYE_MAX_TURNS=20
SKYE_RUN_TIMEOUT_SECONDS=300
SKYE_MAX_ATTACHMENT_BYTES=26214400
SKYE_SHELL_NETWORK=disabled
SKYE_TRACING=false
COMPOSIO_API_KEY=
```

`COMPOSIO_API_KEY` is optional. Without it, hosted app connections are hidden
and custom HTTPS MCP still works.

## Dependencies

Keep the direct dependency list short:

- `aiogram` for Telegram;
- `openai` and `openai-agents` for the only AI provider/runtime;
- `httpx` for the Composio REST client;
- `pydantic-settings` for configuration;
- `aiosqlite` for durable local state;
- `structlog` for structured logs;
- `tenacity` only if SDK/client retry hooks are insufficient.

Use the standard library for ids, HMAC, zip validation, paths, queues, and
datetimes. Do not add FastAPI, SQLAlchemy, Redis, Celery, an ORM, or a DI
framework until a concrete requirement demands one.

## Test strategy

The architecture is tested at its boundaries:

- unit tests for scope resolution, allowlist precedence, callback parsing,
  bundle validation, prompt composition, and Telegram rendering;
- SQLite integration tests for every repository and migration;
- runtime tests with a fake Agents model that emits text, web, shell, image,
  nested-agent, timeout, and failure events;
- Telegram update fixtures for private chats, groups, topics, replies, media,
  duplicate updates, and cancellation;
- contract smoke tests, manually enabled with a real OpenAI key, for the three
  models and every hosted tool;
- golden tests for the English `/settings`, `/agents`, and `/skills` flows.

The most important end-to-end scenarios are:

1. an allowlisted private user changes model and continues a conversation;
2. every member of an allowlisted group can use the one group model;
3. private memory never appears in a group run;
4. an image is generated and a replied-to image is edited;
5. shell operates only in a hosted container and can use an uploaded skill;
6. a shared agent/skill version can be imported without future silent changes;
7. a process restart replays a pending update exactly once.

## Delivery sequence

### Iteration 1: thin vertical slice

- package skeleton, configuration, SQLite migrations, logs;
- owner + user/chat allowlist;
- private/group text and image input;
- three-model `/settings` keyboard;
- OpenAI conversation state, streamed replies, `/reset`, `/stop`;
- hosted web search, image generation/editing, and shell;
- durable update inbox, restart replay, retries, per-thread serialization, and
  essential tests.

This iteration should already feel like the product.

### Iteration 2: durable memory

- scoped memory repository and FTS;
- memory tools and compact context injection;
- memory UI, deletion, and privacy tests.

### Iteration 3: custom agents

- declarative versions, Telegram creation/edit flow;
- manager-style `Agent.as_tool()` orchestration;
- active agent selection, deep-link sharing, and import.

### Iteration 4: skills

- Markdown/ZIP ingestion and validation;
- hosted-shell mounting, enable/disable, agent attachment;
- immutable version sharing and import.

### Iteration 5: hardening

- resource cleanup, cost telemetry, operator diagnostics;
- eval set from real conversations and prompt/tool simplification;
- webhook production mode if deployment topology benefits from it.

Connectors, reminders, voice output, proactive group participation, channel
management, and public discovery are deliberately outside the first four
iterations. Each can later enter as a vertical feature without changing the
runtime core.

## What to carry forward from the old Skye

Keep the proven ideas:

- request context distinguishes user, chat, and forum topic;
- allowlisting a negative Telegram chat id grants group-local access;
- per-thread serialization, cancellation, attachment limits, and polling lock;
- streamed Telegram drafts and safe message splitting;
- explicit long-term memory tools;
- custom agents represented as data;
- structured run/audit metadata without sensitive payloads.

Do not port the accidental complexity:

- Mini App and panel routes;
- subscription, token quota, and provider multipliers;
- OpenRouter/Perplexity routing and compatibility fallbacks;
- legacy hand-written tool loop;
- global module registry and order-dependent service discovery;
- Daytona sandbox when OpenAI hosted shell is sufficient;
- YAML composition of many optional subsystems.

## Architectural guardrail

Before adding a subsystem, ask:

1. Can OpenAI or Telegram already own this state or execution loop?
2. Is it required by a real user flow in the current iteration?
3. Can it be expressed as data, a hosted tool, or one small function tool?
4. Does it preserve user/chat isolation and immutable sharing?

If the first answer is yes or the second is no, Skye probably should not own
the code yet.
