import { ArrowPathIcon, TrashIcon } from "@heroicons/react/24/outline"
import { Button, Input, Separator, Textarea } from "sunkit-ui"
import { ProjectIcon } from "@/components/project-icon"
import type { Project } from "@/lib/types"
import {
  COLOR_LABELS,
  COLOR_ORDER,
  ICON_LABELS,
  ICON_ORDER,
  PROJECT_PASTELS,
} from "@/lib/icons"
import { cn } from "@/lib/utils"

export function SettingsPanel({
  project,
  onChange,
  onReset,
  onDelete,
}: {
  project: Project
  onChange: (patch: Partial<Project>) => void
  onReset: () => void
  onDelete: () => void
}) {
  return (
    <div className="sk-scrollbar flex h-full flex-col gap-5 overflow-y-auto p-4">
      <div className="flex flex-col items-center gap-2 pt-2 text-center">
        <ProjectIcon icon={project.icon} color={project.color} size="lg" />
        <p className="text-[13px] text-[var(--sk-text-desc)]">
          {project.kind === "skye"
            ? "Your main Skye conversation"
            : "Project settings"}
        </p>
      </div>

      <Input
        id="settings-name"
        value={project.name}
        label="Name"
        tone="lavender"
        radius={14}
        disabled={project.kind === "skye"}
        onChange={(event) => onChange({ name: event.target.value })}
      />

      <Textarea
        id="settings-instructions"
        value={project.instructions}
        label="Instructions"
        description="Describe what this project should do."
        placeholder="e.g. You are a concise product coach."
        tone="lavender"
        radius={14}
        rows={5}
        autoResize
        maxLength={12000}
        showCount
        onChange={(event) => onChange({ instructions: event.target.value })}
      />

      <Separator label="Appearance" tone="lavender" />

      <div className="flex flex-col gap-4">
        <div>
          <p className="mb-2 text-[11px] font-semibold tracking-wide text-[var(--sk-text-muted)] uppercase">
            Color
          </p>
          <div
            className="flex flex-wrap gap-1.5"
            role="group"
            aria-label="Project color"
          >
            {COLOR_ORDER.map((item) => {
              const active = project.color === item
              const label = COLOR_LABELS[item] ?? item
              return (
                <button
                  key={item}
                  type="button"
                  aria-label={label}
                  aria-pressed={active}
                  title={label}
                  className={cn(
                    "size-8 cursor-pointer rounded-full border border-black/10 outline-none transition-transform hover:scale-110 focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/60",
                    active &&
                      "ring-2 ring-[var(--sk-accent)] ring-offset-2 ring-offset-[var(--sk-bg-solid)]"
                  )}
                  style={{ background: PROJECT_PASTELS[item] }}
                  onClick={() => onChange({ color: item })}
                />
              )
            })}
          </div>
        </div>

        <div>
          <p className="mb-2 text-[11px] font-semibold tracking-wide text-[var(--sk-text-muted)] uppercase">
            Icon
          </p>
          <div
            className="flex flex-wrap gap-1"
            role="group"
            aria-label="Project icon"
          >
            {ICON_ORDER.map((item) => {
              const active = project.icon === item
              const label = ICON_LABELS[item] ?? item
              return (
                <button
                  key={item}
                  type="button"
                  aria-label={label}
                  aria-pressed={active}
                  title={label}
                  className={cn(
                    "flex size-11 cursor-pointer items-center justify-center rounded-2xl outline-none transition-colors hover:bg-[var(--sk-surface-filled)] focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/60",
                    active &&
                      "bg-[var(--sk-surface-filled)] ring-2 ring-[var(--sk-accent)]"
                  )}
                  onClick={() => onChange({ icon: item })}
                >
                  <ProjectIcon icon={item} color={project.color} size="md" />
                </button>
              )
            })}
          </div>
        </div>
      </div>

      <div className="mt-auto flex flex-col gap-2 pt-4">
        <Button
          variant="outline"
          color="lavender"
          radius={999}
          icon="left"
          iconLeft={<ArrowPathIcon />}
          className="h-11 w-full"
          onClick={onReset}
        >
          Reset this chat
        </Button>
        {project.deletable ? (
          <Button
            variant="outline"
            color="rose"
            radius={999}
            icon="left"
            iconLeft={<TrashIcon />}
            className="h-11 w-full"
            onClick={onDelete}
          >
            Delete project
          </Button>
        ) : null}
      </div>
    </div>
  )
}
