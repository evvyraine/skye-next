import { useState } from "react"
import {
  CircleHelp,
  LogOut,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Search,
  Settings,
  Trash2,
} from "lucide-react"
import { motion, AnimatePresence } from "motion/react"
import { toast } from "sonner"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { ProjectIcon } from "@/components/project-icon"
import { formatWhen, initials } from "@/lib/api"
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

  return (
    <div className="flex h-full min-h-0 flex-col bg-sidebar text-sidebar-foreground">
      <header className="flex items-center gap-2 px-3 pt-[max(0.75rem,env(safe-area-inset-top))] pb-3">
        <MobileProfileDrawer user={user} onLogout={onLogout} />
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id="project-search"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="Search projects"
            aria-label="Search projects"
            className="h-11 rounded-full border-transparent bg-background pr-4 pl-9 text-base shadow-sm sm:text-sm"
          />
        </div>
        <Button
          variant="outline"
          size="icon-lg"
          className="rounded-full bg-background shadow-sm active:scale-[0.96]"
          onClick={onCreate}
          aria-label="New project"
        >
          <Plus />
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-2">
        <AnimatePresence initial={false}>
          {filtered.map((project) => (
            <motion.div
              key={project.id}
              layout
              initial={{ opacity: 0, y: 8, filter: "blur(4px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              exit={{ opacity: 0, y: -6, filter: "blur(4px)" }}
              transition={{ duration: 0.2, ease: "easeOut" }}
            >
              <ProjectRow
                project={project}
                selected={selectedId === project.id}
                onSelect={() => onSelect(project.id)}
                onPin={() => onPin(project.id)}
                onEdit={() => onEdit(project.id)}
                onDelete={() => onDelete(project.id)}
              />
            </motion.div>
          ))}
        </AnimatePresence>
        {filtered.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-muted-foreground">
            No projects match “{query}”.
          </p>
        ) : null}
      </div>
      <ProfileMenu user={user} onLogout={onLogout} />
    </div>
  )
}

