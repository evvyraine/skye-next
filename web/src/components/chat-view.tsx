import { useEffect, useRef, useState, type ReactNode } from "react"
import {
  ArrowDownIcon,
  ArrowLeftIcon,
  ArrowPathIcon,
  MicrophoneIcon,
  PaperAirplaneIcon,
  PaperClipIcon,
} from "@heroicons/react/24/outline"
import { StopIcon } from "@heroicons/react/24/solid"
import { AnimatePresence, motion } from "motion/react"
import {
  Button,
  Message,
  MessageBubble,
  MessageList,
  TypingIndicator,
  toast,
} from "sunkit-ui"
import {
  AttachmentDeck,
  AttachmentPreview,
  type AttachmentItem,
} from "@/components/attachment-card"
import { MessageMarkdown } from "@/components/markdown"
import { ProjectIcon } from "@/components/project-icon"
import { ToolCall } from "@/components/tool-call"
import { useStickToBottom } from "@/hooks/use-stick-to-bottom"
import { formatWhen, listMessages, sendMessage, stopProject, transcribe } from "@/lib/api"
import type { ChatFile, ChatMessage, Project } from "@/lib/types"

type ToolRow = {
  id: string
  name: string
  label: string
  status: string
  args: string
  output: string
}

type PendingAttachment = AttachmentItem & {
  file: File
}

