import type {
  ConfigPayload,
  LogEntry,
  LogFilters,
  LogRow,
  Overview,
  TraceDetail,
  TraceFilters,
  TraceSummary,
} from "@/ops/types"

export class OpsError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = "OpsError"
    this.status = status
  }
}

type Raw = { status: number; data: unknown }

async function raw(path: string, init?: RequestInit): Promise<Raw> {
  const response = await fetch(path, { credentials: "include", ...init })
  const text = await response.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }
  return { status: response.status, data }
}

function messageOf(data: unknown, fallback: string): string {
  if (typeof data === "string" && data.trim()) {
    return data.trim()
  }
  if (data && typeof data === "object") {
    const errors = (data as { errors?: unknown }).errors
    if (errors && typeof errors === "object") {
      const first = Object.values(errors as Record<string, unknown>)[0]
      if (typeof first === "string") {
        return first
      }
    }
    const error = (data as { error?: unknown }).error
    if (typeof error === "string") {
      return error
    }
  }
  return fallback
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const { status, data } = await raw(path, init)
  if (status >= 400) {
    throw new OpsError(status, messageOf(data, `Request failed (${status}).`))
  }
  return data as T
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value))
    }
  }
  const text = search.toString()
  return text ? `?${text}` : ""
}

export function getOverview(): Promise<Overview> {
  return json<Overview>("/api/admin/overview")
}

export function listLogs(
  filters: LogFilters,
  limit = 80
): Promise<{ logs: LogRow[] }> {
  return json(`/api/admin/logs${query({ ...filters, limit })}`)
}

export function listLogEvents(): Promise<{
  events: { event: string; count: number }[]
}> {
  return json("/api/admin/logs/events")
}

export function getLog(id: number): Promise<{ log: LogEntry }> {
  return json(`/api/admin/logs/${id}`)
}

export function listTraces(
  filters: TraceFilters,
  limit = 60
): Promise<{ traces: TraceSummary[]; models: string[] }> {
  return json(`/api/admin/traces${query({ ...filters, limit })}`)
}

export function getTrace(id: string): Promise<{ trace: TraceDetail }> {
  return json(`/api/admin/traces/${id}`)
}

export function getConfig(): Promise<ConfigPayload> {
  return json("/api/admin/config")
}

export type ConfigResult = {
  ok: boolean
  errors: Record<string, string>
  normalized?: Record<string, unknown>
  changed?: string[]
  reverted?: string[]
  restart_required?: boolean
}

export async function validateConfig(
  values: Record<string, unknown>
): Promise<ConfigResult> {
  const { data } = await raw("/api/admin/config/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values }),
  })
  return normalizeConfigResult(data)
}

export async function applyConfig(
  values: Record<string, unknown>
): Promise<ConfigResult> {
  const { status, data } = await raw("/api/admin/config/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values }),
  })
  if (status >= 500) {
    throw new OpsError(status, messageOf(data, "Unable to save changes."))
  }
  return normalizeConfigResult(data)
}

function normalizeConfigResult(data: unknown): ConfigResult {
  if (data && typeof data === "object") {
    const value = data as ConfigResult
    return {
      ok: Boolean(value.ok),
      errors:
        value.errors && typeof value.errors === "object" ? value.errors : {},
      normalized: value.normalized,
      changed: value.changed ?? [],
      reverted: value.reverted ?? [],
      restart_required: value.restart_required,
    }
  }
  return { ok: false, errors: { __root__: String(data) } }
}

export function revertOverride(env?: string): Promise<{ ok: boolean }> {
  return json("/api/admin/config/revert", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(env ? { env } : {}),
  })
}

export function restartSkye(): Promise<{ ok: boolean }> {
  return json("/api/admin/restart", { method: "POST" })
}

export function runMaintenance(): Promise<{
  ok: boolean
  logs: number
  traces: number
}> {
  return json("/api/admin/maintenance", { method: "POST" })
}

export function mediaUrl(traceId: string, name: string): string {
  return `/api/admin/traces/${traceId}/media/${encodeURIComponent(name)}`
}
