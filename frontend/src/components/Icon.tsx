type Props = {
  name: IconName;
  size?: number;
  className?: string;
  strokeWidth?: number;
  style?: React.CSSProperties;
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

const paths: Record<IconName, React.ReactNode> = {
  trace: (
    <>
      <path d="M4 19.5 9 9l5 6 6-10.5" />
      <circle cx="4" cy="19.5" r="1.6" />
      <circle cx="9" cy="9" r="1.6" />
      <circle cx="14" cy="15" r="1.6" />
      <circle cx="20" cy="4.5" r="1.6" />
    </>
  ),
  timeline: (
    <>
      <path d="M3 12h18" />
      <circle cx="7" cy="12" r="2.2" />
      <circle cx="13" cy="12" r="2.2" />
      <path d="M7 9.8V6m0 12v-3.8M13 9.8V6m0 12v-3.8" />
    </>
  ),
  chat: (
    <>
      <path d="M20 12.5a7.5 7.5 0 0 1-7.5 7.5H7l-3.5 2v-4.2A7.5 7.5 0 0 1 12.5 5 7.5 7.5 0 0 1 20 12.5Z" />
      <path d="M9 11.5h6M9 14.5h3.5" />
    </>
  ),
  branch: (
    <>
      <circle cx="6" cy="5.5" r="2.2" />
      <circle cx="6" cy="18.5" r="2.2" />
      <circle cx="18" cy="12" r="2.2" />
      <path d="M6 7.7v8.6M8.2 5.5h4.3a3 3 0 0 1 3 3v1.3M8.2 18.5h4.3a3 3 0 0 0 3-3v-1.3" />
    </>
  ),
  report: (
    <>
      <path d="M6 3.5h8l4 4v13H6z" />
      <path d="M14 3.5v4h4M9 12h6M9 15.5h6" />
    </>
  ),
  play: <path d="M8 5.5v13l11-6.5z" />,
  pause: (
    <>
      <path d="M9 5.5v13M15 5.5v13" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.2" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  camera: (
    <>
      <path d="M3.5 8.5h11v8h-11z" />
      <path d="M14.5 12l6-3.2v7.4l-6-3.2" />
    </>
  ),
  mute: (
    <>
      <path d="M4 9.5h3L11 6v12L7 14.5H4z" />
      <path d="M16 9.5 21 15M21 9.5 16 15" />
    </>
  ),
  check: <path d="m4.5 12.5 5 5 10-11" />,
  alert: (
    <>
      <path d="M12 4.5 21 20H3z" />
      <path d="M12 10v4.5M12 17.2v.2" />
    </>
  ),
  close: <path d="M6 6l12 12M18 6 6 18" />,
  arrowUpRight: <path d="M7 17 17 7M9 7h8v8" />,
  chevronDown: <path d="m6 9.5 6 6 6-6" />,
  chevronRight: <path d="m9.5 6 6 6-6 6" />,
  download: (
    <>
      <path d="M12 4v10.5M8 11l4 4 4-4" />
      <path d="M4.5 18.5h15" />
    </>
  ),
  filter: <path d="M4 6.5h16M7 12h10M10 17.5h4" />,
  search: (
    <>
      <circle cx="11" cy="11" r="6.2" />
      <path d="m15.6 15.6 4 4" />
    </>
  ),
  link: (
    <>
      <path d="M10 13.5a4 4 0 0 1 0-5.6l2-2a4 4 0 0 1 5.6 5.6l-1.2 1.2" />
      <path d="M14 10.5a4 4 0 0 1 0 5.6l-2 2A4 4 0 0 1 6.4 12.5l1.2-1.2" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3.5 19 6v6c0 4-3 7-7 8.5C8 19 5 16 5 12V6z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8.5" r="3.5" />
      <path d="M5 20a7 7 0 0 1 14 0" />
    </>
  ),
  sparkle: (
    <>
      <path d="M12 4v4M12 16v4M4 12h4M16 12h4" />
      <path d="m7.5 7.5 2.5 2.5M14 14l2.5 2.5M16.5 7.5 14 10M10 14l-2.5 2.5" />
    </>
  ),
  eye: (
    <>
      <path d="M3 12s3.5-6 9-6 9 6 9 6-3.5 6-9 6-9-6-9-6Z" />
      <circle cx="12" cy="12" r="2.6" />
    </>
  ),
  hand: (
    <>
      <path d="M9 11V5.8a1.6 1.6 0 0 1 3.2 0V11" />
      <path d="M12.2 10.6V4.9a1.6 1.6 0 0 1 3.2 0v5.7" />
      <path d="M15.4 11V7.4a1.6 1.6 0 0 1 3.2 0V14c0 3.6-2.4 6.5-6.2 6.5S6.2 17.6 6.2 14v-2.2a1.6 1.6 0 0 1 3.2 0" />
    </>
  ),
  moon: <path d="M20 14.5A8.2 8.2 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" />,
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4" />
    </>
  ),
};

export function Icon({
  name,
  size = 18,
  className,
  strokeWidth = 1.75,
  style,
}: Props) {
  return (
    <svg
      className={className}
      style={style}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {paths[name]}
    </svg>
  );
}
