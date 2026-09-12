import { randomUUID } from "node:crypto"
import { spawn } from "node:child_process"
import { createServer } from "node:http"

const host = "127.0.0.1"
const apiPort = Number(process.env.SKYE_MOCK_PORT ?? 8080)

const now = () => new Date().toISOString()

let loggedIn = true
let projects = [
  {
    id: "inbox",
    kind: "skye",
    name: "Inbox",
    instructions: "",
    icon: "chat-bubble-left-right",
    color: "zinc",
    pinned: true,
    last_message_preview: "Ask me anything",
    last_message_at: now(),
    created_at: now(),
    updated_at: now(),
    deletable: false,
  },
  {
    id: "general",
    kind: "custom",
    name: "General questions",
    instructions: "",
    icon: "sparkles",
    color: "zinc",
    pinned: true,
    last_message_preview: "Markdown, files, and smooth motion",
    last_message_at: now(),
    created_at: now(),
    updated_at: now(),
    deletable: true,
  },
  {
    id: "frontend",
    kind: "custom",
    name: "Frontend developer",
    instructions: "Help with polished, accessible interfaces.",
    icon: "code-bracket",
    color: "violet",
    pinned: false,
    last_message_preview: "The responsive sidebar is ready",
    last_message_at: new Date(Date.now() - 86_400_000).toISOString(),
    created_at: now(),
    updated_at: now(),
    deletable: true,
  },
  {
    id: "work",
    kind: "custom",
    name: "Work expert",
    instructions: "Help organize projects and decisions.",
    icon: "briefcase",
    color: "green",
    pinned: false,
    last_message_preview: "Ready for the next project",
    last_message_at: new Date(Date.now() - 172_800_000).toISOString(),
    created_at: now(),
    updated_at: now(),
    deletable: true,
  },
]

const messages = new Map([
  [
    "inbox",
    [
      message(
        "inbox",
        "assistant",
        "Hi! This is your **Inbox** — the main Skye conversation. Reset it any time from the button up top."
      ),
    ],
  ],
  [
    "general",
    [
      message(
        "general",
        "assistant",
        "## Welcome back\n\nThis local instance renders **Markdown**, including:\n\n- Lists\n- `inline code`\n- [Links](https://docs.skye-bot.com/)"
      ),
      message("general", "user", "Keep my messages on the **right**."),
      message(
        "general",
        "assistant",
        "Done. Assistant messages stay on the left, with fully rounded bubbles."
      ),
    ],
  ],
  [
    "frontend",
    [
      message(
        "frontend",
        "assistant",
        "The sidebar can be resized with a pointer or the keyboard."
      ),
    ],
  ],
  ["work", []],
])
const uploadedFiles = new Map()

function seedFile(projectId, filename, mime, data) {
  const id = randomUUID()
  const file = {
    id,
    project_id: projectId,
    filename,
    mime,
    size: data.length,
    kind: mime.startsWith("image/") ? "image" : "upload",
    url: `/api/mock-files/${id}`,
    thumbnail_url: mime.startsWith("image/") ? `/api/mock-files/${id}/thumbnail` : null,
    created_at: now(),
    data,
  }
  uploadedFiles.set(id, file)
  return file
}

seedFile("general", "brief.md", "text/markdown", Buffer.from("# Brief\n\nProject notes."))
seedFile("general", "budget.csv", "text/csv", Buffer.from("item,amount\ncoffee,3\n"))
seedFile(
  "general",
  "sky-mark.svg",
  "image/svg+xml",
  Buffer.from(
    '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96"><rect width="96" height="96" rx="24" fill="#7c6cdc"/><circle cx="48" cy="40" r="16" fill="#fff"/></svg>'
  )
)
seedFile("general", "voice-note.ogg", "audio/ogg", Buffer.from("OggS-voice"))
seedFile("general", "script.py", "text/x-python", Buffer.from("print('hi')"))


let memories = [
  {
    id: 1,
    category: "preference",
    content: "Prefers dark mode and short, direct answers.",
    created_at: now(),
    updated_at: now(),
  },
  {
    id: 2,
    category: "personal",
    content: "Lives in Berlin and works in product design.",
    created_at: now(),
    updated_at: now(),
  },
  {
    id: 3,
    category: "instruction",
    content: "Always reply in Russian unless asked otherwise.",
    created_at: now(),
    updated_at: now(),
  },
  {
    id: 4,
    category: "project",
    content: "The Skye web chat is being rebuilt on sunkit-ui.",
    created_at: now(),
    updated_at: now(),
  },
]

