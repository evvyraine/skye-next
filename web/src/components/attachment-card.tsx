import { useState } from "react"
import { AudioLines, ExternalLink, FileText, X } from "lucide-react"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import { Button, Dialog, Spinner } from "sunkit-ui"
import { cn } from "@/lib/utils"

export type AttachmentItem = {
  id: string
  filename: string
  mime: string
  size: number
  url: string
  thumbnailUrl?: string | null
  uploading?: boolean
}

export function AttachmentDeck({
  items,
  align = "start",
  onOpen,
  onRemove,
}: {
  items: AttachmentItem[]
  align?: "start" | "end"
  onOpen: (item: AttachmentItem) => void
  onRemove?: (id: string) => void
}) {
  return (
    <div
      className={cn(
        "sk-scrollbar flex w-fit max-w-full flex-nowrap gap-2 overflow-x-auto overflow-y-hidden px-1 pt-1 pb-4 sm:gap-2.5",
        align === "end" && "ms-auto"
      )}
    >
      <AnimatePresence initial={false}>
        {items.map((item, index) => (
          <AttachmentCard
            key={item.id}
            item={item}
            rotation={[-1.6, 1.3, -0.8][index % 3]}
            onOpen={() => onOpen(item)}
            onRemove={onRemove ? () => onRemove(item.id) : undefined}
          />
        ))}
      </AnimatePresence>
    </div>
  )
}

function AttachmentCard({
  item,
  rotation,
  onOpen,
  onRemove,
}: {
  item: AttachmentItem
  rotation: number
  onOpen: () => void
  onRemove?: () => void
}) {
  const image = item.mime.startsWith("image/")
  const audio = item.mime.startsWith("audio/")
  const reducedMotion = useReducedMotion()

  return (
    <motion.article
      layout
      initial={
        reducedMotion
          ? { opacity: 0 }
          : { opacity: 0, y: 12, rotate: rotation * 2, filter: "blur(6px)" }
      }
      animate={{ opacity: 1, y: 0, rotate: rotation, filter: "blur(0px)" }}
      exit={
        reducedMotion
          ? { opacity: 0 }
          : { opacity: 0, y: -8, rotate: rotation * -2, filter: "blur(5px)" }
      }
      whileHover={reducedMotion ? undefined : { rotate: 0, y: -3, scale: 1.02 }}
      transition={{ type: "spring", duration: 0.32, bounce: 0.18 }}
      className="relative w-32 shrink-0 overflow-hidden rounded-3xl border border-[var(--sk-border)] bg-[var(--sk-bg-solid)] shadow-[0_10px_24px_-12px_var(--sk-shadow-a)] sm:w-52"
    >
      <button
        type="button"
        className="block w-full cursor-pointer text-start outline-none focus-visible:ring-3 focus-visible:ring-[var(--sk-accent)]/40"
        onClick={onOpen}
        aria-label={`Preview ${item.filename}`}
      >
        {image ? (
          <LoadedImage
            src={item.thumbnailUrl || item.url}
            alt={item.filename}
            eager={Boolean(onRemove)}
          />
        ) : (
          <div className="flex aspect-[4/3] items-center justify-center bg-[var(--sk-surface-filled)]">
            {audio ? (
              <AudioLines
                className="size-8 text-[var(--sk-text-muted)] sm:size-9"
                aria-hidden="true"
              />
            ) : (
              <FileText
                className="size-8 text-[var(--sk-text-muted)] sm:size-9"
                aria-hidden="true"
              />
            )}
          </div>
        )}
        <span className="flex min-w-0 items-center gap-1.5 px-2.5 py-2 sm:gap-2 sm:px-3">
          <span className="min-w-0 flex-1">
            <span className="block truncate text-xs font-medium sm:text-[13px]">
              {item.filename}
            </span>
            <span className="block text-[0.6875rem] text-[var(--sk-text-desc)]">
              {formatBytes(item.size)}
            </span>
          </span>
          <ExternalLink
            className="size-3.5 shrink-0 text-[var(--sk-text-muted)]"
            aria-hidden="true"
          />
        </span>
      </button>
      {item.uploading ? (
        <div className="absolute inset-0 flex items-center justify-center rounded-3xl bg-[var(--sk-bg)]/65 backdrop-blur-sm">
          <Spinner label={`Uploading ${item.filename}`} tone="lavender" />
        </div>
      ) : null}
      {onRemove ? (
        <Button
          type="button"
          variant="solid"
          color="neutral"
          size="icon-only"
          icon="only"
          iconOnly={<X />}
          radius={999}
          className="absolute top-1.5 right-1.5 size-8 min-h-0 bg-[var(--sk-bg-solid)]/90 p-0 backdrop-blur-md"
          onClick={onRemove}
          aria-label={`Remove ${item.filename}`}
        />
      ) : null}
    </motion.article>
  )
}

