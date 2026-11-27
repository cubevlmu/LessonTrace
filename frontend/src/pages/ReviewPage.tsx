import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Layers,
  Activity,
  Eye,
  EyeOff,
  User,
  Table2,
} from "lucide-react";
import { useSession } from "../state/session";
import { playbackActions } from "../adapters/playbackActions";
import { actionSpans, observationRows } from "../adapters/timeline";
import { visualItems } from "../adapters/event";
import { startJob, controlJob } from "../api/jobs";
import {
  getRunResults,
  getTrackGroups,
  saveTrackGroup,
  revokeTrackGroup,
  getFullPlan,
  getPlaybackObservations,
  getPlaybackIdentities,
  personLabel,
  personTrackLabel,
  type TrackGroup,
  type WorkbenchIdentity,
} from "../api/workbench";
import {
  canonicalTrack,
  memberTracks,
  groupedTracks,
  groupedObservations,
  presentationLabels,
} from "../adapters/trackGroups";
import { WorkbenchDialog } from "../components/WorkbenchDialog";
import type {
  DiscoveredEvent,
  WindowResult,
  TrackObservation,
} from "../models/backend";
import { videoUrl } from "../api/videos";
import {
  fmt,
  videoTitle,
  observationView,
  observationBasis,
  showObservation,
} from "../models/view";
import { SessionBar } from "../components/SessionBar";
import { VideoStage } from "../components/VideoStage";
import { EventTimeline } from "../components/EventTimeline";
import { EvidencePanel } from "../components/EvidencePanel";
import { CorrectionPanel } from "../components/CorrectionPanel";
import { PersonProfiles } from "../components/PersonProfiles";
import { useTrackQuality } from "../components/TrackQuality";
import {
  AlertBanner,
  badgeClass,
  btnClass,
  cardClass,
  cardHeadClass,
  cardTitleClass,
  cn,
  fieldClass,
  labelClass,
  selectClass,
  statusTone,
  textareaClass,
} from "../components/ui/primitives";

const jobBadgeLabels: Record<string, string> = {
  completed: "分析已保存",
  completed_with_errors: "分析已保存（含异常）",
  running: "分析中",
  queued: "等待分析",
  paused: "已暂停",
  pausing: "正在暂停",
  failed: "分析需重试",
  cancelled: "已取消",
};

