import { hexToAccentPair } from "sunkit-ui"
import { PROJECT_ICONS, PROJECT_PASTELS } from "@/lib/icons"
import { cn } from "@/lib/utils"

const SIZE_PX = { sm: 20, md: 24, lg: 40 } as const

/**
 * A project's icon as a bare, tinted glyph — no sticker, shape, or plate
 * behind it. The tint is derived from the project color so it stays legible
 * on both light and dark surfaces.
 */
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
  const pastel = PROJECT_PASTELS[color] ?? PROJECT_PASTELS.zinc
  const { border } = hexToAccentPair(pastel)
  const px = SIZE_PX[size]

  return (
    <Glyph
      aria-hidden="true"
      className={cn("shrink-0", className)}
      style={{ width: px, height: px, color: border }}
    />
  )
}
