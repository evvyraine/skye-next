import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { ProjectIcon } from "@/components/project-icon"
import { useIsMobile } from "@/hooks/use-mobile"
import { COLOR_ORDER, ICON_ORDER, PROJECT_COLORS } from "@/lib/icons"
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
  const [color, setColor] = useState("zinc")
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
      setColor("zinc")
      onOpenChange(false)
    } finally {
      setPending(false)
    }
  }

  const form = (
    <>
      <div className="flex justify-center">
        <ProjectIcon icon={icon} color={color} size="lg" />
      </div>
      <FieldGroup>
        <Field>
          <FieldLabel htmlFor="project-name" className="sr-only">
            Name
          </FieldLabel>
          <Input
            id="project-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Name your project"
            className="h-12 rounded-2xl bg-muted text-base"
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                void submit()
              }
            }}
          />
        </Field>
      </FieldGroup>
      <div className="flex flex-col gap-4 rounded-2xl bg-muted p-4">
        <div className="flex flex-wrap gap-2">
          {COLOR_ORDER.map((item) => (
            <button
              key={item}
              type="button"
              aria-label={item}
              className={cn(
                "size-8 rounded-full ring-offset-2 ring-offset-muted",
                PROJECT_COLORS[item],
                color === item && "ring-2 ring-foreground"
              )}
              onClick={() => setColor(item)}
            />
          ))}
        </div>
        <div className="h-px bg-border" />
        <div className="flex flex-wrap gap-2">
          {ICON_ORDER.map((item) => (
            <button
              key={item}
              type="button"
              aria-label={item}
              className={cn(
                "rounded-xl p-1",
                icon === item && "ring-2 ring-foreground"
              )}
              onClick={() => setIcon(item)}
            >
              <ProjectIcon
                icon={item}
                color="zinc"
                size="sm"
                className="rounded-xl"
              />
            </button>
          ))}
        </div>
      </div>
      <Button
        className="h-12 rounded-full"
        disabled={!name.trim() || pending}
        onClick={() => void submit()}
      >
        Create
      </Button>
    </>
  )

  if (isMobile) {
    return (
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent
          side="bottom"
          className="max-h-[90dvh] gap-6 overflow-y-auto rounded-t-3xl px-4 pt-4 pb-[max(1rem,env(safe-area-inset-bottom))]"
        >
          <SheetHeader>
            <SheetTitle className="text-center">Create new project</SheetTitle>
          </SheetHeader>
          {form}
        </SheetContent>
      </Sheet>
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md gap-6 rounded-3xl">
        <DialogHeader>
          <DialogTitle className="text-center">Create new project</DialogTitle>
        </DialogHeader>
        {form}
      </DialogContent>
    </Dialog>
  )
}
