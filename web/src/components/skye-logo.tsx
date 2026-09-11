import logoMain from "@/assets/logo/logo-horizontal-main.svg"
import logoWhite from "@/assets/logo/logo-horizontal-white.svg"
import signMain from "@/assets/logo/sign-main.svg"
import signWhite from "@/assets/logo/sign-white.svg"
import { cn } from "@/lib/utils"

/**
 * The designer wordmark (sign + "Skye"). Colored in light, white in dark.
 * Height is set by the caller; width follows the intrinsic ratio.
 */
export function SkyeLogo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center", className)}>
      <img src={logoMain} alt="Skye" className="h-full w-auto dark:hidden" />
      <img
        src={logoWhite}
        alt="Skye"
        className="hidden h-full w-auto dark:block"
      />
    </span>
  )
}

/** The designer sign on its own, for large/centered placements. */
export function SkyeSign({
  className,
  decorative = true,
}: {
  className?: string
  decorative?: boolean
}) {
  const alt = decorative ? "" : "Skye"
  const hidden = decorative ? true : undefined
  return (
    <span className={cn("inline-flex items-center", className)}>
      <img
        src={signMain}
        alt={alt}
        aria-hidden={hidden}
        className="h-full w-auto dark:hidden"
      />
      <img
        src={signWhite}
        alt={alt}
        aria-hidden={hidden}
        className="hidden h-full w-auto dark:block"
      />
    </span>
  )
}
