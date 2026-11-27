import type { CSSProperties } from "react";
import {
  Activity,
  SlidersHorizontal,
  MessageSquare,
  GitBranch,
  FileText,
  Play,
  Pause,
  Clock,
  Video,
  VolumeX,
  Check,
  AlertTriangle,
  X,
  ArrowUpRight,
  ChevronDown,
  ChevronRight,
  Download,
  Filter,
  Search,
  Link2,
  ShieldCheck,
  User,
  Sparkles,
  Eye,
  Hand,
  Moon,
  Sun,
  type LucideIcon,
} from "lucide-react";

type Props = {
  name: IconName;
  size?: number;
  className?: string;
  strokeWidth?: number;
  style?: CSSProperties;
};

export type IconName =
  | "trace"
  | "timeline"
  | "chat"
  | "branch"
  | "report"
  | "play"
  | "pause"
  | "clock"
  | "camera"
  | "mute"
  | "check"
  | "alert"
  | "close"
  | "arrowUpRight"
  | "chevronDown"
  | "chevronRight"
  | "download"
  | "filter"
  | "search"
  | "link"
  | "shield"
  | "user"
  | "sparkle"
  | "eye"
  | "hand"
  | "moon"
  | "sun";

const iconMap: Record<IconName, LucideIcon> = {
  trace: Activity,
  timeline: SlidersHorizontal,
  chat: MessageSquare,
  branch: GitBranch,
  report: FileText,
  play: Play,
  pause: Pause,
  clock: Clock,
  camera: Video,
  mute: VolumeX,
  check: Check,
  alert: AlertTriangle,
  close: X,
  arrowUpRight: ArrowUpRight,
  chevronDown: ChevronDown,
  chevronRight: ChevronRight,
  download: Download,
  filter: Filter,
  search: Search,
  link: Link2,
  shield: ShieldCheck,
  user: User,
  sparkle: Sparkles,
  eye: Eye,
  hand: Hand,
  moon: Moon,
  sun: Sun,
};

export function Icon({
  name,
  size = 18,
  className,
  strokeWidth = 1.8,
  style,
}: Props) {
  const Comp = iconMap[name];
  return (
    <Comp
      size={size}
      strokeWidth={strokeWidth}
      className={className}
      style={style}
      aria-hidden="true"
      focusable="false"
    />
  );
}
