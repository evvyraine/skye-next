import { useCallback, useEffect, useState, type ReactNode } from "react"
import { AnimatePresence, motion } from "motion/react"
import {
  AdjustmentsHorizontalIcon,
  ArrowPathIcon,
  BoltIcon,
  ChatBubbleLeftRightIcon,
  DocumentTextIcon,
  ExclamationTriangleIcon,
  ServerStackIcon,
} from "@heroicons/react/24/outline"
import { Badge, Button, Spinner, ThemeProvider } from "sunkit-ui"
import { OpsError, getOverview } from "@/ops/api"
import { ConfigView } from "@/ops/config-view"
import { LogsView } from "@/ops/logs-view"
import { TracesView } from "@/ops/traces-view"
import { ThemeToggle } from "@/components/theme-toggle"
import { SkyeSign } from "@/components/skye-logo"
import { useTheme } from "@/components/theme-provider"
import { formatBytes, formatCount } from "@/ops/format"
import type { Overview } from "@/ops/types"
import { cn } from "@/lib/utils"

type View = "logs" | "traces" | "config"
type Gate =
  "loading" | "ready" | "unauthenticated" | "forbidden" | "disabled" | "error"

const VIEWS: { value: View; label: string; Icon: typeof DocumentTextIcon }[] = [
  { value: "logs", label: "Logs", Icon: DocumentTextIcon },
  { value: "traces", label: "Requests", Icon: ServerStackIcon },
  { value: "config", label: "Config", Icon: AdjustmentsHorizontalIcon },
]

export function OpsApp() {
  const { resolvedTheme } = useTheme()
  const [overview, setOverview] = useState<Overview | null>(null)
  const [gate, setGate] = useState<Gate>("loading")
  const [view, setView] = useState<View>("logs")
  const [runId, setRunId] = useState("")
  const [reloadKey, setReloadKey] = useState(0)

  const loadOverview = useCallback(async () => {
    try {
      const data = await getOverview()
      setOverview(data)
      setGate("ready")
    } catch (error) {
      if (error instanceof OpsError) {
        if (error.status === 401) setGate("unauthenticated")
        else if (error.status === 403) setGate("forbidden")
        else if (error.status === 404) setGate("disabled")
        else setGate("error")
      } else {
        setGate("error")
      }
    }
  }, [])

  useEffect(() => {
    const handle = window.setTimeout(() => void loadOverview(), 0)
    return () => window.clearTimeout(handle)
  }, [loadOverview])

  useEffect(() => {
    if (gate !== "ready") return
    const handle = window.setInterval(() => void loadOverview(), 30_000)
    return () => window.clearInterval(handle)
  }, [gate, loadOverview])

  function refresh() {
    setReloadKey((value) => value + 1)
    void loadOverview()
  }

  return (
    <ThemeProvider dark={resolvedTheme === "dark"}>
      {gate === "ready" ? (
        <div className="h-dvh overflow-y-auto">
          <header className="sticky top-0 z-30 border-b border-[var(--sk-border-subtle)] bg-[var(--app-bg)]">
            <div className="mx-auto flex max-w-[1200px] items-center gap-3 px-4 py-3 md:px-6">
              <a
                href="/"
                className="flex shrink-0 items-center gap-2.5"
                aria-label="Back to chat"
              >
                <SkyeSign className="h-6" />
                <span className="flex flex-col leading-none">
                  <span className="text-[14px] font-semibold tracking-tight">
                    Operations
                  </span>
                  <span className="mt-0.5 text-[11px] text-[var(--sk-text-muted)]">
                    Owner console
                  </span>
                </span>
              </a>

              <nav
                className="ml-2 hidden items-center gap-1 md:flex"
                aria-label="Sections"
              >
                {VIEWS.map((item) => (
                  <NavButton
                    key={item.value}
                    active={view === item.value}
                    icon={<item.Icon className="size-4" />}
                    label={item.label}
                    onClick={() => setView(item.value)}
                  />
                ))}
              </nav>

              <div className="ml-auto flex items-center gap-1.5">
                <StatusChips overview={overview} />
                <Button
                  variant="ghost"
                  color="neutral"
                  size="icon-only"
                  icon="only"
                  iconOnly={<ArrowPathIcon />}
                  radius={999}
                  aria-label="Refresh"
                  onClick={refresh}
                />
                <ThemeToggle />
                <a
                  href="/"
                  className="hidden h-9 items-center gap-1.5 rounded-full border border-[var(--sk-border-subtle)] px-3 text-[12.5px] font-medium text-[var(--sk-text)] transition-colors outline-none hover:bg-[var(--sk-surface-hover)] focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/50 sm:inline-flex"
                >
                  <ChatBubbleLeftRightIcon
                    className="size-4"
                    aria-hidden="true"
                  />
                  Chat
                </a>
              </div>
            </div>

            <nav
              className="flex items-center gap-1 overflow-x-auto px-4 pb-2 md:hidden"
              aria-label="Sections"
            >
              {VIEWS.map((item) => (
                <NavButton
                  key={item.value}
                  active={view === item.value}
                  icon={<item.Icon className="size-4" />}
                  label={item.label}
                  onClick={() => setView(item.value)}
                />
              ))}
            </nav>
          </header>

          <main className="mx-auto w-full max-w-[1200px] px-4 py-4 md:px-6">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={`${view}-${reloadKey}`}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.18, ease: "easeOut" }}
              >
                {view === "logs" ? (
                  <LogsView
                    runId={runId}
                    onRunIdChange={setRunId}
                    onInspectRun={(next) => {
                      setRunId(next)
                      setView("traces")
                    }}
                  />
                ) : null}
                {view === "traces" ? (
                  <TracesView
                    runId={runId}
                    onRunIdChange={setRunId}
                    onInspectLogs={(next) => {
                      setRunId(next)
                      setView("logs")
                    }}
                  />
                ) : null}
                {view === "config" ? <ConfigView /> : null}
              </motion.div>
            </AnimatePresence>
          </main>
        </div>
      ) : (
        <GateScreen gate={gate} onRetry={() => void loadOverview()} />
      )}
    </ThemeProvider>
  )
}

