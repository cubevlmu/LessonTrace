import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useSession } from "../state/session";
import { controlJob, getDiagnostics } from "../api/jobs";
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
  const s = useSession(),
    j = s.data.job;
  const full=j?.config_and_runtime.config.analysis_mode==='full_timeline';
  const [busy, setBusy] = useState(false),
    [additional, setAdditional] = useState(0);
  const [clock,setClock]=useState(Date.now());
  useEffect(()=>{if(!['queued','running','pausing'].includes(j?.status||''))return;const t=setInterval(()=>setClock(Date.now()),1000);return()=>clearInterval(t);},[j?.status]);
  const [diagnostics, setDiagnostics] = useState<
    { message: string; detail: string }[]
  >([]);
  useEffect(() => {
    setDiagnostics([]);
    let active = true;
    if (
      j &&
      (["failed", "paused", "completed_with_errors"].includes(j.status) ||
        j.failed_windows > 0)
    )
      void getDiagnostics(j.job_id)
        .then((r) => {
          if (active) setDiagnostics(r.errors);
        })
        .catch((e) => {
          if (active) s.setError(String(e));
        });
    return () => {
      active = false;
    };
  }, [j?.job_id, j?.status, j?.failed_windows]);
  async function act(action: "pause" | "resume" | "cancel" | "retry") {
    if (!j) return;
    setBusy(true);
    try {
      await controlJob(
        j.job_id,
        action,
        action === "resume" || action === "retry" ? additional : 0,
      );
      await s.loadJob(j.job_id);
    } catch (e) {
      s.setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  if (!j)
    return (
      <div className="src-bar">
        <span>当前没有分析任务。</span>
        <Link to="/">导入并分析录像</Link>
      </div>
    );
  return (
    <section className="card" data-status={j.status} aria-label="分析任务进度">
      <div className="src-bar">
        <div className="src-bar__info">
          <span className="src-bar__dot is-imported" />
          <strong>{names[j.status] || j.status}</strong>
          <span>{names[j.stage] || j.stage}</span>
        </div>
        <Link className="btn btn--ghost btn--sm" to="/">
          切换录像或任务
        </Link>
      </div>
      {['completed','completed_with_errors'].includes(j.status)&&j.config_and_runtime.coverage&&<p className="job-scope-summary">{full?'全量计划':'本次查看'} {j.config_and_runtime.coverage.selected_tracks}/{j.config_and_runtime.coverage.total_tracks}条轨迹、{j.config_and_runtime.coverage.selected_windows}/{j.config_and_runtime.coverage.eligible_windows_before_budget}个窗口。{full?'计划覆盖所有选定轨迹的可见区间；动作以实际采样为依据。':'其余范围未分析。'}</p>}
      <details className="job-runtime-details" open={j.status!=='completed'}><summary>分析范围与运行信息</summary>
      <div className="card__body">
        <div className="progress-visual" role="progressbar" aria-label="窗口处理进度" aria-valuemin={0} aria-valuemax={j.total_windows||1} aria-valuenow={j.processed_windows}><span style={{width:`${j.total_windows?Math.min(100,100*j.processed_windows/j.total_windows):0}%`}}/></div>
        {['queued','running'].includes(j.status)&&<p className="runtime-hint">分析在后台进行，您可以离开或刷新页面。CPU 模型可能需要数分钟；这里的进度仅对应本次选定窗口，不代表整节课已经完整分析。</p>}
        {diagnostics.map((d, i) => (
          <div className="upload__error" role="alert" key={i}>
            {d.message}
            <details>
              <summary>实际错误详情</summary>
              <pre>{d.detail}</pre>
            </details>
          </div>
        ))}
        <div className="stat-strip">
          {['queued','running','pausing'].includes(j.status)&&typeof j.created_at==='string'&&<span>任务已创建 {Math.max(0,Math.floor((clock-Date.parse(j.created_at))/1000))}秒（含等待）</span>}
          <span>
            窗口 {j.processed_windows}/{j.total_windows}
          </span>
          <span>模型调用完成 {j.completed_calls}</span>
          <span>失败 {j.failed_calls}</span>
          <span>
            预算已用 {j.attempted_calls}/{j.call_limit}
          </span>
          <span>
            执行累计 {(j.config_and_runtime.elapsed_seconds || 0).toFixed(1)}秒
          </span>
        </div>
        {j.config_and_runtime.coverage && (
          <p className="muted">
            本次只选择 {j.config_and_runtime.coverage.selected_tracks}/
            {j.config_and_runtime.coverage.total_tracks} 条轨迹、
            {j.config_and_runtime.coverage.selected_windows}/
            {j.config_and_runtime.coverage.eligible_windows_before_budget}{" "}
            个窗口。未选范围没有完成行为分析。
          </p>
        )}
        <div className="product-actions">
          {["queued", "running", "pausing"].includes(j.status) && (
            <button
              className="btn btn--secondary"
              disabled={!s.canWrite || busy || j.status === "pausing"}
              onClick={() => void act("pause")}
            >
              暂停任务
            </button>
          )}
          {["paused", "cancelled", "failed", "completed_with_errors"].includes(
            j.status,
          ) && (
            <>
              <label>
                追加调用额度
                <input
                  aria-label="追加调用额度"
                  type="number"
                  min="0"
                  max="500"
                  value={additional}
                  onChange={(e) => setAdditional(Number(e.target.value))}
                />
              </label>
              <button
                className="btn btn--primary"
                disabled={!s.canWrite || busy}
                onClick={() => void act("resume")}
              >
                恢复任务
              </button>
              <button
                className="btn btn--secondary"
                disabled={!s.canWrite || busy}
                onClick={() => void act("retry")}
              >
                重试失败窗口
              </button>
            </>
          )}
          {!["completed", "cancelled"].includes(j.status) && (
            <button
              className="btn btn--ghost"
              disabled={!s.canWrite || busy}
              onClick={() => void act("cancel")}
            >
              取消任务
            </button>
          )}
          <button
            className="btn btn--ghost"
            disabled={busy}
            onClick={() => void s.loadJob(j.job_id)}
          >
            刷新进度
          </button>
        </div>
        {j.status!=='completed'&&<p className="faint">
          暂停在当前调用结束后生效；恢复不补造失败结果，已完成窗口使用后端缓存。
          {!s.canWrite ? " 当前为只读账户。" : ""}
        </p>}
        <details>
          <summary>任务技术详情</summary>
          <pre>{JSON.stringify(j, null, 2)}</pre>
        </details>
      </div>
      </details>
    </section>
  );
}
