import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { SlidersHorizontal, X, Clock, Play } from "lucide-react";
import type { Track, WindowResult } from "../models/backend";
import type { VisualItem } from "../models/view";
import { actionLabel, fmt, observationText } from "../models/view";
import type { ActionSpan } from "../adapters/timeline";
import { coverageIntervals, coverageNames } from "../adapters/coverage";
import { personTrackLabel, type IdentityLabel } from "../api/workbench";
import { cardClass, cardHeadClass, cardTitleClass, cn } from "./ui/primitives";

interface Props {
  duration: number;
  time: number;
  items: VisualItem[];
  spans: ActionSpan[];
  selectedId?: string;
  onSelect: (item: VisualItem) => void;
  onSeek: (t: number) => void;
  tracks?: Track[];
  windows?: WindowResult[];
  selectedTrack?: number;
  identityLabels?: IdentityLabel[];
  onSelectTrack?: (id: number) => void;
  offset?: number;
  singlePerson?: boolean;
  coverageJobId?: string;
}

interface CategoryStyle {
  id: string;
  label: string;
  bar: string;
  badge: string;
  dot: string;
}

const CATEGORY_STYLES: Record<string, CategoryStyle> = {
  writing: {
    id: "writing",
    label: "书写/作业",
    bar: "border-l-sky-600 border-sky-300/90 bg-sky-100/95 text-sky-950 hover:bg-sky-200/90",
    badge: "bg-sky-100 text-sky-800 border-sky-200",
    dot: "bg-sky-500",
  },
  reading: {
    id: "reading",
    label: "阅读/看书",
    bar: "border-l-teal-600 border-teal-300/90 bg-teal-100/95 text-teal-950 hover:bg-teal-200/90",
    badge: "bg-teal-100 text-teal-800 border-teal-200",
    dot: "bg-teal-500",
  },
  hand_raise: {
    id: "hand_raise",
    label: "举手/互动",
    bar: "border-l-amber-600 border-amber-300/90 bg-amber-100/95 text-amber-950 hover:bg-amber-200/90",
    badge: "bg-amber-100 text-amber-800 border-amber-200",
    dot: "bg-amber-500",
  },
  posture_move: {
    id: "posture_move",
    label: "站立/移动",
    bar: "border-l-orange-600 border-orange-300/90 bg-orange-100/95 text-orange-950 hover:bg-orange-200/90",
    badge: "bg-orange-100 text-orange-800 border-orange-200",
    dot: "bg-orange-500",
  },
  discuss: {
    id: "discuss",
    label: "交流/讨论",
    bar: "border-l-indigo-600 border-indigo-300/90 bg-indigo-100/95 text-indigo-950 hover:bg-indigo-200/90",
    badge: "bg-indigo-100 text-indigo-800 border-indigo-200",
    dot: "bg-indigo-500",
  },
  rest: {
    id: "rest",
    label: "托头/伏桌",
    bar: "border-l-rose-500 border-rose-300/90 bg-rose-100/95 text-rose-950 hover:bg-rose-200/90",
    badge: "bg-rose-100 text-rose-800 border-rose-200",
    dot: "bg-rose-500",
  },
  device_other: {
    id: "device_other",
    label: "设备/饮食",
    bar: "border-l-violet-600 border-violet-300/90 bg-violet-100/95 text-violet-950 hover:bg-violet-200/90",
    badge: "bg-violet-100 text-violet-800 border-violet-200",
    dot: "bg-violet-500",
  },
  listening: {
    id: "listening",
    label: "坐姿/听讲",
    bar: "border-l-[#992777] border-[#dfb5d3] bg-[#f7e7f3] text-[#4a183b] hover:bg-[#efd4e8]",
    badge: "bg-[#f7e7f3] text-[#7c1d60] border-[#e5c2dc]",
    dot: "bg-[#992777]",
  },
  other: {
    id: "other",
    label: "其他观察",
    bar: "border-l-slate-500 border-slate-300/90 bg-slate-100/95 text-slate-800 hover:bg-slate-200/90",
    badge: "bg-slate-100 text-slate-700 border-slate-200",
    dot: "bg-slate-500",
  },
};

