import { useCallback, useEffect, useRef, useState } from "react"
import { AnimatePresence, motion } from "motion/react"
import { Button, Dialog, Sheet, ThemeProvider, toast } from "sunkit-ui"
import { ChatView } from "@/components/chat-view"
import { CreateProjectDialog } from "@/components/create-project"
import { ProjectList } from "@/components/project-list"
import { SettingsPanel } from "@/components/settings-panel"
import { SkyeSign } from "@/components/skye-logo"
import { useTheme } from "@/components/theme-provider"
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
  const [resetConfirm, setResetConfirm] = useState(false)
  const isMobile = useIsMobile()
  const { resolvedTheme } = useTheme()
  const [isWide, setIsWide] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(min-width: 72rem)").matches
  )
  const denied =
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).has("denied")

  const selected = projects.find((item) => item.id === selectedId) ?? null

  const persistTimer = useRef<number | null>(null)
  const pendingPatch = useRef<Partial<Project>>({})

  const refresh = useCallback(async () => {
    const next = await listProjects()
    setProjects(next)
    return next
  }, [])

  useEffect(() => {
    void getMe()
      .then(setMe)
      .catch((error: unknown) => {
        toast.error({
          title: "Couldn't reach Skye",
          description:
            (error instanceof Error && error.message) ||
            "Check your connection and reload the page.",
        })
      })
  }, [])

  useEffect(() => {
    const media = window.matchMedia("(min-width: 72rem)")
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
        toast.error({
          title: "Couldn't load projects",
          description:
            (error instanceof Error && error.message) ||
            "Check your connection and try again.",
        })
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
        toast.error({
          title: "Couldn't load this chat",
          description:
            (error instanceof Error && error.message) ||
            "Select the project again to retry.",
        })
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

  // Flush any pending settings patch before the selected project changes.
  useEffect(() => {
    return () => {
      if (persistTimer.current) {
        window.clearTimeout(persistTimer.current)
      }
    }
  }, [selectedId])

  async function flushPatch(id: string) {
    const patch = pendingPatch.current
    pendingPatch.current = {}
    if (Object.keys(patch).length === 0) {
      return
    }
    try {
      const updated = await updateProject(id, patch)
      setProjects((current) =>
        current.map((item) => (item.id === updated.id ? updated : item))
      )
    } catch (error) {
      toast.error({
        title: "Couldn't save changes",
        description:
          (error instanceof Error && error.message) ||
          "Check your connection and try again.",
      })
    }
  }

  function persist(patch: Partial<Project>) {
    if (!selected) {
      return
    }
    const id = selected.id
    pendingPatch.current = { ...pendingPatch.current, ...patch }
    setProjects((current) =>
      current.map((item) =>
        item.id === id ? { ...item, ...patch } : item
      )
    )
    if (persistTimer.current) {
      window.clearTimeout(persistTimer.current)
    }
    persistTimer.current = window.setTimeout(() => {
      void flushPatch(id)
    }, 450)
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
      toast.error({
        title: "Couldn't delete that project",
        description:
          (error instanceof Error && error.message) || "Try again in a moment.",
      })
    }
  }

  async function resetChat(project: Project) {
    try {
      await resetProject(project.id)
      setMessages([])
      setFiles([])
      setResetConfirm(false)
      await refresh()
      toast.success({ title: "Chat reset" })
    } catch (error) {
      toast.error({
        title: "Couldn't reset this chat",
        description:
          (error instanceof Error && error.message) || "Try again in a moment.",
      })
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
    return <Gate title="Skye" loading />
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
              toast.error({
                title: "Couldn't update that pin",
                description:
                  (error instanceof Error && error.message) ||
                  "Try again in a moment.",
              })
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
        <div className="flex h-full items-center justify-center">
          <EmptyChat />
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
      onChange={(patch) => persist(patch)}
      onReset={() => setResetConfirm(true)}
      onDelete={() => setDeleteTarget(selected)}
    />
  ) : null

  return (
    <ThemeProvider dark={resolvedTheme === "dark"}>
      <div className="h-dvh min-h-0 overflow-hidden">
        <div className="hidden h-full min-h-0 md:flex">
          <aside className="h-full min-h-0 w-[300px] shrink-0 border-r border-[var(--sk-border-subtle)] bg-[var(--sk-surface)] backdrop-blur-xl lg:w-[340px]">
            {renderList()}
          </aside>
          <main className="relative h-full min-h-0 min-w-0 flex-1">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={selected?.id ?? "empty"}
                className="h-full min-h-0"
                initial={{ opacity: 0, y: 10, filter: "blur(6px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                exit={{ opacity: 0, y: -8, filter: "blur(6px)" }}
                transition={{ duration: 0.22, ease: "easeOut" }}
              >
                {renderChat()}
              </motion.div>
            </AnimatePresence>
          </main>
          <AnimatePresence initial={false}>
            {selected && settingsOpen && isWide ? (
              <motion.aside
                key="project-settings"
                initial={{ width: 0, opacity: 0, filter: "blur(8px)" }}
                animate={{ width: 340, opacity: 1, filter: "blur(0px)" }}
                exit={{ width: 0, opacity: 0, filter: "blur(8px)" }}
                transition={{ duration: 0.26, ease: [0.2, 0, 0, 1] }}
                className="hidden h-full min-h-0 shrink-0 overflow-hidden border-l border-[var(--sk-border-subtle)] bg-[var(--sk-surface)] backdrop-blur-xl lg:block"
              >
                <div className="h-full w-[340px]">{settings}</div>
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
          side={isMobile ? "bottom" : "right"}
          size="lg"
          title="Project settings"
          description={selected?.name}
          tone="lavender"
        >
          {settings}
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
            toast.success({ title: "Project created" })
          }}
        />

        <Dialog
          open={Boolean(deleteTarget)}
          onOpenChange={(open) => !open && setDeleteTarget(null)}
          title="Delete project?"
          description={`This permanently deletes “${deleteTarget?.name}” and its chat history.`}
          size="default"
          tone="rose"
          radius={28}
          footer={
            <>
              <Button
                variant="ghost"
                color="neutral"
                radius={999}
                onClick={() => setDeleteTarget(null)}
              >
                Cancel
              </Button>
              <Button
                variant="solid"
                color="rose"
                radius={999}
                onClick={() => deleteTarget && void removeProject(deleteTarget)}
              >
                Delete project
              </Button>
            </>
          }
        />

        <Dialog
          open={resetConfirm}
          onOpenChange={(open) => !open && setResetConfirm(false)}
          title="Reset this chat?"
          description={`This clears the conversation for “${selected?.name}”. The project and its memories stay.`}
          size="default"
          tone="rose"
          radius={28}
          footer={
            <>
              <Button
                variant="ghost"
                color="neutral"
                radius={999}
                onClick={() => setResetConfirm(false)}
              >
                Cancel
              </Button>
              <Button
                variant="solid"
                color="rose"
                radius={999}
                onClick={() => selected && void resetChat(selected)}
              >
                Reset chat
              </Button>
            </>
          }
        />
      </div>
    </ThemeProvider>
  )
}

function EmptyChat() {
  return (
    <div className="flex max-w-sm flex-col items-center gap-4 px-6 text-center">
      <div className="flex items-end gap-2">
        <SkyeSign className="h-11" />
      </div>
      <h2 className="text-[19px] font-semibold tracking-tight text-balance">
        Pick a project to start chatting
      </h2>
      <p className="text-[13.5px] leading-relaxed text-[var(--sk-text-desc)]">
        Or create a new one and give it a personality.
      </p>
    </div>
  )
}

function Gate({
  title,
  body,
  action,
  onAction,
  loading = false,
}: {
  title: string
  body?: string
  action?: string
  onAction?: () => void
  loading?: boolean
}) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.97, filter: "blur(10px)" }}
      animate={{ opacity: 1, scale: 1, filter: "blur(0px)" }}
      exit={{ opacity: 0, scale: 0.97, filter: "blur(10px)" }}
      transition={{ duration: 0.32, ease: [0.2, 0, 0, 1] }}
      className="flex min-h-dvh flex-col items-center justify-center gap-4 px-6 pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] text-center"
    >
      <SkyeSign className="h-14" />
      <h1 className="text-[26px] font-semibold tracking-tight">{title}</h1>
      {body ? (
        <p className="max-w-sm text-[14px] text-[var(--sk-text-desc)]">{body}</p>
      ) : null}
      {loading ? (
        <span className="flex items-center gap-1.5" role="status" aria-label="Loading">
          {[0, 1, 2].map((index) => (
            <span
              key={index}
              className="app-float inline-block size-2.5 rounded-full bg-pastel-lavender"
              style={{ animationDelay: `${index * 160}ms` }}
              aria-hidden="true"
            />
          ))}
        </span>
      ) : null}
      {action && onAction ? (
        <Button
          variant="solid"
          color="lavender"
          radius={999}
          className="mt-1 h-11 min-w-40"
          onClick={onAction}
        >
          {action}
        </Button>
      ) : null}
    </motion.div>
  )
}

export default App