function ProjectRow({
  project,
  selected,
  onSelect,
  onPin,
  onEdit,
  onDelete,
}: {
  project: Project
  selected: boolean
  onSelect: () => void
  onPin: () => void
  onEdit: () => void
  onDelete: () => void
}) {
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div
      className={cn(
        "group/project my-1 flex min-w-0 items-center rounded-3xl transition-colors",
        selected ? "bg-sidebar-accent" : "hover:bg-sidebar-accent/60"
      )}
      onContextMenu={(event) => {
        event.preventDefault()
        setMenuOpen(true)
      }}
    >
      <button
        type="button"
        className="flex min-w-0 flex-1 items-center gap-3 rounded-3xl px-3 py-3 text-start outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring"
        onClick={onSelect}
      >
        <ProjectIcon icon={project.icon} color={project.color} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate font-medium">{project.name}</span>
            {project.pinned ? (
              <Pin
                className="size-3 text-muted-foreground"
                aria-label="Pinned"
              />
            ) : null}
          </span>
          <span className="block truncate text-sm text-muted-foreground">
            {project.last_message_preview || "No messages yet"}
          </span>
        </span>
        <span className="shrink-0 self-start text-xs text-muted-foreground tabular-nums">
          {formatWhen(project.last_message_at)}
        </span>
      </button>
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenuTrigger
          render={
            <Button
              variant="ghost"
              size="icon"
              className="mr-2 rounded-full opacity-0 transition-opacity group-hover/project:opacity-100 focus-visible:opacity-100 data-[popup-open]:opacity-100"
              aria-label={`Project actions for ${project.name}`}
            />
          }
        >
          <MoreHorizontal />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-52 rounded-2xl p-1.5">
          <DropdownMenuGroup>
            <DropdownMenuItem
              className="rounded-xl px-2.5 py-2"
              onClick={onPin}
            >
              {project.pinned ? <PinOff /> : <Pin />}
              {project.pinned ? "Unpin" : "Pin"}
            </DropdownMenuItem>
            <DropdownMenuItem
              className="rounded-xl px-2.5 py-2"
              onClick={onEdit}
            >
              <Pencil />
              Edit project
            </DropdownMenuItem>
          </DropdownMenuGroup>
          {project.deletable ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuItem
                  variant="destructive"
                  className="rounded-xl px-2.5 py-2"
                  onClick={onDelete}
                >
                  <Trash2 />
                  Delete project
                </DropdownMenuItem>
              </DropdownMenuGroup>
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

function ProfileMenu({
  user,
  onLogout,
}: {
  user: User | null
  onLogout: () => void
}) {
  return (
    <div className="hidden border-t border-sidebar-border p-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] md:block">
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <button
              type="button"
              className="flex w-full items-center gap-3 rounded-2xl p-2 text-start transition-colors outline-none hover:bg-sidebar-accent focus-visible:ring-2 focus-visible:ring-sidebar-ring"
            />
          }
        >
          <Avatar className="size-10">
            <AvatarFallback>{initials(user)}</AvatarFallback>
          </Avatar>
          <span className="min-w-0 flex-1 truncate font-medium">
            {user?.name || "Skye user"}
          </span>
          <MoreHorizontal className="size-4 text-muted-foreground" />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          side="top"
          align="start"
          className="w-64 rounded-2xl p-1.5"
        >
          <DropdownMenuGroup>
            <DropdownMenuItem
              className="rounded-xl px-2.5 py-2"
              onClick={() => toast("Profile settings are coming soon.")}
            >
              <Settings />
              Settings
            </DropdownMenuItem>
            <DropdownMenuItem
              className="rounded-xl px-2.5 py-2"
              onClick={() =>
                window.open(
                  "https://ai.skye-bot.com/",
                  "_blank",
                  "noopener,noreferrer"
                )
              }
            >
              <CircleHelp />
              Help Center
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem
              className="rounded-xl px-2.5 py-2"
              onClick={onLogout}
            >
              <LogOut />
              Log out
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

function MobileProfileDrawer({
  user,
  onLogout,
}: {
  user: User | null
  onLogout: () => void
}) {
  const [open, setOpen] = useState(false)

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        render={
          <button
            type="button"
            className="flex size-11 shrink-0 items-center justify-center rounded-full bg-background shadow-sm ring-1 ring-foreground/10 outline-none transition-transform active:scale-[0.96] focus-visible:ring-2 focus-visible:ring-ring md:hidden"
            aria-label={`Open profile for ${user?.name || "Skye user"}`}
          />
        }
      >
        <Avatar className="size-9">
          <AvatarFallback>{initials(user)}</AvatarFallback>
        </Avatar>
      </SheetTrigger>
      <SheetContent
        side="bottom"
        className="rounded-t-3xl border-t bg-popover/95 px-2 pt-2 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur-xl md:hidden"
      >
        <SheetHeader className="flex-row items-center gap-3 px-3 pt-3 pb-2">
          <Avatar className="size-11">
            <AvatarFallback>{initials(user)}</AvatarFallback>
          </Avatar>
          <SheetTitle className="truncate text-lg">
            {user?.name || "Skye user"}
          </SheetTitle>
        </SheetHeader>
        <div className="flex flex-col gap-1 pb-1">
          <Button
            variant="ghost"
            className="h-12 justify-start rounded-2xl px-3 text-base"
            onClick={() => toast("Profile settings are coming soon.")}
          >
            <Settings data-icon="inline-start" />
            Settings
          </Button>
          <Button
            variant="ghost"
            className="h-12 justify-start rounded-2xl px-3 text-base"
            onClick={() =>
              window.open(
                "https://ai.skye-bot.com/",
                "_blank",
                "noopener,noreferrer"
              )
            }
          >
            <CircleHelp data-icon="inline-start" />
            Help Center
          </Button>
          <Button
            variant="ghost"
            className="h-12 justify-start rounded-2xl px-3 text-base"
            onClick={() => {
              setOpen(false)
              onLogout()
            }}
          >
            <LogOut data-icon="inline-start" />
            Log out
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  )
}
