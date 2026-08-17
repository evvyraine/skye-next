import { useRef, useState } from "react"
import { ArrowLeft, Mic, Paperclip, Send, Square, Wrench } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { toast } from "sonner"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
import { Button } from "@/components/ui/button"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker"
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller"
import { Spinner } from "@/components/ui/spinner"
import { MessageMarkdown } from "@/components/markdown"
import { ProjectIcon } from "@/components/project-icon"
import { sendMessage, listMessages, stopProject, transcribe } from "@/lib/api"
import type { ChatFile, ChatMessage, Project } from "@/lib/types"
import { cn } from "@/lib/utils"

type ToolRow = {
  id: string
  label: string
  status: string
}

export function ChatView({
  project,
  messages,
  files,
  onMessages,
  onBack,
  onOpenSettings,
  onStop,
}: {
  project: Project
  messages: ChatMessage[]
  files: ChatFile[]
  onMessages: (messages: ChatMessage[], files: ChatFile[]) => void
  onBack: () => void
  onOpenSettings: () => void
  onStop?: () => void
}) {
  const [draft, setDraft] = useState("")
  const [attachments, setAttachments] = useState<File[]>([])
  const [streaming, setStreaming] = useState(false)
  const [pendingText, setPendingText] = useState("")
  const [tools, setTools] = useState<ToolRow[]>([])
  const [listening, setListening] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const recorder = useRef<MediaRecorder | null>(null)

  async function send() {
    const text = draft.trim()
    if ((!text && attachments.length === 0) || streaming) {
      return
    }
    setDraft("")
    setAttachments([])
    setStreaming(true)
    setPendingText("")
    setTools([])
    const localFiles = attachments
    try {
      await sendMessage(project.id, text, localFiles, {
        onUser: (message) => onMessages([...messages, message], files),
        onDelta: setPendingText,
        onTool: (tool) => {
          setTools((current) => {
            const next = current.filter((item) => item.id !== tool.id)
            next.push({ id: tool.id, label: tool.label, status: tool.status })
            return next
          })
        },
        onImage: (file) => onMessages(messages, [...files, file]),
        onFile: (file) => onMessages(messages, [...files, file]),
        onDone: async () => {
          const payload = await listMessages(project.id)
          onMessages(payload.messages, payload.files)
          setPendingText("")
          setTools([])
        },
        onError: (message) => toast.error(message),
      })
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not send that."
      )
    } finally {
      setStreaming(false)
    }
  }

  async function dictation() {
    if (listening) {
      recorder.current?.stop()
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const media = new MediaRecorder(stream)
      const chunks: Blob[] = []
      media.ondataavailable = (event) => {
        if (event.data.size) {
          chunks.push(event.data)
        }
      }
      media.onstop = () => {
        stream.getTracks().forEach((track) => track.stop())
        setListening(false)
        const blob = new Blob(chunks, { type: media.mimeType || "audio/webm" })
        void transcribe(blob)
          .then((text) =>
            setDraft((current) => [current, text].filter(Boolean).join(" "))
          )
          .catch((error: unknown) => {
            toast.error(
              error instanceof Error
                ? error.message
                : "Could not transcribe that."
            )
          })
      }
      recorder.current = media
      media.start()
      setListening(true)
    } catch {
      toast.error("Microphone access is needed for dictation.")
    }
  }

  const fileMap = Object.fromEntries(files.map((item) => [item.id, item]))

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="absolute inset-x-0 top-0 isolate z-20 flex items-center gap-2 px-3 pt-[max(0.5rem,env(safe-area-inset-top))] pb-4 before:pointer-events-none before:absolute before:inset-0 before:-z-10 before:bg-transparent before:[mask-image:linear-gradient(to_bottom,black_0%,black_55%,transparent_100%)] before:backdrop-blur-xl md:relative md:border-b md:pb-2 md:before:hidden">
        <Button
          variant="ghost"
          size="icon"
          className="rounded-full bg-background/75 shadow-sm ring-1 ring-foreground/10 backdrop-blur-xl active:scale-[0.96] md:hidden"
          onClick={onBack}
          aria-label="Back"
        >
          <ArrowLeft />
        </Button>
        <Button
          variant="ghost"
          className="min-w-0 rounded-full bg-background/75 shadow-sm ring-1 ring-foreground/10 backdrop-blur-xl active:scale-[0.96] md:bg-background md:ring-border"
          onClick={onOpenSettings}
        >
          <ProjectIcon
            icon={project.icon}
            color={project.color}
            size="sm"
            className="size-6 rounded-lg"
          />
          <span className="truncate font-medium">{project.name}</span>
        </Button>
      </header>
      <MessageScrollerProvider>
        <MessageScroller className="min-h-0 flex-1">
          <MessageScrollerViewport>
            <MessageScrollerContent className="gap-4 px-4 pt-20 pb-6 md:py-6">
              <AnimatePresence initial={false}>
                {messages.map((message) => (
                  <MessageScrollerItem key={message.id}>
                    <motion.div
                      layout
                      className={cn(
                        "flex w-full",
                        message.role === "user"
                          ? "justify-end"
                          : "justify-start"
                      )}
                      initial={{ opacity: 0, y: 10, filter: "blur(5px)" }}
                      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                      exit={{ opacity: 0, y: -8, filter: "blur(4px)" }}
                      transition={{ duration: 0.22, ease: "easeOut" }}
                    >
                      {message.role === "tool" ? (
                        <Marker>
                          <MarkerIcon>
                            <Wrench />
                          </MarkerIcon>
                          <MarkerContent>
                            <MessageMarkdown>{message.text}</MessageMarkdown>
                          </MarkerContent>
                        </Marker>
                      ) : (
                        <Bubble
                          variant={
                            message.role === "user" ? "default" : "muted"
                          }
                          align={message.role === "user" ? "end" : "start"}
                        >
                          <BubbleContent className="rounded-3xl px-4 py-2.5">
                            <MessageMarkdown>{message.text}</MessageMarkdown>
                            {message.file_ids.map((id) => {
                              const file = fileMap[id]
                              if (!file) {
                                return null
                              }
                              if (file.mime.startsWith("image/")) {
                                return (
                                  <img
                                    key={id}
                                    src={file.url}
                                    alt={file.filename}
                                    className="mt-2 max-w-full rounded-2xl outline-1 -outline-offset-1 outline-black/10 dark:outline-white/10"
                                  />
                                )
                              }
                              return (
                                <a
                                  key={id}
                                  href={file.url}
                                  className="mt-2 block font-medium underline underline-offset-3"
                                >
                                  {file.filename}
                                </a>
                              )
                            })}
                          </BubbleContent>
                        </Bubble>
                      )}
                    </motion.div>
                  </MessageScrollerItem>
                ))}
                {tools.map((tool) => (
                  <MessageScrollerItem key={tool.id}>
                    <motion.div
                      initial={{ opacity: 0, y: 8, filter: "blur(4px)" }}
                      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                      exit={{ opacity: 0, y: -6, filter: "blur(4px)" }}
                    >
                      <Marker role="status">
                        <MarkerIcon>
                          {tool.status === "running" ? <Spinner /> : <Wrench />}
                        </MarkerIcon>
                        <MarkerContent
                          className={cn(tool.status === "running" && "shimmer")}
                        >
                          {tool.label}
                        </MarkerContent>
                      </Marker>
                    </motion.div>
                  </MessageScrollerItem>
                ))}
                {pendingText ? (
                  <MessageScrollerItem key="pending-response">
                    <motion.div
                      initial={{ opacity: 0, y: 8, filter: "blur(4px)" }}
                      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                      exit={{ opacity: 0, y: -6, filter: "blur(4px)" }}
                      role="status"
                      aria-live="polite"
                    >
                      <Bubble variant="muted" align="start">
                        <BubbleContent className="rounded-3xl px-4 py-2.5">
                          <MessageMarkdown>{pendingText}</MessageMarkdown>
                        </BubbleContent>
                      </Bubble>
                    </motion.div>
                  </MessageScrollerItem>
                ) : null}
              </AnimatePresence>
            </MessageScrollerContent>
          </MessageScrollerViewport>
          <MessageScrollerButton />
        </MessageScroller>
      </MessageScrollerProvider>
      <form
        className="px-3 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]"
        onSubmit={(event) => {
          event.preventDefault()
          void send()
        }}
      >
        <AnimatePresence initial={false}>
          {attachments.length ? (
            <motion.p
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
              className="mx-auto mb-2 max-w-3xl truncate px-3 text-xs text-muted-foreground"
            >
              {attachments.map((item) => item.name).join(", ")}
            </motion.p>
          ) : null}
        </AnimatePresence>
        <InputGroup className="mx-auto h-14 max-w-3xl rounded-full border-border/80 bg-background shadow-[0_8px_30px_oklch(0_0_0/0.08)]">
          <InputGroupInput
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={`Message ${project.name}`}
            aria-label={`Message ${project.name}`}
            className="h-full text-base sm:text-base"
            disabled={streaming}
          />
          <InputGroupAddon
            align="inline-start"
            className="pl-2 has-[>button]:ml-0"
          >
            <InputGroupButton
              type="button"
              size="icon-sm"
              variant="secondary"
              className="size-10 rounded-full shadow-sm active:scale-[0.96]"
              aria-label="Attach a file"
              onClick={() => fileRef.current?.click()}
            >
              <Paperclip />
            </InputGroupButton>
          </InputGroupAddon>
          <InputGroupAddon
            align="inline-end"
            className="pr-2 has-[>button]:mr-0"
          >
            {streaming ? (
              <InputGroupButton
                type="button"
                size="icon-sm"
                variant="default"
                className="size-10 rounded-full shadow-sm active:scale-[0.96]"
                aria-label="Stop generating"
                onClick={() => {
                  void stopProject(project.id)
                  onStop?.()
                }}
              >
                <Square />
              </InputGroupButton>
            ) : draft.trim() || attachments.length ? (
              <InputGroupButton
                type="submit"
                size="icon-sm"
                variant="default"
                className="size-10 rounded-full shadow-sm active:scale-[0.96]"
                aria-label="Send message"
              >
                <Send />
              </InputGroupButton>
            ) : (
              <InputGroupButton
                type="button"
                size="icon-sm"
                variant="default"
                className="size-10 rounded-full shadow-sm active:scale-[0.96]"
                aria-label={listening ? "Stop dictation" : "Start dictation"}
                onClick={() => void dictation()}
              >
                {listening ? <Square /> : <Mic />}
              </InputGroupButton>
            )}
          </InputGroupAddon>
        </InputGroup>
        <input
          ref={fileRef}
          type="file"
          multiple
          className="hidden"
          onChange={(event) => {
            setAttachments(Array.from(event.target.files ?? []))
            event.target.value = ""
          }}
        />
      </form>
    </div>
  )
}
