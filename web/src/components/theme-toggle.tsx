import { Monitor, Moon, Sun } from "lucide-react"
import { Button, DropdownMenu } from "sunkit-ui"
import { useTheme, type Theme } from "@/components/theme-provider"

const OPTIONS: { value: Theme; label: string; Icon: typeof Sun }[] = [
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
  { value: "system", label: "System", Icon: Monitor },
]

export function ThemeToggle() {
  const { theme, resolvedTheme, setTheme } = useTheme()
  const Icon = resolvedTheme === "dark" ? Moon : Sun

  return (
    <DropdownMenu
      align="end"
      side="bottom"
      contentClassName="w-44"
      trigger={
        <Button
          variant="ghost"
          color="neutral"
          size="icon-only"
          icon="only"
          iconOnly={<Icon />}
          radius={999}
          aria-label="Change theme"
        />
      }
    >
      <DropdownMenu.Label>Theme</DropdownMenu.Label>
      {OPTIONS.map((option) => (
        <DropdownMenu.Item
          key={option.value}
          icon={<option.Icon className="size-4" aria-hidden="true" />}
          shortcut={theme === option.value ? "✓" : undefined}
          onSelect={() => setTheme(option.value)}
        >
          {option.label}
        </DropdownMenu.Item>
      ))}
    </DropdownMenu>
  )
}
