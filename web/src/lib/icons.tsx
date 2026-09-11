import type { ComponentType, SVGProps } from "react"
import type { ShapeType } from "sunkit-ui"
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
 * A pastel sticker surface per backend color key
 * (`src/skye/projects.py::PROJECT_COLORS`).
 */
export const PROJECT_PASTELS: Record<string, string> = {
  zinc: "#DAD7E6",
  slate: "#C7D2E4",
  stone: "#E0D8CA",
  neutral: "#E8E4DC",
  red: "#F7C6C9",
  orange: "#FBD6B0",
  amber: "#F6E79C",
  green: "#BEEAC6",
  teal: "#B4E8DA",
  blue: "#B8D8FE",
  indigo: "#C6C9FA",
  violet: "#D4C5F9",
  pink: "#F6C9E4",
}

/** The geometric sticker shape each icon gets, for a playful, non-rectangular feel. */
export const PROJECT_SHAPES: Record<string, ShapeType> = {
  cloud: "hexagon-flat",
  "chat-bubble-left-right": "circle",
  "code-bracket": "parallelogram",
  "cog-6-tooth": "octagon",
  briefcase: "shield",
  "academic-cap": "pentagon",
  heart: "heart",
  sparkles: "star6",
  "globe-alt": "circle",
  "paint-brush": "diamond",
  beaker: "triangle-down",
  "musical-note": "star5",
  camera: "hexagon",
  folder: "square",
  "light-bulb": "star4",
  star: "star5",
}

export const ICON_ORDER = Object.keys(PROJECT_ICONS)
export const COLOR_ORDER = Object.keys(PROJECT_PASTELS)
