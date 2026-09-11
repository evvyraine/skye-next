import type { ComponentType, SVGProps } from "react"
import {
  AcademicCapIcon,
  BeakerIcon,
  BriefcaseIcon,
  CameraIcon,
  ChatBubbleLeftRightIcon,
  CloudIcon,
  CodeBracketIcon,
  Cog6ToothIcon,
  FolderIcon,
  GlobeAltIcon,
  HeartIcon,
  LightBulbIcon,
  MusicalNoteIcon,
  PaintBrushIcon,
  SparklesIcon,
  StarIcon,
} from "@heroicons/react/24/solid"

/**
 * Project icons are stored on the backend by these exact keys
 * (`src/skye/projects.py::PROJECT_ICONS`). Do not rename them.
 */
export const PROJECT_ICONS: Record<
  string,
  ComponentType<SVGProps<SVGSVGElement>>
> = {
  cloud: CloudIcon,
  "chat-bubble-left-right": ChatBubbleLeftRightIcon,
  "code-bracket": CodeBracketIcon,
  "cog-6-tooth": Cog6ToothIcon,
  briefcase: BriefcaseIcon,
  "academic-cap": AcademicCapIcon,
  heart: HeartIcon,
  sparkles: SparklesIcon,
  "globe-alt": GlobeAltIcon,
  "paint-brush": PaintBrushIcon,
  beaker: BeakerIcon,
  "musical-note": MusicalNoteIcon,
  camera: CameraIcon,
  folder: FolderIcon,
  "light-bulb": LightBulbIcon,
  star: StarIcon,
}

/**
 * A tint per backend color key (`src/skye/projects.py::PROJECT_COLORS`).
 * Eight hues, chosen to sit in a single row on desktop.
 */
export const PROJECT_PASTELS: Record<string, string> = {
  zinc: "#C9C5D8",
  red: "#F0A7AC",
  orange: "#F4C08A",
  amber: "#EAD37A",
  green: "#9FDCAB",
  teal: "#8FD8C8",
  blue: "#9CC6F2",
  violet: "#BFA9F0",
}

export const ICON_ORDER = Object.keys(PROJECT_ICONS)
export const COLOR_ORDER = Object.keys(PROJECT_PASTELS)

/** Human-readable names for assistive tech and menu copy. */
export const ICON_LABELS: Record<string, string> = {
  cloud: "Cloud",
  "chat-bubble-left-right": "Chat",
  "code-bracket": "Code",
  "cog-6-tooth": "Settings",
  briefcase: "Briefcase",
  "academic-cap": "Academic",
  heart: "Heart",
  sparkles: "Sparkles",
  "globe-alt": "Globe",
  "paint-brush": "Paint",
  beaker: "Beaker",
  "musical-note": "Music",
  camera: "Camera",
  folder: "Folder",
  "light-bulb": "Idea",
  star: "Star",
}

export const COLOR_LABELS: Record<string, string> = {
  zinc: "Granite",
  red: "Coral",
  orange: "Tangerine",
  amber: "Honey",
  green: "Meadow",
  teal: "Lagoon",
  blue: "Sky",
  violet: "Lavender",
}

