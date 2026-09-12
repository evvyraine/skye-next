export type LogRow = {
  id: number
  ts: string
  level: string
  event: string
  logger: string | null
  chat_id: number | null
  user_id: number | null
  thread_id: number | null
  run_key: string | null
  run_id: string | null
  transport: string | null
  preview: string
  has_exception: number
}

export type LogEntry = Omit<LogRow, "preview" | "has_exception"> & {
  exception: string | null
  context: unknown
}

export type MediaItem = {
  name: string
  mime: string
  kind: string
  size: number
  where: string
  detail: string
}

export type TraceSummary = {
  seq: number
  id: string
  ts: string
  run_id: string | null
  run_key: string | null
  transport: string | null
  label: string | null
  chat_id: number | null
  user_id: number | null
  method: string
  url: string
  host: string
  status: number
  ok: boolean
  duration_ms: number
  model: string | null
  stream: boolean
  request_bytes: number
  response_bytes: number
  response_content_type: string | null
  has_error: boolean
  media_count: number
  media_preview: { name: string; mime: string }[]
  tokens: number | null
}

export type TraceDetail = TraceSummary & {
  request_content_type: string | null
  request_headers: Record<string, string>
  response_headers: Record<string, string>
  request_body: unknown
  response_body: unknown
  error: string | null
  media: MediaItem[]
  logs: { id: number; ts: string; level: string; event: string }[]
}

export type FieldKind =
  | "text"
  | "secret"
  | "int"
  | "float"
  | "bool"
  | "select"
  | "list"
  | "path"
  | "url"

export type FieldSource = "override" | "environment" | "default"

export type ConfigField = {
  key: string
  env: string
  label: string
  description: string
  group: string
  kind: FieldKind
  secret: boolean
  read_only: boolean
  advanced: boolean
  choices: string[]
  minimum: number | null
  maximum: number | null
  default: unknown
  value: unknown
  is_set: boolean
  override: boolean
  source: FieldSource
}

export type ConfigPayload = {
  fields: ConfigField[]
  groups: string[]
  overrides: string[]
  env_file: string
  env_file_exists: boolean
  capture: { enabled: boolean; media: boolean }
}

export type Overview = {
  logs: number
  errors: number
  traces: number
  failed_traces: number
  traces_24h: number
  overrides: number
  media_bytes: number
  dropped_logs: number
  dropped_traces: number
  last_error: { ts: string; event: string; exception: string | null } | null
  capture: { enabled: boolean; media: boolean }
  me: number
}

export type LogFilters = {
  level?: string
  event?: string
  chat_id?: string
  user_id?: string
  transport?: string
  q?: string
  run_id?: string
  since?: string
  before?: number
}

export type TraceFilters = {
  status?: string
  transport?: string
  chat_id?: string
  user_id?: string
  model?: string
  q?: string
  run_id?: string
  since?: string
  before?: number
}