const opsMediaSvg = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#d4c5f9"/><stop offset="1" stop-color="#9cc6f2"/></linearGradient></defs><rect width="320" height="240" fill="url(#g)"/><circle cx="110" cy="96" r="34" fill="#ffffff" opacity="0.85"/><path d="M0 240 L130 130 L210 200 L320 120 L320 240 Z" fill="#7c6cdc" opacity="0.75"/></svg>'
)

let opsConfigOverrides = { SKYE_MAX_TURNS: 24 }

const opsLogsSeed = [
  {
    id: 6,
    ts: new Date(Date.now() - 40_000).toISOString(),
    level: "error",
    event: "web_run_failed",
    logger: null,
    chat_id: 42,
    user_id: 7,
    thread_id: 0,
    run_key: "web:inbox",
    run_id: "run-b",
    transport: "web",
    exception:
      "Traceback (most recent call last):\n  File \"runtime.py\", line 505, in send_message\n    raise RuntimeError(\"provider refused\")\nRuntimeError: provider refused",
    context: { project_id: "inbox", error: "RateLimitError", attempt: 2 },
  },
  {
    id: 5,
    ts: new Date(Date.now() - 90_000).toISOString(),
    level: "warning",
    event: "chat_context_trimmed",
    logger: null,
    chat_id: 42,
    user_id: 7,
    thread_id: 0,
    run_key: "web:inbox",
    run_id: "run-b",
    transport: "web",
    exception: null,
    context: { original_tokens: 91240, admitted_tokens: 78010, dropped_items: 12 },
  },
  {
    id: 4,
    ts: new Date(Date.now() - 220_000).toISOString(),
    level: "info",
    event: "openai_run_started",
    logger: null,
    chat_id: 9001,
    user_id: 12,
    thread_id: 3,
    run_key: "tg:9001:3",
    run_id: "run-a",
    transport: "telegram",
    exception: null,
    context: { queued: false, queue_wait_seconds: 0.02, active_runs: 2, max_concurrent_runs: 8 },
  },
  {
    id: 3,
    ts: new Date(Date.now() - 260_000).toISOString(),
    level: "info",
    event: "image_generate_route",
    logger: null,
    chat_id: 9001,
    user_id: 12,
    thread_id: 3,
    run_key: "tg:9001:3",
    run_id: "run-a",
    transport: "telegram",
    exception: null,
    context: { model: "gpt-image-2", prompt_chars: 84 },
  },
  {
    id: 2,
    ts: new Date(Date.now() - 3_600_000).toISOString(),
    level: "debug",
    event: "sandbox_scope_touched",
    logger: null,
    chat_id: 9001,
    user_id: 12,
    thread_id: 0,
    run_key: "tg:9001:0",
    run_id: null,
    transport: "telegram",
    exception: null,
    context: { scope: "chat-9001" },
  },
  {
    id: 1,
    ts: new Date(Date.now() - 5_400_000).toISOString(),
    level: "info",
    event: "web_listen",
    logger: null,
    chat_id: null,
    user_id: null,
    thread_id: null,
    run_key: null,
    run_id: null,
    transport: "system",
    exception: null,
    context: { host: "0.0.0.0", port: 8080, origin: "https://chat.skye-bot.com" },
  },
]

