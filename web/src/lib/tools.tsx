import type { ComponentType, SVGProps } from "react"
import {
  CircleStackIcon,
  ClockIcon,
  CodeBracketIcon,
  CommandLineIcon,
  CpuChipIcon,
  DocumentTextIcon,
  GlobeAltIcon,
  LinkIcon,
  MagnifyingGlassIcon,
  PhotoIcon,
  PlayCircleIcon,
  WrenchIcon,
} from "@heroicons/react/24/outline"
import type { Tone } from "sunkit-ui"

export type IconComponent = ComponentType<SVGProps<SVGSVGElement>>

export type ToolPresentation = {
  label: string
  icon: IconComponent
  tone: Tone
}

const TOOLS: Record<string, ToolPresentation> = {
  shell: { label: "Ran a command", icon: CommandLineIcon, tone: "sky" },
  shell_call: { label: "Ran a command", icon: CommandLineIcon, tone: "sky" },
  local_shell_call: { label: "Ran a command", icon: CommandLineIcon, tone: "sky" },
  shell_exec: { label: "Ran a command", icon: CommandLineIcon, tone: "sky" },
  python: { label: "Ran Python", icon: CodeBracketIcon, tone: "sky" },
  read_file: { label: "Read a file", icon: DocumentTextIcon, tone: "lemon" },
  write_file: { label: "Wrote a file", icon: CodeBracketIcon, tone: "peach" },
  web_search: { label: "Searched the web", icon: MagnifyingGlassIcon, tone: "mint" },
  web_search_call: { label: "Searched the web", icon: MagnifyingGlassIcon, tone: "mint" },
  web_fetch: { label: "Fetched a page", icon: GlobeAltIcon, tone: "mint" },
  generate_image: { label: "Generated an image", icon: PhotoIcon, tone: "lilac" },
  image_generation: { label: "Generated an image", icon: PhotoIcon, tone: "lilac" },
  image_generation_call: { label: "Generated an image", icon: PhotoIcon, tone: "lilac" },
  edit_image: { label: "Edited an image", icon: PhotoIcon, tone: "lilac" },
  remember: { label: "Saved a memory", icon: CircleStackIcon, tone: "lavender" },
  recall: { label: "Recalled a memory", icon: CircleStackIcon, tone: "lavender" },
  forget: { label: "Forgot a memory", icon: CircleStackIcon, tone: "lavender" },
  mcp_call: { label: "Used a connected app", icon: LinkIcon, tone: "peach" },
  list_automations: { label: "Listed automations", icon: ClockIcon, tone: "peach" },
  create_scheduled_automation: {
    label: "Scheduled an automation",
    icon: ClockIcon,
    tone: "peach",
  },
  create_webhook_automation: { label: "Created a webhook", icon: LinkIcon, tone: "peach" },
  update_automation: { label: "Updated an automation", icon: ClockIcon, tone: "peach" },
  show_webhook_automation: { label: "Showed webhook details", icon: LinkIcon, tone: "peach" },
  delete_automation: { label: "Deleted an automation", icon: ClockIcon, tone: "peach" },
  youtube_get_transcript: { label: "Read a transcript", icon: PlayCircleIcon, tone: "rose" },
}

const KEY_HINTS = [
  "command",
  "query",
  "path",
  "url",
  "filename",
  "text",
  "prompt",
  "content",
]

function humanize(name: string): string {
  const cleaned = name.replace(/[_-]+/g, " ").trim()
  if (!cleaned) return "Used a tool"
  return cleaned[0].toUpperCase() + cleaned.slice(1)
}

export function toolPresentation(
  name: string | null | undefined,
  fallbackLabel?: string | null
): ToolPresentation {
  const key = (name ?? "").trim().toLowerCase()
  const known = TOOLS[key]
  if (known) {
    return known
  }
  if (key.startsWith("agent_")) {
    return { label: "Asked a specialist", icon: CpuChipIcon, tone: "lilac" }
  }
  if (key.includes("search")) {
    return { label: fallbackLabel || "Searched", icon: MagnifyingGlassIcon, tone: "mint" }
  }
  if (key.includes("image")) {
    return { label: fallbackLabel || "Worked on an image", icon: PhotoIcon, tone: "lilac" }
  }
  return {
    label: fallbackLabel || humanize(key),
    icon: WrenchIcon,
    tone: "neutral",
  }
}

/** A short, human-readable hint pulled from the tool's JSON arguments. */
export function toolSummary(args?: string | null): string {
  const raw = (args ?? "").trim()
  if (!raw) return ""
  try {
    const parsed: unknown = JSON.parse(raw)
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const record = parsed as Record<string, unknown>
      for (const key of KEY_HINTS) {
        const value = record[key]
        if (typeof value === "string" && value.trim()) {
          return value.trim().replace(/\s+/g, " ")
        }
      }
      const first = Object.values(record).find(
        (value) => typeof value === "string" && value.trim()
      )
      if (typeof first === "string") {
        return first.trim().replace(/\s+/g, " ")
      }
    }
  } catch {
    // Not JSON — fall back to the raw text.
  }
  return raw.replace(/\s+/g, " ")
}

/** Pretty-print JSON arguments, leaving non-JSON text untouched. */
export function formatToolDetail(value: string): string {
  const text = value.trim()
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
}

export function isToolError(output?: string | null): boolean {
  if (!output) return false
  const head = output.trim().slice(0, 200).toLowerCase()
  return (
    head.startsWith("error") ||
    head.startsWith("traceback") ||
    head.includes("exception:") ||
    head.startsWith('{"error"')
  )
}
