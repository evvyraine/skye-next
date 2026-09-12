import { useMemo, useState, type ReactNode } from "react"
import {
  ArrowsPointingInIcon,
  ArrowsPointingOutIcon,
  CheckIcon,
  ClipboardIcon,
  ChevronRightIcon,
  PhotoIcon,
} from "@heroicons/react/24/outline"
import { Button, COLOR_MAP, type Tone } from "sunkit-ui"
import { useTheme } from "@/components/theme-provider"
import { cn } from "@/lib/utils"

const MAX_ENTRIES = 300
const STRING_CLAMP = 600

type JsonViewProps = {
  data: unknown
  onMedia?: (name: string) => void
  className?: string
}

export function JsonView({ data, onMedia, className }: JsonViewProps) {
  const text = useMemo(() => safeStringify(data), [data])
  const [defaultDepth, setDefaultDepth] = useState(2)
  const [version, setVersion] = useState(0)
  const [copied, setCopied] = useState(false)

  function setDepth(depth: number) {
    setDefaultDepth(depth)
    setVersion((value) => value + 1)
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className={cn("flex min-h-0 flex-col", className)}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="font-mono text-[11px] text-[var(--sk-text-muted)]">
          {text.length.toLocaleString()} chars
        </span>
        <div className="flex items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            color="neutral"
            size="sm"
            icon="left"
            iconLeft={<ArrowsPointingOutIcon />}
            radius={999}
            onClick={() => setDepth(64)}
          >
            Expand all
          </Button>
          <Button
            type="button"
            variant="ghost"
            color="neutral"
            size="sm"
            icon="left"
            iconLeft={<ArrowsPointingInIcon />}
            radius={999}
            onClick={() => setDepth(0)}
          >
            Collapse
          </Button>
          <Button
            type="button"
            variant="ghost"
            color="neutral"
            size="sm"
            icon="left"
            iconLeft={copied ? <CheckIcon /> : <ClipboardIcon />}
            radius={999}
            onClick={() => void copy()}
          >
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      </div>
      <div className="sk-scrollbar min-h-0 overflow-auto rounded-2xl border border-[var(--sk-border-subtle)] bg-[var(--sk-surface-filled)] p-3 font-mono text-[12px] leading-[1.7]">
        <JsonNode
          key={version}
          value={data}
          name={null}
          depth={0}
          defaultDepth={defaultDepth}
          onMedia={onMedia}
        />
      </div>
    </div>
  )
}

type JsonNodeProps = {
  value: unknown
  name: string | null
  depth: number
  defaultDepth: number
  onMedia?: (name: string) => void
}

