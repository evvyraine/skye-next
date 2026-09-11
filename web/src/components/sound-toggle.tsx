import { SpeakerWaveIcon, SpeakerXMarkIcon } from "@heroicons/react/24/outline"
import { Button, useSound } from "sunkit-ui"

export function SoundToggle() {
  const { enabled, setEnabled } = useSound()

  return (
    <Button
      variant="ghost"
      color="neutral"
      size="icon-only"
      icon="only"
      iconOnly={enabled ? <SpeakerWaveIcon /> : <SpeakerXMarkIcon />}
      radius={999}
      aria-pressed={enabled}
      aria-label={enabled ? "Mute sounds" : "Unmute sounds"}
      onClick={() => setEnabled(!enabled)}
    />
  )
}
