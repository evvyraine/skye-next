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

export const PROJECT_COLORS: Record<string, string> = {
  zinc: "bg-zinc-800 text-white",
  slate: "bg-slate-700 text-white",
  stone: "bg-stone-700 text-white",
  neutral: "bg-neutral-800 text-white",
  red: "bg-red-700 text-white",
  orange: "bg-orange-600 text-white",
  amber: "bg-amber-500 text-black",
  green: "bg-green-700 text-white",
  teal: "bg-teal-700 text-white",
  blue: "bg-blue-700 text-white",
  indigo: "bg-indigo-700 text-white",
  violet: "bg-violet-700 text-white",
  pink: "bg-pink-600 text-white",
}

export const ICON_ORDER = Object.keys(PROJECT_ICONS)
export const COLOR_ORDER = Object.keys(PROJECT_COLORS)
