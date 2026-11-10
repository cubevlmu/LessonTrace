import { useEffect, useMemo,useRef, useState } from "react";
import type { TrackObservation } from "../models/backend";
import { fmt } from "../models/view";
import { projectBox, observationFrames,visibleObservations } from "../adapters/video";
import {personLabel,type IdentityLabel} from '../api/workbench';
import { Icon } from "./Icon";
interface Props {
  src?: string;
  time: number;
  duration: number;
  onTime: (time: number) => void;
  onSeek: (time: number) => void;
  observations?: TrackObservation[];
  label?: string;
  selectedTrack?:number;
  boxMode?:'all'|'single'|'hidden';
  identityLabels?:IdentityLabel[];
  onSelectTrack?:(trackId:number)=>void;
}
export function VideoStage({
  src,
  time,
  duration,
  onTime,
  onSeek,
  observations = [],
  label = "单段录像",
  selectedTrack=0,boxMode='all',identityLabels=[],onSelectTrack,
}: Props) {
  const frameIndex=useMemo(()=>observationFrames(observations),[observations]);
  const vref = useRef<HTMLVideoElement>(null),
    canvas = useRef<HTMLCanvasElement>(null),
    rows = useRef(frameIndex);
  const display=useRef({selectedTrack,boxMode,identityLabels});
  display.current={selectedTrack,boxMode,identityLabels};
  const redraw = useRef<((t: number) => void) | null>(null);
  const lastEmitted = useRef<number | null>(null);
  rows.current = frameIndex;
  const [error, setError] = useState(""),
    [playing, setPlaying] = useState(false);
  useEffect(() => {
    setError("");
    setPlaying(false);
    lastEmitted.current = null;
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
        setError("尚未加载视频时间信息，请稍后重试定位");
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
      const options=display.current;
      const visible=options.boxMode==='hidden'?[]:visibleObservations(rows.current,mediaTime).filter(o=>options.boxMode!=='single'||o.track_id===options.selectedTrack);
      c.dataset.visibleTracks=String(visible.length);
      for(const o of visible){
        const b=projectBox(o,c.width,c.height),focused=o.track_id===options.selectedTrack;
        ctx.strokeStyle=focused?'#ffd25d':'#30e0df';ctx.lineWidth=focused?3:1.5;
        ctx.globalAlpha=options.selectedTrack&&!focused ? .55 : 1;
        ctx.strokeRect(b.x,b.y,b.width,b.height);ctx.font='12px system-ui';
        const name=personLabel(options.identityLabels,o.track_id,mediaTime);const width=ctx.measureText(name).width+8;const x=Math.min(b.x,Math.max(0,c.width-width));const y=Math.max(16,b.y);
        ctx.fillStyle=focused?'#6c347c':'#16353dcc';ctx.fillRect(x,y-16,width,17);ctx.fillStyle='#fff';ctx.fillText(name,x+4,y-3);
      }
      ctx.globalAlpha=1;
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
  }, [observations, time,selectedTrack,boxMode,identityLabels]);
  async function toggle() {
    const v = vref.current;
    if (!v) return;
    try {
      if (v.paused) await v.play();
      else v.pause();
    } catch (e) {
      setError("浏览器未能播放，请重试：" + String(e));
    }
  }
  return (
    <section className="stage" aria-label="课堂录像">
      <div
        className="stage__frame stage__frame--video"
        style={{ position: "relative" }}
      >
        {src ? (
          <video
            ref={vref}
            data-testid="classroom-video"
            className="stage__video"
            src={src}
            playsInline
            preload="metadata"
            onTimeUpdate={(e) => {
              lastEmitted.current = e.currentTarget.currentTime;
              onTime(e.currentTarget.currentTime);
            }}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onLoadedMetadata={() => {
              if (
                vref.current &&
                Math.abs(vref.current.duration - duration) > 0.2
              )
                setError("浏览器时长与登记时长不一致，请核查视频时间基");
            }}
            onError={() =>
              setError(
                "真实媒体加载失败，请检查登录、源文件及浏览器编码。可以刷新后重试。",
              )
            }
          />
        ) : (
          <div className="stage__note">
            当前没有可播放的服务器录像，请先导入。
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
            pointerEvents: onSelectTrack?'auto':"none",
            cursor:onSelectTrack?'pointer':undefined,
          }}
          onClick={e=>{const c=canvas.current,v=vref.current;if(!c||!v||!onSelectTrack||boxMode==='hidden')return;const rect=c.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;const hit=visibleObservations(rows.current,v.currentTime).map(o=>({o,b:projectBox(o,c.width,c.height)})).filter(({o,b})=>(boxMode!=='single'||o.track_id===selectedTrack)&&x>=b.x&&x<=b.x+b.width&&y>=b.y&&y<=b.y+b.height).sort((a,b)=>a.b.width*a.b.height-b.b.width*b.b.height)[0];if(hit)onSelectTrack(hit.o.track_id);}}
        />
        <div className="stage__badges">
          <span className="stage__cam">
            <Icon name="camera" size={13} />
            {label}
          </span>
        </div>
        <div className="stage__hint">{boxMode==='all'?'显示全部人物，点击框可单独查看':boxMode==='single'?'仅显示所选人物':'人物框已隐藏'}</div>
      </div>
      {error && (
        <p role="alert" className="upload__error">
          {error}
          <button
            className="btn btn--ghost"
            onClick={() => vref.current?.load()}
          >
            重试播放
          </button>
        </p>
      )}
      <div className="stage__bar">
        <button
          type="button"
          className="stage__play"
          aria-label={playing ? "暂停" : "播放"}
          onClick={() => void toggle()}
          disabled={!src}
        >
          <Icon name={playing ? "pause" : "play"} size={16} />
        </button>
        <span className="stage__time tabular">
          {fmt(time)} / {fmt(duration)}
        </span>
        <input
          className="product-seek"
          aria-label="视频时间定位"
          type="range"
          min="0"
          max={Math.max(duration, 0.01)}
          step="0.001"
          value={Math.min(time, duration)}
          onChange={(e) => onSeek(Number(e.target.value))}
        />
        <span className="faint">局部秒</span>
      </div>
    </section>
  );
}
