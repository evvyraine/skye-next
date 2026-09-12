import {
  ArrowDownTrayIcon,
  DocumentIcon,
  DocumentTextIcon,
  MusicalNoteIcon,
  PhotoIcon,
  VideoCameraIcon,
} from "@heroicons/react/24/outline"
import { Button, Dialog } from "sunkit-ui"
import { mediaUrl } from "@/ops/api"
import { formatBytes } from "@/ops/format"
import type { MediaItem } from "@/ops/types"
import { cn } from "@/lib/utils"

export function MediaGallery({
  traceId,
  media,
  onOpen,
}: {
  traceId: string
  media: MediaItem[]
  onOpen: (item: MediaItem) => void
}) {
  if (media.length === 0) {
    return (
      <p className="rounded-2xl border border-dashed border-[var(--sk-border-subtle)] px-4 py-6 text-center text-[13px] text-[var(--sk-text-desc)]">
        No images or files in this request.
      </p>
    )
  }
  return (
    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
      {media.map((item) => {
        const image = item.mime.startsWith("image/")
        const url = mediaUrl(traceId, item.name)
        if (image) {
          return (
            <button
              key={item.name}
              type="button"
              onClick={() => onOpen(item)}
              className="group overflow-hidden rounded-2xl border border-[var(--sk-border-subtle)] bg-[var(--sk-surface)] text-start outline-none focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/50"
            >
              <span className="block aspect-[4/3] overflow-hidden bg-[var(--sk-surface-filled)]">
                <img
                  src={url}
                  alt={item.name}
                  loading="lazy"
                  decoding="async"
                  className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                />
              </span>
              <span className="block px-2.5 py-2">
                <span className="block truncate text-[12px] font-medium">
                  {item.name}
                </span>
                <span className="block truncate text-[11px] text-[var(--sk-text-desc)]">
                  {item.detail || item.where} · {formatBytes(item.size)}
                </span>
              </span>
            </button>
          )
        }
        return (
          <a
            key={item.name}
            href={url}
            download={item.name}
            className="flex items-center gap-3 rounded-2xl border border-[var(--sk-border-subtle)] bg-[var(--sk-surface)] p-3 transition-colors outline-none hover:bg-[var(--sk-surface-hover)] focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/50"
          >
            <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-[var(--sk-surface-filled)]">
              <FileGlyph
                mime={item.mime}
                className="size-4.5 text-[var(--sk-text-muted)]"
              />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[12px] font-medium">
                {item.name}
              </span>
              <span className="block truncate text-[11px] text-[var(--sk-text-desc)]">
                {formatBytes(item.size)}
              </span>
            </span>
            <ArrowDownTrayIcon
              className="size-4 shrink-0 text-[var(--sk-text-muted)]"
              aria-hidden="true"
            />
          </a>
        )
      })}
    </div>
  )
}

export function MediaLightbox({
  traceId,
  item,
  onOpenChange,
}: {
  traceId: string
  item: MediaItem | null
  onOpenChange: (open: boolean) => void
}) {
  if (!item) {
    return null
  }
  const url = mediaUrl(traceId, item.name)
  const image = item.mime.startsWith("image/")
  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      title={item.name}
      description={`${item.detail || item.where} · ${item.mime} · ${formatBytes(item.size)}`}
      size="lg"
      tone="lavender"
      radius={24}
      footer={
        <Button
          variant="outline"
          color="lavender"
          radius={999}
          icon="left"
          iconLeft={<ArrowDownTrayIcon />}
          onClick={() => {
            const link = document.createElement("a")
            link.href = url
            link.download = item.name
            link.click()
          }}
        >
          Download
        </Button>
      }
    >
      {image ? (
        <div className="flex max-h-[68vh] items-center justify-center overflow-hidden rounded-2xl bg-[var(--sk-surface-filled)]">
          <img
            src={url}
            alt={item.name}
            className="max-h-[68vh] w-auto object-contain"
          />
        </div>
      ) : (
        <p className="rounded-2xl bg-[var(--sk-surface-filled)] px-4 py-8 text-center text-[13px] text-[var(--sk-text-desc)]">
          This file has no inline preview.
        </p>
      )}
    </Dialog>
  )
}

export function FileGlyph({
  mime,
  className,
}: {
  mime: string
  className?: string
}) {
  const clean = mime.toLowerCase()
  if (clean.startsWith("image/"))
    return <PhotoIcon className={className} aria-hidden="true" />
  if (clean.startsWith("audio/"))
    return <MusicalNoteIcon className={className} aria-hidden="true" />
  if (clean.startsWith("video/"))
    return <VideoCameraIcon className={className} aria-hidden="true" />
  if (clean.includes("pdf") || clean.startsWith("text/")) {
    return <DocumentTextIcon className={className} aria-hidden="true" />
  }
  return <DocumentIcon className={cn(className)} aria-hidden="true" />
}
