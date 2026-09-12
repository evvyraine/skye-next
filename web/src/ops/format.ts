import type { Tone } from "sunkit-ui"

export function parseDate(value: string | null | undefined): Date | null {
  if (!value) {
    return null
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatTime(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) {
    return "-"
  }
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

export function formatDateTime(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) {
    return "-"
  }
  return date.toLocaleString([], {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

export function formatDay(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) {
    return "Unknown"
  }
  const today = new Date()
  if (date.toDateString() === today.toDateString()) {
    return "Today"
  }
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  if (date.toDateString() === yesterday.toDateString()) {
    return "Yesterday"
  }
  return date.toLocaleDateString([], { day: "numeric", month: "long" })
}

export function relativeTime(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) {
    return ""
  }
  const seconds = Math.round((Date.now() - date.getTime()) / 1000)
  if (seconds < 5) return "just now"
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days}d ago`
  return date.toLocaleDateString([], { day: "numeric", month: "short" })
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "-"
  }
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024)
    return `${(value / 1024).toFixed(value < 10 * 1024 ? 1 : 0)} KB`
  if (value < 1024 * 1024 * 1024)
    return `${(value / 1024 / 1024).toFixed(1)} MB`
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) {
    return "-"
  }
  if (ms < 1) return "<1 ms"
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`
}

export function formatCount(value: number): string {
  if (value < 1000) return String(value)
  if (value < 1_000_000)
    return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}k`
  return `${(value / 1_000_000).toFixed(1)}M`
}

export function levelTone(level: string): Tone {
  switch (level.toLowerCase()) {
    case "critical":
    case "error":
      return "rose"
    case "warning":
    case "warn":
      return "lemon"
    case "info":
      return "sky"
    case "debug":
      return "neutral"
    default:
      return "neutral"
  }
}

export function levelLabel(level: string): string {
  const clean = level.toLowerCase()
  if (clean === "warn" || clean === "warning") return "warning"
  return clean
}

export function transportLabel(transport: string | null): string {
  if (transport === "web") return "Web"
  if (transport === "telegram") return "Telegram"
  if (transport === "system") return "System"
  return transport || "Unknown"
}

export function statusTone(status: number): Tone {
  if (status >= 200 && status < 300) return "mint"
  if (status >= 400) return "rose"
  if (status >= 300) return "lemon"
  return "neutral"
}

export function shortPath(url: string): string {
  try {
    const parsed = new URL(url)
    return `${parsed.pathname}`
  } catch {
    return url
  }
}

export function sinceFromPreset(value: string): string | undefined {
  const minutes: Record<string, number> = {
    "15m": 15,
    "1h": 60,
    "24h": 60 * 24,
    "7d": 60 * 24 * 7,
  }
  const offset = minutes[value]
  if (!offset) {
    return undefined
  }
  return new Date(Date.now() - offset * 60_000).toISOString()
}

export function initialsFor(id: number | null): string {
  return id === null ? "-" : String(id)
}
