export type Project = {
  id: string
  kind: "skye" | "custom"
  name: string
  instructions: string
  icon: string
  color: string
  pinned: boolean
  last_message_preview: string
  last_message_at: string | null
  created_at: string
  updated_at: string
  deletable: boolean
}

export type ChatMessage = {
  id: string
  project_id: string
  role: "user" | "assistant" | "tool" | "system"
  text: string
  tool_name: string | null
  tool_status: "running" | "done" | null
  file_ids: string[]
  created_at: string
}

export type ChatFile = {
  id: string
  project_id: string
  filename: string
  mime: string
  size: number
  kind: "upload" | "image" | "document"
  url: string
  created_at: string
}

export type User = {
  id: number
  name: string
  username: string | null
}

export type Me = {
  user: User | null
  allowed: boolean
}
