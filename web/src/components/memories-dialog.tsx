import { useEffect, useMemo, useState } from "react"
import {
  CircleStackIcon,
  MagnifyingGlassIcon,
  TrashIcon,
} from "@heroicons/react/24/outline"
import {
  Button,
  Dialog,
  EmptyState,
  Input,
  ScrollArea,
  Sheet,
  Spinner,
  toast,
} from "sunkit-ui"
import { useIsMobile } from "@/hooks/use-mobile"
import { clearMemories, deleteMemory, listMemories } from "@/lib/api"
import type { Memory, MemoryCategory } from "@/lib/types"
import { cn } from "@/lib/utils"

const CATEGORY_ORDER: MemoryCategory[] = [
  "preference",
  "personal",
  "instruction",
  "project",
  "other",
]

const CATEGORY_LABELS: Record<MemoryCategory, string> = {
  preference: "Preferences",
  personal: "Personal",
  instruction: "Instructions",
  project: "Projects",
  other: "Other",
}

export function MemoriesDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const isMobile = useIsMobile()
  // `null` means "not loaded yet"; the list is cached between openings so the
  // dialog can show the last known memories while it refreshes.
  const [memories, setMemories] = useState<Memory[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [query, setQuery] = useState("")
  const [busyId, setBusyId] = useState<number | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const [clearing, setClearing] = useState(false)

  useEffect(() => {
    if (!open) {
      return
    }
    let active = true
    listMemories()
      .then((items) => {
        if (active) {
          setMemories(items)
          setFailed(false)
        }
      })
      .catch(() => {
        if (active) {
          setFailed(true)
        }
      })
    return () => {
      active = false
    }
  }, [open])

  function handleOpenChange(next: boolean) {
    if (!next) {
      setQuery("")
      setConfirmClear(false)
    }
    onOpenChange(next)
  }

  const needle = query.trim().toLowerCase()
  const groups = useMemo(() => {
    const items = memories ?? []
    const filtered = needle
      ? items.filter((memory) => memory.content.toLowerCase().includes(needle))
      : items
    return CATEGORY_ORDER.map((category) => ({
      category,
      items: filtered.filter((memory) => memory.category === category),
    })).filter((group) => group.items.length > 0)
  }, [memories, needle])

  async function remove(memory: Memory) {
    const previous = memories ?? []
    setBusyId(memory.id)
    setMemories(previous.filter((item) => item.id !== memory.id))
    try {
      await deleteMemory(memory.id)
    } catch (error) {
      setMemories(previous)
      toast.error({
        title: "Couldn't delete that memory",
        description:
          (error instanceof Error && error.message) || "Try again in a moment.",
      })
    } finally {
      setBusyId(null)
    }
  }

  async function removeAll() {
    setClearing(true)
    try {
      await clearMemories()
      setMemories([])
      setConfirmClear(false)
      toast.success({ title: "Memories deleted" })
    } catch (error) {
      toast.error({
        title: "Couldn't delete your memories",
        description:
          (error instanceof Error && error.message) || "Try again in a moment.",
      })
    } finally {
      setClearing(false)
    }
  }

  const loading = memories === null && !failed
  const count = memories?.length ?? 0

  const body = (
    <div className="flex flex-col gap-4">
      <Input
        id="memory-search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Search memories"
        aria-label="Search memories"
        variant="filled"
        radius={999}
        leftAdornment={
          <MagnifyingGlassIcon
            className="size-4 text-[var(--sk-text-muted)]"
            aria-hidden="true"
          />
        }
      />

      {loading ? (
        <div className="flex justify-center py-10">
          <Spinner size="lg" label="Loading memories" />
        </div>
      ) : failed && count === 0 ? (
        <EmptyState
          size="sm"
          tone="rose"
          icon={<CircleStackIcon className="size-6" aria-hidden="true" />}
          title="Couldn't load memories"
          description="Close this and try again in a moment."
        />
      ) : count === 0 ? (
        <EmptyState
          size="sm"
          tone="lavender"
          icon={<CircleStackIcon className="size-6" aria-hidden="true" />}
          title="No memories yet"
          description="Ask Skye to remember something and it will show up here."
        />
      ) : groups.length === 0 ? (
        <EmptyState
          size="sm"
          tone="lavender"
          icon={<MagnifyingGlassIcon className="size-6" aria-hidden="true" />}
          title="No matches"
          description={`Nothing matches “${query.trim()}”.`}
        />
      ) : (
        <ScrollArea className="max-h-[52vh]" fade>
          <div className="flex flex-col gap-5 pr-1">
            {groups.map(({ category, items }) => (
              <section key={category}>
                <div className="mb-2 flex items-center gap-2" aria-hidden="true">
                  <span className="text-[10.5px] font-semibold tracking-[0.08em] text-[var(--sk-text-muted)] uppercase">
                    {CATEGORY_LABELS[category]}
                  </span>
                  <span className="h-px flex-1 bg-[var(--sk-border-subtle)]" />
                  <span className="text-[10.5px] text-[var(--sk-text-muted)] tabular-nums">
                    {items.length}
                  </span>
                </div>
                <ul className="flex flex-col gap-2">
                  {items.map((memory) => (
                    <li
                      key={memory.id}
                      className="flex items-start gap-2 rounded-2xl border border-[var(--sk-border-subtle)] bg-[var(--sk-surface-filled)] p-3"
                    >
                      <p className="min-w-0 flex-1 text-[13.5px] leading-relaxed break-words">
                        {memory.content}
                      </p>
                      <Button
                        variant="ghost"
                        color="rose"
                        size="icon-only"
                        icon="only"
                        iconOnly={
                          busyId === memory.id ? <Spinner size="xs" /> : <TrashIcon />
                        }
                        radius={999}
                        className="h-8 w-8 shrink-0"
                        aria-label={`Delete memory: ${memory.content}`}
                        disabled={busyId === memory.id}
                        onClick={() => void remove(memory)}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        </ScrollArea>
      )}

      {count > 0 ? (
        <div className="border-t border-[var(--sk-border-subtle)] pt-3">
          {confirmClear ? (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-[13px] text-[var(--sk-text-desc)]">
                Delete every memory?
              </span>
              <div className="ml-auto flex gap-2">
                <Button
                  variant="ghost"
                  color="neutral"
                  radius={999}
                  onClick={() => setConfirmClear(false)}
                >
                  Cancel
                </Button>
                <Button
                  variant="solid"
                  color="rose"
                  radius={999}
                  disabled={clearing}
                  onClick={() => void removeAll()}
                >
                  {clearing ? "Deleting…" : "Delete all"}
                </Button>
              </div>
            </div>
          ) : (
            <Button
              variant="ghost"
              color="rose"
              radius={999}
              icon="left"
              iconLeft={<TrashIcon />}
              className={cn("h-10", isMobile && "w-full")}
              onClick={() => setConfirmClear(true)}
            >
              Delete all memories
            </Button>
          )}
        </div>
      ) : null}
    </div>
  )

  if (isMobile) {
    return (
      <Sheet
        open={open}
        onOpenChange={handleOpenChange}
        side="bottom"
        size="lg"
        title="Memories"
        description="What Skye remembers about you."
        tone="lavender"
      >
        {body}
      </Sheet>
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={handleOpenChange}
      title="Memories"
      description="What Skye remembers about you."
      size="default"
      tone="lavender"
      radius={28}
    >
      {body}
    </Dialog>
  )
}
