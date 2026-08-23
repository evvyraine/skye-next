import type { ChatFile, ChatMessage, Me, Project, User } from "@/lib/types"

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "include", ...init })
  if (!response.ok) {
    const text = await response.text()
    throw new Error(text || response.statusText)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export async function getMe(): Promise<Me> {
  const response = await fetch("/api/me", { credentials: "include" })
  if (response.status === 401) {
    return { user: null, allowed: false }
  }
  if (!response.ok) {
    throw new Error("Could not load your session.")
  }
  return (await response.json()) as Me
}

export function login() {
  window.location.assign("/auth/telegram")
}

export async function logout() {
  await request("/auth/logout", { method: "POST" })
}

export async function listProjects(): Promise<Project[]> {
  const payload = await request<{ projects: Project[] }>("/api/projects")
  return payload.projects
}

export async function createProject(body: {
  name: string
  icon: string
  color: string
  instructions?: string
}): Promise<Project> {
  const payload = await request<{ project: Project }>("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  return payload.project
}

export async function updateProject(
  id: string,
  body: Partial<Pick<Project, "name" | "instructions" | "icon" | "color" | "pinned">>,
): Promise<Project> {
  const payload = await request<{ project: Project }>(`/api/projects/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  return payload.project
}

export async function deleteProject(id: string): Promise<void> {
  await request(`/api/projects/${id}`, { method: "DELETE" })
}

export async function pinProject(id: string): Promise<Project> {
  const payload = await request<{ project: Project }>(`/api/projects/${id}/pin`, {
    method: "POST",
  })
  return payload.project
}

export async function resetProject(id: string): Promise<Project> {
  const payload = await request<{ project: Project }>(`/api/projects/${id}/reset`, {
    method: "POST",
  })
  return payload.project
}

export async function stopProject(id: string): Promise<void> {
  await request(`/api/projects/${id}/stop`, { method: "POST" })
}

export async function listMessages(
  id: string,
): Promise<{ messages: ChatMessage[]; files: ChatFile[] }> {
  return request(`/api/projects/${id}/messages`)
}

export async function getMeta(): Promise<{ icons: string[]; colors: string[] }> {
  return request("/api/meta")
}

export async function search(query: string): Promise<{
  projects: Project[]
  messages: { project: Project; message: ChatMessage }[]
}> {
  return request(`/api/search?q=${encodeURIComponent(query)}`)
}

export async function transcribe(file: Blob): Promise<string> {
  const body = new FormData()
  body.append("file", file, "dictation.webm")
  const payload = await request<{ text: string }>("/api/transcribe", {
    method: "POST",
    body,
  })
  return payload.text
}

export type StreamHandlers = {
  onUser?: (message: ChatMessage) => void
  onAssistant?: (message: ChatMessage) => void
  onDelta?: (text: string) => void
  onTool?: (tool: { id: string; name: string; label: string; status: string }) => void
  onImage?: (file: ChatFile) => void
  onFile?: (file: ChatFile) => void
  onDone?: (message: ChatMessage) => void
  onError?: (message: string) => void
}

export async function sendMessage(
  projectId: string,
  text: string,
  files: File[],
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const body = new FormData()
  body.set("text", text)
  for (const file of files) {
    body.append("files", file)
  }
  const response = await fetch(`/api/projects/${projectId}/messages`, {
    method: "POST",
    body,
    credentials: "include",
    signal,
  })
  if (!response.ok || !response.body) {
    const message = await response.text()
    throw new Error(message || "Could not send that message.")
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  while (true) {
    const { value, done } = await reader.read()
    if (done) {
      break
    }
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split("\n\n")
    buffer = chunks.pop() ?? ""
    for (const chunk of chunks) {
      const event = parseSse(chunk)
      if (!event) {
        continue
      }
      dispatch(event, handlers)
    }
  }
}

function parseSse(chunk: string): { event: string; data: unknown } | null {
  let event = "message"
  let data = ""
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim()
    } else if (line.startsWith("data:")) {
      data += line.slice(5).trim()
    }
  }
  if (!data) {
    return null
  }
  return { event, data: JSON.parse(data) as unknown }
}

function dispatch(event: { event: string; data: unknown }, handlers: StreamHandlers) {
  const data = event.data as never
  if (event.event === "user") {
    handlers.onUser?.(data)
  } else if (event.event === "assistant") {
    handlers.onAssistant?.(data)
  } else if (event.event === "delta") {
    handlers.onDelta?.((data as { text: string }).text)
  } else if (event.event === "tool") {
    handlers.onTool?.(data)
  } else if (event.event === "image") {
    handlers.onImage?.(data)
  } else if (event.event === "file") {
    handlers.onFile?.(data)
  } else if (event.event === "done") {
    handlers.onDone?.(data)
  } else if (event.event === "error") {
    handlers.onError?.((data as { message: string }).message)
  }
}

export function initials(user: User | null): string {
  if (!user?.name) {
    return "SK"
  }
  const parts = user.name.split(" ").filter(Boolean)
  return parts
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("")
}

export function formatWhen(value: string | null): string {
  if (!value) {
    return ""
  }
  const date = new Date(value.includes("T") ? value : `${value.replace(" ", "T")}Z`)
  if (Number.isNaN(date.getTime())) {
    return ""
  }
  const now = new Date()
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
  }
  return date.toLocaleDateString([], { weekday: "long" })
}