function StatusChips({ overview }: { overview: Overview | null }) {
  if (!overview) return null
  return (
    <div className="hidden items-center gap-1.5 lg:flex">
      <Badge
        tone={overview.errors > 0 ? "rose" : "neutral"}
        variant="soft"
        size="sm"
      >
        <ExclamationTriangleIcon className="size-3.5" aria-hidden="true" />
        {formatCount(overview.errors)} errors
      </Badge>
      <Badge tone="sky" variant="soft" size="sm">
        <BoltIcon className="size-3.5" aria-hidden="true" />
        {formatCount(overview.traces_24h)} calls / 24h
      </Badge>
      <Badge
        tone={overview.capture.enabled ? "mint" : "neutral"}
        variant="soft"
        size="sm"
      >
        {overview.capture.enabled ? "capturing" : "capture off"}
        {overview.media_bytes > 0
          ? ` · ${formatBytes(overview.media_bytes)}`
          : ""}
      </Badge>
    </div>
  )
}

function NavButton({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean
  icon: ReactNode
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-current={active ? "page" : undefined}
      onClick={onClick}
      className={cn(
        "inline-flex h-9 shrink-0 cursor-pointer items-center gap-1.5 rounded-full px-3 text-[12.5px] font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/50",
        active
          ? "bg-[var(--sk-accent)]/14 text-[var(--sk-text)]"
          : "text-[var(--sk-text-desc)] hover:bg-[var(--sk-surface-hover)] hover:text-[var(--sk-text)]"
      )}
    >
      <span aria-hidden="true">{icon}</span>
      {label}
    </button>
  )
}

function GateScreen({ gate, onRetry }: { gate: Gate; onRetry: () => void }) {
  if (gate === "loading") {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Spinner size="lg" label="Loading the operator panel" />
      </div>
    )
  }
  const copy: Record<
    Exclude<Gate, "loading" | "ready">,
    { title: string; body: string }
  > = {
    unauthenticated: {
      title: "Sign in to continue",
      body: "The operator panel uses your Telegram login. Only owner accounts can open it.",
    },
    forbidden: {
      title: "This panel is for the operator",
      body: "Your account is signed in but is not an owner of this instance.",
    },
    disabled: {
      title: "The panel is disabled",
      body: "Set SKYE_OPS_ENABLED=true in the environment to turn it on.",
    },
    error: {
      title: "Couldn't reach the panel",
      body: "Check that Skye is running and try again.",
    },
  }
  const current = copy[gate as Exclude<Gate, "loading" | "ready">]
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-3 px-6 text-center">
      <SkyeSign className="h-12" />
      <h1 className="text-[22px] font-semibold tracking-tight">
        {current.title}
      </h1>
      <p className="max-w-sm text-[13.5px] leading-relaxed text-[var(--sk-text-desc)]">
        {current.body}
      </p>
      {gate === "unauthenticated" ? (
        <Button
          variant="solid"
          color="lavender"
          radius={999}
          className="mt-1 h-11 min-w-40"
          onClick={() => window.location.assign("/auth/telegram")}
        >
          Continue with Telegram
        </Button>
      ) : (
        <div className="mt-1 flex items-center gap-2">
          <Button
            variant="outline"
            color="neutral"
            radius={999}
            onClick={onRetry}
          >
            Try again
          </Button>
          <a
            href="/"
            className="inline-flex h-9 items-center rounded-full px-3 text-[12.5px] font-medium text-[var(--sk-text-desc)] hover:text-[var(--sk-text)]"
          >
            Back to chat
          </a>
        </div>
      )}
    </div>
  )
}