const opsTracesSeed = [
  {
    id: "trace-a",
    seq: 4,
    ts: new Date(Date.now() - 30_000).toISOString(),
    run_id: "run-b",
    run_key: "web:inbox",
    transport: "web",
    label: "Web project inbox",
    chat_id: 42,
    user_id: 7,
    method: "POST",
    url: "https://api.example.com/v1/chat/completions",
    host: "api.example.com",
    status: 200,
    ok: true,
    duration_ms: 8420,
    model: "gpt-5.6-luna",
    stream: true,
    request_bytes: 48210,
    response_bytes: 9214,
    request_content_type: "application/json",
    response_content_type: "text/event-stream",
    request_headers: { authorization: "***", "content-type": "application/json" },
    response_headers: { "content-type": "text/event-stream" },
    request_body: {
      model: "gpt-5.6-luna",
      messages: [
        { role: "system", content: "You are Skye." },
        {
          role: "user",
          content: [
            { type: "input_text", text: "Edit this photo and describe it." },
            {
              type: "input_image",
              image_url: { __media__: "request-1.svg", mime: "image/svg+xml", bytes: 512 },
            },
          ],
        },
      ],
      tools: [{ type: "function", function: { name: "send_message" } }],
    },
    response_body: {
      __stream__: true,
      count: 3,
      text: "On it. Here it is.",
      events: [
        { event: "delta", data: { choices: [{ delta: { content: "On it." } }] } },
        { event: "delta", data: { choices: [{ delta: { content: " Here it is." } }] } },
        { event: "done", data: { usage: { total_tokens: 1420 } } },
      ],
    },
    request_text:
      'model: gpt-5.6-luna\nstream: true\ntools (1): send_message\n\n[system]\nYou are Skye.\n\n[user]\nEdit this photo and describe it.\n[image: request-1.svg (image/svg+xml, 512 B)]',
    response_text: "On it. Here it is.",
    error: null,
    tokens: 1420,
    media: [
      { name: "request-1.svg", mime: "image/svg+xml", kind: "image", size: 512, where: "request", detail: "input image" },
      { name: "response-1.svg", mime: "image/svg+xml", kind: "image", size: 640, where: "response", detail: "output image" },
    ],
    logs: [
      { id: 5, ts: new Date(Date.now() - 90_000).toISOString(), level: "warning", event: "chat_context_trimmed" },
      { id: 6, ts: new Date(Date.now() - 40_000).toISOString(), level: "error", event: "web_run_failed" },
    ],
  },
  {
    id: "trace-b",
    seq: 3,
    ts: new Date(Date.now() - 210_000).toISOString(),
    run_id: "run-a",
    run_key: "tg:9001:3",
    transport: "telegram",
    label: "Chat 9001",
    chat_id: 9001,
    user_id: 12,
    method: "POST",
    url: "https://api.example.com/v1/images/generations",
    host: "api.example.com",
    status: 200,
    ok: true,
    duration_ms: 15230,
    model: "gpt-image-2",
    stream: false,
    request_bytes: 320,
    response_bytes: 148230,
    request_content_type: "application/json",
    response_content_type: "application/json",
    request_headers: { authorization: "***" },
    response_headers: { "content-type": "application/json" },
    request_body: { model: "gpt-image-2", prompt: "a calm violet horizon" },
    response_body: {
      data: [{ b64_json: { __media__: "response-1.svg", mime: "image/svg+xml", bytes: 640 } }],
    },
    request_text:
      'model: gpt-image-2\n\n{\n  "prompt": "a calm violet horizon"\n}',
    response_text:
      "[generated media]\nresponse-1.svg (image/svg+xml, 640 B)",
    error: null,
    tokens: null,
    media: [
      { name: "response-1.svg", mime: "image/svg+xml", kind: "image", size: 640, where: "response", detail: "output image" },
    ],
    logs: [],
  },
  {
    id: "trace-c",
    seq: 2,
    ts: new Date(Date.now() - 600_000).toISOString(),
    run_id: "run-a",
    run_key: "tg:9001:3",
    transport: "telegram",
    label: "Chat 9001",
    chat_id: 9001,
    user_id: 12,
    method: "POST",
    url: "https://api.example.com/v1/chat/completions",
    host: "api.example.com",
    status: 429,
    ok: false,
    duration_ms: 640,
    model: "gpt-5.6-luna",
    stream: false,
    request_bytes: 9120,
    response_bytes: 210,
    request_content_type: "application/json",
    response_content_type: "application/json",
    request_headers: { authorization: "***" },
    response_headers: { "content-type": "application/json" },
    request_body: { model: "gpt-5.6-luna", messages: [{ role: "user", content: "hello" }] },
    response_body: { error: { message: "Rate limit reached. Try again in 12s.", code: "rate_limit" } },
    request_text: "model: gpt-5.6-luna\nstream: false\n\n[user]\nhello",
    response_text: "Rate limit reached. Try again in 12s.",
    error: "Rate limit reached. Try again in 12s.",
    tokens: null,
    media: [],
    logs: [],
  },
]