function classifySpan(title: string, category?: string): CategoryStyle {
  if (category === "homework") return CATEGORY_STYLES.writing;
  if (category === "readbook") return CATEGORY_STYLES.reading;
  if (category === "stand" || category === "walk" || category === "chase") return CATEGORY_STYLES.posture_move;
  if (category === "chat" || category === "discuss" || category === "hubbub") return CATEGORY_STYLES.discuss;
  if (category === "sleep") return CATEGORY_STYLES.rest;
  if (category === "playphone" || category === "call" || category === "usecomputer" || category === "eatordrink") {
    return CATEGORY_STYLES.device_other;
  }
  if (/举手|举[左右]手|手势/.test(title)) return CATEGORY_STYLES.hand_raise;
  if (/站立|站姿|起身|走动|移动|站在/.test(title)) return CATEGORY_STYLES.posture_move;
  if (/写|记笔记|作业|书写|画/.test(title)) return CATEGORY_STYLES.writing;
  if (/读|看书|翻书|阅读|看资料|看向书|看着.*书|看着.*纸/.test(title)) return CATEGORY_STYLES.reading;
  if (/交流|讨论|说话|聊天|转头/.test(title)) return CATEGORY_STYLES.discuss;
  if (/托着头|低头|趴|睡|撑头|伏在桌|摸额头|放在头上|后仰/.test(title)) return CATEGORY_STYLES.rest;
  if (/手机|电脑|进食|饮水|喝水|吃/.test(title)) return CATEGORY_STYLES.device_other;
  if (/坐姿|坐在|看向前方|看向教师|听讲|前倾|抬眼/.test(title)) return CATEGORY_STYLES.listening;
  return CATEGORY_STYLES.other;
}

interface PopoutState {
  span: ActionSpan;
  x: number;
  y: number;
}

