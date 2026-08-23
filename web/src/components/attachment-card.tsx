import { useState } from "react"
import { AudioLines, ExternalLink, FileText, X } from "lucide-react"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
        "flex w-fit max-w-full flex-nowrap gap-1.5 overflow-x-auto overflow-y-hidden px-4 pt-3 pb-5 [filter:drop-shadow(0_8px_12px_oklch(0_0_0/0.1))] sm:gap-2 sm:py-5 sm:[filter:none]",
        align === "end" && "ms-auto"
      )}
    >
      <AnimatePresence initial={false}>
        {items.map((item, index) => (
          <AttachmentCard
            key={item.id}
            item={item}
            rotation={[-1.5, 1.25, -0.75][index % 3]}
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
          : { opacity: 0, y: 10, rotate: rotation * 2, filter: "blur(6px)" }
      }
      animate={{ opacity: 1, y: 0, rotate: rotation, filter: "blur(0px)" }}
      exit={
        reducedMotion
          ? { opacity: 0 }
          : { opacity: 0, y: -8, rotate: rotation * -2, filter: "blur(5px)" }
      }
      whileHover={reducedMotion ? undefined : { rotate: 0, y: -2 }}
      transition={{ type: "spring", duration: 0.3, bounce: 0 }}
      className="relative w-32 shrink-0 overflow-hidden rounded-2xl bg-card ring-1 ring-foreground/10 sm:w-56 sm:rounded-3xl sm:shadow-[0_10px_30px_oklch(0_0_0/0.1)]"
    >
      <button
        type="button"
        className="block w-full text-start outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        onClick={onOpen}
        aria-label={`Preview ${item.filename}`}
      >
        {image ? (
          <LoadedImage
            src={item.thumbnailUrl || item.url}
            alt={item.filename}
            eager={Boolean(onRemove)}
          />
        ) : audio ? (
          <div className="flex aspect-[4/3] items-center justify-center bg-muted">
            <AudioLines
              className="size-8 text-muted-foreground sm:size-10"
              aria-hidden="true"
            />
          </div>
        ) : (
          <div className="flex aspect-[4/3] items-center justify-center bg-muted">
            <FileText
              className="size-8 text-muted-foreground sm:size-10"
              aria-hidden="true"
            />
          </div>
        )}
        <span className="flex min-w-0 items-center gap-1.5 px-2.5 py-2 sm:gap-2 sm:px-3 sm:py-2.5">
          <span className="min-w-0 flex-1">
            <span className="block truncate text-xs font-medium sm:text-sm">
              {item.filename}
            </span>
            <span className="block text-[0.6875rem] text-muted-foreground sm:text-xs">
              {formatBytes(item.size)}
            </span>
          </span>
          <ExternalLink
            className="size-3.5 shrink-0 text-muted-foreground sm:size-4"
            aria-hidden="true"
          />
        </span>
      </button>
      {item.uploading ? (
        <div className="absolute inset-0 flex items-center justify-center rounded-2xl bg-background/60 backdrop-blur-sm sm:rounded-3xl">
          <DotsGrid label={`Uploading ${item.filename}`} />
        </div>
      ) : null}
      {onRemove ? (
        <Button
          type="button"
          variant="secondary"
          size="icon"
          className="absolute top-1.5 right-1.5 size-8 rounded-full bg-background/85 shadow-sm backdrop-blur-md active:scale-[0.96] sm:top-2 sm:right-2 sm:size-10"
          onClick={onRemove}
          aria-label={`Remove ${item.filename}`}
        >
          <X />
        </Button>
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
        "relative flex overflow-hidden bg-muted",
        contain
          ? "max-h-[80dvh] min-h-60 items-center justify-center"
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
            <DotsGrid label={`Loading ${alt}`} />
          </motion.div>
        ) : null}
      </AnimatePresence>
      {failed ? (
        <div className="flex flex-col items-center gap-2 text-sm text-muted-foreground">
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
                : { opacity: 0, scale: 0.25, filter: "blur(4px)" }
          }
          transition={{ type: "spring", duration: 0.3, bounce: 0 }}
          className={cn(
            "size-full outline-1 -outline-offset-1 outline-black/10 dark:outline-white/10",
            contain ? "max-h-[80dvh] object-contain" : "object-cover"
          )}
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
        />
      )}
    </div>
  )
}

function DotsGrid({ label }: { label: string }) {
  return (
    <span role="status" aria-label={label} className="grid grid-cols-3 gap-1">
      {Array.from({ length: 9 }, (_, index) => (
        <span
          key={index}
          className="size-1.5 animate-pulse rounded-full bg-foreground/60"
          style={{ animationDelay: `${index * 55}ms` }}
          aria-hidden="true"
        />
      ))}
    </span>
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
    <Dialog open={Boolean(item)} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[calc(100%-1rem)] gap-2 overflow-hidden rounded-3xl bg-background/90 p-2 backdrop-blur-xl sm:max-w-4xl">
        <DialogHeader className="min-w-0 px-3 pt-2">
          <DialogTitle className="block max-w-[calc(100%-3rem)] truncate">
            {item?.filename || "Attachment"}
          </DialogTitle>
        </DialogHeader>
        {item && image ? (
          <LoadedImage src={item.url} alt={item.filename} eager contain />
        ) : null}
        {item && pdf ? (
          <iframe
            src={item.url}
            title={item.filename}
            className="h-[75dvh] w-full rounded-2xl bg-muted"
          />
        ) : null}
        {item && audio ? (
          <div className="flex min-h-40 items-center justify-center rounded-2xl bg-muted p-6">
            <audio src={item.url} controls autoPlay className="w-full max-w-xl" />
          </div>
        ) : null}
        {item && !image && !pdf && !audio ? (
          <div className="flex min-h-64 flex-col items-center justify-center gap-4 rounded-2xl bg-muted p-6 text-center">
            <FileText
              className="size-12 text-muted-foreground"
              aria-hidden="true"
            />
            <p className="max-w-sm text-sm text-muted-foreground">
              Open this file in a new tab to view or download it.
            </p>
            <Button
              onClick={() =>
                window.open(item.url, "_blank", "noopener,noreferrer")
              }
              className="rounded-full"
            >
              <ExternalLink data-icon="inline-start" />
              Open file
            </Button>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}