function LoadedImage({
  src,
  alt,
  eager = false,
  contain = false,
}: {
  src: string
  alt: string
  eager?: boolean
  contain?: boolean
}) {
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)
  const reducedMotion = useReducedMotion()

  return (
    <div
      className={cn(
        "relative flex overflow-hidden bg-[var(--sk-surface-filled)]",
        contain
          ? "max-h-[72dvh] min-h-60 items-center justify-center"
          : "aspect-[4/3]"
      )}
    >
      <AnimatePresence initial={false}>
        {!loaded && !failed ? (
          <motion.div
            key="loader"
            exit={{ opacity: 0, scale: 0.8, filter: "blur(4px)" }}
            className="absolute inset-0 flex items-center justify-center"
          >
            <Spinner label={`Loading ${alt}`} tone="lavender" />
          </motion.div>
        ) : null}
      </AnimatePresence>
      {failed ? (
        <div className="flex flex-col items-center gap-2 text-sm text-[var(--sk-text-muted)]">
          <FileText className="size-8" aria-hidden="true" />
          Preview unavailable
        </div>
      ) : (
        <motion.img
          src={src}
          alt={alt}
          loading={eager ? "eager" : "lazy"}
          decoding="async"
          initial={false}
          animate={
            loaded
              ? { opacity: 1, scale: 1, filter: "blur(0px)" }
              : reducedMotion
                ? { opacity: 0 }
                : { opacity: 0, scale: 0.9, filter: "blur(6px)" }
          }
          transition={{ type: "spring", duration: 0.32, bounce: 0 }}
          className={cn(
            "size-full ring-1 ring-black/10 ring-inset dark:ring-white/10",
            contain ? "max-h-[72dvh] object-contain" : "object-cover"
          )}
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
        />
      )}
    </div>
  )
}

export function AttachmentPreview({
  item,
  onOpenChange,
}: {
  item: AttachmentItem | null
  onOpenChange: (open: boolean) => void
}) {
  const image = item?.mime.startsWith("image/")
  const pdf = item?.mime === "application/pdf"
  const audio = item?.mime.startsWith("audio/")

  return (
    <Dialog
      open={Boolean(item)}
      onOpenChange={onOpenChange}
      title={item?.filename || "Attachment"}
      size="lg"
      tone="lavender"
      radius={24}
      footer={
        item && !image && !pdf && !audio ? (
          <Button
            color="lavender"
            icon="left"
            iconLeft={<ExternalLink />}
            radius={999}
            onClick={() =>
              window.open(item.url, "_blank", "noopener,noreferrer")
            }
          >
            Open file
          </Button>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-3">
        {item && image ? (
          <div className="overflow-hidden rounded-2xl">
            <LoadedImage src={item.url} alt={item.filename} eager contain />
          </div>
        ) : null}
        {item && pdf ? (
          <iframe
            src={item.url}
            title={item.filename}
            className="h-[70dvh] w-full rounded-2xl bg-[var(--sk-surface-filled)]"
          />
        ) : null}
        {item && audio ? (
          <div className="flex min-h-40 items-center justify-center rounded-2xl bg-[var(--sk-surface-filled)] p-6">
            <audio
              src={item.url}
              controls
              autoPlay
              className="w-full max-w-xl"
            />
          </div>
        ) : null}
        {item && !image && !pdf && !audio ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-3 rounded-2xl bg-[var(--sk-surface-filled)] p-6 text-center">
            <FileText
              className="size-10 text-[var(--sk-text-muted)]"
              aria-hidden="true"
            />
            <p className="max-w-sm text-sm text-[var(--sk-text-desc)]">
              Open this file in a new tab to view or download it.
            </p>
          </div>
        ) : null}
      </div>
    </Dialog>
  )
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}
