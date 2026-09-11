import { useState } from "react"
import {
  EllipsisHorizontalIcon,
  MagnifyingGlassIcon,
  MapPinIcon,
  PencilIcon,
  PlusIcon,
  TrashIcon,
} from "@heroicons/react/24/outline"
import { AnimatePresence, motion } from "motion/react"
import { Button, DropdownMenu, EmptyState, Input } from "sunkit-ui"
import { ProfileMenu, ProfileTrigger } from "@/components/profile-menu"
import { ProjectIcon } from "@/components/project-icon"
import { SkyeLogo } from "@/components/skye-logo"
import { SoundToggle } from "@/components/sound-toggle"
import { ThemeToggle } from "@/components/theme-toggle"
import { formatWhen } from "@/lib/api"
import type { Project, User } from "@/lib/types"
import { cn } from "@/lib/utils"

const rowMotion = {
  layout: "position",
  initial: { opacity: 0, y: 8, filter: "blur(4px)" },
  animate: { opacity: 1, y: 0, filter: "blur(0px)" },
  exit: { opacity: 0, y: -6, filter: "blur(4px)" },
  transition: { duration: 0.2, ease: "easeOut" },
} as const

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
  onOpenMemories,
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
  onOpenMemories: () => void
}) {
  const needle = query.trim().toLowerCase()
  const matches = (project: Project) =>
    !needle ||
    `${project.name} ${project.last_message_preview}`.toLowerCase().includes(needle)

  // The inbox is the one project Skye always owns. It lives above the rest and
  // keeps its place no matter how the others are pinned or sorted.
  const inbox = projects.find((project) => project.kind === "skye" && matches(project)) ?? null
  const rest = projects.filter((project) => project.kind !== "skye" && matches(project))
  const isEmpty = !inbox && rest.length === 0

  const [openMenuId, setOpenMenuId] = useState<string | null>(null)

  const renderRow = (project: Project) => (
    <motion.div
      key={project.id}
      {...rowMotion}
      className="relative"
      style={{ zIndex: openMenuId === project.id ? 40 : undefined }}
    >
      <ProjectRow
        project={project}
        selected={selectedId === project.id}
        menuOpen={openMenuId === project.id}
        onMenuOpenChange={(open) => setOpenMenuId(open ? project.id : null)}
        onSelect={() => onSelect(project.id)}
        onPin={() => onPin(project.id)}
        onEdit={() => onEdit(project.id)}
        onDelete={() => onDelete(project.id)}
      />
    </motion.div>
  )

  return (
    <div className="flex h-full min-h-0 flex-col font-sans">
      <header className="shrink-0 px-4 pt-[max(0.85rem,env(safe-area-inset-top))] pb-3">
        <div className="flex items-center gap-1.5">
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <span className="md:hidden">
              <ProfileTrigger user={user} onLogout={onLogout} onOpenMemories={onOpenMemories} />
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
            iconOnly={<PlusIcon />}
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
              <MagnifyingGlassIcon
                className="size-4 text-[var(--sk-text-muted)]"
                aria-hidden="true"
              />
            }
          />
        </div>
      </header>

      <div className="sk-scrollbar relative min-h-0 flex-1 overflow-y-auto px-2">
        <AnimatePresence initial={false}>{inbox ? renderRow(inbox) : null}</AnimatePresence>

        {rest.length ? (
          <>
            <div className="mt-2 mb-1 flex items-center gap-2 px-3" aria-hidden="true">
              <span className="text-[10.5px] font-semibold tracking-[0.08em] text-[var(--sk-text-muted)] uppercase">
                Projects
              </span>
              <span className="h-px flex-1 bg-[var(--sk-border-subtle)]" />
            </div>
            <AnimatePresence initial={false}>{rest.map(renderRow)}</AnimatePresence>
          </>
        ) : null}

        <AnimatePresence>
          {isEmpty ? (
            <motion.div
              key="empty"
              initial={{ opacity: 0, scale: 0.97 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.18, ease: "easeOut" }}
              className="pointer-events-none absolute inset-0 flex items-center justify-center px-4 pb-16"
            >
              <EmptyState
                size="sm"
                tone="lavender"
                icon={<MagnifyingGlassIcon className="size-6" aria-hidden="true" />}
                title="Nothing here yet"
                description={
                  needle
                    ? `No projects match “${query.trim()}”.`
                    : "Create your first project to begin."
                }
              />
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>

      <div className="hidden border-t border-[var(--sk-border-subtle)] p-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] md:block">
        <ProfileMenu user={user} onLogout={onLogout} onOpenMemories={onOpenMemories} />
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
  const isInbox = project.kind === "skye"
  return (
    <div
      className={cn(
        "group/project relative my-1 flex items-center rounded-2xl transition-colors",
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
        className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 rounded-2xl py-2.5 pr-1 pl-2.5 text-start outline-none focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/50"
        onClick={onSelect}
        aria-current={selected ? "page" : undefined}
      >
        <ProjectIcon icon={project.icon} color={project.color} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-[14px] font-semibold">{project.name}</span>
            {project.pinned && !isInbox ? (
              <MapPinIcon
                className="size-3 shrink-0 text-[var(--sk-accent)]"
                aria-hidden="true"
              />
            ) : null}
          </span>
          <span className="mt-0.5 block truncate text-[12.5px] text-[var(--sk-text-desc)]">
            {project.last_message_preview || "No messages yet"}
          </span>
        </span>
      </button>

      <div className="flex shrink-0 flex-col items-end justify-between gap-1 self-stretch py-2.5 pr-1.5">
        <span className="pl-2 text-[11px] text-[var(--sk-text-muted)] tabular-nums">
          {formatWhen(project.last_message_at)}
        </span>
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
              iconOnly={<EllipsisHorizontalIcon />}
              radius={999}
              className="h-8 w-8 opacity-100 outline-none md:opacity-0 md:transition-opacity md:group-hover/project:opacity-100 focus-visible:opacity-100"
              aria-label={`Project actions for ${project.name}`}
            />
          }
        >
          {isInbox ? null : (
            <DropdownMenu.Item
              icon={<MapPinIcon className="size-4" aria-hidden="true" />}
              onSelect={onPin}
            >
              {project.pinned ? "Unpin" : "Pin"}
            </DropdownMenu.Item>
          )}
          <DropdownMenu.Item
            icon={<PencilIcon className="size-4" />}
            onSelect={onEdit}
          >
            Edit project
          </DropdownMenu.Item>
          {project.deletable ? (
            <>
              <DropdownMenu.Separator />
              <DropdownMenu.Item
                destructive
                icon={<TrashIcon className="size-4" />}
                onSelect={onDelete}
              >
                Delete project
              </DropdownMenu.Item>
            </>
          ) : null}
        </DropdownMenu>
      </div>
    </div>
  )
}
