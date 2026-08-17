import { useCallback, useEffect, useState } from "react"
import { AnimatePresence, motion } from "motion/react"
import { Group, Panel, Separator as ResizeHandle } from "react-resizable-panels"
import { toast } from "sonner"
import { ChatView } from "@/components/chat-view"
import { CreateProjectDialog } from "@/components/create-project"
import { ProjectList } from "@/components/project-list"
import { SettingsPanel } from "@/components/settings-panel"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import {
  createProject,
  deleteProject,
  getMe,
  listMessages,
  listProjects,
  login,
  logout,
  pinProject,
  resetProject,
  search,
  updateProject,
} from "@/lib/api"
import type { ChatFile, ChatMessage, Me, Project } from "@/lib/types"
import { useIsMobile } from "@/hooks/use-mobile"

export function App() {
  const [me, setMe] = useState<Me | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [files, setFiles] = useState<ChatFile[]>([])
  const [query, setQuery] = useState("")
  const [creating, setCreating] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null)
  const isMobile = useIsMobile()
  const [isWide, setIsWide] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(min-width: 64rem)").matches
  )
  const denied =
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).has("denied")

  const selected = projects.find((item) => item.id === selectedId) ?? null

  const refresh = useCallback(async () => {
    const next = await listProjects()
    setProjects(next)
    return next
  }, [])

  useEffect(() => {
    void getMe()
      .then(setMe)
      .catch((error: unknown) => {
        toast.error(
          error instanceof Error ? error.message : "Could not load Skye."
        )
      })
  }, [])

  useEffect(() => {
    const media = window.matchMedia("(min-width: 64rem)")
    const update = () => setIsWide(media.matches)
    media.addEventListener("change", update)
    return () => media.removeEventListener("change", update)
  }, [])

  useEffect(() => {
    if (!me?.user || !me.allowed) {
      return
    }
    void listProjects()
      .then(setProjects)
      .catch((error: unknown) => {
        toast.error(
          error instanceof Error ? error.message : "Could not load projects."
        )
      })
  }, [me])

  useEffect(() => {
    if (!selectedId || !me?.allowed) {
      return
    }
    void listMessages(selectedId)
      .then((payload) => {
        setMessages(payload.messages)
        setFiles(payload.files)
      })
      .catch((error: unknown) => {
        toast.error(
          error instanceof Error ? error.message : "Could not load this chat."
        )
      })
  }, [selectedId, me])

  useEffect(() => {
    if (!query.trim() || !me?.allowed) {
      return
    }
    const handle = window.setTimeout(() => {
      void search(query).then((payload) => {
        if (payload.projects.length) {
          setProjects((current) => {
            const extras = payload.projects.filter(
              (item) => !current.some((existing) => existing.id === item.id)
            )
            return extras.length ? [...current, ...extras] : current
          })
        }
      })
    }, 250)
    return () => window.clearTimeout(handle)
  }, [query, me])

  async function persist(patch: Partial<Project>) {
    if (!selected) {
      return
    }
    const updated = await updateProject(selected.id, patch)
    setProjects((current) =>
      current.map((item) => (item.id === updated.id ? updated : item))
    )
  }

  function selectProject(id: string) {
    if (id !== selectedId) {
      setMessages([])
      setFiles([])
    }
    setSelectedId(id)
    setSettingsOpen(false)
  }

  async function removeProject(project: Project) {
    try {
      await deleteProject(project.id)
      if (selectedId === project.id) {
        setSelectedId(null)
        setSettingsOpen(false)
        setMessages([])
        setFiles([])
      }
      setDeleteTarget(null)
      await refresh()
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Could not delete that project."
      )
    }
  }

  if (denied) {
    return (
      <Gate
        title="This bot is private"
        action="Back to Telegram"
        onAction={() => window.location.assign("https://t.me/skye_ai_bot")}
      />
    )
  }

  if (!me) {
    return <Gate title="Skye" />
  }

  if (!me.user) {
    return (
      <Gate
        title="Skye"
        body="Sign in with Telegram to continue."
        action="Continue with Telegram"
        onAction={login}
      />
    )
  }

  if (!me.allowed) {
    return (
      <Gate
        title="This bot is private"
        action="Sign out"
        onAction={() =>
          void logout().then(() => setMe({ user: null, allowed: false }))
        }
      />
    )
  }

  const user = me.user

  function renderList() {
    return (
      <ProjectList
        user={user}
        projects={projects}
        selectedId={selectedId}
        query={query}
        onQuery={setQuery}
        onSelect={selectProject}
        onCreate={() => setCreating(true)}
        onPin={(id) => {
          void pinProject(id)
            .then((updated) => {
              setProjects((current) =>
                current.map((item) => (item.id === updated.id ? updated : item))
              )
            })
            .catch((error: unknown) =>
              toast.error(
                error instanceof Error ? error.message : "Could not pin that."
              )
            )
        }}
        onEdit={(id) => {
          selectProject(id)
          setSettingsOpen(true)
        }}
        onDelete={(id) => {
          const project = projects.find((item) => item.id === id)
          if (project) {
            setDeleteTarget(project)
          }
        }}
        onLogout={() => {
          void logout().then(() => setMe({ user: null, allowed: false }))
        }}
      />
    )
  }

  function renderChat() {
    if (!selected) {
      return (
        <div className="flex h-full items-center justify-center px-6 text-center text-sm text-muted-foreground">
          Choose a project to start chatting.
        </div>
      )
    }
    return (
      <ChatView
        project={selected}
        messages={messages}
        files={files}
        onMessages={(nextMessages, nextFiles) => {
          setMessages(nextMessages)
          setFiles(nextFiles)
          void refresh()
        }}
        onBack={() => {
          setSelectedId(null)
          setSettingsOpen(false)
        }}
        onOpenSettings={() => setSettingsOpen((current) => !current)}
      />
    )
  }

  const settings = selected ? (
    <SettingsPanel
      project={selected}
      onChange={(patch) => void persist(patch)}
      onReset={() => {
        void resetProject(selected.id)
          .then(() => {
            setMessages([])
            setFiles([])
            return refresh()
          })
          .catch((error: unknown) =>
            toast.error(
              error instanceof Error ? error.message : "Could not reset."
            )
          )
      }}
      onDelete={() => setDeleteTarget(selected)}
    />
  ) : null

  return (
    <TooltipProvider>
      <div className="h-dvh min-h-0 overflow-hidden bg-background">
        <div className="hidden h-full min-h-0 md:flex">
          <Group orientation="horizontal" className="min-w-0 flex-1">
            <Panel id="projects" defaultSize={320} minSize={260} maxSize={460}>
              {renderList()}
            </Panel>
            <ResizeHandle
              id="projects-resize"
              className="group relative w-px bg-border transition-colors outline-none focus-visible:bg-ring"
            >
              <span className="absolute inset-y-0 -left-2 w-4 cursor-col-resize group-focus-visible:ring-2 group-focus-visible:ring-ring/50" />
            </ResizeHandle>
            <Panel id="chat" minSize={420}>
              <AnimatePresence mode="wait" initial={false}>
                <motion.div
                  key={selected?.id ?? "empty"}
                  className="h-full min-h-0"
                  initial={{ opacity: 0, x: 8, filter: "blur(6px)" }}
                  animate={{ opacity: 1, x: 0, filter: "blur(0px)" }}
                  exit={{ opacity: 0, x: -8, filter: "blur(6px)" }}
                  transition={{ duration: 0.22, ease: "easeOut" }}
                >
                  {renderChat()}
                </motion.div>
              </AnimatePresence>
            </Panel>
          </Group>
          <AnimatePresence initial={false}>
            {selected && settingsOpen && isWide ? (
              <motion.aside
                key="project-settings"
                initial={{ width: 0, opacity: 0, filter: "blur(8px)" }}
                animate={{ width: 320, opacity: 1, filter: "blur(0px)" }}
                exit={{ width: 0, opacity: 0, filter: "blur(8px)" }}
                transition={{ duration: 0.24, ease: [0.2, 0, 0, 1] }}
                className="hidden shrink-0 overflow-hidden border-l lg:block"
              >
                <div className="h-full w-80">{settings}</div>
              </motion.aside>
            ) : null}
          </AnimatePresence>
        </div>
        <div className="h-full md:hidden">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={selected ? `mobile-chat-${selected.id}` : "mobile-projects"}
              className="h-full"
              initial={{
                opacity: 0,
                x: selected ? 20 : -20,
                filter: "blur(6px)",
              }}
              animate={{ opacity: 1, x: 0, filter: "blur(0px)" }}
              exit={{ opacity: 0, x: selected ? 20 : -20, filter: "blur(6px)" }}
              transition={{ duration: 0.22, ease: "easeOut" }}
            >
              {selected ? renderChat() : renderList()}
            </motion.div>
          </AnimatePresence>
        </div>
        <Sheet
          open={!isWide && settingsOpen && Boolean(selected)}
          onOpenChange={setSettingsOpen}
        >
          <SheetContent
            className="data-[side=bottom]:h-[90dvh] data-[side=bottom]:max-h-[90dvh] data-[side=bottom]:overflow-hidden data-[side=bottom]:rounded-t-3xl lg:hidden"
            side={isMobile ? "bottom" : "right"}
          >
            <SheetHeader>
              <SheetTitle>Settings</SheetTitle>
            </SheetHeader>
            {settings}
          </SheetContent>
        </Sheet>
        <CreateProjectDialog
          open={creating}
          onOpenChange={setCreating}
          onCreate={async (values) => {
            const project = await createProject(values)
            const next = await refresh()
            setSelectedId(project.id)
            if (!next.some((item) => item.id === project.id)) {
              setProjects((current) => [project, ...current])
            }
          }}
        />
        <Dialog
          open={Boolean(deleteTarget)}
          onOpenChange={(open) => !open && setDeleteTarget(null)}
        >
          <DialogContent className="rounded-3xl">
            <DialogHeader>
              <DialogTitle>Delete project?</DialogTitle>
              <DialogDescription>
                This permanently deletes “{deleteTarget?.name}” and its chat
                history.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter className="rounded-b-3xl">
              <Button variant="outline" onClick={() => setDeleteTarget(null)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={() => deleteTarget && void removeProject(deleteTarget)}
              >
                Delete project
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <Toaster />
      </div>
    </TooltipProvider>
  )
}

function Gate({
  title,
  body,
  action,
  onAction,
}: {
  title: string
  body?: string
  action?: string
  onAction?: () => void
}) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.985, filter: "blur(10px)" }}
      animate={{ opacity: 1, scale: 1, filter: "blur(0px)" }}
      exit={{ opacity: 0, scale: 0.985, filter: "blur(10px)" }}
      transition={{ duration: 0.3, ease: [0.2, 0, 0, 1] }}
      className="flex min-h-dvh flex-col items-center justify-center gap-4 px-6 pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] text-center"
    >
      <h1 className="text-2xl font-medium">{title}</h1>
      {body ? <p className="max-w-sm text-muted-foreground">{body}</p> : null}
      {action && onAction ? (
        <Button className="rounded-full" onClick={onAction}>
          {action}
        </Button>
      ) : null}
    </motion.div>
  )
}

export default App
