import { cn } from "@/lib/utils"
import { PROJECT_COLORS, PROJECT_ICONS } from "@/lib/icons"

export function ProjectIcon({
  icon,
  color,
  className,
  size = "md",
}: {
  icon: string
  color: string
  className?: string
  size?: "sm" | "md" | "lg"
}) {
  const Glyph = PROJECT_ICONS[icon] ?? PROJECT_ICONS.sparkles
  const tone = PROJECT_COLORS[color] ?? PROJECT_COLORS.zinc
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-2xl",
        size === "sm" && "size-9",
        size === "md" && "size-12",
        size === "lg" && "size-16",
        tone,
        className,
      )}
    >
      <Glyph className={cn(size === "lg" ? "size-8" : "size-5")} />
    </span>
  )
}
