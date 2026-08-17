import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { ProjectIcon } from "@/components/project-icon"
import type { Project } from "@/lib/types"
import { COLOR_ORDER, ICON_ORDER, PROJECT_COLORS } from "@/lib/icons"
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
    <div className="flex h-full flex-col gap-6 overflow-y-auto p-4">
      <div className="flex justify-center pt-4">
        <ProjectIcon icon={project.icon} color={project.color} size="lg" />
      </div>
      <FieldGroup>
        <Field>
          <FieldLabel htmlFor="settings-name">Name</FieldLabel>
          <Input
            id="settings-name"
            value={project.name}
            disabled={project.kind === "skye"}
            onChange={(event) => onChange({ name: event.target.value })}
          />
        </Field>
        <Field>
          <FieldLabel htmlFor="settings-instructions">Instructions</FieldLabel>
          <Textarea
            id="settings-instructions"
            value={project.instructions}
            placeholder="Describe what this project should do."
            className="min-h-40"
            onChange={(event) => onChange({ instructions: event.target.value })}
          />
        </Field>
      </FieldGroup>
      <div className="flex flex-col gap-3">
        <p className="text-sm font-medium">Icon</p>
        <div className="flex flex-wrap gap-2">
          {ICON_ORDER.map((item) => (
            <button
              key={item}
              type="button"
              aria-label={item}
              className={cn("rounded-xl", project.icon === item && "ring-2 ring-foreground")}
              onClick={() => onChange({ icon: item })}
            >
              <ProjectIcon icon={item} color={project.color} size="sm" className="rounded-xl" />
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          {COLOR_ORDER.map((item) => (
            <button
              key={item}
              type="button"
              aria-label={item}
              className={cn(
                "size-7 rounded-full",
                PROJECT_COLORS[item],
                project.color === item && "ring-2 ring-foreground ring-offset-2",
              )}
              onClick={() => onChange({ color: item })}
            />
          ))}
        </div>
      </div>
      <div className="mt-auto flex flex-col gap-2">
        <Button variant="outline" onClick={onReset}>
          Reset this chat
        </Button>
        {project.deletable ? (
          <Button variant="destructive" onClick={onDelete}>
            Delete project
          </Button>
        ) : null}
      </div>
    </div>
  )
}
