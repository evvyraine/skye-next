import { useState, type CSSProperties } from "react"
import {
  CheckIcon,
  ChevronRightIcon,
  ClipboardIcon,
} from "@heroicons/react/24/outline"
import { Button, COLOR_MAP, Spinner, toast } from "sunkit-ui"
import { useTheme } from "@/components/theme-provider"
import {
  formatToolDetail,
  isToolError,
  toolPresentation,
  toolSummary,
} from "@/lib/tools"
import { cn } from "@/lib/utils"

export type ToolCallData = {
  id: string
  name?: string | null
  label?: string | null
  status?: string | null
  args?: string | null
  output?: string | null
}

export function ToolCall({ data }: { data: ToolCallData }) {
  const { resolvedTheme } = useTheme()
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  const { label, icon: Icon, tone } = toolPresentation(data.name, data.label)
  const running = data.status === "running"
  const failed = isToolError(data.output)
  const args = (data.args ?? "").trim()
  const output = (data.output ?? "").trim()
  const summary = toolSummary(args)
  const expandable = args.length > 0 || output.length > 0
  const panelId = `tool-panel-${data.id}`
  const swatch = COLOR_MAP[tone] ?? COLOR_MAP.neutral
  const chipColor = resolvedTheme === "dark" ? swatch.hex : swatch.darkHex

  async function copy() {
    const body = [formatToolDetail(args), output].filter(Boolean).join("\n\n")
    try {
      await navigator.clipboard.writeText(body)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error({
        title: "Couldn't copy",
        description: "Select the text and copy it manually.",
      })
    }
  }

  return (
    <div className="w-full overflow-hidden rounded-2xl border border-[var(--sk-border-subtle)] bg-[var(--sk-surface)]">
      <button
        type="button"
        aria-expanded={expandable ? open : undefined}
        aria-controls={expandable ? panelId : undefined}
        disabled={!expandable}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "flex w-full items-center gap-2.5 px-3 py-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/50",
          expandable ? "cursor-pointer" : "cursor-default"
        )}
      >
        <span
          aria-hidden="true"
          className="flex size-7 shrink-0 items-center justify-center rounded-lg"
          style={{ background: `${swatch.hex}33`, color: chipColor } as CSSProperties}
        >
          <Icon className="size-3.5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate text-[13px] font-medium text-[var(--sk-text)]">
              {label}
            </span>
            {running ? <Spinner size="xs" /> : null}
            {failed ? (
              <span className="shrink-0 text-[11px] font-medium text-[var(--sk-text-error)]">
                Failed
              </span>
            ) : null}
          </span>
          {summary ? (
            <span className="mt-0.5 block truncate text-[12px] text-[var(--sk-text-desc)]">
              {summary}
            </span>
          ) : null}
        </span>
        {expandable ? (
          <ChevronRightIcon
            aria-hidden="true"
            className={cn(
              "size-4 shrink-0 text-[var(--sk-text-muted)] transition-transform duration-150",
              open && "rotate-90"
            )}
          />
        ) : null}
      </button>

      {expandable && open ? (
        <div
          id={panelId}
          className="border-t border-[var(--sk-border-subtle)] px-3 py-2.5"
        >
          {args ? <Detail title="Arguments" body={formatToolDetail(args)} /> : null}
          {output ? (
            <Detail title="Output" body={output} tone={failed ? "error" : "default"} />
          ) : null}
          <div className="mt-2 flex justify-end">
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
      ) : null}
    </div>
  )
}

function Detail({
  title,
  body,
  tone = "default",
}: {
  title: string
  body: string
  tone?: "default" | "error"
}) {
  return (
    <div className="mt-2 first:mt-0">
      <p className="mb-1 text-[10px] font-semibold tracking-wide text-[var(--sk-text-muted)] uppercase">
        {title}
      </p>
      <pre
        className={cn(
          "sk-scrollbar max-h-64 overflow-auto rounded-xl border p-2.5 font-mono text-[12px] leading-relaxed break-words whitespace-pre-wrap",
          tone === "error"
            ? "border-[var(--sk-text-error)]/30 bg-[var(--sk-text-error)]/5 text-[var(--sk-text-error)]"
            : "border-[var(--sk-border-subtle)] bg-[var(--sk-surface-filled)] text-[var(--sk-text)]"
        )}
      >
        {body}
      </pre>
    </div>
  )
}
