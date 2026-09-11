import { Shape, hexToAccentPair } from "sunkit-ui"
import { PROJECT_ICONS, PROJECT_PASTELS, PROJECT_SHAPES } from "@/lib/icons"
import { cn } from "@/lib/utils"

const SIZE_PX = { sm: 36, md: 48, lg: 64 } as const

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
  const pastel = PROJECT_PASTELS[color] ?? PROJECT_PASTELS.neutral
  const { border } = hexToAccentPair(pastel)
  const px = SIZE_PX[size]
  const glyph = Math.round(px * 0.42)

  return (
    <Shape
      shape={PROJECT_SHAPES[icon] ?? "hexagon"}
      size={px}
      accentColor={pastel}
      radius={size === "sm" ? 20 : 26}
      className={cn(
        "shrink-0 drop-shadow-[0_6px_10px_rgba(0,0,0,0.12)]",
        className
      )}
    >
      <Glyph
        aria-hidden="true"
        style={{ width: glyph, height: glyph, color: border }}
      />
    </Shape>
  )
}
