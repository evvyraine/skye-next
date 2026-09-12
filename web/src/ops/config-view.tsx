import { useCallback, useEffect, useMemo, useState } from "react"
import {
  ArrowPathIcon,
  ArrowUturnLeftIcon,
  CheckIcon,
  ChevronDownIcon,
  ExclamationTriangleIcon,
  EyeIcon,
  EyeSlashIcon,
  MagnifyingGlassIcon,
  LockClosedIcon,
} from "@heroicons/react/24/outline"
import {
  Alert,
  Badge,
  Button,
  Dialog,
  Input,
  Select,
  Skeleton,
  Toggle,
  toast,
} from "sunkit-ui"
import {
  applyConfig,
  getConfig,
  restartSkye,
  revertOverride,
  validateConfig,
} from "@/ops/api"
import type { ConfigField, ConfigPayload } from "@/ops/types"
import { cn } from "@/lib/utils"

type DraftValue = string | boolean

const LIVE_KEYS = new Set([
  "skye_ops_capture_payloads",
  "skye_ops_capture_media",
  "skye_ops_log_retention_days",
  "skye_ops_log_max_rows",
  "skye_ops_trace_retention_days",
  "skye_ops_trace_max_rows",
  "skye_ops_max_body_bytes",
])

export function ConfigView() {
  const [payload, setPayload] = useState<ConfigPayload | null>(null)
  const [draft, setDraft] = useState<Record<string, DraftValue>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [query, setQuery] = useState("")
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [restartOpen, setRestartOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [restartRequired, setRestartRequired] = useState(false)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const load = useCallback(async () => {
    try {
      const data = await getConfig()
      setPayload(data)
    } catch (error) {
      toast.error({
        title: "Couldn't load configuration",
        description: error instanceof Error ? error.message : "Try again.",
      })
    }
  }, [])

  useEffect(() => {
    const handle = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(handle)
  }, [load])

  const changed = useMemo(() => {
    if (!payload) return []
    return payload.fields.filter((field) => isDirty(field, draft))
  }, [payload, draft])
  const needsRestart = changed.some((field) => !LIVE_KEYS.has(field.key))

  const needle = query.trim().toLowerCase()
  const groups = useMemo(() => {
    if (!payload) return []
    return payload.groups
      .map((group) => ({
        group,
        fields: payload.fields.filter((field) => {
          if (field.group !== group) return false
          if (field.advanced && !showAdvanced && !needle) return false
          if (!needle) return true
          return matches(field, needle)
        }),
      }))
      .filter((entry) => entry.fields.length > 0)
  }, [payload, showAdvanced, needle])

  function update(field: ConfigField, value: DraftValue) {
    setDraft((current) => ({ ...current, [field.key]: value }))
    setErrors((current) => {
      if (!(field.key in current)) return current
      const next = { ...current }
      delete next[field.key]
      return next
    })
  }

  function discard() {
    setDraft({})
    setErrors({})
  }

  async function review() {
    if (!payload || changed.length === 0) return
    setSaving(true)
    try {
      const result = await validateConfig(valuesFor(changed, draft))
      if (!result.ok) {
        setErrors(result.errors)
        const first = Object.values(result.errors)[0]
        toast.error({
          title: "Check the highlighted fields",
          description: first || "Some values are not valid.",
        })
        return
      }
      setErrors({})
      setConfirmOpen(true)
    } catch (error) {
      toast.error({
        title: "Couldn't validate changes",
        description: error instanceof Error ? error.message : "Try again.",
      })
    } finally {
      setSaving(false)
    }
  }

  async function apply() {
    if (!payload || changed.length === 0) return
    setSaving(true)
    try {
      const result = await applyConfig(valuesFor(changed, draft))
      if (!result.ok) {
        setErrors(result.errors)
        setConfirmOpen(false)
        toast.error({
          title: "Couldn't save changes",
          description:
            Object.values(result.errors)[0] || "Some values are not valid.",
        })
        return
      }
      setConfirmOpen(false)
      setDraft({})
      setRestartRequired(Boolean(result.restart_required))
      await load()
      toast.success({
        title: "Configuration saved",
        description: result.restart_required
          ? "Restart Skye to apply the new values."
          : "Observability changes are live now.",
      })
    } catch (error) {
      toast.error({
        title: "Couldn't save changes",
        description: error instanceof Error ? error.message : "Try again.",
      })
    } finally {
      setSaving(false)
    }
  }

  async function revert(field: ConfigField) {
    try {
      await revertOverride(field.env)
      await load()
      toast.success({
        title: `${field.label} reset`,
        description: "Back to the environment value.",
      })
    } catch (error) {
      toast.error({
        title: "Couldn't reset that setting",
        description: error instanceof Error ? error.message : "Try again.",
      })
    }
  }

  async function restart() {
    try {
      await restartSkye()
      setRestartOpen(false)
      toast.success({
        title: "Restarting Skye",
        description: "The panel reconnects once the process is back.",
      })
    } catch (error) {
      toast.error({
        title: "Couldn't restart",
        description:
          error instanceof Error ? error.message : "Restart it from the host.",
      })
    }
  }

  if (!payload) {
    return (
      <div className="flex flex-col gap-3 pt-2">
        <Skeleton variant="rounded" height={64} />
        <Skeleton variant="rounded" height={220} />
        <Skeleton variant="rounded" height={220} />
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-col gap-3 pb-24">
      <div className="sticky top-[105px] z-10 -mx-4 flex flex-col gap-2.5 border-b border-[var(--sk-border-subtle)] bg-[var(--app-bg)] px-4 pt-1 pb-3 md:top-[61px] md:-mx-6 md:px-6">
        <div className="flex flex-wrap items-center gap-2">
          <div className="min-w-[180px] flex-1">
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search settings"
              aria-label="Search settings"
              variant="filled"
              radius={999}
              leftAdornment={
                <MagnifyingGlassIcon
                  className="size-4 text-[var(--sk-text-muted)]"
                  aria-hidden="true"
                />
              }
            />
          </div>
          <Toggle
            checked={showAdvanced}
            onCheckedChange={setShowAdvanced}
            label="Advanced"
            size="sm"
          />
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-[var(--sk-text-desc)]">
          <span className="font-mono">{payload.env_file}</span>
          {payload.overrides.length > 0 ? (
            <Badge tone="lavender" variant="soft" size="sm">
              {payload.overrides.length} panel{" "}
              {payload.overrides.length === 1 ? "override" : "overrides"}
            </Badge>
          ) : (
            <Badge tone="neutral" variant="outline" size="sm">
              no overrides
            </Badge>
          )}
          <Badge
            tone={payload.capture.enabled ? "mint" : "neutral"}
            variant="soft"
            size="sm"
          >
            {payload.capture.enabled ? "capture on" : "capture off"}
          </Badge>
        </div>
      </div>

      {restartRequired ? (
        <Alert
          variant="warning"
          title="Restart required"
          icon={
            <ExclamationTriangleIcon className="size-5" aria-hidden="true" />
          }
        >
          <div className="flex flex-col gap-2">
            <p>Some of these settings are read when Skye starts.</p>
            <div>
              <Button
                variant="outline"
                color="peach"
                radius={999}
                icon="left"
                iconLeft={<ArrowPathIcon />}
                onClick={() => setRestartOpen(true)}
              >
                Restart Skye
              </Button>
            </div>
          </div>
        </Alert>
      ) : null}

      {groups.map(({ group, fields }) => {
        const isCollapsed = collapsed[group] === true
        return (
          <section
            key={group}
            className="overflow-hidden rounded-3xl border border-[var(--sk-border-subtle)] bg-[var(--sk-surface)]"
          >
            <button
              type="button"
              aria-expanded={!isCollapsed}
              onClick={() =>
                setCollapsed((current) => ({
                  ...current,
                  [group]: !current[group],
                }))
              }
              className="flex w-full cursor-pointer items-center gap-2 px-4 py-3 text-start"
            >
              <span className="flex-1 text-[14px] font-semibold tracking-tight">
                {group}
              </span>
              <span className="text-[11px] text-[var(--sk-text-muted)]">
                {fields.length} {fields.length === 1 ? "setting" : "settings"}
              </span>
              <ChevronDownIcon
                className={cn(
                  "size-4 text-[var(--sk-text-muted)] transition-transform duration-200",
                  !isCollapsed && "rotate-180"
                )}
                aria-hidden="true"
              />
            </button>
            {!isCollapsed ? (
              <div className="flex flex-col divide-y divide-[var(--sk-border-subtle)] border-t border-[var(--sk-border-subtle)]">
                {fields.map((field) => (
                  <ConfigFieldRow
                    key={field.key}
                    field={field}
                    value={draft[field.key] ?? initialFor(field)}
                    error={errors[field.key]}
                    onChange={(value) => update(field, value)}
                    onRevert={() => void revert(field)}
                  />
                ))}
              </div>
            ) : null}
          </section>
        )
      })}

      {changed.length > 0 ? (
        <div className="fixed inset-x-0 bottom-0 z-20 border-t border-[var(--sk-border-subtle)] bg-[var(--app-bg)]">
          <div className="mx-auto flex max-w-[1200px] items-center gap-3 px-4 py-3 md:px-6">
            <p className="min-w-0 flex-1 text-[12.5px] text-[var(--sk-text-desc)]">
              <span className="font-semibold text-[var(--sk-text)]">
                {changed.length} unsaved{" "}
                {changed.length === 1 ? "change" : "changes"}
              </span>
              <span className="hidden sm:inline">
                {" "}
                · {changed.map((f) => f.label).join(", ")}
              </span>
            </p>
            <Button
              variant="ghost"
              color="neutral"
              radius={999}
              onClick={discard}
            >
              Discard
            </Button>
            <Button
              variant="solid"
              color="lavender"
              radius={999}
              icon="right"
              iconRight={<CheckIcon />}
              disabled={saving}
              onClick={() => void review()}
            >
              Review and save
            </Button>
          </div>
        </div>
      ) : null}

      <Dialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Apply configuration changes?"
        description={
          needsRestart
            ? "These values are stored for the panel and applied on the next restart."
            : "These observability settings take effect immediately."
        }
        tone="lavender"
        radius={28}
        footer={
          <>
            <Button
              variant="ghost"
              color="neutral"
              radius={999}
              onClick={() => setConfirmOpen(false)}
            >
              Cancel
            </Button>
            <Button
              variant="solid"
              color="lavender"
              radius={999}
              disabled={saving}
              onClick={() => void apply()}
            >
              Apply changes
            </Button>
          </>
        }
      >
        <ul className="flex max-h-64 flex-col gap-1.5 overflow-y-auto text-[12.5px]">
          {changed.map((field) => (
            <li
              key={field.key}
              className="flex items-center justify-between gap-3"
            >
              <span className="truncate">{field.label}</span>
              <span className="shrink-0 font-mono text-[11px] text-[var(--sk-text-muted)]">
                {field.secret ? "updated" : describe(field, draft[field.key])}
              </span>
            </li>
          ))}
        </ul>
      </Dialog>

      <Dialog
        open={restartOpen}
        onOpenChange={setRestartOpen}
        title="Restart Skye?"
        description="The bot stops and starts again. In-progress runs are interrupted and Telegram polling resumes on its own."
        tone="rose"
        radius={28}
        footer={
          <>
            <Button
              variant="ghost"
              color="neutral"
              radius={999}
              onClick={() => setRestartOpen(false)}
            >
              Cancel
            </Button>
            <Button
              variant="solid"
              color="rose"
              radius={999}
              onClick={() => void restart()}
            >
              Restart Skye
            </Button>
          </>
        }
      />
    </div>
  )
}

function ConfigFieldRow({
  field,
  value,
  error,
  onChange,
  onRevert,
}: {
  field: ConfigField
  value: DraftValue
  error?: string
  onChange: (value: DraftValue) => void
  onRevert: () => void
}) {
  const [reveal, setReveal] = useState(false)
  const dirty = isDirty(field, { [field.key]: value })
  const control = renderControl(field, value, reveal, setReveal, onChange)
  return (
    <div className="flex flex-col gap-2 px-4 py-3.5 sm:flex-row sm:items-start sm:gap-6">
      <div className="min-w-0 sm:w-[46%]">
        <div className="flex flex-wrap items-center gap-1.5">
          <label
            htmlFor={`cfg-${field.key}`}
            className="text-[13px] font-medium text-[var(--sk-text)]"
          >
            {field.label}
          </label>
          {dirty ? (
            <Badge tone="lavender" variant="soft" size="sm">
              edited
            </Badge>
          ) : null}
          {field.override ? (
            <Badge tone="peach" variant="soft" size="sm">
              override
            </Badge>
          ) : null}
          {field.advanced ? (
            <Badge tone="neutral" variant="outline" size="sm">
              advanced
            </Badge>
          ) : null}
          {field.read_only ? (
            <LockClosedIcon
              className="size-3.5 text-[var(--sk-text-muted)]"
              aria-hidden="true"
            />
          ) : null}
        </div>
        <p className="mt-1 text-[12px] leading-relaxed text-[var(--sk-text-desc)]">
          {field.description}
        </p>
        <p className="mt-1 font-mono text-[10.5px] text-[var(--sk-text-muted)]">
          {field.env}
        </p>
      </div>
      <div className="min-w-0 flex-1">
        {control}
        {field.override && !field.read_only ? (
          <button
            type="button"
            onClick={onRevert}
            className="mt-1.5 inline-flex cursor-pointer items-center gap-1 text-[11.5px] text-[var(--sk-accent)] hover:underline"
          >
            <ArrowUturnLeftIcon className="size-3.5" aria-hidden="true" />
            Reset to environment
          </button>
        ) : null}
        {error ? (
          <p
            role="alert"
            className="mt-1.5 text-[12px] text-[var(--sk-text-error)]"
          >
            {error}
          </p>
        ) : null}
      </div>
    </div>
  )
}

function renderControl(
  field: ConfigField,
  value: DraftValue,
  reveal: boolean,
  setReveal: (value: boolean) => void,
  onChange: (value: DraftValue) => void
) {
  const id = `cfg-${field.key}`
  if (field.kind === "bool") {
    return (
      <Toggle
        id={id}
        checked={Boolean(value)}
        disabled={field.read_only}
        onCheckedChange={(checked) => onChange(checked)}
      />
    )
  }
  if (field.kind === "select") {
    return (
      <Select
        id={id}
        options={field.choices.map((choice) => ({
          value: choice,
          label: choice,
        }))}
        value={String(value)}
        disabled={field.read_only}
        onChange={onChange}
        radius={14}
        tone="lavender"
        containerClassName="w-full"
      />
    )
  }
  if (field.kind === "secret") {
    return (
      <Input
        id={id}
        type={reveal ? "text" : "password"}
        value={String(value)}
        disabled={field.read_only}
        autoComplete="off"
        spellCheck={false}
        radius={14}
        tone="lavender"
        placeholder={
          field.is_set ? "Leave blank to keep the current value" : "Not set"
        }
        onChange={(event) => onChange(event.target.value)}
        rightAdornment={
          <button
            type="button"
            aria-label={reveal ? "Hide value" : "Show value"}
            onClick={() => setReveal(!reveal)}
            className="cursor-pointer text-[var(--sk-text-muted)]"
          >
            {reveal ? (
              <EyeSlashIcon className="size-4" />
            ) : (
              <EyeIcon className="size-4" />
            )}
          </button>
        }
      />
    )
  }
  const numeric = field.kind === "int" || field.kind === "float"
  return (
    <Input
      id={id}
      type={numeric ? "number" : "text"}
      inputMode={numeric ? "numeric" : undefined}
      min={numeric ? (field.minimum ?? undefined) : undefined}
      max={numeric ? (field.maximum ?? undefined) : undefined}
      step={field.kind === "float" ? "any" : undefined}
      value={String(value)}
      disabled={field.read_only}
      radius={14}
      tone="lavender"
      spellCheck={false}
      onChange={(event) => onChange(event.target.value)}
      description={
        field.kind === "list"
          ? "Comma-separated values."
          : numeric && (field.minimum !== null || field.maximum !== null)
            ? `Range ${field.minimum ?? "-"} to ${field.maximum ?? "-"}.`
            : undefined
      }
    />
  )
}

function initialFor(field: ConfigField): DraftValue {
  if (field.kind === "bool") {
    return Boolean(field.value)
  }
  if (field.secret) {
    return ""
  }
  if (field.value === null || field.value === undefined) {
    return ""
  }
  return String(field.value)
}

function isDirty(
  field: ConfigField,
  draft: Record<string, DraftValue>
): boolean {
  if (!(field.key in draft)) return false
  const value = draft[field.key]
  return value !== initialFor(field)
}

function valuesFor(
  fields: ConfigField[],
  draft: Record<string, DraftValue>
): Record<string, unknown> {
  const values: Record<string, unknown> = {}
  for (const field of fields) {
    const value = draft[field.key]
    if (field.kind === "bool") {
      values[field.key] = Boolean(value)
    } else {
      values[field.key] = String(value ?? "")
    }
  }
  return values
}

function describe(field: ConfigField, value: DraftValue | undefined): string {
  if (field.kind === "bool") return value ? "on" : "off"
  const text = String(value ?? "")
  if (field.kind === "secret") return text ? "updated" : "cleared"
  if (!text) return "empty"
  return text.length > 24 ? `${text.slice(0, 24)}…` : text
}

function matches(field: ConfigField, needle: string): boolean {
  return (
    field.label.toLowerCase().includes(needle) ||
    field.key.toLowerCase().includes(needle) ||
    field.env.toLowerCase().includes(needle) ||
    field.description.toLowerCase().includes(needle)
  )
}
