import clsx, { type ClassValue } from "clsx";
import type { ReactNode } from "react";
import { AlertCircle, CheckCircle2, Info } from "lucide-react";

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

export function btnClass(
  variant: ButtonVariant = "secondary",
  size: ButtonSize = "md",
  extra?: ClassValue,
) {
  return cn(
    "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-all duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#992777] disabled:pointer-events-none disabled:opacity-45 shrink-0",
    {
      "px-2.5 py-1.5 text-xs": size === "sm",
      "px-3.5 py-2 text-sm": size === "md",
      "px-4 py-2.5 text-sm": size === "lg",
    },
    {
      "bg-[#992777] text-white hover:bg-[#7f1e62] shadow-xs":
        variant === "primary",
      "bg-white text-[#3d2542] border border-[#e3d2e6] hover:bg-[#faf4fb] hover:border-[#cfaed4] shadow-2xs":
        variant === "secondary",
      "bg-transparent text-[#5f4965] hover:bg-[#f4e8f6] hover:text-[#4a153b]":
        variant === "ghost",
      "bg-rose-50 text-rose-700 border border-rose-200 hover:bg-rose-100":
        variant === "danger",
    },
    extra,
  );
}

export const fieldClass =
  "w-full rounded-xl border border-[#dfd0e2] bg-white px-3 py-2 text-sm text-[#231426] placeholder:text-[#9c89a2] shadow-2xs transition focus:border-[#992777] focus:outline-none focus:ring-2 focus:ring-[#992777]/15 disabled:bg-[#f7f2f8] disabled:text-[#9c89a2]";

export const selectClass = cn(fieldClass, "lt-select");
export const textareaClass = cn(fieldClass, "resize-y min-h-[84px]");

export const labelClass =
  "flex flex-col gap-1.5 text-xs font-medium text-[#4a3450]";

export const cardClass =
  "rounded-2xl border border-[#e8dce9] bg-white shadow-[0_2px_10px_-4px_rgba(74,21,59,0.08)]";

export const cardHeadClass =
  "flex items-center justify-between gap-3 border-b border-[#f0e5f2] px-4 py-3";

export const cardTitleClass =
  "text-sm font-semibold tracking-tight text-[#231426] flex items-center gap-2";

export type BadgeTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger"
  | "accent";

export function badgeClass(tone: BadgeTone = "neutral", extra?: ClassValue) {
  return cn(
    "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium border",
    {
      "bg-[#f5eff7] text-[#58425e] border-[#e2d4e5]": tone === "neutral",
      "bg-[#f7eaf3] text-[#841f66] border-[#e3bed8]": tone === "info",
      "bg-emerald-50 text-emerald-800 border-emerald-200/80":
        tone === "success",
      "bg-amber-50 text-amber-800 border-amber-200/80": tone === "warning",
      "bg-rose-50 text-rose-800 border-rose-200/80": tone === "danger",
      "bg-[#f1e8f5] text-[#6c347c] border-[#d9c1e3]": tone === "accent",
    },
    extra,
  );
}

export function statusTone(status: string): BadgeTone {
  if (["confirmed", "supported", "completed"].includes(status))
    return "success";
  if (["rejected", "contradicted", "failed", "error"].includes(status))
    return "danger";
  if (
    ["uncertain", "paused", "pausing", "completed_with_errors"].includes(status)
  )
    return "warning";
  if (
    ["running", "queued", "tracking", "windowing", "discovery"].includes(status)
  )
    return "info";
  return "neutral";
}

export function AlertBanner({
  tone = "danger",
  children,
  action,
  className,
}: {
  tone?: "danger" | "status" | "info";
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  const IconComp =
    tone === "danger"
      ? AlertCircle
      : tone === "status"
        ? CheckCircle2
        : Info;
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={cn(
        "flex flex-wrap items-center justify-between gap-2 rounded-xl border px-3.5 py-2.5 text-sm",
        {
          "border-rose-200 bg-rose-50/90 text-rose-800": tone === "danger",
          "border-emerald-200 bg-emerald-50/90 text-emerald-800":
            tone === "status",
          "border-[#e3bed8] bg-[#f9eff6] text-[#6e1a55]": tone === "info",
        },
        className,
      )}
    >
      <div className="flex items-start gap-2 min-w-0">
        <IconComp size={16} className="mt-0.5 shrink-0" />
        <div className="min-w-0 break-words">{children}</div>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        cardClass,
        "flex flex-col items-center justify-center gap-2 px-6 py-12 text-center",
        className,
      )}
    >
      {icon ? (
        <div className="mb-1 flex h-11 w-11 items-center justify-center rounded-xl bg-[#f7eaf3] text-[#992777]">
          {icon}
        </div>
      ) : null}
      <h3 className="text-base font-semibold text-[#231426]">{title}</h3>
      {description ? (
        <p className="max-w-md text-sm text-[#6e5a74]">{description}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
