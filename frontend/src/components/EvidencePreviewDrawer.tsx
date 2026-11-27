import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { getPlaybackObservations } from "../api/workbench";
import type { PlaybackTarget } from "../api/review";
import type { Playback, TrackObservation } from "../models/backend";
import { fmt } from "../models/view";
import { VideoStage } from "./VideoStage";
import { btnClass } from "./ui/primitives";

export type PreviewHandler = (
  reportId: string, target: PlaybackTarget, trigger: HTMLButtonElement,
) => void;
export type PreviewIssue = {trigger: HTMLButtonElement; message: string};
export const playbackReasons: Record<Extract<Playback, {status: "unavailable"}>["reason"], string> = {
  report_stale: "回答的来源或授权已变化，不能播放旧引用。",
  reference_missing: "这条回答中没有该证据引用。",
  not_video: "该引用不是视频来源。",
  source_mismatch: "引用与登记录像不一致，不能播放。",
  media_unavailable: "原录像已删除、不可访问或播放文件缺失。",
  time_out_of_range: "引用时间不在可核实的录像范围内。",
};

export function playbackSegment(time: number, duration: number) {
  const segment = Math.floor(Math.max(0, Math.min(time, duration)) / 5);
  return {start: Math.max(0, segment * 5 - 0.2), end: Math.min(duration, (segment + 1) * 5 + 0.2)};
}

export function EvidencePreviewDrawer({
  playback, onClose, onMediaError,
}: {
  playback: Extract<Playback, {status: "available"}> | null;
  onClose: () => void;
  onMediaError: (message: string) => void;
}) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => close.current?.focus({preventScroll: true}), []);
  return (
    <aside
      role="dialog"
      aria-label="证据回放"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
      className="absolute inset-y-0 right-0 z-20 flex w-full max-w-xl flex-col overflow-y-auto border-l border-[#e8dce9] bg-white shadow-xl"
    >
      <header className="flex items-center justify-between gap-2 border-b border-[#e8dce9] px-4 py-3">
        <h2 className="text-base font-semibold text-[#231426]">证据回放</h2>
        <button ref={close} type="button" className={btnClass("ghost", "sm")} onClick={onClose}>
          <X size={14} />关闭回放
        </button>
      </header>
      {playback
        ? <PreviewPlayer playback={playback} onMediaError={onMediaError} />
        : <p role="status" className="p-4 text-sm text-slate-600">正在核对录像来源…</p>}
    </aside>
  );
}

function PreviewPlayer({playback, onMediaError}: {
  playback: Extract<Playback, {status: "available"}>;
  onMediaError: (message: string) => void;
}) {
  const {run_id, track_id, media_url, duration, local_range} = playback;
  const [time, setTime] = useState(local_range[0]);
  const [observations, setObservations] = useState<{
    start: number; end: number; rows: TrackObservation[];
  } | null>(null);
  const {start, end} = playbackSegment(time, duration);
  useEffect(() => {
    if (track_id === null) return;
    let active = true;
    void getPlaybackObservations(run_id, start, end)
      .then((rows) => {
        if (active) setObservations({start, end, rows: rows.filter((r) =>
          r.track_id === track_id && r.tracker_state === "matched_visible_detection")});
      })
      .catch(() => {
        if (active) setObservations(null);
      });
    return () => { active = false; };
  }, [run_id, track_id, start, end]);
  const rows = observations?.start === start && observations.end === end ? observations.rows : [];
  return (
    <div className="space-y-3 p-4">
      <p className="text-xs text-slate-600">
        文件内 {fmt(local_range[0])}{local_range[0] !== local_range[1] && `–${fmt(local_range[1])}`}
      </p>
      <VideoStage
        src={media_url}
        time={time}
        duration={duration}
        onTime={setTime}
        onSeek={setTime}
        observations={rows}
        selectedTrack={track_id ?? undefined}
        boxMode={track_id !== null && rows.length ? "single" : "hidden"}
        onMediaError={onMediaError}
      />
    </div>
  );
}
