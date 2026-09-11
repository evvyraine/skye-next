import { useState } from "react"
import {
  ArrowPathIcon,
  CodeBracketIcon,
  DocumentIcon,
  DocumentTextIcon,
  MagnifyingGlassIcon,
  MapPinIcon,
  PhotoIcon,
  SpeakerWaveIcon,
  TrashIcon,
  VideoCameraIcon,
} from "@heroicons/react/24/outline"
import { Button, EmptyState, Input, Tabs, Textarea } from "sunkit-ui"
import {
  AttachmentPreview,
  type AttachmentItem,
} from "@/components/attachment-card"
import { ProjectIcon } from "@/components/project-icon"
import { formatDateTime } from "@/lib/api"
import {
  COLOR_LABELS,
  COLOR_ORDER,
  ICON_LABELS,
  ICON_ORDER,
  PROJECT_PASTELS,
} from "@/lib/icons"
import type { ChatFile, ChatMessage, Project } from "@/lib/types"
import { cn } from "@/lib/utils"

const CODE_EXTENSIONS = new Set([
  "c",
  "cpp",
  "cs",
  "css",
  "go",
  "html",
  "java",
  "js",
  "json",
  "jsx",
  "kt",
  "php",
  "py",
  "rb",
  "rs",
  "sh",
  "sql",
  "swift",
  "toml",
  "ts",
  "tsx",
  "xml",
  "yaml",
  "yml",
])

