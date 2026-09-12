import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  ArrowPathIcon,
  BoltIcon,
  ChevronDownIcon,
  MagnifyingGlassIcon,
  XMarkIcon,
} from "@heroicons/react/24/outline"
import {
  Badge,
  Button,
  EmptyState,
  Input,
  Select,
  Sheet,
  Skeleton,
  toast,
} from "sunkit-ui"
import { getLog, listLogEvents, listLogs } from "@/ops/api"
import {
  formatDateTime,
  formatDay,
  levelLabel,
  levelTone,
  relativeTime,
  sinceFromPreset,
  transportLabel,
} from "@/ops/format"
import { JsonView } from "@/ops/json-view"
import type { LogEntry, LogRow } from "@/ops/types"
import { useIsMobile } from "@/hooks/use-mobile"
import { cn } from "@/lib/utils"

const PAGE = 80
const RANGES = ["15m", "1h", "24h", "7d"]

type Filters = {
  q: string
  level: string
  event: string
  transport: string
  chatId: string
  userId: string
  range: string
}

const EMPTY: Filters = {
  q: "",
  level: "",
  event: "",
  transport: "",
  chatId: "",
  userId: "",
  range: "",
}

export function LogsView({
  runId,
  onRunIdChange,
  onInspectRun,
}: {
  runId: string
  onRunIdChange: (runId: string) => void
  onInspectRun: (runId: string) => void
}) {
  const [filters, setFilters] = useState<Filters>(EMPTY)
  const [debouncedQuery, setDebouncedQuery] = useState("")
  const [rows, setRows] = useState<LogRow[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [events, setEvents] = useState<{ event: string; count: number }[]>([])
  const [selected, setSelected] = useState<LogEntry | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [live, setLive] = useState(false)
  const requestId = useRef(0)

  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedQuery(filters.q), 300)
    return () => window.clearTimeout(handle)
  }, [filters.q])

  const baseFilters = useMemo(
    () => ({
      level: filters.level || undefined,
      event: filters.event || undefined,
      transport: filters.transport || undefined,
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
      if (mode === "reset") {
        setLoading(true)
      } else {
        setLoadingMore(true)
      }
      try {
        const before =
          mode === "more" && rows.length > 0
            ? rows[rows.length - 1]?.id
            : undefined
        const payload = await listLogs({ ...baseFilters, before }, PAGE)
        if (id !== requestId.current) {
          return
        }
        setRows((current) =>
          mode === "more" ? [...current, ...payload.logs] : payload.logs
        )
        setHasMore(payload.logs.length === PAGE)
      } catch (error) {
        if (id === requestId.current) {
          toast.error({
            title: "Couldn't load logs",
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

  useEffect(() => {
    void listLogEvents()
      .then((payload) => setEvents(payload.events))
      .catch(() => setEvents([]))
  }, [])

  useEffect(() => {
    if (!live) {
      return
    }
    const handle = window.setInterval(() => void load("reset"), 5000)
    return () => window.clearInterval(handle)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live, baseFilters])

  async function openDetail(row: LogRow) {
    setDetailOpen(true)
    setSelected(null)
    try {
      const payload = await getLog(row.id)
      setSelected(payload.log)
    } catch (error) {
      setDetailOpen(false)
      toast.error({
        title: "Couldn't load the entry",
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
  const eventOptions = useMemo(
    () => [
      { value: "", label: "Any event" },
      ...events.map((item) => ({
        value: item.event,
        label: `${item.event} · ${item.count}`,
      })),
    ],
    [events]
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
                placeholder="Search events, context, errors"
                aria-label="Search logs"
                variant="filled"
                radius={14}
                leftAdornment={
                  <MagnifyingGlassIcon
                    className="size-4 text-[var(--sk-text-muted)]"
                    aria-hidden="true"
                  />
                }
                rightAdornment={
                  filters.q ? (
                    <button
                      type="button"
                      aria-label="Clear search"
                      onClick={() => setFilters((f) => ({ ...f, q: "" }))}
                      className="cursor-pointer text-[var(--sk-text-muted)]"
                    >
                      <XMarkIcon className="size-4" />
                    </button>
                  ) : undefined
                }
              />
            </div>
            <Select
              options={[
                { value: "", label: "Any level" },
                { value: "debug", label: "Debug" },
                { value: "info", label: "Info" },
                { value: "warning", label: "Warning" },
                { value: "error", label: "Error" },
                { value: "critical", label: "Critical" },
              ]}
              value={filters.level}
              onChange={(value) => setFilters((f) => ({ ...f, level: value }))}
              variant="filled"
              radius={14}
              aria-label="Filter by level"
              containerClassName="w-[130px]"
            />
            <Select
              options={eventOptions}
              value={filters.event}
              onChange={(value) => setFilters((f) => ({ ...f, event: value }))}
              variant="filled"
              radius={14}
              searchable
              aria-label="Filter by event"
              containerClassName="w-[190px]"
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
                  : `${rows.length} ${rows.length === 1 ? "entry" : "entries"}`}
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
                variant={live ? "solid" : "ghost"}
                color="lavender"
                size="sm"
                radius={999}
                icon="left"
                iconLeft={<BoltIcon />}
                aria-pressed={live}
                onClick={() => setLive((value) => !value)}
              >
                Live
              </Button>
              <Button
                variant="ghost"
                color="neutral"
                size="icon-only"
                icon="only"
                iconOnly={<ArrowPathIcon />}
                radius={999}
                aria-label="Reload logs"
                onClick={() => void load("reset")}
              />
            </div>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="flex flex-col gap-2 py-1">
          {Array.from({ length: 8 }).map((_, index) => (
            <Skeleton key={index} variant="rounded" height={52} />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          tone="lavender"
          icon={<MagnifyingGlassIcon className="size-6" aria-hidden="true" />}
          title="No matching entries"
          description={
            filtered
              ? "Adjust the filters or widen the time range."
              : "Skye hasn't logged anything yet."
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
        <ul className="flex flex-col">
          {rows.map((row, index) => {
            const previous = rows[index - 1]
            const newDay =
              !previous || formatDay(previous.ts) !== formatDay(row.ts)
            const isError = ["error", "critical"].includes(row.level)
            return (
              <li key={row.id}>
                {newDay ? (
                  <div className="-mx-1 mt-2 mb-1 px-1 py-1 text-[11px] font-semibold tracking-wide text-[var(--sk-text-muted)] uppercase">
                    {formatDay(row.ts)}
                  </div>
                ) : null}
                <button
                  type="button"
                  onClick={() => void openDetail(row)}
                  className={cn(
                    "flex w-full cursor-pointer items-start gap-3 rounded-2xl border border-transparent px-2.5 py-2 text-start transition-colors outline-none hover:bg-[var(--sk-surface-hover)] focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/50",
                    isError && "border-rose/20 bg-rose/[0.04]"
                  )}
                >
                  <span className="w-[62px] shrink-0 pt-0.5 font-mono text-[11px] text-[var(--sk-text-muted)] tabular-nums">
                    {relativeTime(row.ts)}
                  </span>
                  <Badge
                    tone={levelTone(row.level)}
                    variant="soft"
                    size="sm"
                    dot
                  >
                    {levelLabel(row.level)}
                  </Badge>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-baseline gap-x-2">
                      <span className="font-mono text-[12.5px] font-medium text-[var(--sk-text)]">
                        {row.event}
                      </span>
                      {row.has_exception ? (
                        <span className="text-[11px] font-medium text-[var(--sk-text-error)]">
                          exception
                        </span>
                      ) : null}
                    </span>
                    {row.preview && row.preview !== "{}" ? (
                      <span className="mt-0.5 block truncate text-[12px] text-[var(--sk-text-desc)]">
                        {row.preview}
                      </span>
                    ) : null}
                  </span>
                  <span className="hidden shrink-0 flex-col items-end gap-1 pt-0.5 text-[11px] text-[var(--sk-text-muted)] sm:flex">
                    {row.chat_id !== null ? (
                      <span>chat {row.chat_id}</span>
                    ) : null}
                    {row.user_id !== null ? (
                      <span>user {row.user_id}</span>
                    ) : null}
                  </span>
                </button>
              </li>
            )
          })}
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

      <LogDetail
        log={selected}
        open={detailOpen}
        onOpenChange={(open) => {
          setDetailOpen(open)
          if (!open) setSelected(null)
        }}
        onInspectRun={(runId) => {
          setDetailOpen(false)
          onInspectRun(runId)
        }}
      />
    </div>
  )
}

function LogDetail({
  log,
  open,
  onOpenChange,
  onInspectRun,
}: {
  log: LogEntry | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onInspectRun: (runId: string) => void
}) {
  const isMobile = useIsMobile()
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      side={isMobile ? "bottom" : "right"}
      size="lg"
      title={log?.event ?? "Log entry"}
      description={log ? formatDateTime(log.ts) : undefined}
      tone="lavender"
    >
      {log ? (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={levelTone(log.level)} variant="soft" dot>
              {levelLabel(log.level)}
            </Badge>
            {log.transport ? (
              <Badge tone="neutral" variant="outline">
                {transportLabel(log.transport)}
              </Badge>
            ) : null}
            {log.chat_id !== null ? (
              <Badge tone="sky" variant="outline">
                chat {log.chat_id}
              </Badge>
            ) : null}
            {log.user_id !== null ? (
              <Badge tone="mint" variant="outline">
                user {log.user_id}
              </Badge>
            ) : null}
          </div>

          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-2xl border border-[var(--sk-border-subtle)] bg-[var(--sk-surface-filled)] p-3.5 text-[12px]">
            <Meta label="Time" value={formatDateTime(log.ts)} />
            <Meta label="Event" value={log.event} />
            <Meta label="Logger" value={log.logger ?? "-"} />
            <Meta label="Run key" value={log.run_key ?? "-"} />
          </dl>

          {log.run_id ? (
            <Button
              variant="outline"
              color="lavender"
              radius={999}
              onClick={() => onInspectRun(log.run_id as string)}
            >
              View requests in this run
            </Button>
          ) : null}

          {log.exception ? (
            <div>
              <p className="mb-1.5 text-[11px] font-semibold tracking-wide text-[var(--sk-text-muted)] uppercase">
                Exception
              </p>
              <pre className="sk-scrollbar border-rose/30 bg-rose/[0.06] max-h-64 overflow-auto rounded-2xl border p-3 font-mono text-[12px] leading-relaxed whitespace-pre-wrap text-[var(--sk-text-error)]">
                {log.exception}
              </pre>
            </div>
          ) : null}

          <div>
            <p className="mb-1.5 text-[11px] font-semibold tracking-wide text-[var(--sk-text-muted)] uppercase">
              Context
            </p>
            <JsonView data={log.context} />
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <Skeleton variant="text" lines={6} />
        </div>
      )}
    </Sheet>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-[var(--sk-text-muted)]">{label}</dt>
      <dd className="truncate font-mono text-[12px] text-[var(--sk-text)]">
        {value}
      </dd>
    </div>
  )
}