export function ChatView({
  project,
  messages,
  files,
  onMessages,
  onBack,
  onOpenSettings,
  onReset,
}: {
  project: Project
  messages: ChatMessage[]
  files: ChatFile[]
  onMessages: (messages: ChatMessage[], files: ChatFile[]) => void
  onBack: () => void
  onOpenSettings: () => void
  onReset: () => void
}) {
  const [draft, setDraft] = useState("")
  const [attachments, setAttachments] = useState<PendingAttachment[]>([])
  const [sendingAttachments, setSendingAttachments] = useState<PendingAttachment[]>([])
  const [preview, setPreview] = useState<AttachmentItem | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [pendingText, setPendingText] = useState("")
  const [tools, setTools] = useState<ToolRow[]>([])
  const [listening, setListening] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const textRef = useRef<HTMLTextAreaElement>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const objectUrls = useRef<Set<string>>(new Set())

  const { ref: scrollRef, atBottom, onScroll, scrollToBottom } =
    useStickToBottom<HTMLDivElement>([messages.length, pendingText, tools.length])

  useEffect(() => {
    const urls = objectUrls.current
    return () => {
      urls.forEach((url) => URL.revokeObjectURL(url))
      urls.clear()
    }
  }, [])

  // Auto-grow the composer.
  useEffect(() => {
    const el = textRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [draft])

  function release(items: PendingAttachment[]) {
    for (const item of items) {
      URL.revokeObjectURL(item.url)
      objectUrls.current.delete(item.url)
    }
  }

  function removeAttachment(id: string) {
    setAttachments((current) => {
      const removed = current.find((item) => item.id === id)
      if (removed) {
        release([removed])
      }
      return current.filter((item) => item.id !== id)
    })
  }

  function addAttachments(selected: File[]) {
    const next = selected.map((file) => {
      const url = URL.createObjectURL(file)
      objectUrls.current.add(url)
      return {
        id: crypto.randomUUID(),
        filename: file.name,
        mime: file.type || "application/octet-stream",
        size: file.size,
        url,
        thumbnailUrl: url,
        file,
      }
    })
    setAttachments((current) => [...current, ...next])
  }

  async function send() {
    const text = draft.trim()
    if ((!text && attachments.length === 0) || streaming) {
      return
    }
    const localAttachments = attachments
    const localFiles = localAttachments.map((item) => item.file)
    let accepted = false
    let nextMessages = messages
    let nextFiles = files
    setDraft("")
    setPreview(null)
    setAttachments([])
    setSendingAttachments(
      localAttachments.map((item) => ({ ...item, uploading: true }))
    )
    setStreaming(true)
    setPendingText("")
    setTools([])
    try {
      await sendMessage(project.id, text, localFiles, {
        onUser: (message) => {
          accepted = true
          nextMessages = [
            ...nextMessages.filter((item) => item.id !== message.id),
            message,
          ]
          onMessages(nextMessages, nextFiles)
          release(localAttachments)
          setSendingAttachments([])
        },
        onAssistant: (message) => {
          nextMessages = [
            ...nextMessages.filter((item) => item.id !== message.id),
            message,
          ]
          onMessages(nextMessages, nextFiles)
        },
        onDelta: setPendingText,
        onTool: (tool) => {
          setTools((current) => {
            const existing = current.find((item) => item.id === tool.id)
            const merged: ToolRow = {
              id: tool.id,
              name: tool.name || existing?.name || "",
              label: tool.label || existing?.label || "",
              status: tool.status,
              args: tool.args || existing?.args || "",
              output: tool.output || existing?.output || "",
            }
            return [...current.filter((item) => item.id !== tool.id), merged]
          })
        },
        onImage: (file) => {
          nextFiles = [...nextFiles.filter((item) => item.id !== file.id), file]
          onMessages(nextMessages, nextFiles)
        },
        onFile: (file) => {
          nextFiles = [...nextFiles.filter((item) => item.id !== file.id), file]
          onMessages(nextMessages, nextFiles)
        },
        onDone: async () => {
          const payload = await listMessages(project.id)
          onMessages(payload.messages, payload.files)
          setPendingText("")
          setTools([])
        },
        onError: (message) =>
          toast.error({
            title: "Skye couldn't finish that reply",
            description: message || "Try again in a moment.",
          }),
        onNotice: (message) => toast.success({ title: message }),
      })
    } catch (error) {
      if (!accepted) {
        setAttachments(localAttachments)
        setSendingAttachments([])
      }
      toast.error({
        title: "Couldn't send that message",
        description:
          (error instanceof Error && error.message) ||
          "Check your connection and try again.",
      })
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
          .catch(() => {
            toast.error({
              title: "Couldn't transcribe that",
              description: "Try again, or type your message instead.",
            })
          })
      }
      recorder.current = media
      media.start()
      setListening(true)
    } catch {
      toast.error({ title: "Microphone access is needed for dictation." })
    }
  }

  const fileMap = Object.fromEntries(files.map((item) => [item.id, item]))
  const canSend = Boolean(draft.trim()) || attachments.length > 0
  const hasActivity =
    messages.length > 0 || tools.length > 0 || streaming || Boolean(pendingText)

  return (
    <div className="relative flex h-full min-h-0 flex-col font-sans">
      <header className="absolute inset-x-0 top-0 isolate z-20 flex items-center gap-2 px-4 pt-[max(0.5rem,env(safe-area-inset-top))] pb-4 before:pointer-events-none before:absolute before:inset-0 before:-z-10 before:bg-transparent before:[mask-image:linear-gradient(to_bottom,black_0%,black_55%,transparent_100%)] before:backdrop-blur-xl md:relative md:pb-2.5 md:before:hidden">
        <Button
          variant="ghost"
          color="neutral"
          size="icon-only"
          icon="only"
          iconOnly={<ArrowLeftIcon />}
          radius={999}
          className="h-10 w-10 bg-[var(--sk-bg-solid)]/75 shadow-sm ring-1 ring-[var(--sk-border)] backdrop-blur-xl md:hidden"
          onClick={onBack}
          aria-label="Back to projects"
        />
        <button
          type="button"
          className="flex min-w-0 cursor-pointer items-center gap-3 rounded-full bg-[var(--sk-bg-solid)]/75 px-3.5 py-2 text-start shadow-sm ring-1 ring-[var(--sk-border)] outline-none backdrop-blur-xl transition-colors hover:bg-[var(--sk-bg-solid)] focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/50"
          onClick={onOpenSettings}
        >
          <ProjectIcon icon={project.icon} color={project.color} size="sm" />
          <span className="min-w-0">
            <span className="block truncate text-[14px] font-semibold">
              {project.name}
            </span>
            <span className="block truncate text-[11px] text-[var(--sk-text-desc)]">
              {streaming ? "Thinking…" : "Tap for settings"}
            </span>
          </span>
        </button>
        {project.kind === "skye" ? (
          <Button
            variant="ghost"
            color="neutral"
            size="icon-only"
            icon="only"
            iconOnly={<ArrowPathIcon />}
            radius={999}
            className="ml-auto h-10 w-10 bg-[var(--sk-bg-solid)]/75 shadow-sm ring-1 ring-[var(--sk-border)] backdrop-blur-xl"
            onClick={onReset}
            aria-label="Reset this chat"
          />
        ) : null}
      </header>

      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="sk-scrollbar min-h-0 flex-1 overflow-y-auto px-4 pt-20 pb-4 md:pt-5"
      >
        {hasActivity ? (
          <MessageList
            label={`Conversation with ${project.name}`}
            className="mx-auto max-w-3xl"
          >
          <AnimatePresence initial={false}>
            {messages.map((message) => {
              const messageFiles = message.file_ids
                .map((id) => fileMap[id])
                .filter((file): file is ChatFile => Boolean(file))
                .map(fileItem)
              const isUser = message.role === "user"
              if (message.role === "tool") {
                return (
                  <MessageScrollerRow key={message.id}>
                    <ToolCall
                      data={{
                        id: message.id,
                        name: message.tool_name,
                        label: message.text,
                        status: "done",
                        args: message.tool_args,
                        output: message.tool_output,
                      }}
                    />
                  </MessageScrollerRow>
                )
              }
              return (
                <MessageScrollerRow key={message.id}>
                  <Message
                    align={isUser ? "end" : "start"}
                    time={formatWhen(message.created_at)}
                  >
                    {message.text ? (
                      <MessageBubble
                        variant={isUser ? "solid" : "ghost"}
                        tone={isUser ? "lilac" : "neutral"}
                        tail={isUser ? "end" : "start"}
                      >
                        <MessageMarkdown>{message.text}</MessageMarkdown>
                      </MessageBubble>
                    ) : null}
                    {messageFiles.length ? (
                      <AttachmentDeck
                        items={messageFiles}
                        align={isUser ? "end" : "start"}
                        onOpen={setPreview}
                      />
                    ) : null}
                  </Message>
                </MessageScrollerRow>
              )
            })}
          </AnimatePresence>

          {tools.map((tool) => (
            <motion.div
              key={tool.id}
              initial={{ opacity: 0, y: 8, filter: "blur(4px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              exit={{ opacity: 0, y: -6, filter: "blur(4px)" }}
            >
              <ToolCall data={tool} />
            </motion.div>
          ))}

          {pendingText ? (
            <Message align="start">
              <MessageBubble variant="ghost" tone="neutral" tail="start" streaming>
                <MessageMarkdown>{pendingText}</MessageMarkdown>
              </MessageBubble>
            </Message>
          ) : streaming && tools.length === 0 ? (
            <motion.div
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
            >
              <TypingIndicator tone="neutral" label={`${project.name} is typing`} />
            </motion.div>
          ) : null}
          </MessageList>
        ) : (
          <div className="flex h-full items-center justify-center">
            <ChatEmpty project={project} />
          </div>
        )}
      </div>

      <AnimatePresence>
        {!atBottom && messages.length > 2 ? (
          <motion.div
            initial={{ opacity: 0, y: 8, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.9 }}
            className="pointer-events-none absolute inset-x-0 bottom-28 z-20 flex justify-center md:bottom-32"
          >
            <Button
              variant="solid"
              color="lavender"
              size="icon-only"
              icon="only"
              iconOnly={<ArrowDownIcon />}
              radius={999}
              className="pointer-events-auto h-10 w-10 shadow-lg"
              onClick={() => scrollToBottom()}
              aria-label="Scroll to latest"
            />
          </motion.div>
        ) : null}
      </AnimatePresence>

      <form
        className="shrink-0 px-3 pt-2 pb-[max(0.75rem,env(safe-area-inset-bottom))]"
        onSubmit={(event) => {
          event.preventDefault()
          void send()
        }}
      >
        <div className="relative mx-auto max-w-3xl">
          <AnimatePresence initial={false}>
            {attachments.length || sendingAttachments.length ? (
              <motion.div
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 4 }}
                className="relative z-10 mb-1 px-2"
              >
                {sendingAttachments.length ? (
                  <AttachmentDeck items={sendingAttachments} onOpen={setPreview} />
                ) : null}
                {attachments.length ? (
                  <AttachmentDeck
                    items={attachments}
                    onOpen={setPreview}
                    onRemove={removeAttachment}
                  />
                ) : null}
              </motion.div>
            ) : null}
          </AnimatePresence>

          <div className="flex items-end gap-1.5 rounded-[26px] border border-[var(--sk-border)] bg-[var(--sk-bg)]/85 p-1.5 shadow-[0_12px_34px_-14px_var(--sk-shadow-a),0_4px_12px_-6px_var(--sk-shadow-b)] backdrop-blur-xl transition-shadow focus-within:shadow-[0_16px_40px_-14px_var(--sk-shadow-a)] focus-within:ring-1 focus-within:ring-[var(--sk-accent)]/30">
            <Button
              type="button"
              variant="ghost"
              color="lavender"
              size="icon-only"
              icon="only"
              iconOnly={<PaperClipIcon />}
              radius={999}
              className="h-10 w-10 shrink-0"
              aria-label="Attach a file"
              onClick={() => fileRef.current?.click()}
              disabled={streaming}
            />
            <textarea
              ref={textRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault()
                  void send()
                }
              }}
              rows={1}
              placeholder={`Message ${project.name}`}
              aria-label={`Message ${project.name}`}
              disabled={streaming}
              className="max-h-40 min-h-10 flex-1 resize-none bg-transparent px-1.5 py-[9px] text-base leading-[22px] text-[var(--sk-text)] outline-none placeholder:text-[var(--sk-text-placeholder)] disabled:opacity-60 sm:text-[15px]"
            />
            <div className="shrink-0 pb-0.5">
              {streaming ? (
                <Button
                  type="button"
                  variant="solid"
                  color="rose"
                  size="icon-only"
                  icon="only"
                  iconOnly={<StopIcon />}
                  radius={999}
                  className="h-10 w-10"
                  aria-label="Stop generating"
                  onClick={() => void stopProject(project.id)}
                />
              ) : canSend ? (
                <motion.div
                  initial={{ scale: 0.7, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={{ type: "spring", stiffness: 500, damping: 26 }}
                >
                  <Button
                    type="submit"
                    variant="solid"
                    color="lavender"
                    size="icon-only"
                    icon="only"
                    iconOnly={<PaperAirplaneIcon />}
                    radius={999}
                    className="h-10 w-10"
                    aria-label="Send message"
                  />
                </motion.div>
              ) : (
                <Button
                  type="button"
                  variant="solid"
                  color="lavender"
                  size="icon-only"
                  icon="only"
                  iconOnly={listening ? <StopIcon /> : <MicrophoneIcon />}
                  radius={999}
                  className="h-10 w-10"
                  aria-pressed={listening}
                  aria-label={listening ? "Stop dictation" : "Start dictation"}
                  onClick={() => void dictation()}
                />
              )}
            </div>
          </div>
        </div>
        <input
          ref={fileRef}
          type="file"
          multiple
          className="hidden"
          onChange={(event) => {
            addAttachments(Array.from(event.target.files ?? []))
            event.target.value = ""
          }}
        />
      </form>

      <AttachmentPreview
        item={preview}
        onOpenChange={(open) => !open && setPreview(null)}
      />
    </div>
  )
}

function ChatEmpty({ project }: { project: Project }) {
  return (
    <div className="mx-auto flex max-w-sm flex-col items-center gap-3 px-4 text-center">
      <span className="flex size-16 items-center justify-center rounded-3xl bg-[var(--sk-surface-filled)] ring-1 ring-[var(--sk-border-subtle)]">
        <ProjectIcon icon={project.icon} color={project.color} size="lg" />
      </span>
      <h2 className="text-[18px] font-semibold tracking-tight">
        {project.kind === "skye" ? "Hi, I'm Skye" : `Start with ${project.name}`}
      </h2>
      <p className="text-[13.5px] leading-relaxed text-[var(--sk-text-desc)]">
        Type a message below. Skye can search the web, work with files, run
        commands, and remember what matters.
      </p>
    </div>
  )
}

function MessageScrollerRow({ children }: { children: ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10, filter: "blur(5px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      exit={{ opacity: 0, y: -8, filter: "blur(4px)" }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      layout
    >
      {children}
    </motion.div>
  )
}

function fileItem(file: ChatFile): AttachmentItem {
  return {
    id: file.id,
    filename: file.filename,
    mime: file.mime,
    size: file.size,
    url: file.url,
    thumbnailUrl: file.thumbnail_url,
  }
}
