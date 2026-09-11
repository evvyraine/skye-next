import { Volume2, VolumeX } from "lucide-react"
import { Button, useSound } from "sunkit-ui"

export function SoundToggle() {
  const { enabled, setEnabled } = useSound()

  return (
    <Button
      variant="ghost"
      color="neutral"
      size="icon-only"
      icon="only"
      iconOnly={enabled ? <Volume2 /> : <VolumeX />}
      radius={999}
      aria-pressed={enabled}
      aria-label={enabled ? "Mute sounds" : "Unmute sounds"}
      onClick={() => setEnabled(!enabled)}
    />
  )
}