export function EventTimeline({
  duration,
  time,
  items,
  spans,
  selectedId,
  onSelect,
  onSeek,
  tracks = [],
  windows = [],
  selectedTrack = 0,
  identityLabels = [],
  onSelectTrack,
  offset = 0,
  singlePerson = false,
  coverageJobId,
}: Props) {
  const d = Math.max(duration, 0.01);
  const pct = (t: number) => `${Math.max(0, Math.min(100, (100 * t) / d))}%`;
  const [popout, setPopout] = useState<PopoutState | null>(null);
  const popoutRef = useRef<HTMLDivElement>(null);

  const itemById = useMemo(
    () => new Map(items.map((item) => [item.id, item])),
    [items],
  );

  const spanCategory = (s: ActionSpan): CategoryStyle => {
    const linked = s.itemIds
      .map((id) => itemById.get(id))
      .find((i) => i && i.category && i.category !== "unknown" && i.category !== "other_observable_action");
    return classifySpan(s.title, linked?.category);
  };

  useEffect(() => {
    if (!popout) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPopout(null);
    };
    const onDown = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null;
      if (popoutRef.current?.contains(target)) return;
      if (target?.closest("[data-timeline-span]")) return;
      setPopout(null);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [popout]);

  const activeCategories = useMemo(() => {
    const seen = new Map<string, CategoryStyle>();
    for (const s of spans) {
      if (s.kind === "human") continue;
      const cat = spanCategory(s);
      if (!seen.has(cat.id)) seen.set(cat.id, cat);
    }
    return [...seen.values()];
  }, [spans, itemById]);

  const coverage = useMemo(
    () =>
      coverageIntervals(
        tracks,
        coverageJobId
          ? windows.filter((w) => w.job_id === coverageJobId)
          : windows,
        offset,
      ),
    [tracks, windows, offset, coverageJobId],
  );
  const placed = useMemo(() => {
    const ends = new Map<number, number[]>();
    return spans.map((s) => {
      const lanes = ends.get(s.trackId) || [];
      let row = lanes.findIndex((end) => end <= s.start + 0.002);
      if (row < 0) row = lanes.length;
      lanes[row] = s.end > s.start ? s.end : s.start + d * 0.015;
      ends.set(s.trackId, lanes);
      return { ...s, row };
    });
  }, [spans, d]);

  const rows = tracks.filter(
    (t) => !singlePerson || !selectedTrack || t.track_id === selectedTrack,
  );

  return (
    <section className={cardClass}>
      <div className={cardHeadClass}>
        <div className={cardTitleClass}>
          <SlidersHorizontal
            size={15}
            className="text-[#992777]"
          />
          <span>人物与动作时间线</span>
          <span className="tabular font-normal text-xs text-[#6e5a74]">
            真实录像 {fmt(duration)}
          </span>
        </div>
        <div className="hidden md:flex flex-wrap items-center gap-3 text-[11px] text-[#6e5a74]">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-3 rounded-full bg-[#d8c9dc]"/>
            人物可见
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-3 rounded-full bg-[#992777]/65"/>
            取证窗口
          </span>
          {activeCategories.map((cat) => (
            <span key={cat.id} className="inline-flex items-center gap-1">
              <span className={cn("h-2 w-2 rounded-full", cat.dot)} />
              {cat.label}
            </span>
          ))}
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-3.5 rounded-xs bg-emerald-600"/>
            教师确认
          </span>
        </div>
      </div>

      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2.5 text-[11px] text-[#6e5a74] md:hidden">
          <span className="inline-flex items-center gap-1">
            <span className="h-1.5 w-2.5 rounded-full bg-[#d8c9dc]"/>
            人物可见
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="h-1.5 w-2.5 rounded-full bg-[#992777]/65"/>
            取证窗口
          </span>
          {activeCategories.map((cat) => (
            <span key={cat.id} className="inline-flex items-center gap-1">
              <span className={cn("h-2 w-2 rounded-full", cat.dot)} />
              {cat.label}
            </span>
          ))}
          <span className="inline-flex items-center gap-1">
            <span className="h-2.5 w-3 rounded-xs bg-emerald-600"/>
            教师确认
          </span>
        </div>

        {/* Time ruler */}
        <div className="grid grid-cols-[120px_1fr] items-center gap-2 text-[11px] text-[#85708c]">
          <span className="pl-1 font-medium">人物</span>
          <div className="relative h-4">
            {Array.from({ length: 6 }, (_, i) => (d * i) / 5).map((t) => (
              <small
                key={t}
                className="tabular absolute top-0 text-[10px]"
                style={{
                  left: pct(t),
                  transform: t === d ? "translateX(-100%)": undefined,
                }}
              >
                {fmt(t)}
              </small>
            ))}
          </div>
        </div>

        {/* Lanes */}
        <div
          aria-label="人物动作区间"
          onScroll={() => setPopout(null)}
          className="max-h-64 space-y-1.5 overflow-y-auto pr-1"
        >
          {rows.map((track) => {
            const trackSpans = placed.filter(
              (s) => s.trackId === track.track_id,
            );
            const laneHeight = Math.max(
              54,
              52 + 26 * Math.max(0, ...trackSpans.map((s) => s.row)),
            );
            const isSelected = track.track_id === selectedTrack;
            const label = personTrackLabel(identityLabels, track.track_id, time);

            return (
              <div
                key={track.track_id}
                className={cn(
                  "grid grid-cols-[120px_1fr] items-stretch gap-2 rounded-xl border p-1.5 transition",
                  isSelected
                    ? "border-[#cfa1c3] bg-[#faf0f7]"
                    : "border-[#f0e5f2] bg-[#fcf9fd] hover:border-[#dfc9e3]",
                )}
              >
                <button
                  type="button"
                  onClick={() => onSelectTrack?.(track.track_id)}
                  title={label}
                  className={cn(
                    "truncate rounded-lg px-2 text-left text-xs font-medium transition hover:bg-[#f2e2ef]",
                    isSelected
                      ? "font-semibold text-[#992777]"
                      : "text-[#4a3450]",
                  )}
                >
                  {label}
                </button>

                <div
                  className="relative overflow-hidden rounded-lg bg-white"
                  style={{ height: laneHeight }}
                >
                  {track.segments.map((s, i) => (
                    <button
                      type="button"
                      key={"visible"+ i}
                      style={{
                        left: pct(s.local_start),
                        width: pct(s.local_end - s.local_start),
                        minWidth: 2,
                      }}
                      onClick={() => {
                        onSelectTrack?.(track.track_id);
                        onSeek(s.local_start);
                      }}
                      title={`人物可见：${fmt(s.local_start)}–${fmt(s.local_end)}`}
                      className="absolute top-1.5 h-1.5 rounded-full bg-[#dfd2e3] transition hover:bg-[#cbb7d2]"
                    />
                  ))}

                  {coverage
                    .filter((c) => c.trackId === track.track_id)
                    .map((c, index) => (
                      <button
                        type="button"
                        key={"coverage"+ index}
                        style={{
                          left: pct(c.start),
                          width: pct(c.end - c.start),
                          minWidth: 2,
                        }}
                        onClick={() => {
                          onSelectTrack?.(track.track_id);
                          onSeek(c.start);
                        }}
                        title={`${coverageNames[c.state]} ${fmt(c.start)}–${fmt(c.end)}`}
                        className={cn(
                          "absolute top-3.5 h-1.5 overflow-hidden rounded-full text-[9px] leading-3 text-transparent",
                          c.state === "observed"
                            ? "bg-[#992777]/50 hover:bg-[#992777]/75"
                            : c.state === "running"
                              ? "bg-[#ab2a84]/35 animate-pulse"
                              : c.state === "error"
                                ? "bg-rose-400/55"
                                : c.state === "unobservable"
                                  ? "bg-amber-300/50"
                                  : "bg-[#d8c9dc]/60",
                        )}
                      >
                        <span>{coverageNames[c.state]}</span>
                      </button>
                    ))}

                  {trackSpans.map((s) => {
                    const active =
                      (selectedId && s.itemIds.includes(selectedId)) ||
                      popout?.span.id === s.id;
                    const spanDuration = s.end - s.start;
                    const cat = spanCategory(s);
                    return (
                      <button
                        type="button"
                        key={s.id}
                        data-timeline-span={s.id}
                        style={{
                          left: pct(s.start),
                          width: pct(spanDuration),
                          top: 22 + s.row * 26,
                          minWidth: s.kind === "point"? 8 : 18,
                        }}
                        title={`${s.title} · ${fmt(s.start)}–${fmt(s.end)} · ${
                          s.kind === "human" ? "教师确认" : cat.label
                        }`}
                        onClick={(e) => {
                          const rect = e.currentTarget.getBoundingClientRect();
                          const hasPointer = e.clientX !== 0 || e.clientY !== 0;
                          const x = hasPointer ? e.clientX : rect.left + rect.width / 2;
                          const y = hasPointer ? e.clientY : rect.top + rect.height / 2;
                          setPopout((prev) =>
                            prev?.span.id === s.id ? null : { span: s, x, y },
                          );
                          const item = items.find((i) => i.id === s.itemIds[0]);
                          if (item) onSelect(item);
                          onSeek(s.start);
                        }}
                        className={cn(
                          "absolute flex h-6 items-center overflow-hidden rounded-sm px-2 text-left text-[11px] font-medium leading-none transition",
                          s.kind === "human"
                            ? "bg-emerald-600 text-white hover:bg-emerald-700"
                            : s.kind === "estimate"
                              ? cn("border-y border-r border-l-[3px]", cat.bar)
                              : cn("border-y border-r border-l-[3px]", cat.bar),
                          active && "z-10 ring-2 ring-[#6c347c] ring-offset-1",
                        )}
                      >
                        <span className="truncate">{s.title}</span>
                      </button>
                    );
                  })}

                  {/* Playhead */}
                  <div
                    className="pointer-events-none absolute inset-y-0 z-10 w-0.5 bg-[#ab2a84]"
                    style={{ left: pct(time) }}
                  />
                </div>
              </div>
            );
          })}

          {!rows.length ? (
            <p className="py-4 text-center text-xs text-[#6e5a74]">
              人物跟踪完成后显示可见区间。
            </p>
          ) : null}
        </div>

        <input
          aria-label="时间线定位"
          className="lt-range w-full"
          type="range"
          min={0}
          max={d}
          step=".001"
          value={Math.min(time, d)}
          onChange={(e) => onSeek(Number(e.target.value))}
        />
      </div>

      {popout && typeof document !== "undefined" ? createPortal((() => {
        const s = popout.span;
        const cat = spanCategory(s);
        const linkedItems = s.itemIds
          .map((id) => itemById.get(id))
          .filter((i): i is VisualItem => Boolean(i));
        const descriptions = [
          ...new Set(
            linkedItems.flatMap((i) =>
              i.description.map((t) => observationText(t).trim()).filter(Boolean),
            ),
          ),
        ].filter((text) => text !== s.title);
        const rawCategory = linkedItems.find(
          (i) => i.category && i.category !== "unknown" && i.category !== "other_observable_action",
        )?.category;
        const personName = personTrackLabel(identityLabels, s.trackId, s.start);
        const spanLen = Math.max(0, s.end - s.start);
        const popWidth = 320;
        const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
        const vh = typeof window !== "undefined" ? window.innerHeight : 800;
        const offset = 12;
        const preferredLeft =
          popout.x + offset + popWidth <= vw - 12
            ? popout.x + offset
            : popout.x - offset - popWidth;
        const left = Math.max(12, Math.min(vw - popWidth - 12, preferredLeft));
        const spaceBelow = vh - popout.y - offset;
        const spaceAbove = popout.y - offset;
        const showBelow = spaceBelow >= 240 || spaceBelow >= spaceAbove;
        return (
          <div
            ref={popoutRef}
            role="dialog"
            aria-label="时间线事件详情"
            style={{
              left,
              top: showBelow ? Math.min(vh - 60, popout.y + offset) : undefined,
              bottom: showBelow ? undefined : Math.max(12, vh - popout.y + offset),
            }}
            className="fixed z-50 w-80 max-w-[calc(100vw-24px)] rounded-xl border border-[#decbe3] bg-white p-3.5 shadow-xl shadow-[#3b1544]/15"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex flex-wrap items-center gap-1.5">
                <span
                  className={cn(
                    "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
                    s.kind === "human"
                      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                      : cat.badge,
                  )}
                >
                  <span
                    className={cn(
                      "h-1.5 w-1.5 rounded-full",
                      s.kind === "human" ? "bg-emerald-600" : cat.dot,
                    )}
                  />
                  {s.kind === "human" ? "教师确认" : cat.label}
                </span>
                <span className="text-xs font-medium text-[#6e5a74]">
                  {personName}
                </span>
              </div>
              <button
                type="button"
                aria-label="关闭详情"
                onClick={() => setPopout(null)}
                className="rounded-md p-0.5 text-[#85708c] transition hover:bg-[#f5ebf7] hover:text-[#4a3450]"
              >
                <X size={14} />
              </button>
            </div>

            <p className="mt-2 break-words text-sm font-semibold leading-snug text-[#2d1a33]">
              {s.title}
            </p>

            <div className="mt-2 flex items-center gap-1.5 text-xs text-[#6e5a74]">
              <Clock size={12} className="shrink-0 text-[#992777]" />
              <span className="tabular">
                {fmt(s.start)} – {fmt(s.end)}
              </span>
              {spanLen > 0 ? (
                <span className="tabular text-[11px] text-[#85708c]">
                  （{spanLen.toFixed(2)} 秒）
                </span>
              ) : null}
            </div>

            {rawCategory ? (
              <p className="mt-1.5 text-xs text-[#6e5a74]">
                事件类别：
                <span className="font-medium text-[#4a3450]">
                  {actionLabel[rawCategory] || rawCategory}
                </span>
              </p>
            ) : null}

            {descriptions.length > 0 ? (
              <div className="mt-2 max-h-28 space-y-1 overflow-y-auto rounded-lg bg-[#faf5fc] p-2 text-xs leading-relaxed text-[#4a3450]">
                {descriptions.map((desc, idx) => (
                  <p key={idx} className="break-words">
                    {desc}
                  </p>
                ))}
              </div>
            ) : null}

            {s.samples.length > 0 ? (
              <div className="mt-2 flex flex-wrap items-center gap-1 text-[11px] text-[#85708c]">
                <span>记录时刻：</span>
                {s.samples.slice(0, 6).map((t, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => onSeek(t)}
                    className="tabular rounded bg-[#f4eaf6] px-1.5 py-0.5 text-[#6e3b7a] transition hover:bg-[#e8d2ee]"
                  >
                    {fmt(t)}
                  </button>
                ))}
                {s.samples.length > 6 ? (
                  <span>等 {s.samples.length} 处</span>
                ) : null}
              </div>
            ) : null}

            <div className="mt-3 flex items-center justify-end gap-2 border-t border-[#f0e5f2] pt-2">
              <button
                type="button"
                onClick={() => onSeek(s.start)}
                className="inline-flex items-center gap-1 rounded-lg bg-[#f6ebf8] px-2.5 py-1 text-xs font-medium text-[#992777] transition hover:bg-[#ecd7f0]"
              >
                <Play size={11} />
                跳至起点
              </button>
              {spanLen > 0 ? (
                <button
                  type="button"
                  onClick={() => onSeek(s.end)}
                  className="inline-flex items-center gap-1 rounded-lg border border-[#e5d5ea] px-2.5 py-1 text-xs font-medium text-[#5c4563] transition hover:bg-[#faf4fc]"
                >
                  跳至终点
                </button>
              ) : null}
            </div>
          </div>
        );
      })(), document.body) : null}
    </section>
  );
}
