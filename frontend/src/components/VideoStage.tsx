import { useEffect, useMemo, useRef, useState } from "react";
import { Play, Pause, Video, RotateCcw } from "lucide-react";
import { playbackActionAt, type PlaybackActions } from "../adapters/playbackActions";
import { fmt } from "../models/view";
import {
  type PlaybackObservation,
  projectBox,
  observationFrames,
  visibleObservations,
} from "../adapters/video";
import { personLabel, type IdentityLabel } from "../api/workbench";
import { AlertBanner, btnClass } from "./ui/primitives";

interface Props {
  src?: string;
  time: number;
  duration: number;
  onTime: (time: number) => void;
  onSeek: (time: number) => void;
  observations?: PlaybackObservation[];
  actions?: PlaybackActions;
  label?: string;
  selectedTrack?: number;
  boxMode?: "all"| "single"| "hidden";
  identityLabels?: IdentityLabel[];
  onSelectTrack?: (trackId: number) => void;
  onMediaError?: (message: string) => void;
}

export function VideoStage({
  src,
  time,
  duration,
  onTime,
  onSeek,
  observations = [],
  actions,
  label = "单段录像",
  selectedTrack = 0,
  boxMode = "all",
  identityLabels = [],
  onSelectTrack,
  onMediaError,
}: Props) {
  const frameIndex = useMemo(
    () => observationFrames(observations),
    [observations],
  );
  const vref = useRef<HTMLVideoElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const rows = useRef(frameIndex);
  const display = useRef({ selectedTrack, boxMode, identityLabels, actions });
  display.current = { selectedTrack, boxMode, identityLabels, actions };
  const redraw = useRef<((t: number) => void) | null>(null);
  const lastEmitted = useRef<number | null>(null);
  rows.current = frameIndex;

  const [error, setError] = useState("");
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    const v = vref.current;
    setError("");
    setPlaying(false);
    lastEmitted.current = null;
    return () => {
      if (v && !v.paused) v.pause();
    };
  }, [src]);

  useEffect(() => {
    const v = vref.current;
    if (
      lastEmitted.current !== null &&
      Math.abs(time - lastEmitted.current) < 0.0005
    )
      return;
    if (v && Number.isFinite(time) && Math.abs(v.currentTime - time) > 0.002) {
      try {
        v.currentTime = Math.max(0, Math.min(duration, time));
      } catch {
        if (v.readyState > 0) setError("尚未加载视频时间信息，请稍后重试定位");
      }
    }
  }, [time, duration, src]);

  useEffect(() => {
    const v = vref.current;
    if (!v) return;
    let token = 0;
    const draw = (mediaTime: number) => {
      const c = canvas.current;
      if (!c) return;
      const box = v.getBoundingClientRect();
      c.width = box.width;
      c.height = box.height;
      const ctx = c.getContext("2d");
      if (!ctx) return;
      ctx.clearRect(0, 0, c.width, c.height);
      const options = display.current;
      const visible =
        options.boxMode === "hidden"
          ? []
          : visibleObservations(rows.current, mediaTime).filter(
              (o) =>
                options.boxMode !== "single"||
                o.track_id === options.selectedTrack,
            );
      c.dataset.visibleTracks = String(visible.length);
      for (const o of visible) {
        const b = projectBox(o, c.width, c.height);
        const focused = o.track_id === options.selectedTrack;
        ctx.strokeStyle = focused ? "#ffd25d": "#30e0df";
        ctx.lineWidth = focused ? 3 : 1.5;
        ctx.globalAlpha = options.selectedTrack && !focused ? 0.55 : 1;
        ctx.strokeRect(b.x, b.y, b.width, b.height);
        ctx.font = "600 12px system-ui, -apple-system, sans-serif";
        const name = personLabel(options.identityLabels, o.track_id, mediaTime);
        const action = playbackActionAt(options.actions, o.sourceTrackId ?? o.track_id, mediaTime);
        const shortAction = action.length > 20 ? action.slice(0, 19) + "…" : action;
        const caption = `${shortAction} · ${name}`;
        const width = Math.min(c.width, ctx.measureText(caption).width + 10);
        const x = Math.min(b.x, Math.max(0, c.width - width));
        const y = Math.max(18, b.y);
        ctx.fillStyle = focused ? "#6c347c": "rgba(22, 53, 61, 0.85)";
        ctx.fillRect(x, y - 18, width, 18);
        ctx.fillStyle = "#ffffff";
        ctx.fillText(caption, x + 5, y - 5, Math.max(1, width - 10));
      }
      ctx.globalAlpha = 1;
    };
    redraw.current = draw;
    const listener = () => draw(v.currentTime);
    v.addEventListener("seeked", listener);
    const resize =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(listener)
        : null;
    resize?.observe(v);
    if (v.requestVideoFrameCallback) {
      const tick: VideoFrameRequestCallback = (_n, m) => {
        draw(m.mediaTime);
        token = v.requestVideoFrameCallback(tick);
      };
      token = v.requestVideoFrameCallback(tick);
      return () => {
        v.cancelVideoFrameCallback(token);
        v.removeEventListener("seeked", listener);
        resize?.disconnect();
        redraw.current = null;
      };
    }
    v.addEventListener("timeupdate", listener);
    return () => {
      v.removeEventListener("timeupdate", listener);
      v.removeEventListener("seeked", listener);
      resize?.disconnect();
      redraw.current = null;
    };
  }, [src]);

  useEffect(() => {
    if (vref.current && !vref.current.seeking)
      redraw.current?.(vref.current.currentTime);
  }, [observations, time, selectedTrack, boxMode, identityLabels, actions]);

  async function toggle() {
    const v = vref.current;
    if (!v) return;
    try {
      if (v.paused) await v.play();
      else v.pause();
    } catch (e) {
      setError("浏览器未能播放，请重试："+ String(e));
    }
  }

  return (
    <section
      aria-label="课堂录像"
      className="overflow-hidden rounded-t-2xl bg-[#130c17]"
    >
      <div className="relative aspect-video max-h-[54vh] w-full overflow-hidden bg-[#130c17]">
        {src ? (
          <video
            ref={vref}
            data-testid="classroom-video"
            className="h-full w-full object-contain"
            src={src}
            playsInline
            preload="metadata"
            onTimeUpdate={(e) => {
              lastEmitted.current = e.currentTarget.currentTime;
              onTime(e.currentTarget.currentTime);
            }}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onLoadedMetadata={(e) => {
              const video = e.currentTarget;
              if (Number.isFinite(time)) video.currentTime = Math.max(0, Math.min(duration, time));
              setError(Math.abs(video.duration - duration) > 0.2
                ? "浏览器时长与登记时长不一致，请核查视频时间基"
                : "");
              onMediaError?.("");
            }}
            onError={() => {
              const message = "真实媒体加载失败，请检查登录、源文件及浏览器编码。";
              setError(message);
              onMediaError?.(message);
            }}
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-[#b7a4be]">
            <Video size={28} className="opacity-50"/>
            <p>当前没有可播放的服务器录像，请先导入。</p>
          </div>
        )}

        <canvas
          ref={canvas}
          data-testid="track-overlay"
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            pointerEvents: onSelectTrack ? "auto": "none",
            cursor: onSelectTrack ? "pointer": undefined,
          }}
          onClick={(e) => {
            const c = canvas.current;
            const v = vref.current;
            if (!c || !v || !onSelectTrack || boxMode === "hidden") return;
            const rect = c.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            const hit = visibleObservations(rows.current, v.currentTime)
              .map((o) => ({ o, b: projectBox(o, c.width, c.height) }))
              .filter(
                ({ o, b }) =>
                  (boxMode !== "single"|| o.track_id === selectedTrack) &&
                  x >= b.x &&
                  x <= b.x + b.width &&
                  y >= b.y &&
                  y <= b.y + b.height,
              )
              .sort(
                (a, b) => a.b.width * a.b.height - b.b.width * b.b.height,
              )[0];
            if (hit) onSelectTrack(hit.o.track_id);
          }}
        />

        {/* Top-left camera badge */}
        <div className="pointer-events-none absolute top-3 left-3 flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-[#1a0f21]/80 px-2.5 py-1 text-[11px] font-medium text-white backdrop-blur-sm">
            <Video size={12} className="text-[#e076c1]"/>
            {label}
          </span>
        </div>

        {/* Bottom-right overlay hint */}
        <div className="pointer-events-none absolute right-3 bottom-3 rounded-full bg-[#1a0f21]/80 px-2.5 py-1 text-[11px] text-[#f0e5f2] backdrop-blur-sm">
          {boxMode === "all"
            ? "显示全部人物，点击框可单独查看"
            : boxMode === "single"
              ? "仅显示所选人物"
              : "人物框已隐藏"}
        </div>
      </div>

      {error ? (
        <div className="bg-white p-3">
          <AlertBanner
            tone="danger"
            action={
              <button
                type="button"
                className={btnClass("ghost", "sm")}
                onClick={() => vref.current?.load()}
              >
                <RotateCcw size={13} />
                重试播放
              </button>
            }
          >
            {error}
          </AlertBanner>
        </div>
      ) : null}

      {/* Cinema Transport Bar */}
      <div className="flex items-center gap-3 border-t border-[#2d1e36] bg-[#1b1122] px-4 py-2.5 text-white">
        <button
          type="button"
          aria-label={playing ? "暂停": "播放"}
          onClick={() => void toggle()}
          disabled={!src}
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#992777] text-white transition hover:bg-[#ab2a84] disabled:opacity-40"
        >
          {playing ? <Pause size={15} /> : <Play size={15} className="ml-0.5"/>}
        </button>

        <span className="tabular shrink-0 text-xs font-medium text-[#e9dced]">
          {fmt(time)} <span className="text-[#8e7496]">/</span> {fmt(duration)}
        </span>

        <input
          className="lt-range flex-1"
          aria-label="视频时间定位"
          type="range"
          min="0"
          max={Math.max(duration, 0.01)}
          step="0.001"
          value={Math.min(time, duration)}
          onChange={(e) => onSeek(Number(e.target.value))}
        />

        <span className="hidden sm:inline shrink-0 text-[11px] text-[#a18ea8]">
          局部秒
        </span>
      </div>
    </section>
  );
}
