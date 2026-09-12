import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"
import {
  ArrowPathIcon,
  CheckIcon,
  ChevronDownIcon,
  ClipboardIcon,
  MagnifyingGlassIcon,
  XMarkIcon,
} from "@heroicons/react/24/outline"
import {
  Badge,
  Button,
  EmptyState,
  Select,
  Input,
  Sheet,
  Skeleton,
  Tabs,
  toast,
} from "sunkit-ui"
import { getTrace, listTraces, mediaUrl } from "@/ops/api"
import {
  formatBytes,
  formatDateTime,
  formatDuration,
  levelTone,
  relativeTime,
  shortPath,
  sinceFromPreset,
  statusTone,
  transportLabel,
} from "@/ops/format"
import { JsonView } from "@/ops/json-view"
import { MediaGallery, MediaLightbox } from "@/ops/media-gallery"
import type { MediaItem, TraceDetail, TraceSummary } from "@/ops/types"
import { useIsMobile } from "@/hooks/use-mobile"
import { cn } from "@/lib/utils"

const PAGE = 60
const RANGES = ["15m", "1h", "24h", "7d"]

type Filters = {
  q: string
  status: string
  transport: string
  model: string
  chatId: string
  userId: string
  range: string
}

const EMPTY: Filters = {
  q: "",
  status: "",
  transport: "",
  model: "",
  chatId: "",
  userId: "",
  range: "",
}