const opsConfigSeed = [
  ["skye_provider_api_key", "secret", "Provider key", "Model provider", false],
  ["skye_provider_base_url", "url", "Provider base URL", "Model provider", false],
  ["skye_default_model", "text", "Default model", "Model provider", false],
  ["skye_default_reasoning", "select", "Reasoning effort", "Model provider", false, ["none", "low", "medium", "high"]],
  ["skye_image_model", "text", "Image model", "Model provider", false],
  ["skye_exa_api_key", "secret", "Exa key", "Model provider", true],
  ["telegram_bot_token", "secret", "Bot token", "Telegram", false],
  ["skye_owner_ids", "list", "Owner user ids", "Telegram", false],
  ["skye_max_turns", "int", "Max turns per run", "Runtime", false, null, 2, 100],
  ["skye_run_timeout_seconds", "int", "Run timeout", "Runtime", false, null, 10, 1800],
  ["skye_max_concurrent_runs", "int", "Max concurrent runs", "Runtime", false, null, 1, 64],
  ["skye_ops_enabled", "bool", "Panel enabled", "Observability", false],
  ["skye_ops_capture_payloads", "bool", "Capture model payloads", "Observability", false],
  ["skye_ops_capture_media", "bool", "Capture images and files", "Observability", false],
  ["skye_ops_log_retention_days", "int", "Log retention (days)", "Observability", false, null, 1, 365],
  ["skye_ops_max_body_bytes", "int", "Stored body cap", "Observability", true, null, 10000, null],
  ["skye_database_path", "path", "Database path", "Advanced", false, null, null, null, true],
  ["skye_proxy_url", "url", "HTTP proxy URL", "Advanced", true],
]

function opsField([key, kind, label, group, advanced, choices, minimum, maximum, readOnly]) {
  const overridden = key in opsConfigOverrides
  const secret = kind === "secret"
  const values = {
    skye_provider_api_key: "sk-live-abcdef",
    skye_provider_base_url: "https://api.openai.com/v1",
    skye_default_model: "gpt-5.6-luna",
    skye_default_reasoning: "medium",
    skye_image_model: "gpt-image-2",
    skye_exa_api_key: "exa-key",
    telegram_bot_token: "123:token",
    skye_owner_ids: [1, 42],
    skye_max_turns: 24,
    skye_run_timeout_seconds: 300,
    skye_max_concurrent_runs: 8,
    skye_ops_enabled: true,
    skye_ops_capture_payloads: true,
    skye_ops_capture_media: true,
    skye_ops_log_retention_days: 14,
    skye_ops_max_body_bytes: 2000000,
    skye_database_path: "/data/skye.db",
    skye_proxy_url: null,
  }
  const raw = overridden ? opsConfigOverrides[key] : values[key]
  const displayed =
    raw === null || raw === undefined
      ? ""
      : Array.isArray(raw)
        ? raw.join(", ")
        : String(raw)
  return {
    key,
    env: key.toUpperCase(),
    label,
    description: "Environment setting. A restart applies the change.",
    group,
    kind,
    secret,
    read_only: Boolean(readOnly),
    advanced: Boolean(advanced),
    choices: choices ?? [],
    minimum: minimum ?? null,
    maximum: maximum ?? null,
    default: null,
    value: secret ? null : displayed,
    is_set: raw !== null && raw !== undefined && raw !== "",
    override: overridden,
    source: overridden ? "override" : "environment",
  }
}

function traceSummary(trace) {
  return {
    seq: trace.seq,
    id: trace.id,
    ts: trace.ts,
    run_id: trace.run_id,
    run_key: trace.run_key,
    transport: trace.transport,
    label: trace.label,
    chat_id: trace.chat_id,
    user_id: trace.user_id,
    method: trace.method,
    url: trace.url,
    host: trace.host,
    status: trace.status,
    ok: trace.ok,
    duration_ms: trace.duration_ms,
    model: trace.model,
    stream: trace.stream,
    request_bytes: trace.request_bytes,
    response_bytes: trace.response_bytes,
    response_content_type: trace.response_content_type,
    has_error: Boolean(trace.error),
    media_count: trace.media.length,
    media_preview: trace.media.filter((item) => item.mime.startsWith("image/")).slice(0, 3),
    tokens: trace.tokens,
  }
}


function message(projectId, role, text, extra = {}) {
  return {
    id: randomUUID(),
    project_id: projectId,
    role,
    text,
    tool_name: null,
    tool_status: null,
    tool_args: null,
    tool_output: null,
    file_ids: [],
    created_at: now(),
    ...extra,
  }
}

function json(response, status, payload) {
  const body = JSON.stringify(payload)
  response.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(body),
  })
  response.end(body)
}

function empty(response, status = 204) {
  response.writeHead(status)
  response.end()
}

