import {
  ArrowRightOnRectangleIcon,
  Cog6ToothIcon,
  EllipsisHorizontalIcon,
  QuestionMarkCircleIcon,
} from "@heroicons/react/24/outline"
import type { ReactNode } from "react"
import {
  Avatar,
  Button,
  DropdownMenu,
  Separator,
  Sheet,
  toast,
} from "sunkit-ui"
import type { User } from "@/lib/types"

function comingSoon() {
  toast.info({
    title: "Coming soon",
    description: "Profile settings will land in a future update.",
  })
}

function openHelp() {
  window.open("https://docs.skye-bot.com/", "_blank", "noopener,noreferrer")
}

export function ProfileMenu({
  user,
  onLogout,
}: {
  user: User | null
  onLogout: () => void
}) {
  return (
    <DropdownMenu
      side="top"
      align="start"
      contentClassName="w-64"
      className="w-full"
      trigger={
        <button
          type="button"
          className="flex w-full cursor-pointer items-center gap-3 rounded-2xl p-2 text-start outline-none transition-colors hover:bg-[var(--sk-surface-filled)] focus-visible:ring-2 focus-visible:ring-[var(--sk-accent)]/50"
        >
          <Avatar
            name={user?.name ?? "Skye user"}
            tone="lavender"
            size="default"
            status="online"
            decorative
          />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] font-semibold">
              {user?.name || "Skye user"}
            </span>
            {user?.username ? (
              <span className="block truncate text-[11px] text-[var(--sk-text-desc)]">
                @{user.username}
              </span>
            ) : null}
          </span>
          <EllipsisHorizontalIcon
            className="size-4 shrink-0 text-[var(--sk-text-muted)]"
            aria-hidden="true"
          />
        </button>
      }
    >
      <div className="flex items-center gap-3 px-2.5 py-2">
        <Avatar
          name={user?.name ?? "Skye user"}
          tone="lavender"
          size="default"
          status="online"
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-semibold">
            {user?.name || "Skye user"}
          </span>
          {user?.username ? (
            <span className="block truncate text-[11px] text-[var(--sk-text-desc)]">
              @{user.username}
            </span>
          ) : null}
        </span>
      </div>
      <Separator className="my-1" />
      <DropdownMenu.Item
        icon={<Cog6ToothIcon className="size-4" aria-hidden="true" />}
        onSelect={comingSoon}
      >
        Settings
      </DropdownMenu.Item>
      <DropdownMenu.Item
        icon={<QuestionMarkCircleIcon className="size-4" aria-hidden="true" />}
        onSelect={openHelp}
      >
        Help Center
      </DropdownMenu.Item>
      <Separator className="my-1" />
      <DropdownMenu.Item
        destructive
        icon={<ArrowRightOnRectangleIcon className="size-4" aria-hidden="true" />}
        onSelect={onLogout}
      >
        Log out
      </DropdownMenu.Item>
    </DropdownMenu>
  )
}

export function ProfileTrigger({
  user,
  onLogout,
}: {
  user: User | null
  onLogout: () => void
}) {
  return (
    <Sheet
      side="bottom"
      size="sm"
      title={user?.name || "Skye user"}
      description={user?.username ? `@${user.username}` : undefined}
      tone="lavender"
      trigger={
        <Button
          variant="solid"
          color="lavender"
          size="icon-only"
          icon="only"
          iconOnly={
            <Avatar
              name={user?.name ?? "Skye user"}
              tone="lavender"
              size="sm"
              decorative
            />
          }
          radius={999}
          className="h-11 w-11"
          aria-label={`Open profile for ${user?.name || "Skye user"}`}
        />
      }
    >
      <div className="flex flex-col gap-1.5">
        <SheetAction icon={<Cog6ToothIcon />} label="Settings" onClick={comingSoon} />
        <SheetAction
          icon={<QuestionMarkCircleIcon />}
          label="Help Center"
          onClick={openHelp}
        />
        <SheetAction
          icon={<ArrowRightOnRectangleIcon />}
          label="Log out"
          onClick={onLogout}
          tone="rose"
        />
      </div>
    </Sheet>
  )
}

function SheetAction({
  icon,
  label,
  onClick,
  tone = "lavender",
}: {
  icon: ReactNode
  label: string
  onClick: () => void
  tone?: "lavender" | "rose"
}) {
  return (
    <Button
      variant="ghost"
      color={tone}
      radius={16}
      icon="left"
      iconLeft={icon}
      className="h-12 w-full justify-start px-3 text-[15px]"
      onClick={onClick}
    >
      {label}
    </Button>
  )
}