export function TracesView({
  runId,
  onRunIdChange,
  onInspectLogs,
}: {
  runId: string
  onRunIdChange: (runId: string) => void
  onInspectLogs: (runId: string) => void
}) {
  const [filters, setFilters] = useState<Filters>(EMPTY)
  const [debouncedQuery, setDebouncedQuery] = useState("")
  const [rows, setRows] = useState<TraceSummary[]>([])
  const [models, setModels] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [detail, setDetail] = useState<TraceDetail | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [activeMedia, setActiveMedia] = useState<MediaItem | null>(null)
  const requestId = useRef(0)

  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedQuery(filters.q), 300)
    return () => window.clearTimeout(handle)
  }, [filters.q])

  const baseFilters = useMemo(
    () => ({
      status: filters.status || undefined,
      transport: filters.transport || undefined,
      model: filters.model || undefined,
      chat_id: filters.chatId.trim() || undefined,
      user_id: filters.userId.trim() || undefined,
      q: debouncedQuery.trim() || undefined,
      run_id: runId || undefined,
      since: sinceFromPreset(filters.range),
    }),
    [filters, debouncedQuery, runId]
  )

  const load = useCallback(
    async (mode: "reset" | "more") => {
      const id = ++requestId.current
      if (mode === "reset") setLoading(true)
      else setLoadingMore(true)
      try {
        const before =
          mode === "more" && rows.length > 0
            ? rows[rows.length - 1]?.seq
            : undefined
        const payload = await listTraces({ ...baseFilters, before }, PAGE)
        if (id !== requestId.current) return
        setRows((current) =>
          mode === "more" ? [...current, ...payload.traces] : payload.traces
        )
        setModels(payload.models)
        setHasMore(payload.traces.length === PAGE)
      } catch (error) {
        if (id === requestId.current) {
          toast.error({
            title: "Couldn't load requests",
            description: error instanceof Error ? error.message : "Try again.",
          })
        }
      } finally {
        if (id === requestId.current) {
          setLoading(false)
          setLoadingMore(false)
        }
      }
    },
    [baseFilters, rows]
  )

  useEffect(() => {
    const handle = window.setTimeout(() => void load("reset"), 0)
    return () => window.clearTimeout(handle)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseFilters])

  async function openDetail(row: TraceSummary) {
    setDetailOpen(true)
    setDetail(null)
    try {
      const payload = await getTrace(row.id)
      setDetail(payload.trace)
    } catch (error) {
      setDetailOpen(false)
      toast.error({
        title: "Couldn't load the request",
        description: error instanceof Error ? error.message : "Try again.",
      })
    }
  }

  function clear() {
    setFilters(EMPTY)
    onRunIdChange("")
  }

  const filtered =
    Object.values(filters).some((value) => value !== "") || runId !== ""
  const modelOptions = useMemo(
    () => [
      { value: "", label: "Any model" },
      ...models.map((model) => ({ value: model, label: model })),
    ],
    [models]
  )

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="sticky top-[105px] z-10 -mx-4 bg-[var(--app-bg)] px-4 pt-2 pb-3 md:top-[61px] md:-mx-6 md:px-6">
        <div className="flex flex-col gap-2.5 rounded-3xl border border-[var(--sk-border-subtle)] bg-[var(--sk-surface)] p-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="min-w-[180px] flex-1">
              <Input
                value={filters.q}
                onChange={(event) =>
                  setFilters((f) => ({ ...f, q: event.target.value }))
                }
                placeholder="Search URL, model, error"
                aria-label="Search requests"
                variant="filled"
                radius={14}
                leftAdornment={
                  <MagnifyingGlassIcon
                    className="size-4 text-[var(--sk-text-muted)]"
                    aria-hidden="true"
                  />
                }
              />
            </div>
            <Select
              options={[
                { value: "", label: "Any status" },
                { value: "ok", label: "Succeeded" },
                { value: "error", label: "Failed" },
              ]}
              value={filters.status}
              onChange={(value) => setFilters((f) => ({ ...f, status: value }))}
              variant="filled"
              radius={14}
              aria-label="Filter by status"
              containerClassName="w-[135px]"
            />
            <Select
              options={modelOptions}
              value={filters.model}
              onChange={(value) => setFilters((f) => ({ ...f, model: value }))}
              variant="filled"
              radius={14}
              searchable
              aria-label="Filter by model"
              containerClassName="w-[180px]"
            />
            <Select
              options={[
                { value: "", label: "All sources" },
                { value: "telegram", label: "Telegram" },
                { value: "web", label: "Web" },
              ]}
              value={filters.transport}
              onChange={(value) =>
                setFilters((f) => ({ ...f, transport: value }))
              }
              variant="filled"
              radius={14}
              aria-label="Filter by source"
              containerClassName="w-[140px]"
            />
            <Input
              value={filters.chatId}
              onChange={(event) =>
                setFilters((f) => ({ ...f, chatId: event.target.value }))
              }
              placeholder="Chat id"
              aria-label="Filter by chat id"
              inputMode="numeric"
              variant="filled"
              radius={14}
              containerClassName="w-[100px]"
            />
            <Input
              value={filters.userId}
              onChange={(event) =>
                setFilters((f) => ({ ...f, userId: event.target.value }))
              }
              placeholder="User id"
              aria-label="Filter by user id"
              inputMode="numeric"
              variant="filled"
              radius={14}
              containerClassName="w-[100px]"
            />
            <Select
              options={[
                { value: "", label: "Any time" },
                ...RANGES.map((value) => ({ value, label: `Last ${value}` })),
              ]}
              value={filters.range}
              onChange={(value) => setFilters((f) => ({ ...f, range: value }))}
              variant="filled"
              radius={14}
              aria-label="Filter by time"
              containerClassName="w-[130px]"
            />
          </div>
          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <p
                className="text-[12px] text-[var(--sk-text-desc)]"
                role="status"
              >
                {loading
                  ? "Loading…"
                  : `${rows.length} ${rows.length === 1 ? "request" : "requests"}`}
                {filtered ? " · filtered" : ""}
              </p>
              {runId ? (
                <Badge tone="lavender" variant="soft" size="sm">
                  run {runId.slice(0, 8)}
                  <button
                    type="button"
                    aria-label="Clear run filter"
                    onClick={() => onRunIdChange("")}
                    className="ml-1 cursor-pointer"
                  >
                    <XMarkIcon className="size-3" />
                  </button>
                </Badge>
              ) : null}
            </div>
            <div className="flex items-center gap-1">
              {filtered ? (
                <Button
                  variant="ghost"
                  color="neutral"
                  size="sm"
                  radius={999}
                  icon="left"
                  iconLeft={<XMarkIcon />}
                  onClick={clear}
                >
                  Clear
                </Button>
              ) : null}
              <Button
                variant="ghost"
                color="neutral"
                size="icon-only"
                icon="only"
                iconOnly={<ArrowPathIcon />}
                radius={999}
                aria-label="Reload requests"
                onClick={() => void load("reset")}
              />
            </div>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="flex flex-col gap-2 py-1">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} variant="rounded" height={72} />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          tone="lavender"
          icon={<MagnifyingGlassIcon className="size-6" aria-hidden="true" />}
          title="No captured requests"
          description={
            filtered
              ? "Adjust the filters or widen the time range."
              : "Enable payload capture and send Skye a message."
          }
          action={
            filtered ? (
              <Button
                variant="outline"
                color="lavender"
                radius={999}
                onClick={clear}
              >
                Clear filters
              </Button>
            ) : undefined
          }
        />
      ) : (
        <ul className="flex flex-col gap-1.5">
          {rows.map((row) => (
            <li key={row.id}>
              <button
                type="button"
                onClick={() => void openDetail(row)}
                className={cn(
                  "w-full cursor-pointer rounded-2xl border border-[var(--sk-border-subtle)] bg-[var(--sk-surface)] px-3 py-2.5 text-start transition-colors outline-none hover:bg-[var(--sk-surface-hover)] focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/50",
                  !row.ok && "border-rose/25"
                )}
              >
                <div className="flex items-center gap-2">
                  <Badge tone="neutral" variant="outline" size="sm">
                    {row.method}
                  </Badge>
                  <Badge tone={statusTone(row.status)} variant="soft" size="sm">
                    {row.status || "-"}
                  </Badge>
                  <span className="min-w-0 truncate font-mono text-[12.5px] font-medium">
                    {row.model ?? row.host}
                  </span>
                  <span className="ml-auto shrink-0 font-mono text-[11px] text-[var(--sk-text-muted)] tabular-nums">
                    {formatDuration(row.duration_ms)}
                    {row.tokens !== null ? ` · ${row.tokens} tok` : ""}
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-1.5 text-[11.5px] text-[var(--sk-text-desc)]">
                  <span className="min-w-0 truncate">{shortPath(row.url)}</span>
                  <span className="text-[var(--sk-text-muted)]">·</span>
                  <span className="shrink-0">
                    {transportLabel(row.transport)}
                  </span>
                  {row.label ? (
                    <>
                      <span className="text-[var(--sk-text-muted)]">·</span>
                      <span className="min-w-0 truncate">{row.label}</span>
                    </>
                  ) : null}
                  <span className="ml-auto shrink-0">
                    {relativeTime(row.ts)}
                  </span>
                </div>
                {row.media_preview.length > 0 ? (
                  <div className="mt-2 flex items-center gap-1.5">
                    {row.media_preview.map((item) => (
                      <span
                        key={item.name}
                        className="block size-10 overflow-hidden rounded-lg border border-[var(--sk-border-subtle)] bg-[var(--sk-surface-filled)]"
                      >
                        <img
                          src={mediaUrl(row.id, item.name)}
                          alt=""
                          loading="lazy"
                          decoding="async"
                          className="size-full object-cover"
                        />
                      </span>
                    ))}
                    {row.media_count > row.media_preview.length ? (
                      <span className="text-[11px] text-[var(--sk-text-muted)]">
                        +{row.media_count - row.media_preview.length}
                      </span>
                    ) : null}
                  </div>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      )}

      {hasMore && !loading ? (
        <div className="flex justify-center py-2">
          <Button
            variant="outline"
            color="neutral"
            radius={999}
            icon="left"
            iconLeft={<ChevronDownIcon />}
            disabled={loadingMore}
            onClick={() => void load("more")}
          >
            {loadingMore ? "Loading…" : "Load older"}
          </Button>
        </div>
      ) : null}

      <TraceDetail
        trace={detail}
        open={detailOpen}
        onOpenChange={(open) => {
          setDetailOpen(open)
          if (!open) setDetail(null)
        }}
        onMedia={setActiveMedia}
        onInspectLogs={onInspectLogs}
      />
      <MediaLightbox
        traceId={detail?.id ?? ""}
        item={activeMedia}
        onOpenChange={(open) => !open && setActiveMedia(null)}
      />
    </div>
  )
}

function TraceDetail({
  trace,
  open,
  onOpenChange,
  onMedia,
  onInspectLogs,
}: {
  trace: TraceDetail | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onMedia: (item: MediaItem) => void
  onInspectLogs: (runId: string) => void
}) {
  const isMobile = useIsMobile()

  function openNamed(name: string) {
    const item = trace?.media.find((entry) => entry.name === name)
    if (item) onMedia(item)
    else
      toast.error({
        title: "Media not stored",
        description: "Capture may have been off.",
      })
  }

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      side={isMobile ? "bottom" : "right"}
      size="full"
      title={trace ? `${trace.method} ${shortPath(trace.url)}` : "Request"}
      description={trace ? formatDateTime(trace.ts) : undefined}
      tone="lavender"
    >
      {trace ? (
        <Tabs
          key={trace.id}
          defaultValue="overview"
          tone="lavender"
          className="flex h-full min-h-0 flex-col"
        >
          <Tabs.List className="mb-3 overflow-x-auto">
            <Tabs.Trigger value="overview">Overview</Tabs.Trigger>
            <Tabs.Trigger value="request">Request</Tabs.Trigger>
            <Tabs.Trigger value="response">Response</Tabs.Trigger>
            <Tabs.Trigger value="media">
              Media {trace.media.length ? `(${trace.media.length})` : ""}
            </Tabs.Trigger>
            <Tabs.Trigger value="logs">
              Logs {trace.logs.length ? `(${trace.logs.length})` : ""}
            </Tabs.Trigger>
          </Tabs.List>

          <div className="sk-scrollbar min-h-0 flex-1 overflow-y-auto pb-4">
            <Tabs.Content value="overview" className="flex flex-col gap-4">
              {trace.error ? (
                <div className="border-rose/30 bg-rose/[0.06] rounded-2xl border p-3 text-[12.5px] text-[var(--sk-text-error)]">
                  {trace.error}
                </div>
              ) : null}
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-2xl border border-[var(--sk-border-subtle)] bg-[var(--sk-surface-filled)] p-4 text-[12.5px]">
                <Meta
                  label="Status"
                  value={`${trace.status} ${trace.ok ? "ok" : "failed"}`}
                />
                <Meta
                  label="Duration"
                  value={formatDuration(trace.duration_ms)}
                />
                <Meta label="Model" value={trace.model ?? "-"} />
                <Meta
                  label="Tokens"
                  value={trace.tokens !== null ? String(trace.tokens) : "-"}
                />
                <Meta label="Host" value={trace.host} />
                <Meta
                  label="Transport"
                  value={transportLabel(trace.transport)}
                />
                <Meta
                  label="Chat"
                  value={trace.chat_id !== null ? String(trace.chat_id) : "-"}
                />
                <Meta
                  label="User"
                  value={trace.user_id !== null ? String(trace.user_id) : "-"}
                />
                <Meta
                  label="Request size"
                  value={formatBytes(trace.request_bytes)}
                />
                <Meta
                  label="Response size"
                  value={formatBytes(trace.response_bytes)}
                />
                <Meta label="Streamed" value={trace.stream ? "yes" : "no"} />
                <Meta
                  label="Run"
                  value={trace.run_id ? trace.run_id.slice(0, 12) : "-"}
                />
              </dl>
              <div className="rounded-2xl border border-[var(--sk-border-subtle)] p-3">
                <p className="mb-1 text-[11px] font-semibold tracking-wide text-[var(--sk-text-muted)] uppercase">
                  URL
                </p>
                <p className="font-mono text-[12px] break-all">{trace.url}</p>
              </div>
              {trace.run_id ? (
                <Button
                  variant="outline"
                  color="lavender"
                  radius={999}
                  onClick={() => onInspectLogs(trace.run_id as string)}
                >
                  View logs for this run
                </Button>
              ) : null}
            </Tabs.Content>

            <Tabs.Content value="request" className="flex flex-col gap-3">
              <BodyView
                label="Request"
                contentType={trace.request_content_type}
                bytes={trace.request_bytes}
                text={trace.request_text}
                data={trace.request_body}
                onMedia={openNamed}
              />
              <Headers
                headers={trace.request_headers}
                label="Request headers"
              />
            </Tabs.Content>

            <Tabs.Content value="response" className="flex flex-col gap-3">
              <BodyView
                label="Response"
                contentType={trace.response_content_type}
                bytes={trace.response_bytes}
                text={trace.response_text}
                hint={
                  trace.stream && trace.response_text
                    ? "Assembled from streamed deltas. Raw events stay under JSON."
                    : undefined
                }
                data={trace.response_body}
                onMedia={openNamed}
              />
              <Headers
                headers={trace.response_headers}
                label="Response headers"
              />
            </Tabs.Content>

            <Tabs.Content value="media">
              <MediaGallery
                traceId={trace.id}
                media={trace.media}
                onOpen={onMedia}
              />
            </Tabs.Content>

            <Tabs.Content value="logs">
              {trace.logs.length === 0 ? (
                <p className="text-[13px] text-[var(--sk-text-desc)]">
                  No log lines share this run.
                </p>
              ) : (
                <ul className="flex flex-col">
                  {trace.logs.map((line) => (
                    <li
                      key={line.id}
                      className="flex items-center gap-2 border-b border-[var(--sk-border-subtle)] py-2 last:border-0"
                    >
                      <Badge
                        tone={levelTone(line.level)}
                        variant="soft"
                        size="sm"
                      >
                        {line.level}
                      </Badge>
                      <span className="min-w-0 flex-1 truncate font-mono text-[12px]">
                        {line.event}
                      </span>
                      <span className="shrink-0 text-[11px] text-[var(--sk-text-muted)]">
                        {relativeTime(line.ts)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Tabs.Content>
          </div>
        </Tabs>
      ) : (
        <div className="flex flex-col gap-3">
          <Skeleton variant="text" lines={8} />
        </div>
      )}
    </Sheet>
  )
}

function BodyView({
  label,
  contentType,
  bytes,
  text,
  hint,
  data,
  onMedia,
}: {
  label: string
  contentType: string | null
  bytes: number
  text: string | null
  hint?: string
  data: unknown
  onMedia: (name: string) => void
}) {
  const [mode, setMode] = useState<"text" | "json">(text ? "text" : "json")
  const [copied, setCopied] = useState(false)

  async function copy() {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <BodyHeader label={label} contentType={contentType} bytes={bytes} />
        <div
          className="flex items-center gap-0.5 rounded-full border border-[var(--sk-border-subtle)] bg-[var(--sk-surface-filled)] p-0.5"
          role="group"
          aria-label={`${label} format`}
        >
          <SegmentedButton
            active={mode === "text"}
            onClick={() => setMode("text")}
          >
            Text
          </SegmentedButton>
          <SegmentedButton
            active={mode === "json"}
            onClick={() => setMode("json")}
          >
            JSON
          </SegmentedButton>
        </div>
      </div>

      {mode === "text" ? (
        text ? (
          <>
            <div className="flex items-center justify-between gap-2">
              {hint ? (
                <p className="text-[11.5px] text-[var(--sk-text-desc)]">
                  {hint}
                </p>
              ) : (
                <span />
              )}
              <Button
                variant="ghost"
                color="neutral"
                size="sm"
                radius={999}
                icon="left"
                iconLeft={copied ? <CheckIcon /> : <ClipboardIcon />}
                onClick={() => void copy()}
              >
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
            <TextBlock text={text} />
          </>
        ) : (
          <p className="rounded-2xl border border-dashed border-[var(--sk-border-subtle)] px-4 py-6 text-center text-[13px] text-[var(--sk-text-desc)]">
            No text representation for this body.{" "}
            <button
              type="button"
              onClick={() => setMode("json")}
              className="cursor-pointer text-[var(--sk-accent)] hover:underline"
            >
              Open the JSON view
            </button>
            .
          </p>
        )
      ) : (
        <JsonView data={data} onMedia={onMedia} />
      )}
    </div>
  )
}

function TextBlock({ text }: { text: string }) {
  return (
    <pre className="sk-scrollbar max-h-[55vh] overflow-auto rounded-2xl border border-[var(--sk-border-subtle)] bg-[var(--sk-surface-filled)] p-3 font-mono text-[12px] leading-[1.7] break-words whitespace-pre-wrap">
      {text}
    </pre>
  )
}

function SegmentedButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "h-7 cursor-pointer rounded-full px-3 text-[12px] font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/50",
        active
          ? "bg-[var(--sk-surface)] text-[var(--sk-text)]"
          : "text-[var(--sk-text-desc)] hover:text-[var(--sk-text)]"
      )}
    >
      {children}
    </button>
  )
}

function BodyHeader({
  label,
  contentType,
  bytes,
}: {
  label: string
  contentType: string | null
  bytes: number
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[11px] font-semibold tracking-wide text-[var(--sk-text-muted)] uppercase">
        {label}
      </span>
      {contentType ? (
        <Badge tone="neutral" variant="outline" size="sm">
          {contentType}
        </Badge>
      ) : null}
      <span className="text-[11px] text-[var(--sk-text-muted)]">
        {formatBytes(bytes)}
      </span>
    </div>
  )
}

function Headers({
  headers,
  label,
}: {
  headers: Record<string, string>
  label: string
}) {
  const [open, setOpen] = useState(false)
  const count = Object.keys(headers).length
  return (
    <div className="rounded-2xl border border-[var(--sk-border-subtle)]">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full cursor-pointer items-center justify-between px-3 py-2 text-start"
      >
        <span className="text-[11px] font-semibold tracking-wide text-[var(--sk-text-muted)] uppercase">
          {label} · {count}
        </span>
        <ChevronDownIcon
          className={cn(
            "size-4 text-[var(--sk-text-muted)] transition-transform",
            open && "rotate-180"
          )}
          aria-hidden="true"
        />
      </button>
      {open ? (
        <div className="border-t border-[var(--sk-border-subtle)] p-3">
          <JsonView data={headers} />
        </div>
      ) : null}
    </div>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-[var(--sk-text-muted)]">{label}</dt>
      <dd className="truncate font-mono text-[12.5px] text-[var(--sk-text)]">
        {value}
      </dd>
    </div>
  )
}
