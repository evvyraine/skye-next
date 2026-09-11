import { useState } from "react"
import {
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Search,
  Trash2,
} from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import {
  Button,
  DropdownMenu,
  EmptyState,
  Input,
} from "sunkit-ui"
import { ProfileMenu, ProfileTrigger } from "@/components/profile-menu"
import { ProjectIcon } from "@/components/project-icon"
import { SkyeLogo } from "@/components/skye-logo"
import { SoundToggle } from "@/components/sound-toggle"
import { ThemeToggle } from "@/components/theme-toggle"
import { formatWhen } from "@/lib/api"
import type { Project, User } from "@/lib/types"
import { cn } from "@/lib/utils"

export function ProjectList({
  user,
  projects,
  selectedId,
  query,
  onQuery,
  onSelect,
  onCreate,
  onPin,
  onEdit,
  onDelete,
  onLogout,
}: {
  user: User | null
  projects: Project[]
  selectedId: string | null
  query: string
  onQuery: (value: string) => void
  onSelect: (id: string) => void
  onCreate: () => void
  onPin: (id: string) => void
  onEdit: (id: string) => void
  onDelete: (id: string) => void
  onLogout: () => void
}) {
  const filtered = projects.filter((project) => {
    if (!query.trim()) {
      return true
    }
    const haystack =
      `${project.name} ${project.last_message_preview}`.toLowerCase()
    return haystack.includes(query.trim().toLowerCase())
  })

  const [openMenuId, setOpenMenuId] = useState<string | null>(null)

  return (
    <div className="flex h-full min-h-0 flex-col font-sans">
      <header className="shrink-0 px-4 pt-[max(0.85rem,env(safe-area-inset-top))] pb-3">
        <div className="flex items-center gap-1.5">
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <span className="md:hidden">
              <ProfileTrigger user={user} onLogout={onLogout} />
            </span>
            <SkyeLogo className="h-7" />
          </div>
          <SoundToggle />
          <ThemeToggle />
          <Button
            variant="solid"
            color="lavender"
            size="icon-only"
            icon="only"
            iconOnly={<Plus />}
            radius={999}
            className="h-11 w-11"
            onClick={onCreate}
            aria-label="New project"
          />
        </div>

        <div className="mt-3">
          <Input
            id="project-search"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="Search projects"
            aria-label="Search projects"
            variant="filled"
            radius={999}
            leftAdornment={
              <Search
                className="size-4 text-[var(--sk-text-muted)]"
                aria-hidden="true"
              />
            }
          />
        </div>
      </header>

      <div className="sk-scrollbar min-h-0 flex-1 overflow-y-auto px-2">
        <AnimatePresence initial={false}>
          {filtered.map((project) => (
            <motion.div
              key={project.id}
              layout="position"
              initial={{ opacity: 0, y: 8, filter: "blur(4px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              exit={{ opacity: 0, y: -6, filter: "blur(4px)" }}
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="relative"
              style={{ zIndex: openMenuId === project.id ? 40 : undefined }}
            >
              <ProjectRow
                project={project}
                selected={selectedId === project.id}
                menuOpen={openMenuId === project.id}
                onMenuOpenChange={(open) =>
                  setOpenMenuId(open ? project.id : null)
                }
                onSelect={() => onSelect(project.id)}
                onPin={() => onPin(project.id)}
                onEdit={() => onEdit(project.id)}
                onDelete={() => onDelete(project.id)}
              />
            </motion.div>
          ))}
        </AnimatePresence>
        {filtered.length === 0 ? (
          <div className="py-10">
            <EmptyState
              size="sm"
              tone="lavender"
              icon={<Search className="size-6" aria-hidden="true" />}
              title="Nothing here yet"
              description={
                query.trim()
                  ? `No projects match “${query.trim()}”.`
                  : "Create your first project to begin."
              }
            />
          </div>
        ) : null}
      </div>

      <div className="hidden border-t border-[var(--sk-border-subtle)] p-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] md:block">
        <ProfileMenu user={user} onLogout={onLogout} />
      </div>
    </div>
  )
}

function ProjectRow({
  project,
  selected,
  menuOpen,
  onMenuOpenChange,
  onSelect,
  onPin,
  onEdit,
  onDelete,
}: {
  project: Project
  selected: boolean
  menuOpen: boolean
  onMenuOpenChange: (open: boolean) => void
  onSelect: () => void
  onPin: () => void
  onEdit: () => void
  onDelete: () => void
}) {
  return (
    <div
      className={cn(
        "group/project relative my-1 flex min-w-0 items-center rounded-2xl transition-colors",
        selected
          ? "bg-[var(--sk-surface)] shadow-[inset_0_0_0_1px_var(--sk-border)]"
          : "hover:bg-[var(--sk-surface-filled)]"
      )}
      onContextMenu={(event) => {
        event.preventDefault()
        onMenuOpenChange(true)
      }}
    >
      <button
        type="button"
        className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 rounded-2xl px-2.5 py-2.5 text-start outline-none focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/50"
        onClick={onSelect}
        aria-current={selected ? "page" : undefined}
      >
        <ProjectIcon icon={project.icon} color={project.color} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-[14px] font-semibold">
              {project.name}
            </span>
            {project.pinned ? (
              <span className="inline-flex shrink-0 items-center gap-1 text-[10px] font-medium text-[var(--sk-accent)]">
                <Pin className="size-3" aria-hidden="true" />
                <span className="sr-only">Pinned</span>
              </span>
            ) : null}
          </span>
          <span className="mt-0.5 block truncate text-[12.5px] text-[var(--sk-text-desc)]">
            {project.last_message_preview || "No messages yet"}
          </span>
        </span>
        <span className="shrink-0 self-start text-[11px] text-[var(--sk-text-muted)] tabular-nums">
          {formatWhen(project.last_message_at)}
        </span>
      </button>

      <DropdownMenu
        open={menuOpen}
        onOpenChange={onMenuOpenChange}
        align="end"
        side="bottom"
        contentClassName="w-52"
        trigger={
          <Button
            variant="ghost"
            color="neutral"
            size="icon-only"
            icon="only"
            iconOnly={<MoreHorizontal />}
            radius={999}
            className="mr-1.5 h-9 w-9 opacity-100 outline-none md:opacity-0 md:transition-opacity md:group-hover/project:opacity-100 focus-visible:opacity-100"
            aria-label={`Project actions for ${project.name}`}
          />
        }
      >
        <DropdownMenu.Item
          icon={project.pinned ? <PinOff className="size-4" /> : <Pin className="size-4" />}
          onSelect={onPin}
        >
          {project.pinned ? "Unpin" : "Pin"}
        </DropdownMenu.Item>
        <DropdownMenu.Item
          icon={<Pencil className="size-4" />}
          onSelect={onEdit}
        >
          Edit project
        </DropdownMenu.Item>
        {project.deletable ? (
          <>
            <DropdownMenu.Separator />
            <DropdownMenu.Item
              destructive
              icon={<Trash2 className="size-4" />}
              onSelect={onDelete}
            >
              Delete project
            </DropdownMenu.Item>
          </>
        ) : null}
      </DropdownMenu>
    </div>
  )
}
