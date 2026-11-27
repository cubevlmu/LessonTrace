import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw, Pause, Play, XCircle, RotateCcw } from "lucide-react";
import { useSession } from "../state/session";
import { controlJob, getDiagnostics } from "../api/jobs";
import {
  AlertBanner,
  badgeClass,
  btnClass,
  cardClass,
  fieldClass,
  statusTone,
} from "./ui/primitives";

const names: Record<string, string> = {
  queued: "等待开始",
  tracking: "人物检测与跟踪",
  windowing: "生成时序窗口",
  discovery: "视觉观察与证据核验",
  report_ready: "分析结果已保存",
  running: "处理中",
  paused: "已暂停",
  pausing: "正在暂停",
  cancelled: "已取消",
  cancelling: "正在取消",
  failed: "处理异常",
  completed: "已完成",
  completed_with_errors: "分析结束，含异常记录",
};

export function SessionBar() {
  const s = useSession();
  const j = s.data.job;
  const full =
    j?.config_and_runtime.config.analysis_mode === "full_timeline";
  const [busy, setBusy] = useState(false);
  const [additional, setAdditional] = useState(0);
  const [clock, setClock] = useState(Date.now());
  const [diagnostics, setDiagnostics] = useState<
    { message: string; detail: string }[]
  >([]);

  useEffect(() => {
    if (!["queued", "running", "pausing"].includes(j?.status || "")) return;
    const t = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(t);
  }, [j?.status]);

  useEffect(() => {
    setDiagnostics([]);
    let active = true;
    if (
      j &&
      (["failed", "paused", "completed_with_errors"].includes(j.status) ||
        j.failed_windows > 0)
    ) {
      void getDiagnostics(j.job_id)
        .then((r) => {
          if (active) setDiagnostics(r.errors);
        })
        .catch((e) => {
          if (active) s.setError(String(e));
        });
    }
    return () => {
      active = false;
    };
  }, [j?.job_id, j?.status, j?.failed_windows]);

  async function act(action: "pause"| "resume"| "cancel"| "retry") {
    if (!j) return;
    setBusy(true);
    try {
      await controlJob(
        j.job_id,
        action,
        action === "resume"|| action === "retry"? additional : 0,
      );
      await s.loadJob(j.job_id);
    } catch (e) {
      s.setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!j) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
        <span className="text-slate-600">
          当前没有分析任务。
        </span>
        <Link to="/"className={btnClass("primary", "sm")}>
          导入并分析录像
        </Link>
      </div>
    );
  }

  const pct = j.total_windows
    ? Math.min(100, (100 * j.processed_windows) / j.total_windows)
    : 0;

  return (
    <section
      className={cardClass}
      data-status={j.status}
      aria-label="分析任务进度"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
        <div className="flex items-center gap-2.5 text-sm">
          <span className={badgeClass(statusTone(j.status))}>
            {names[j.status] || j.status}
          </span>
          <span className="text-xs text-slate-600">
            {names[j.stage] || j.stage}
          </span>
        </div>
        <Link className={btnClass("ghost", "sm")} to="/">
          切换录像或任务
        </Link>
      </div>

      <div className="space-y-3 p-4 text-sm">
        {["completed", "completed_with_errors"].includes(j.status) &&
          j.config_and_runtime.coverage && (
            <p className="text-xs text-slate-600">
              {j.config_and_runtime.coverage.time_bins ? (
                <>
                  长课分时段抽查覆盖 {j.config_and_runtime.coverage.selected_windows}/{j.config_and_runtime.coverage.time_bins} 个时段。
                  {!!j.config_and_runtime.coverage.empty_time_bins?.length &&
                    `第 ${j.config_and_runtime.coverage.empty_time_bins.join("、")} 段没有合适的人物画面。`}
                </>
              ) : (
                <>
                  {full ? "全量计划": "本次查看"}{" "}
                  {j.config_and_runtime.coverage.selected_tracks}/
                  {j.config_and_runtime.coverage.total_tracks}条轨迹、
                  {j.config_and_runtime.coverage.selected_windows}/
                  {j.config_and_runtime.coverage.eligible_windows_before_budget}
                  个窗口。
                  {full
                    ? "计划覆盖所有选定轨迹的可见区间；动作以实际采样为依据。"
                    : "其余范围未分析。"}
                </>
              )}
            </p>
          )}

        <details open={j.status !== "completed"} className="space-y-3">
          <summary className="cursor-pointer text-xs font-medium text-slate-700">
            分析范围与运行信息
          </summary>

          <div className="mt-3 space-y-3">
            {/* Progress bar */}
            <div
              role="progressbar"
              aria-label="窗口处理进度"
              aria-valuemin={0}
              aria-valuemax={j.total_windows || 1}
              aria-valuenow={j.processed_windows}
              className="h-2 w-full overflow-hidden rounded-full bg-slate-100"
            >
              <div
                className="h-full rounded-full bg-[#992777] transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>

            {["queued", "running"].includes(j.status) && (
              <p className="text-xs text-slate-500">
                分析在后台进行，您可以离开或刷新页面。CPU
                模型可能需要数分钟；这里的进度仅对应本次选定窗口，不代表整节课已经完整分析。
              </p>
            )}

            {diagnostics.map((d, i) => (
              <AlertBanner key={i} tone="danger">
                <div>{d.message}</div>
                <details className="mt-1 text-xs">
                  <summary className="cursor-pointer">实际错误详情</summary>
                  <pre className="mt-1 overflow-auto rounded bg-slate-950 p-2 font-mono text-[11px] text-slate-200">
                    {d.detail}
                  </pre>
                </details>
              </AlertBanner>
            ))}

            {/* Stats grid */}
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 text-xs">
              {["queued", "running", "pausing"].includes(j.status) &&
                typeof j.created_at === "string"&& (
                  <div className="rounded-lg bg-slate-50 px-2.5 py-1.5">
                    任务已创建{" "}
                    {Math.max(
                      0,
                      Math.floor((clock - Date.parse(j.created_at)) / 1000),
                    )}
                    秒（含等待）
                  </div>
                )}
              <div className="rounded-lg bg-slate-50 px-2.5 py-1.5">
                窗口 {j.processed_windows}/{j.total_windows}
              </div>
              <div className="rounded-lg bg-slate-50 px-2.5 py-1.5">
                模型调用完成 {j.completed_calls}
              </div>
              <div className="rounded-lg bg-slate-50 px-2.5 py-1.5">
                失败 {j.failed_calls}
              </div>
              <div className="rounded-lg bg-slate-50 px-2.5 py-1.5">
                预算已用 {j.attempted_calls}/{j.call_limit}
              </div>
              <div className="rounded-lg bg-slate-50 px-2.5 py-1.5">
                执行累计 {(j.config_and_runtime.elapsed_seconds || 0).toFixed(1)}
                秒
              </div>
            </div>

            {j.config_and_runtime.coverage && (
              <p className="text-xs text-slate-500">
                本次只选择 {j.config_and_runtime.coverage.selected_tracks}/
                {j.config_and_runtime.coverage.total_tracks} 条轨迹、
                {j.config_and_runtime.coverage.selected_windows}/
                {j.config_and_runtime.coverage.eligible_windows_before_budget}{" "}
                个窗口。未选范围没有完成行为分析。
              </p>
            )}

            <div className="flex flex-wrap items-end gap-2">
              {["queued", "running", "pausing"].includes(j.status) && (
                <button
                  type="button"
                  className={btnClass("secondary", "sm")}
                  disabled={!s.canWrite || busy || j.status === "pausing"}
                  onClick={() => void act("pause")}
                >
                  <Pause size={13} />
                  暂停任务
                </button>
              )}

              {[
                "paused",
                "cancelled",
                "failed",
                "completed_with_errors",
              ].includes(j.status) && (
                <>
                  <label className="flex flex-col gap-1 text-xs text-slate-600">
                    追加调用额度
                    <input
                      className={`${fieldClass} w-28 py-1 text-xs`}
                      aria-label="追加调用额度"
                      type="number"
                      min="0"
                      max="500"
                      value={additional}
                      onChange={(e) => setAdditional(Number(e.target.value))}
                    />
                  </label>
                  <button
                    type="button"
                    className={btnClass("primary", "sm")}
                    disabled={!s.canWrite || busy}
                    onClick={() => void act("resume")}
                  >
                    <Play size={13} />
                    恢复任务
                  </button>
                  <button
                    type="button"
                    className={btnClass("secondary", "sm")}
                    disabled={!s.canWrite || busy}
                    onClick={() => void act("retry")}
                  >
                    <RotateCcw size={13} />
                    重试失败窗口
                  </button>
                </>
              )}

              {!["completed", "cancelled"].includes(j.status) && (
                <button
                  type="button"
                  className={btnClass("danger", "sm")}
                  disabled={!s.canWrite || busy}
                  onClick={() => void act("cancel")}
                >
                  <XCircle size={13} />
                  取消任务
                </button>
              )}

              <button
                type="button"
                className={btnClass("ghost", "sm")}
                disabled={busy}
                onClick={() => void s.loadJob(j.job_id)}
              >
                <RefreshCw size={13} />
                刷新进度
              </button>
            </div>

            {j.status !== "completed"&& (
              <p className="text-[11px] text-slate-500">
                暂停在当前调用结束后生效；恢复不补造失败结果，已完成窗口使用后端缓存。
                {!s.canWrite ? "当前为只读账户。": ""}
              </p>
            )}

            <details className="rounded-lg border border-slate-200/80 bg-slate-50/50 px-3 py-2 text-xs">
              <summary className="cursor-pointer font-medium text-slate-600">
                任务技术详情
              </summary>
              <pre className="mt-2 max-h-56 overflow-auto rounded bg-slate-950 p-2.5 font-mono text-[11px] text-slate-200">
                {JSON.stringify(j, null, 2)}
              </pre>
            </details>
          </div>
        </details>
      </div>
    </section>
  );
}