export function ReviewPage() {
  const s = useSession();
  const [params] = useSearchParams();

  const [saved, setSaved] = useState<{
    run: string;
    events: DiscoveredEvent[];
    windows: WindowResult[];
    truncated: boolean;
  } | null>(null);
  const [groups, setGroups] = useState<TrackGroup[]>([]);
  const [groupInput, setGroupInput] = useState("");
  const [groupReason, setGroupReason] = useState("");
  const [groupBusy, setGroupBusy] = useState(false);
  const [dialog, setDialog] = useState<
    "analysis" | "groups" | null
  >(null);
  const [sideTab, setSideTab] = useState<"evidence" | "correction">(
    "evidence",
  );
  const [fullPlan, setFullPlan] = useState<Awaited<
    ReturnType<typeof getFullPlan>
  > | null>(null);

  const runId = s.data.job?.run_id;
  const fullJob = s.data.jobs.find(
    (j) =>
      j.run_id === runId &&
      j.config_and_runtime.config.analysis_mode === "full_timeline",
  );

  useEffect(() => {
    setGroups([]);
    setSaved(null);
  }, [runId]);

  const allWindows =
    saved && saved.run === runId ? saved.windows : s.data.windows;
  const allEvents = saved && saved.run === runId ? saved.events : s.data.events;

  useEffect(() => {
    let active = true;
    const run = s.data.job?.run_id;
    if (!run) {
      setSaved(null);
      setGroups([]);
      return;
    }
    const load = () =>
      Promise.all([getRunResults(run), getTrackGroups(run)])
        .then(([r, g]) => {
          if (active) {
            setSaved({ ...r, run });
            setGroups(g);
          }
        })
        .catch((e) => {
          if (active) s.setError(String(e));
        });
    void load();
    window.addEventListener("focus", load);
    const timer = setInterval(load, 15000);
    return () => {
      active = false;
      clearInterval(timer);
      window.removeEventListener("focus", load);
    };
  }, [runId, s.data.job?.processed_windows, s.data.events]);

  const items = useMemo(
    () => visualItems(allEvents, allWindows, s.data.scope?.start),
    [allEvents, allWindows, s.data.scope?.start],
  );

  const actions = useMemo(
    () => playbackActions(items, allWindows, s.data.scope?.start, s.data.tracks),
    [items, allWindows, s.data.scope?.start, s.data.tracks],
  );

  const [time, setTime] = useState(0);
  const [selected, setSelected] = useState("");
  const [track, setTrack] = useState(0);
  const [obs, setObs] = useState<TrackObservation[]>([]);
  const [boxMode, setBoxMode] = useState<"all"| "single"| "hidden">("all");
  const [identity, setIdentity] = useState<WorkbenchIdentity | null>(null);
  const [expanding, setExpanding] = useState(false);
  const [expandError, setExpandError] = useState("");

  // An anonymous display group must not spread a binding to a different raw track.
  const labels = useMemo(
    () =>
      identity
        ? presentationLabels(
            [...identity.bindings, ...identity.track_labels, ...identity.candidates],
            groups,
          )
        : [],
    [identity, groups],
  );

  const [filter, setFilter] = useState("all");
  const quality = useTrackQuality(s.data.job?.run_id);
  const [includePartial, setIncludePartial] = useState(true);
  const excluded = useMemo(() => new Set(
    quality?.tracks
      .filter(t => t.possible_nested_with !== null || t.quality_flags.includes("short_lived_region"))
      .map(t => t.track_id) || [],
  ), [quality]);
  const overlayObservations = useMemo(() => {
    const visible = includePartial || boxMode === "single"
      ? obs
      : obs.filter(o => !excluded.has(o.track_id));
    return groupedObservations(visible, groups);
  }, [excluded, includePartial, boxMode, obs, groups]);
  const members = memberTracks(track, groups);

  useEffect(() => {
    setTrack((id) => canonicalTrack(id, groups));
  }, [groups]);

  const displayTracks = useMemo(
    () => groupedTracks(s.data.tracks, groups),
    [s.data.tracks, groups],
  );
  const shown = useMemo(() => items.filter(i =>
    showObservation(i, filter) && (boxMode !== "single" || !track ||
      memberTracks(track, groups).includes(observationView(i).trackId)),
  ), [items, filter, boxMode, track, groups]);
  const spans = useMemo(() => actionSpans(allWindows, items, s.data.tracks),
    [allWindows, items, s.data.tracks]);
  const rows = useMemo(() => observationRows(shown, spans), [shown, spans]);
  const timelineSpans = useMemo(() => spans.map(span => ({
    ...span, trackId: canonicalTrack(span.trackId, groups),
  })), [spans, groups]);
  const timelineWindows = useMemo(() => allWindows.map(w => ({
    ...w, track_id: canonicalTrack(w.track_id, groups),
  })), [allWindows, groups]);

  const video = s.data.videos.find((v) => v.video_id === s.data.videoId);
  const duration = video?.metadata.duration || 0;
  const newerContinuous = s.data.jobs.find(
    (j) =>
      j.job_id !== s.data.job?.job_id &&
      j.run_id === s.data.job?.run_id &&
      j.config_and_runtime.reused_tracking_run_id &&
      Date.parse(String(j.created_at)) >
        Date.parse(String(s.data.job?.created_at || "")) &&
      !["failed", "cancelled"].includes(j.status),
  );

  const item =
    shown.find((i) => i.id === selected) ||
    (track && boxMode === "single"
      ? shown.find(
          (i) =>
            i.trackId === Number(params.get("track")) &&
            i.frames.some((f) => Math.abs(f.local_time - time) < 0.002),
        ) ||
        [...shown].sort(
          (a, b) => Math.abs(a.localStart - time) - Math.abs(b.localStart - time),
        )[0]
      : shown[0]);

  useEffect(() => {
    const requested = Number(params.get("t") || 0);
    setTime(
      Number.isFinite(requested) && requested >= 0 && requested <= duration
        ? requested
        : 0,
    );
    const tid = Number(params.get("track") || 0);
    setTrack(tid > 0 ? tid : 0);
    setBoxMode(tid > 0 ? "single": "all");
    setSelected("");
  }, [params, duration, runId]);

  useEffect(() => {
    let active = true;
    if (s.data.job?.run_id && duration > 0) {
      void getPlaybackObservations(
        s.data.job.run_id,
        Math.max(0, Math.floor(time / 5) * 5 - 0.2),
        Math.min(duration, (Math.floor(time / 5) + 1) * 5 + 0.2),
      )
        .then((v) => {
          if (active) setObs(v);
        })
        .catch((e) => {
          if (active) {
            setObs([]);
            s.setError(String(e));
          }
        });
    }
    return () => {
      active = false;
    };
  }, [s.data.job?.run_id, Math.floor(time / 5), duration]);

  useEffect(() => {
    setObs([]);
  }, [runId]);

  useEffect(() => {
    let active = true;
    setIdentity(null);
    const run = s.data.job?.run_id;
    const load = () => {
      if (run)
        void getPlaybackIdentities(run)
          .then((result) => {
            if (active) setIdentity(result);
          })
          .catch((e) => {
            if (active) {
              setIdentity(null);
              s.setError(String(e));
            }
          });
    };
    load();
    window.addEventListener("focus", load);
    window.addEventListener("lessontrace:identity-changed", load);
    const timer = setInterval(load, 15000);
    return () => {
      active = false;
      clearInterval(timer);
      window.removeEventListener("focus", load);
      window.removeEventListener("lessontrace:identity-changed", load);
    };
  }, [s.data.job?.run_id]);

  function selectTrack(id: number) {
    const canonical = canonicalTrack(id, groups);
    setTrack(canonical);
    setBoxMode(canonical ? "single": "all");
    setSelected("");
    const person = displayTracks.find((t) => t.track_id === canonical);
    if (
      person &&
      !person.segments.some(
        (seg) => seg.local_start <= time && time <= seg.local_end,
      )
    ) {
      setTime(person.segments[0]?.local_start || 0);
    }
  }

  async function editGroup(revoke?: string) {
    if (!runId) return;
    setGroupBusy(true);
    setExpandError("");
    try {
      if (revoke) await revokeTrackGroup(revoke);
      else {
        const ids = groupInput
          .split(/[,，、\s]+/)
          .filter(Boolean)
          .map(Number);
        if (ids.some((id) => !Number.isInteger(id) || id < 1))
          throw Error("请输入正确的轨迹编号");
        await saveTrackGroup(runId, ids, groupReason);
        setGroupInput("");
        setGroupReason("");
      }
      setGroups(await getTrackGroups(runId));
      setTrack(0);
      setBoxMode("all");
    } catch (e) {
      setExpandError(String(e));
    } finally {
      setGroupBusy(false);
    }
  }

  async function expandTrack() {
    if (!s.data.job?.run_id || !track || !video) return;
    setExpanding(true);
    setExpandError("");
    try {
      const selectedTrack = displayTracks.find((t) => t.track_id === track);
      if (!selectedTrack) throw Error("请先选择人物");
      const planned = selectedTrack.segments.reduce(
        (sum, seg) =>
          sum + Math.max(1, Math.ceil((seg.local_end - seg.local_start) / 8)),
        0,
      );
      const count = Math.min(24, planned);
      if (!count) throw Error("该人物尚无可分析的实际可见区间");
      const previous = s.data.job.config_and_runtime.config as
        | Record<string, unknown>
        | undefined;
      const result = await startJob(
        video.video_id,
        {
          ...previous,
          analysis_mode: "sampled",
          track_ids: members,
          max_tracks: members.length,
          max_windows: count,
          max_calls: count * 2,
          budget_seconds: 3600,
          window_seconds: 8,
          overlap: 0,
          frames: 2,
        },
        s.data.job.run_id,
      );
      await s.refresh();
      await s.loadJob(result.job.job_id);
    } catch (e) {
      setExpandError(String(e));
    } finally {
      setExpanding(false);
    }
  }

  function choose(id: string) {
    const i = items.find((v) => v.id === id);
    if (!i) return;
    setSelected(id);
    setTime(observationView(i).localStart);
    setTrack(canonicalTrack(observationView(i).trackId, groups));
  }

  async function observeClearPeople() {
    if (!video || !s.data.job?.run_id) return;
    setExpanding(true);
    setExpandError("");
    try {
      const result = await startJob(
        video.video_id,
        {
          device: "cpu",
          prefer_clear_targets: true,
          max_tracks: 3,
          max_windows: 6,
          max_calls: 12,
          frames: 2,
          input_long_edge: 384,
          timeout: 240,
          budget_seconds: 900,
        },
        s.data.job.run_id,
      );
      await s.refresh();
      await s.loadJob(result.job.job_id);
    } catch (e) {
      setExpandError(String(e));
    } finally {
      setExpanding(false);
    }
  }

  async function openAnalysisDialog() {
    setDialog("analysis");
    setFullPlan(null);
    setExpandError("");
    if (s.canWrite && runId) {
      try {
        setFullPlan(await getFullPlan(runId));
      } catch (e) {
        setExpandError(String(e));
      }
    }
  }

  async function startFull() {
    if (!video || !runId) return;
    setExpanding(true);
    setExpandError("");
    try {
      const result = await startJob(
        video.video_id,
        {
          analysis_mode: "full_timeline",
          timeline_window_seconds: 2,
          frames: 4,
          input_long_edge: 512,
          device: "cpu",
          timeout: 240,
          budget_seconds: 900,
          prefer_clear_targets: false,
        },
        runId,
      );
      if (result.job.status === "paused")
        await controlJob(result.job.job_id, "resume");
      await s.refresh();
      await s.loadJob(result.job.job_id);
      setTrack(0);
      setBoxMode("all");
      setDialog(null);
    } catch (e) {
      setExpandError(String(e));
    } finally {
      setExpanding(false);
    }
  }

  return (
    <div className="mx-auto max-w-[1520px] space-y-4">
      {/* Clean Studio Header (No redundant outer card chrome) */}
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="truncate text-lg font-bold tracking-tight text-[#231426] sm:text-xl">
              {videoTitle(video)}
            </h1>
            <span className={badgeClass(statusTone(s.data.job?.status || ""))}>
              {jobBadgeLabels[s.data.job?.status || ""] || "请选择课堂"}
            </span>
          </div>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-[#6e5a74]">
            <span>
              {identity?.classroom?.name || "匿名课堂"} · {displayTracks.length}
              组轨迹 · {items.length}条画面记录
            </span>
            <span className="opacity-40">·</span>
            <span className="tabular">单段录像 {duration.toFixed(2)}秒</span>
            <span className="hidden sm:inline opacity-40">·</span>
            <span className="hidden sm:inline">推断</span>
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={btnClass("secondary", "sm")}
            onClick={() => setDialog("groups")}
          >
            <Layers size={13} />
            整理重复人物
          </button>
          <button
            type="button"
            className={btnClass("secondary", "sm")}
            onClick={() => void openAnalysisDialog()}
          >
            <Activity size={13} />
            分析详情与全时段
          </button>
        </div>
      </header>

      {/* Full-timeline progress strip (only when active) */}
      {fullJob &&
        !["completed", "completed_with_errors", "cancelled"].includes(
          fullJob.status,
        ) && (
          <div className="flex flex-wrap items-center gap-3 rounded-xl border border-[#e3bed8] bg-[#faf0f7] px-4 py-2 text-xs">
            <strong className="text-[#992777]">
              全量进度 {fullJob.processed_windows}/
              {fullJob.total_windows || "待规划"}个窗口
            </strong>
            <progress
              max={fullJob.total_windows || 1}
              value={fullJob.processed_windows}
              className="h-2 flex-1 overflow-hidden rounded-full"
            />
            <button
              type="button"
              className={btnClass("ghost", "sm")}
              onClick={() => void s.loadJob(fullJob.job_id)}
            >
              查看全量任务
            </button>
          </div>
        )}

      {/* Dialogs */}
      {dialog === "analysis" && (
        <WorkbenchDialog title="分析详情与全时段分析" onClose={() => setDialog(null)}>
          <p className="text-xs text-[#6e5a74]">
            以下进度与预算仅属于当前任务；工作台时间线已汇总同次跟踪下各任务保存的观察。
          </p>
          <SessionBar />
          {saved?.truncated && (
            <AlertBanner tone="info">
              本次最多显示2000个保存窗口；当前列表不是全部分析范围。
            </AlertBanner>
          )}
          {quality && (
            <div className="space-y-2 rounded-xl border border-[#e8dce9] bg-[#faf7fb] p-3.5 text-xs">
              <p className="font-medium text-[#231426]">
                {quality.raw_track_count}条原始人物区域轨迹，不是学生人数；
                {quality.preferred_region_count}条适合优先观察动作。
              </p>
              <label className="flex items-center gap-2 text-[#4a3450]">
                <input
                  type="checkbox"
                  checked={includePartial}
                  onChange={(e) => setIncludePartial(e.target.checked)}
                />
                显示可能重复及短暂区域
              </label>
              <details className="text-[#6e5a74]">
                <summary className="cursor-pointer font-medium">
                  为什么后脑勺也会有框？
                </summary>
                <p className="mt-1 leading-relaxed">
                  检测器会记录局部或被遮挡的人物区域，轨迹编号不代表独立学生。嵌套区域只标为可能重复，不自动合并身份；默认优先显示稳定区域，原始记录仍可展开。
                </p>
              </details>
              <button
                type="button"
                className={btnClass("secondary", "sm")}
                disabled={expanding || !s.canWrite}
                onClick={() => void observeClearPeople()}
              >
                补充清晰人物观察
              </button>
            </div>
          )}
          {newerContinuous && (
            <div className="flex items-center justify-between gap-2 rounded-xl border border-[#e3bed8] bg-[#f9eff6] p-3 text-xs">
              <span>
                该录像有新的连续人物分析：{newerContinuous.processed_windows}/
                {newerContinuous.total_windows}个窗口已保存。
              </span>
              <button
                type="button"
                className={btnClass("secondary", "sm")}
                disabled={s.loading}
                onClick={() => void s.loadJob(newerContinuous.job_id)}
              >
                {newerContinuous.status === "completed"
                  ? "查看最新连续分析"
                  : "查看连续分析进度"}
              </button>
            </div>
          )}
          {identity?.classroom && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[#e8dce9] bg-[#faf7fb] p-3 text-xs">
              <div className="space-y-0.5">
                <span className="font-semibold text-[#231426]">
                  {identity.classroom.name}
                </span>
                <span className="ml-2 text-[#6e5a74]">
                  {identity.track_labels.length
                    ? `${identity.track_labels.length}个班级轨迹登记区间`
                    : identity.matching_status ===
                        "references_only_no_identity_enrollment"
                      ? "可从人物截图登记到班级，或提前上传获授权的照片匹配"
                      : identity.bindings.length
                        ? `${identity.bindings.length}个已确认身份区间`
                        : "尚无当前有效的姓名关联"}
                </span>
              </div>
              <Link
                className="font-medium text-[#992777] underline"
                to={"/classrooms?classroom=" + identity.classroom.classroom_id}
              >
                查看学生资料
              </Link>
            </div>
          )}

          {s.canWrite && runId && (
            <section className="space-y-3 border-t border-[#f0e5f2] pt-3">
              <h3 className="text-sm font-semibold text-[#231426]">
                全轨迹全时段分析
              </h3>
              <p className="text-xs leading-relaxed text-[#4a3450]">
                逐段分析所有已检测人物的每个可见区间，不再只选少量人物。默认每2秒规划一个窗口，最多4张真实帧，保持单并发。
              </p>
              {!fullPlan && !expandError && (
                <p role="status" className="text-xs text-[#6e5a74]">
                  正在统计全部人物与可见时段…
                </p>
              )}
              {fullPlan && (
                <div className="space-y-2 rounded-xl border border-[#e8dce9] bg-[#faf7fb] p-3.5 text-sm">
                  <p className="font-semibold text-[#231426]">
                    {fullPlan.selected_tracks}条原始轨迹 · {fullPlan.selected_windows}
                    个窗口 · 最多{fullPlan.maximum_model_requests}次模型请求
                  </p>
                  <p className="text-xs text-[#5f4965]">
                    可见轨迹累计{fullPlan.visible_track_seconds.toFixed(1)}
                    秒；同一时刻的不同人物分别计算，轨迹数不是学生人数。
                  </p>
                  {fullPlan.rough_processing_seconds !== null && (
                    <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">
                      按之前本机任务速度粗略估算约
                      {(fullPlan.rough_processing_seconds / 3600).toFixed(1)}
                      小时。这是历史任务参考，设备和帧数不同，不代表本次完成时间。任务可后台运行、暂停和恢复。
                    </p>
                  )}
                  <p className="text-xs text-[#6e5a74]">
                    全量覆盖不等于逐帧准确识别。遮挡、漏检和动作歧义会单独标明；不会用已有动作填满未分析时段。
                  </p>
                </div>
              )}
              {expandError ? <AlertBanner tone="danger">{expandError}</AlertBanner> : null}
              <button
                type="button"
                className={btnClass("primary", "md", "w-full")}
                disabled={!fullPlan || expanding || !s.canWrite}
                onClick={() => void startFull()}
              >
                {expanding ? "正在创建全量任务…" : "开始全量分析"}
              </button>
            </section>
          )}
        </WorkbenchDialog>
      )}

      {dialog === "groups"&& (
        <WorkbenchDialog title="整理重复人物"onClose={() => setDialog(null)}>
          <p className="text-xs text-[#6e5a74]">
            把同一人物的重复区域或断裂轨迹归在一起查看。保留原编号、可见空档和原始观察；归组不自动确认真实身份。
          </p>
          <div className="space-y-2">
            {groups.map((g) => (
              <div
                key={g.group_id}
                className="flex items-start justify-between gap-3 rounded-xl border border-[#e8dce9] bg-[#faf7fb] p-3 text-xs"
              >
                <div className="space-y-0.5">
                  <strong className="text-sm text-[#231426]">
                    人物 {String(g.track_ids[0]).padStart(3, "0")}
                  </strong>
                  <p className="text-[#4a3450]">
                    原始轨迹 {g.track_ids.join("、")} ·{" "}
                    {g.status === "demo_spatial_association"
                      ? "演示空间归组"
                      : "操作者归组"}
                  </p>
                  <p className="text-[#6e5a74]">{g.reason}</p>
                </div>
                {s.canWrite && (
                  <button
                    type="button"
                    className={btnClass("ghost", "sm")}
                    disabled={groupBusy}
                    onClick={() => void editGroup(g.group_id)}
                  >
                    撤销归组
                  </button>
                )}
              </div>
            ))}
            {!groups.length && (
              <p className="text-xs text-[#6e5a74]">
                尚未归组。请先回放核对，再填写需要合并显示的编号。
              </p>
            )}
          </div>
          {s.canWrite && (
            <form
              className="space-y-3 border-t border-[#f0e5f2] pt-3"
              onSubmit={(e) => {
                e.preventDefault();
                void editGroup();
              }}
            >
              <label className={labelClass}>
                原始轨迹编号
                <input
                  className={fieldClass}
                  aria-label="归组轨迹编号"
                  value={groupInput}
                  onChange={(e) => setGroupInput(e.target.value)}
                  placeholder="例如：55, 70, 79"
                  required
                />
              </label>
              <label className={labelClass}>
                归组依据
                <textarea
                  className={textareaClass}
                  aria-label="归组依据"
                  value={groupReason}
                  onChange={(e) => setGroupReason(e.target.value)}
                  minLength={5}
                  required
                />
              </label>
              <button
                type="submit"
                className={btnClass("primary", "md")}
                disabled={groupBusy}
              >
                保存人物归组
              </button>
            </form>
          )}
          {expandError ? <AlertBanner tone="danger">{expandError}</AlertBanner> : null}
        </WorkbenchDialog>
      )}

      {/* ROW 1: Above-the-Fold Cinema Deck + Docked Side Inspector */}
      <div className="grid items-stretch gap-4 xl:grid-cols-[minmax(0,1.65fr)_400px]">
        {/* Unified Cinema Deck (Video + Integrated Track Controls) */}
        <div className={cn(cardClass, "flex flex-col overflow-hidden")}>
          <VideoStage
            src={video ? videoUrl(video.video_id) : undefined}
            time={time}
            duration={duration}
            onTime={setTime}
            onSeek={setTime}
            observations={overlayObservations}
            actions={actions}
            selectedTrack={track}
            boxMode={boxMode}
            identityLabels={labels}
            onSelectTrack={selectTrack}
          />

          {/* Integrated Track & Overlay Strip */}
          <div className="space-y-2.5 border-t border-[#f0e5f2] bg-white px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2.5">
              <div className="flex items-center gap-2">
                <User size={14} className="text-[#992777]"/>
                <span className="text-xs font-semibold text-[#231426]">
                  学生
                </span>
                <select
                  className={`${selectClass} w-auto min-w-[150px] py-1 text-xs`}
                  aria-label="学生"
                  value={track}
                  onChange={(e) => selectTrack(Number(e.target.value))}
                >
                  <option value="0">全部学生</option>
                  {displayTracks.map((t) => (
                    <option key={t.track_id} value={t.track_id}>
                      {personTrackLabel(labels, t.track_id, time)}
                      {memberTracks(t.track_id, groups).length > 1
                        ? "（含"+
                          memberTracks(t.track_id, groups).join("、") +
                          "）"
                        : ""}
                    </option>
                  ))}
                </select>
              </div>

              <div
                className="flex flex-wrap items-center gap-1.5"
                role="group"
                aria-label="人物框显示"
              >
                {(
                  [
                    ["all", "显示全部框", Eye],
                    ["single", "只看选中人物", User],
                    ["hidden", "隐藏框", EyeOff],
                  ] as const
                ).map(([modeKey, modeLabel, ModeIcon]) => (
                  <button
                    key={modeKey}
                    type="button"
                    aria-pressed={boxMode === modeKey}
                    disabled={modeKey === "single"&& !track}
                    onClick={() => setBoxMode(modeKey)}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition disabled:opacity-40",
                      boxMode === modeKey
                        ? "border-[#cfa1c3] bg-[#f7eaf3] text-[#992777]"
                        : "border-[#e3d2e6] bg-white text-[#5f4965] hover:bg-[#faf4fb]",
                    )}
                  >
                    <ModeIcon size={12} />
                    {modeLabel}
                  </button>
                ))}
                {track > 0 && s.canWrite && (
                  <button
                    type="button"
                    className={btnClass("primary", "sm")}
                    disabled={
                      expanding ||
                      ["running", "queued", "pausing", "cancelling"].includes(
                        s.data.job?.status || "",
                      )
                    }
                    onClick={() => void expandTrack()}
                  >
                    {expanding ? "正在创建任务…": "扩展此人物的连续动作分析"}
                  </button>
                )}
              </div>
            </div>

            {expandError ? <AlertBanner tone="danger">{expandError}</AlertBanner> : null}

            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[#6e5a74]">
              {s.data.tracks.length ? (
                <details className="w-full">
                  <summary className="cursor-pointer font-medium text-[#4a3450]">
                    人物可见区间与轨迹详情
                    {members.length > 1
                      ? ` · 当前人物包含原始轨迹 ${members.join("、")}`
                      : ""}
                  </summary>
                  <div className="mt-2 space-y-2">
                    {displayTracks
                      .filter((t) => t.track_id === track)
                      .map((t) => (
                        <div key={t.track_id} className="space-y-1.5">
                          <p>
                            {personTrackLabel(labels, t.track_id, time)} ·{" "}
                            {t.observed_frame_count}次实际观测 ·
                            未观测间隔不补位置
                          </p>
                          <div className="flex flex-wrap gap-1.5">
                            {t.segments.map((seg, i) => (
                              <button
                                type="button"
                                key={i}
                                onClick={() => setTime(seg.local_start)}
                                className="tabular rounded-md border border-[#e3d2e6] bg-[#faf7fb] px-2 py-0.5 text-[11px] font-medium text-[#4a3450] hover:border-[#992777] hover:bg-[#f7eaf3]"
                              >
                                {fmt(seg.local_start)}—{fmt(seg.local_end)}
                              </button>
                            ))}
                          </div>
                        </div>
                      ))}
                  </div>
                </details>
              ) : (
                <p>
                  尚无已保存人物轨迹。无人场景、尚未完成跟踪或处理异常需分别查看任务状态。
                </p>
              )}
            </div>
          </div>
        </div>

        {/* Right Docked Unified Inspector Card (height matched to video card on xl+) */}
        <aside className={cn(cardClass, "relative flex flex-col overflow-hidden")}>
          <div className="flex min-h-0 flex-1 flex-col xl:absolute xl:inset-0">
          <div
            className="grid grid-cols-2 border-b border-[#f0e5f2] bg-[#faf7fb] p-1.5"
            role="tablist"
            aria-label="观察操作"
          >
            {(
              [
                ["evidence", "证据面板"],
                ["correction", "修正观察"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={sideTab === key}
                onClick={() => setSideTab(key)}
                className={cn(
                  "rounded-lg px-2 py-1.5 text-xs font-medium transition",
                  sideTab === key
                    ? "bg-white text-[#992777] font-semibold shadow-2xs"
                    : "text-[#5f4965] hover:text-[#231426]",
                )}
              >
                {label}
              </button>
            ))}
          </div>

          <div
            role="tabpanel"
            aria-label={sideTab === "evidence" ? "证据面板" : "修正观察"}
            className="flex-1 space-y-3 overflow-y-auto p-3"
          >
            {sideTab === "evidence" && (
              <EvidencePanel
                item={item}
                identityLabels={labels}
                onSeek={(t) => {
                  setTime(t);
                  if (item) setTrack(canonicalTrack(item.trackId, groups));
                }}
              />
            )}

            {sideTab === "correction" && <CorrectionPanel item={item} />}
          </div>
          </div>
        </aside>
      </div>

      {/* ROW 2: Full-Width Interactive Timeline */}
      <EventTimeline
        duration={duration}
        time={time}
        items={items}
        spans={timelineSpans}
        selectedId={item?.id}
        onSelect={(i) => choose(i.id)}
        onSeek={setTime}
        tracks={displayTracks}
        windows={timelineWindows}
        selectedTrack={track}
        identityLabels={labels}
        onSelectTrack={selectTrack}
        singlePerson={boxMode === "single"}
        offset={s.data.scope?.start || 0}
        coverageJobId={fullJob?.job_id}
      />

      {/* ROW 3: Side-by-Side Observation Records & Person Profiles */}
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        {/* Observation Records Table */}
        <section className={cardClass}>
          <div className={cardHeadClass}>
            <h2 className={cardTitleClass}>
              <Table2 size={15} className="text-[#992777]"/>
              画面观察记录
            </h2>
            <span className="text-xs text-[#6e5a74]">
              {rows.length} 个事件 · {shown.length} 条原始记录
            </span>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#f0e5f2] bg-[#fcf9fd] px-4 py-2.5 text-xs">
            <label className="flex items-center gap-2 font-medium text-[#4a3450]">
              显示内容
              <select
                className={`${selectClass} w-auto py-1 text-xs`}
                aria-label="观察显示范围"
                value={filter}
                onChange={(e) => {
                  setFilter(e.target.value);
                  setSelected("");
                }}
              >
                <option value="observed">近似识别与动作描述</option>
                <option value="classified">模型支持或教师确认</option>
                <option value="all">全部片段（含待查看）</option>
              </select>
            </label>
            <span className="text-[#6e5a74]">
              点击记录可回放证据
            </span>
          </div>

          <div className="max-h-[420px] overflow-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead className="sticky top-0 z-10 border-b border-[#e8dce9] bg-[#faf7fb]/95 text-[#6e5a74] backdrop-blur-xs">
                <tr>
                  <th className="px-4 py-2.5 font-semibold">时间</th>
                  <th className="px-4 py-2.5 font-semibold">
                    识别状态与画面描述
                  </th>
                  <th className="px-4 py-2.5 font-semibold">观察对象</th>
                  <th className="px-4 py-2.5 font-semibold">观察依据</th>
                  <th className="px-4 py-2.5 font-semibold">结论来源</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#f2eaf4]">
                {rows.map(row => {
                  const i = row.item;
                  const merged = row.members.length > 1;
                  const v = observationView(i);
                  const active = row.members.some(member => member.id === item?.id);
                  return (
                    <tr
                      key={i.id}
                      onClick={() => choose(i.id)}
                      className={cn(
                        "cursor-pointer transition",
                        active
                          ? "bg-[#f7eaf3]/80"
                          : "hover:bg-[#faf6fb]",
                      )}
                    >
                      <td className="whitespace-nowrap px-4 py-2.5">
                        <button
                          type="button"
                          className="tabular rounded-md bg-[#f5eff7] px-2 py-0.5 font-medium text-[#6e1a55] hover:bg-[#edd4e6]"
                        >
                          {fmt(row.start)}{merged && row.end > row.start ? "–" + fmt(row.end) : ""}
                        </button>
                      </td>
                      <td className="px-4 py-2.5">
                        <strong className="block font-semibold text-[#231426]">
                          {row.title}
                        </strong>
                        {merged ? (
                          <details className="mt-1 text-[#5f4965]" onClick={e => e.stopPropagation()}>
                            <summary className="cursor-pointer">查看 {row.members.length} 条原始记录</summary>
                            <div className="mt-1 flex flex-col items-start gap-1">
                              {row.members.map(member => {
                                const detail = observationView(member);
                                return (
                                  <button type="button" key={member.id} onClick={() => choose(member.id)}
                                    className="text-left text-[#6e1a55] hover:underline">
                                    {fmt(detail.localStart)} · {detail.title}
                                  </button>
                                );
                              })}
                            </div>
                          </details>
                        ) : v.description && v.description !== v.title ? (
                          <div className="mt-0.5 text-[#5f4965]">{v.description}</div>
                        ) : null}
                      </td>
                      <td className="whitespace-nowrap px-4 py-2.5 text-[#4a3450]">
                        {personLabel(
                          labels,
                          canonicalTrack(v.trackId, groups),
                          v.localStart,
                        )}
                        {canonicalTrack(i.trackId, groups) !== i.trackId && (
                          <small className="text-[#8f7a96]">
                            {" "}
                            · 原轨迹{i.trackId}
                          </small>
                        )}
                      </td>
                      <td
                        className="whitespace-nowrap px-4 py-2.5 text-[#6e5a74]"
                        title={v.reliabilityReason}
                      >
                        {merged ? `${row.samples} 个取证时刻` : observationBasis(i)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-2.5">
                        <span
                          className={badgeClass(
                            v.source === "教师确认"
                              ? "success"
                              : v.source === "模型支持"
                                ? "info"
                                : "warning",
                          )}
                        >
                          {v.source}
                        </span>
                      </td>
                    </tr>
                  );
                })}
                {!shown.length && (
                  <tr>
                    <td
                      colSpan={5}
                      className="px-4 py-8 text-center text-[#6e5a74]"
                    >
                      当前筛选下没有记录。可切换“全部片段”查看视频与待核对内容；这不表示没有课堂行为。
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        {/* Person Profiles */}
        {s.data.job?.run_id ? (
          <PersonProfiles
            excludedTrackIds={includePartial ? [] : [...excluded]}
            trackGroups={groups}
            runId={s.data.job.run_id}
            currentTime={time}
            selectedTrack={track}
            labels={labels}
            classroomId={identity?.classroom?.classroom_id}
            version={allWindows.filter((w) => w.status === "completed").length}
            onSelect={(id, t) => {
              selectTrack(id);
              if (t !== undefined) setTime(t);
            }}
          />
        ) : null}
      </div>
    </div>
  );
}
