import { randomUUID } from "node:crypto"
import { spawn } from "node:child_process"
import { createServer } from "node:http"

const host = "127.0.0.1"
const apiPort = 8080

const now = () => new Date().toISOString()

let loggedIn = true
let projects = [
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

function message(projectId, role, text) {
  return {
    id: randomUUID(),
    project_id: projectId,
    role,
    text,
    tool_name: null,
    tool_status: null,
    file_ids: [],
    created_at: now(),
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
            Number(b.pinned) - Number(a.pinned) ||
            (b.last_message_at ?? "").localeCompare(a.last_message_at ?? "")
        ),
      })
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
        const items = messages.get(projectId) ?? []
        items.push(userMessage, assistantMessage)
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
  { stdio: "inherit" }
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