function text(response, status, body) {
  response.writeHead(status, {
    "Content-Type": "text/plain; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
  })
  response.end(body)
}

async function bodyBuffer(request) {
  const chunks = []
  for await (const chunk of request) {
    chunks.push(chunk)
  }
  return Buffer.concat(chunks)
}

async function jsonBody(request) {
  const body = await bodyBuffer(request)
  return body.length ? JSON.parse(body.toString("utf8")) : {}
}

async function formBody(request) {
  const body = await bodyBuffer(request)
  const response = new Response(body, {
    headers: { "Content-Type": request.headers["content-type"] ?? "" },
  })
  return response.formData()
}

function projectById(id) {
  return projects.find((project) => project.id === id)
}

function touchProject(project, preview) {
  const timestamp = now()
  project.updated_at = timestamp
  if (preview !== undefined) {
    project.last_message_preview = preview
    project.last_message_at = timestamp
  }
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", `http://${host}:${apiPort}`)
  const method = request.method ?? "GET"

  try {
    if (method === "GET" && url.pathname === "/api/me") {
      json(response, loggedIn ? 200 : 401, {
        user: loggedIn
          ? { id: 1, name: "Evelyn Raine", username: "evelyn" }
          : null,
        allowed: loggedIn,
      })
      return
    }

    if (method === "POST" && url.pathname === "/auth/logout") {
      loggedIn = false
      empty(response)
      return
    }

    if (method === "GET" && url.pathname === "/auth/telegram") {
      loggedIn = true
      response.writeHead(302, { Location: "/" })
      response.end()
      return
    }

    if (method === "GET" && url.pathname === "/api/projects") {
      json(response, 200, {
        projects: [...projects].sort(
          (a, b) =>
            Number(b.kind === "skye") - Number(a.kind === "skye") ||
            Number(b.pinned) - Number(a.pinned) ||
            (b.last_message_at ?? "").localeCompare(a.last_message_at ?? "")
        ),
      })
      return
    }

    if (method === "GET" && url.pathname === "/api/memories") {
      json(response, 200, { memories })
      return
    }

    if (method === "DELETE" && url.pathname === "/api/memories") {
      json(response, 200, { deleted: memories.length })
      memories = []
      return
    }

    const memoryRoute = url.pathname.match(/^\/api\/memories\/(\d+)$/)
    if (method === "DELETE" && memoryRoute) {
      const id = Number(memoryRoute[1])
      const before = memories.length
      memories = memories.filter((memory) => memory.id !== id)
      if (memories.length === before) {
        text(response, 404, "Memory not found.")
        return
      }
      empty(response)
      return
    }

    if (method === "POST" && url.pathname === "/api/projects") {
      const body = await jsonBody(request)
      const project = {
        id: randomUUID(),
        kind: "custom",
        name: String(body.name || "New project").slice(0, 64),
        instructions: String(body.instructions || ""),
        icon: String(body.icon || "sparkles"),
        color: String(body.color || "zinc"),
        pinned: false,
        last_message_preview: "",
        last_message_at: null,
        created_at: now(),
        updated_at: now(),
        deletable: true,
      }
      projects = [project, ...projects]
      messages.set(project.id, [])
      json(response, 201, { project })
      return
    }

    if (method === "GET" && url.pathname === "/api/search") {
      const query = (url.searchParams.get("q") ?? "").trim().toLowerCase()
      const matchingProjects = projects.filter((project) =>
        `${project.name} ${project.last_message_preview}`
          .toLowerCase()
          .includes(query)
      )
      const matchingMessages = []
      for (const [projectId, items] of messages) {
        const project = projectById(projectId)
        if (!project) continue
        for (const item of items) {
          if (item.text.toLowerCase().includes(query)) {
            matchingMessages.push({ project, message: item })
          }
        }
      }
      json(response, 200, {
        projects: matchingProjects,
        messages: matchingMessages,
      })
      return
    }

    if (method === "POST" && url.pathname === "/api/transcribe") {
      await bodyBuffer(request)
      json(response, 200, { text: "This is mocked dictation." })
      return
    }

    const fileRoute = url.pathname.match(
      /^\/api\/mock-files\/([^/]+)(?:\/thumbnail)?$/
    )
    if (method === "GET" && fileRoute) {
      const file = uploadedFiles.get(fileRoute[1])
      if (!file) {
        text(response, 404, "File not found.")
        return
      }
      response.writeHead(200, {
        "Content-Type": file.mime,
        "Cache-Control": "private, max-age=3600",
      })
      response.end(file.data)
      return
    }

    const projectRoute = url.pathname.match(
      /^\/api\/projects\/([^/]+)(?:\/([^/]+))?$/
    )
    if (projectRoute) {
      const [, projectId, action] = projectRoute
      const project = projectById(projectId)
      if (!project) {
        text(response, 404, "Project not found.")
        return
      }

      if (method === "PATCH" && !action) {
        const patch = await jsonBody(request)
        for (const key of ["name", "instructions", "icon", "color", "pinned"]) {
          if (key in patch) project[key] = patch[key]
        }
        touchProject(project)
        json(response, 200, { project })
        return
      }

      if (method === "DELETE" && !action) {
        projects = projects.filter((item) => item.id !== projectId)
        messages.delete(projectId)
        empty(response)
        return
      }

      if (method === "POST" && action === "pin") {
        project.pinned = !project.pinned
        touchProject(project)
        json(response, 200, { project })
        return
      }

      if (method === "POST" && action === "reset") {
        messages.set(projectId, [])
        project.last_message_preview = ""
        project.last_message_at = null
        touchProject(project)
        json(response, 200, { project })
        return
      }

      if (method === "POST" && action === "stop") {
        empty(response)
        return
      }

      if (method === "GET" && action === "messages") {
        json(response, 200, {
          messages: messages.get(projectId) ?? [],
          files: [...uploadedFiles.values()]
            .filter((file) => file.project_id === projectId)
            .map(({ data: _data, ...file }) => file),
        })
        return
      }

      if (method === "POST" && action === "messages") {
        const form = await formBody(request)
        const prompt =
          String(form.get("text") ?? "").trim() || "Shared an attachment."
        const userMessage = message(projectId, "user", prompt)
        const savedFiles = []
        for (const entry of form.getAll("files")) {
          if (typeof entry === "string") continue
          const id = randomUUID()
          const mime = entry.type || "application/octet-stream"
          const file = {
            id,
            project_id: projectId,
            filename: entry.name || "file",
            mime,
            size: entry.size,
            kind: mime.startsWith("image/") ? "image" : "upload",
            url: `/api/mock-files/${id}`,
            thumbnail_url: mime.startsWith("image/")
              ? `/api/mock-files/${id}/thumbnail`
              : null,
            created_at: now(),
            data: Buffer.from(await entry.arrayBuffer()),
          }
          uploadedFiles.set(id, file)
          savedFiles.push(file)
          userMessage.file_ids.push(id)
        }
        const assistantMessage = message(
          projectId,
          "assistant",
          `This is a **mocked response** to:\n\n> ${prompt}`
        )
        const toolCalls = [
          {
            id: randomUUID(),
            name: "web_search",
            label: "Searched the web",
            args: JSON.stringify({ query: prompt.slice(0, 60) }),
            output:
              "1. Example result\n   https://example.com — a short description\n2. Another result\n   https://example.org — more detail",
          },
          {
            id: randomUUID(),
            name: "shell_exec",
            label: "Ran a command",
            args: JSON.stringify({ command: "ls -la /work" }),
            output:
              "total 8\ndrwxr-xr-x  2 skye skye 4096 Jan  1 00:00 .\n-rw-r--r--  1 skye skye   12 Jan  1 00:00 notes.txt",
          },
        ]
        const toolMessages = toolCalls.map((tool) =>
          message(projectId, "tool", tool.label, {
            tool_name: tool.name,
            tool_status: "done",
            tool_args: tool.args,
            tool_output: tool.output,
          })
        )
        const items = messages.get(projectId) ?? []
        items.push(userMessage, ...toolMessages, assistantMessage)
        messages.set(projectId, items)
        touchProject(project, prompt)

        response.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
        })
        for (const { data: _data, ...file } of savedFiles) {
          response.write(`event: file\ndata: ${JSON.stringify(file)}\n\n`)
        }
        response.write(`event: user\ndata: ${JSON.stringify(userMessage)}\n\n`)
        for (const tool of toolCalls) {
          response.write(
            `event: tool\ndata: ${JSON.stringify({ id: tool.id, name: tool.name, label: tool.label, status: "running", args: tool.args, output: "" })}\n\n`
          )
          await new Promise((resolve) => setTimeout(resolve, 250))
          response.write(
            `event: tool\ndata: ${JSON.stringify({ id: tool.id, name: tool.name, label: tool.label, status: "done", args: tool.args, output: tool.output })}\n\n`
          )
        }
        response.write(
          `event: delta\ndata: ${JSON.stringify({ text: assistantMessage.text })}\n\n`
        )
        response.write(
          `event: done\ndata: ${JSON.stringify(assistantMessage)}\n\n`
        )
        response.end()
        return
      }
    }

    if (url.pathname.startsWith("/api/admin/")) {
      if (!loggedIn) {
        text(response, 401, "Sign in with Telegram.")
        return
      }
      if (method === "GET" && url.pathname === "/api/admin/overview") {
        json(response, 200, {
          logs: opsLogsSeed.length,
          errors: opsLogsSeed.filter((item) => ["error", "critical"].includes(item.level)).length,
          traces: opsTracesSeed.length,
          failed_traces: opsTracesSeed.filter((item) => !item.ok).length,
          traces_24h: opsTracesSeed.filter((item) => Date.now() - new Date(item.ts).getTime() < 86_400_000).length,
          overrides: Object.keys(opsConfigOverrides).length,
          media_bytes: 115_238,
          dropped_logs: 0,
          dropped_traces: 0,
          last_error: {
            ts: opsLogsSeed[0].ts,
            event: opsLogsSeed[0].event,
            exception: opsLogsSeed[0].exception,
          },
          capture: { enabled: true, media: true },
          me: 1,
        })
        return
      }
      if (method === "GET" && url.pathname === "/api/admin/logs/events") {
        const counts = new Map()
        for (const entry of opsLogsSeed) {
          counts.set(entry.event, (counts.get(entry.event) ?? 0) + 1)
        }
        json(response, 200, {
          events: [...counts.entries()]
            .map(([event, count]) => ({ event, count }))
            .sort((a, b) => b.count - a.count),
        })
        return
      }
      if (method === "GET" && url.pathname === "/api/admin/logs") {
        const level = url.searchParams.get("level")
        const event = url.searchParams.get("event")
        const q = (url.searchParams.get("q") ?? "").toLowerCase()
        const chatId = url.searchParams.get("chat_id")
        const userId = url.searchParams.get("user_id")
        const runId = url.searchParams.get("run_id")
        let rows = opsLogsSeed
        if (level) {
          rows = rows.filter((item) =>
            level === "warning"
              ? ["warning", "warn"].includes(item.level)
              : item.level === level
          )
        }
        if (event) rows = rows.filter((item) => item.event === event)
        if (chatId) rows = rows.filter((item) => String(item.chat_id) === chatId)
        if (userId) rows = rows.filter((item) => String(item.user_id) === userId)
        if (runId) rows = rows.filter((item) => item.run_id === runId)
        if (q) {
          rows = rows.filter((item) =>
            `${item.event} ${JSON.stringify(item.context)} ${item.exception ?? ""}`
              .toLowerCase()
              .includes(q)
          )
        }
        json(response, 200, {
          logs: rows.map(({ context: _context, exception, ...item }) => ({
            ...item,
            preview: JSON.stringify(_context).slice(0, 200),
            has_exception: exception ? 1 : 0,
          })),
        })
        return
      }
      const logRoute = url.pathname.match(/^\/api\/admin\/logs\/(\d+)$/)
      if (method === "GET" && logRoute) {
        const entry = opsLogsSeed.find((item) => item.id === Number(logRoute[1]))
        if (!entry) {
          text(response, 404, "Log entry not found.")
          return
        }
        json(response, 200, { log: entry })
        return
      }
      if (method === "GET" && url.pathname === "/api/admin/traces") {
        const status = url.searchParams.get("status")
        const runId = url.searchParams.get("run_id")
        const q = (url.searchParams.get("q") ?? "").toLowerCase()
        let rows = opsTracesSeed
        if (status === "ok") rows = rows.filter((item) => item.ok)
        if (status === "error") rows = rows.filter((item) => !item.ok)
        if (runId) rows = rows.filter((item) => item.run_id === runId)
        if (q) {
          rows = rows.filter((item) =>
            `${item.url} ${item.model} ${item.label} ${item.error ?? ""}`.toLowerCase().includes(q)
          )
        }
        json(response, 200, {
          traces: rows.map(traceSummary),
          models: [...new Set(opsTracesSeed.map((item) => item.model).filter(Boolean))],
        })
        return
      }
      const traceMedia = url.pathname.match(
        /^\/api\/admin\/traces\/([^/]+)\/media\/([^/]+)$/
      )
      if (method === "GET" && traceMedia) {
        response.writeHead(200, {
          "Content-Type": "image/svg+xml",
          "Cache-Control": "private, max-age=600",
          "Content-Disposition": `inline; filename="${traceMedia[2]}"`,
        })
        response.end(opsMediaSvg)
        return
      }
      const traceRoute = url.pathname.match(/^\/api\/admin\/traces\/([^/]+)$/)
      if (method === "GET" && traceRoute) {
        const trace = opsTracesSeed.find((item) => item.id === traceRoute[1])
        if (!trace) {
          text(response, 404, "Request not found.")
          return
        }
        json(response, 200, { trace })
        return
      }
      if (method === "GET" && url.pathname === "/api/admin/config") {
        json(response, 200, {
          fields: opsConfigSeed.map(opsField),
          groups: [
            "Model provider",
            "Telegram",
            "Runtime",
            "Observability",
            "Advanced",
          ],
          overrides: Object.keys(opsConfigOverrides),
          env_file: ".env",
          env_file_exists: true,
          capture: { enabled: true, media: true },
        })
        return
      }
      if (method === "POST" && url.pathname === "/api/admin/config/validate") {
        const body = await jsonBody(request)
        const errors = {}
        for (const [key, value] of Object.entries(body.values ?? {})) {
          const definition = opsConfigSeed.find((item) => item[0] === key)
          if (!definition) continue
          const minimum = definition[6]
          const maximum = definition[7]
          const numeric = Number(value)
          if (
            (minimum !== null && minimum !== undefined && numeric < minimum) ||
            (maximum !== null && maximum !== undefined && numeric > maximum)
          ) {
            errors[key] = `Range ${minimum} to ${maximum}.`
          }
        }
        json(response, Object.keys(errors).length ? 400 : 200, {
          ok: Object.keys(errors).length === 0,
          errors,
          normalized: body.values ?? {},
        })
        return
      }
      if (method === "POST" && url.pathname === "/api/admin/config/apply") {
        const body = await jsonBody(request)
        const changed = []
        for (const [key, value] of Object.entries(body.values ?? {})) {
          const definition = opsConfigSeed.find((item) => item[0] === key)
          if (!definition) continue
          if (value === "" || value === null) {
            delete opsConfigOverrides[key]
          } else {
            opsConfigOverrides[key] = definition[1] === "int" ? Number(value) : value
          }
          changed.push(key.toUpperCase())
        }
        json(response, 200, {
          ok: true,
          changed,
          reverted: [],
          restart_required: changed.some(
            (key) => !key.startsWith("SKYE_OPS_")
          ),
        })
        return
      }
      if (method === "POST" && url.pathname === "/api/admin/config/revert") {
        const body = await jsonBody(request)
        if (body.env) delete opsConfigOverrides[body.env]
        else opsConfigOverrides = {}
        json(response, 200, { ok: true })
        return
      }
      if (method === "POST" && url.pathname === "/api/admin/maintenance") {
        json(response, 200, { ok: true, logs: 0, traces: 0 })
        return
      }
      if (method === "POST" && url.pathname === "/api/admin/restart") {
        json(response, 200, { ok: true })
        return
      }
    }

    text(response, 404, "Mock route not found.")
  } catch (error) {
    console.error(error)
    text(response, 500, "Mock API failed.")
  }
})

server.on("error", (error) => {
  if (error.code === "EADDRINUSE") {
    console.error(`Mock API port ${apiPort} is already in use.`)
  } else {
    console.error(error)
  }
  process.exit(1)
})

server.listen(apiPort, host, () => {
  console.log(`Mock API ready at http://${host}:${apiPort}`)
})

const vite = spawn(
  process.platform === "win32"
    ? "node_modules\\.bin\\vite.cmd"
    : "node_modules/.bin/vite",
  ["--host", host],
  { stdio: "inherit", env: { ...process.env, SKYE_API_PORT: String(apiPort) } }
)

function shutdown(signal) {
  vite.kill(signal)
  server.close(() => process.exit(0))
}

process.on("SIGINT", () => shutdown("SIGINT"))
process.on("SIGTERM", () => shutdown("SIGTERM"))

vite.on("exit", (code) => {
  server.close(() => process.exit(code ?? 0))
})