export function SettingsPanel({
  project,
  messages,
  files,
  onChange,
  onPin,
  onReset,
  onDelete,
}: {
  project: Project
  messages: ChatMessage[]
  files: ChatFile[]
  onChange: (patch: Partial<Project>) => void
  onPin: () => void
  onReset: () => void
  onDelete: () => void
}) {
  const [preview, setPreview] = useState<AttachmentItem | null>(null)
  const [mediaQuery, setMediaQuery] = useState("")
  const isInbox = project.kind === "skye"
  const conversationCount = messages.filter(
    (message) => message.role === "user" || message.role === "assistant"
  ).length

  const needle = mediaQuery.trim().toLowerCase()
  const orderedFiles = [...files].sort((a, b) =>
    a.created_at.localeCompare(b.created_at)
  )
  const visibleFiles = needle
    ? orderedFiles.filter((file) => file.filename.toLowerCase().includes(needle))
    : orderedFiles

  return (
    <div className="flex h-full min-h-0 flex-col">
      <Tabs
        defaultValue="info"
        tone="lavender"
        className="flex h-full min-h-0 flex-col"
      >
        <div className="shrink-0 px-3 pt-3">
          <Tabs.List className="flex w-full justify-between">
            {[
              ["info", "Info"],
              ["appearance", "Appearance"],
              ["manage", "Manage"],
              ["media", "Media"],
            ].map(([value, label]) => (
              <Tabs.Trigger
                key={value}
                value={value}
                className="min-w-0 px-2.5 whitespace-nowrap"
              >
                {label}
              </Tabs.Trigger>
            ))}
          </Tabs.List>
        </div>

        <div className="sk-scrollbar min-h-0 flex-1 overflow-y-auto p-4">
          <Tabs.Content value="info" className="flex flex-col gap-4">
            <div className="flex flex-col gap-3 rounded-3xl border border-[var(--sk-border-subtle)] bg-[var(--sk-surface-filled)] p-4">
              <Stat label="Messages" value={String(conversationCount)} />
              <Stat label="Files" value={String(files.length)} />
              <Stat
                label="Last activity"
                value={
                  project.last_message_at
                    ? formatDateTime(project.last_message_at)
                    : "No messages yet"
                }
              />
            </div>

            <Input
              id="settings-name"
              value={project.name}
              label="Name"
              description={
                isInbox ? "The main conversation is always called Inbox." : undefined
              }
              tone="lavender"
              radius={14}
              disabled={isInbox}
              onChange={(event) => onChange({ name: event.target.value })}
            />

            <Textarea
              id="settings-instructions"
              value={project.instructions}
              label="Instructions"
              description="Guides how Skye behaves in this project only. Other projects are unaffected."
              placeholder="e.g. You are a concise product coach."
              tone="lavender"
              radius={14}
              rows={6}
              autoResize
              maxLength={12000}
              showCount
              onChange={(event) => onChange({ instructions: event.target.value })}
            />
          </Tabs.Content>

          <Tabs.Content value="appearance" className="flex flex-col gap-4">
            <div className="flex flex-col items-center gap-2 pt-1 text-center">
              <ProjectIcon icon={project.icon} color={project.color} size="lg" />
              <p className="text-[12.5px] text-[var(--sk-text-desc)]">
                {project.name}
              </p>
            </div>

            <div>
              <p className="mb-2 text-[11px] font-semibold tracking-wide text-[var(--sk-text-muted)] uppercase">
                Color
              </p>
              <div
                className="flex flex-wrap gap-1.5"
                role="group"
                aria-label="Project color"
              >
                {COLOR_ORDER.map((item) => {
                  const active = project.color === item
                  const label = COLOR_LABELS[item] ?? item
                  return (
                    <button
                      key={item}
                      type="button"
                      aria-label={label}
                      aria-pressed={active}
                      title={label}
                      className={cn(
                        "size-8 cursor-pointer rounded-full border border-black/10 outline-none transition-transform hover:scale-110 focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/60",
                        active &&
                          "ring-2 ring-[var(--sk-accent)] ring-offset-2 ring-offset-[var(--sk-bg-solid)]"
                      )}
                      style={{ background: PROJECT_PASTELS[item] }}
                      onClick={() => onChange({ color: item })}
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
                className="flex flex-wrap gap-1"
                role="group"
                aria-label="Project icon"
              >
                {ICON_ORDER.map((item) => {
                  const active = project.icon === item
                  const label = ICON_LABELS[item] ?? item
                  return (
                    <button
                      key={item}
                      type="button"
                      aria-label={label}
                      aria-pressed={active}
                      title={label}
                      className={cn(
                        "flex size-11 cursor-pointer items-center justify-center rounded-2xl outline-none transition-colors hover:bg-[var(--sk-surface-filled)] focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/60",
                        active &&
                          "bg-[var(--sk-surface-filled)] ring-2 ring-[var(--sk-accent)]"
                      )}
                      onClick={() => onChange({ icon: item })}
                    >
                      <ProjectIcon icon={item} color={project.color} size="md" />
                    </button>
                  )
                })}
              </div>
            </div>
          </Tabs.Content>

          <Tabs.Content value="manage" className="flex flex-col gap-2">
            {isInbox ? null : (
              <Button
                variant="outline"
                color="lavender"
                radius={999}
                icon="left"
                iconLeft={<MapPinIcon />}
                className="h-11 w-full justify-start"
                onClick={onPin}
              >
                {project.pinned ? "Unpin project" : "Pin project"}
              </Button>
            )}
            <Button
              variant="outline"
              color="lavender"
              radius={999}
              icon="left"
              iconLeft={<ArrowPathIcon />}
              className="h-11 w-full justify-start"
              onClick={onReset}
            >
              Reset this chat
            </Button>
            {project.deletable ? (
              <Button
                variant="outline"
                color="rose"
                radius={999}
                icon="left"
                iconLeft={<TrashIcon />}
                className="h-11 w-full justify-start"
                onClick={onDelete}
              >
                Delete project
              </Button>
            ) : null}
          </Tabs.Content>

          <Tabs.Content value="media" className="flex flex-col gap-3">
            <Input
              id="media-search"
              value={mediaQuery}
              onChange={(event) => setMediaQuery(event.target.value)}
              placeholder="Search files"
              aria-label="Search files"
              variant="filled"
              radius={999}
              leftAdornment={
                <MagnifyingGlassIcon
                  className="size-4 text-[var(--sk-text-muted)]"
                  aria-hidden="true"
                />
              }
            />

            {files.length === 0 ? (
              <EmptyState
                size="sm"
                tone="lavender"
                icon={<DocumentIcon className="size-6" aria-hidden="true" />}
                title="No files yet"
                description="Uploads and files Skye creates in this project will show up here."
              />
            ) : visibleFiles.length === 0 ? (
              <EmptyState
                size="sm"
                tone="lavender"
                icon={<MagnifyingGlassIcon className="size-6" aria-hidden="true" />}
                title="No matches"
                description={`Nothing matches “${mediaQuery.trim()}”.`}
              />
            ) : (
              <ul className="flex flex-col gap-1">
                {visibleFiles.map((file) => (
                  <MediaRow
                    key={file.id}
                    file={file}
                    onOpen={() => setPreview(fileToItem(file))}
                  />
                ))}
              </ul>
            )}
          </Tabs.Content>
        </div>
      </Tabs>

      <AttachmentPreview
        item={preview}
        onOpenChange={(open) => !open && setPreview(null)}
      />
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-[12.5px] text-[var(--sk-text-desc)]">{label}</span>
      <span className="truncate text-[13px] font-semibold tabular-nums">{value}</span>
    </div>
  )
}

function MediaRow({ file, onOpen }: { file: ChatFile; onOpen: () => void }) {
  const image = file.mime.startsWith("image/")
  return (
    <li>
      <button
        type="button"
        className="flex w-full cursor-pointer items-center gap-3 rounded-2xl p-2 text-start outline-none transition-colors hover:bg-[var(--sk-surface-filled)] focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/50"
        onClick={onOpen}
        aria-label={`Preview ${file.filename}`}
      >
        <span className="flex size-10 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-[var(--sk-surface-filled)] ring-1 ring-[var(--sk-border-subtle)]">
          {image ? (
            <img
              src={file.thumbnail_url || file.url}
              alt=""
              loading="lazy"
              decoding="async"
              className="size-full object-cover"
            />
          ) : (
            <FileGlyph
              file={file}
              className="size-5 text-[var(--sk-text-muted)]"
            />
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium">
            {file.filename}
          </span>
          <span className="block truncate text-[11px] text-[var(--sk-text-desc)]">
            {formatBytes(file.size)} · {formatDateTime(file.created_at)}
          </span>
        </span>
      </button>
    </li>
  )
}

function FileGlyph({ file, className }: { file: ChatFile; className?: string }) {
  const mime = file.mime.toLowerCase()
  const extension = file.filename.split(".").pop()?.toLowerCase() ?? ""
  if (mime.startsWith("image/")) {
    return <PhotoIcon className={className} aria-hidden="true" />
  }
  if (mime.startsWith("audio/")) {
    return <SpeakerWaveIcon className={className} aria-hidden="true" />
  }
  if (mime.startsWith("video/")) {
    return <VideoCameraIcon className={className} aria-hidden="true" />
  }
  if (mime === "application/pdf") {
    return <DocumentTextIcon className={className} aria-hidden="true" />
  }
  if (CODE_EXTENSIONS.has(extension)) {
    return <CodeBracketIcon className={className} aria-hidden="true" />
  }
  if (mime.startsWith("text/")) {
    return <DocumentTextIcon className={className} aria-hidden="true" />
  }
  return <DocumentIcon className={className} aria-hidden="true" />
}

function fileToItem(file: ChatFile): AttachmentItem {
  return {
    id: file.id,
    filename: file.filename,
    mime: file.mime,
    size: file.size,
    url: file.url,
    thumbnailUrl: file.thumbnail_url,
  }
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}
