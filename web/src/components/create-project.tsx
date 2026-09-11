import { useState } from "react"
import { Sparkles } from "lucide-react"
import { Button, Dialog, Input, Sheet, toast } from "sunkit-ui"
import { ProjectIcon } from "@/components/project-icon"
import { useIsMobile } from "@/hooks/use-mobile"
import { COLOR_ORDER, ICON_ORDER, PROJECT_PASTELS } from "@/lib/icons"
import { cn } from "@/lib/utils"

export function CreateProjectDialog({
  open,
  onOpenChange,
  onCreate,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (values: {
    name: string
    icon: string
    color: string
  }) => Promise<void>
}) {
  const [name, setName] = useState("")
  const [icon, setIcon] = useState("sparkles")
  const [color, setColor] = useState("violet")
  const [pending, setPending] = useState(false)
  const isMobile = useIsMobile()

  async function submit() {
    if (!name.trim() || pending) {
      return
    }
    setPending(true)
    try {
      await onCreate({ name: name.trim(), icon, color })
      setName("")
      setIcon("sparkles")
      setColor("violet")
      onOpenChange(false)
    } catch (error) {
      toast.error({
        title: "Couldn't create that project",
        description:
          (error instanceof Error && error.message) ||
          "Check the name and try again.",
      })
    } finally {
      setPending(false)
    }
  }

  const body = (
    <div className="flex flex-col gap-5">
      <div className="flex justify-center">
        <ProjectIcon icon={icon} color={color} size="lg" />
      </div>

      <Input
        id="project-name"
        value={name}
        onChange={(event) => setName(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            void submit()
          }
        }}
        label="Name"
        placeholder="Name your project"
        tone="lavender"
        radius={16}
        autoFocus
      />

      <div className="flex flex-col gap-4 rounded-3xl border border-[var(--sk-border-subtle)] bg-[var(--sk-surface-filled)] p-4">
        <div>
          <p className="mb-2 text-[11px] font-semibold tracking-wide text-[var(--sk-text-muted)] uppercase">
            Color
          </p>
          <div
            className="flex flex-wrap gap-2"
            role="group"
            aria-label="Project color"
          >
            {COLOR_ORDER.map((item) => {
              const active = color === item
              return (
                <button
                  key={item}
                  type="button"
                  aria-label={item}
                  aria-pressed={active}
                  className={cn(
                    "size-8 cursor-pointer rounded-full border border-black/10 outline-none transition-transform hover:scale-110 focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/60",
                    active && "ring-2 ring-[var(--sk-accent)] ring-offset-2 ring-offset-[var(--sk-bg-solid)]"
                  )}
                  style={{ background: PROJECT_PASTELS[item] }}
                  onClick={() => setColor(item)}
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
            className="flex flex-wrap gap-1.5"
            role="group"
            aria-label="Project icon"
          >
            {ICON_ORDER.map((item) => {
              const active = icon === item
              return (
                <button
                  key={item}
                  type="button"
                  aria-label={item}
                  aria-pressed={active}
                  className={cn(
                    "cursor-pointer rounded-2xl p-0.5 outline-none transition-transform hover:scale-105 focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/60",
                    active && "bg-[var(--sk-bg-solid)] shadow-sm ring-2 ring-[var(--sk-accent)]"
                  )}
                  onClick={() => setIcon(item)}
                >
                  <ProjectIcon icon={item} color={color} size="sm" />
                </button>
              )
            })}
          </div>
        </div>
      </div>

      <Button
        variant="solid"
        color="lavender"
        radius={999}
        className="h-12 w-full text-[15px]"
        icon="left"
        iconLeft={<Sparkles />}
        disabled={!name.trim() || pending}
        onClick={() => void submit()}
      >
        {pending ? "Creating…" : "Create project"}
      </Button>
    </div>
  )

  if (isMobile) {
    return (
      <Sheet
        open={open}
        onOpenChange={onOpenChange}
        side="bottom"
        size="lg"
        title="Create a project"
        description="Give it a name and a look."
        tone="lavender"
      >
        {body}
      </Sheet>
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Create a project"
      description="Give it a name and a look."
      size="default"
      tone="lavender"
      radius={28}
    >
      {body}
    </Dialog>
  )
}