function JsonNode({
  value,
  name,
  depth,
  defaultDepth,
  onMedia,
}: JsonNodeProps) {
  const [open, setOpen] = useState(depth < defaultDepth)
  const [showAll, setShowAll] = useState(false)

  if (isMediaMarker(value)) {
    return (
      <MediaRow
        name={name}
        marker={value as Record<string, unknown>}
        onMedia={onMedia}
      />
    )
  }
  if (value !== null && typeof value === "object") {
    const isArray = Array.isArray(value)
    const entries: [string, unknown][] = isArray
      ? (value as unknown[]).map((item, index) => [String(index), item])
      : Object.entries(value as Record<string, unknown>)
    const visible = showAll ? entries : entries.slice(0, MAX_ENTRIES)
    const summary = isArray ? `[${entries.length}]` : `{${entries.length}}`
    return (
      <div className="relative">
        <button
          type="button"
          onClick={() => setOpen((current) => !current)}
          className="group flex w-full cursor-pointer items-start gap-1 rounded-md px-0.5 text-left outline-none hover:bg-[var(--sk-surface-hover)] focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/40"
          aria-expanded={open}
        >
          <ChevronRightIcon
            className={cn(
              "mt-[3px] size-3 shrink-0 text-[var(--sk-text-muted)] transition-transform duration-150",
              open && "rotate-90"
            )}
            aria-hidden="true"
          />
          {name !== null ? <Token kind="key">{name}</Token> : null}
          <span className="text-[var(--sk-text-muted)] select-none">
            {summary}
          </span>
        </button>
        {open ? (
          <div className="ml-[7px] border-l border-[var(--sk-border-subtle)] pl-3">
            {visible.map(([key, item]) => (
              <JsonNode
                key={key}
                value={item}
                name={key}
                depth={depth + 1}
                defaultDepth={defaultDepth}
                onMedia={onMedia}
              />
            ))}
            {entries.length > MAX_ENTRIES && !showAll ? (
              <button
                type="button"
                onClick={() => setShowAll(true)}
                className="cursor-pointer px-1 py-0.5 text-[11px] text-[var(--sk-accent)] hover:underline"
              >
                Show {entries.length - MAX_ENTRIES} more…
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    )
  }
  return <PrimitiveRow name={name} value={value} />
}

function PrimitiveRow({
  name,
  value,
}: {
  name: string | null
  value: unknown
}) {
  const [showAll, setShowAll] = useState(false)
  const kind = value === null ? "null" : typeof value
  let display: ReactNode
  if (typeof value === "string") {
    const long = value.length > STRING_CLAMP
    const shown = long && !showAll ? `${value.slice(0, STRING_CLAMP)}…` : value
    display = (
      <>
        <Token kind="string">{JSON.stringify(shown)}</Token>
        {long && !showAll ? (
          <button
            type="button"
            onClick={() => setShowAll(true)}
            className="ml-1 cursor-pointer text-[11px] text-[var(--sk-accent)] hover:underline"
          >
            {value.length.toLocaleString()} chars
          </button>
        ) : null}
      </>
    )
  } else if (typeof value === "number") {
    display = <Token kind="number">{String(value)}</Token>
  } else if (typeof value === "boolean") {
    display = <Token kind="boolean">{value ? "true" : "false"}</Token>
  } else {
    display = <span className="text-[var(--sk-text-muted)]">null</span>
  }
  return (
    <div className="flex items-start gap-1 px-0.5">
      {name !== null ? <Token kind="key">{name}</Token> : null}
      <span className="min-w-0 break-words whitespace-pre-wrap">{display}</span>
      <span className="sr-only">{kind}</span>
    </div>
  )
}

function MediaRow({
  name,
  marker,
  onMedia,
}: {
  name: string | null
  marker: Record<string, unknown>
  onMedia?: (name: string) => void
}) {
  const mediaName = String(marker.__media__)
  const mime = typeof marker.mime === "string" ? marker.mime : "file"
  const bytes = typeof marker.bytes === "number" ? marker.bytes : null
  return (
    <div className="flex items-start gap-1 px-0.5">
      {name !== null ? <Token kind="key">{name}</Token> : null}
      <button
        type="button"
        onClick={() => onMedia?.(mediaName)}
        disabled={!onMedia}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-lg border border-[var(--sk-border-subtle)] bg-[var(--sk-surface)] px-2 py-0.5 text-[11px] text-[var(--sk-text)]",
          onMedia && "cursor-pointer hover:border-[var(--sk-accent)]/50"
        )}
      >
        <PhotoIcon
          className="size-3.5 text-[var(--sk-accent)]"
          aria-hidden="true"
        />
        {mediaName}
        <span className="text-[var(--sk-text-muted)]">{mime}</span>
        {bytes !== null ? (
          <span className="text-[var(--sk-text-muted)]">
            {formatSize(bytes)}
          </span>
        ) : null}
      </button>
    </div>
  )
}

function Token({
  kind,
  children,
}: {
  kind: "key" | "string" | "number" | "boolean"
  children: ReactNode
}) {
  const { resolvedTheme } = useTheme()
  const tone: Tone =
    kind === "string"
      ? "mint"
      : kind === "number"
        ? "sky"
        : kind === "boolean"
          ? "lemon"
          : "neutral"
  const swatch = COLOR_MAP[tone] ?? COLOR_MAP.neutral
  const color = resolvedTheme === "dark" ? swatch.hex : swatch.darkHex
  return (
    <span className="shrink-0" style={kind === "key" ? undefined : { color }}>
      {kind === "key" ? (
        <span className="text-[var(--sk-text-muted)]">{children}:</span>
      ) : (
        children
      )}
    </span>
  )
}

function isMediaMarker(value: unknown): boolean {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    typeof (value as Record<string, unknown>).__media__ === "string"
  )
}

function safeStringify(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value)
  } catch {
    return String(value)
  }
}

function formatSize(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}
