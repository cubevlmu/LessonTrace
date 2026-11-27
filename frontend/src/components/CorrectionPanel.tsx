import { useEffect, useState } from "react";
import { z } from "zod";
import { ClipboardCheck, RotateCcw } from "lucide-react";
import { request } from "../api/client";
import { getReport } from "../api/review";
import { useSession } from "../state/session";
import { actionLabel, stateLabel, fmt, type VisualItem } from "../models/view";
import {
  AlertBanner,
  btnClass,
  cardClass,
  cardHeadClass,
  cardTitleClass,
  fieldClass,
  labelClass,
  selectClass,
  textareaClass,
} from "./ui/primitives";

const historySchema = z.array(
  z
    .object({
      review_id: z.string(),
      status: z.string(),
      actor: z.string(),
      created_at: z.string(),
      data: z.record(z.unknown()),
    })
    .passthrough(),
);

export function CorrectionPanel({ item }: { item?: VisualItem }) {
  const s = useSession();
  const correctionId =
    item?.eventId || (item?.kind === "window"? item.id : undefined);

  const [category, setCategory] = useState("unknown");
  const [tid, setTid] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [action, setAction] = useState("");
  const [reason, setReason] = useState("");
  const [indices, setIndices] = useState("");
  const [decision, setDecision] = useState("uncertain");
  const [attest, setAttest] = useState(false);
  const [history, setHistory] = useState<z.infer<typeof historySchema>>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function load() {
    if (correctionId)
      setHistory(
        await request(
          "corrections?event_id="+ encodeURIComponent(correctionId),
          historySchema,
        ),
      );
  }

  async function sync() {
    if (s.data.job) await s.loadJob(s.data.job.job_id);
    if (s.data.report) s.setReport(await getReport(s.data.report.report_id));
  }

  useEffect(() => {
    setHistory([]);
    setAttest(false);
    setMessage("");
    setError("");
    setCategory(item?.category || "unknown");
    setTid(item ? String(item.trackId) : "");
    setStart(item ? String(item.localStart) : "");
    setEnd(item ? String(item.localEnd) : "");
    setIndices("");
    setReason("");
    setAction("");
    if (correctionId) void load().catch((e) => setError(String(e)));
  }, [item?.id]);

  async function task(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!item) {
    return (
      <section className={cardClass}>
        <div className="space-y-1.5 p-5 text-center">
          <h3 className="text-sm font-semibold text-slate-900">
            先选择一条观察
          </h3>
          <p className="text-xs leading-relaxed text-slate-500">
            点击时间线上的动作或下方画面观察记录，再在这里修改。当前人物没有已保存的观察时，可使用“扩展此人物的连续动作分析”。
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className={cardClass}>
      <div className={cardHeadClass}>
        <h2 className={cardTitleClass}>
          <ClipboardCheck
            size={15}
            className="text-[#992777]"
          />
          教师纠错
        </h2>
      </div>
      <div className="space-y-3.5 p-4 text-sm">
        <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
          AI建议：{actionLabel[item.category]} ·{" "}
          {stateLabel[item.state] || item.state}
        </p>

        {error ? <AlertBanner tone="danger">{error}</AlertBanner> : null}
        {message ? <AlertBanner tone="status">{message}</AlertBanner> : null}

        {s.canWrite && (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              void task(async () => {
                const frames = indices
                  .split(/[,，\s]+/)
                  .filter(Boolean)
                  .map(Number);
                if (frames.some((x) => !Number.isInteger(x) || x < 0))
                  throw Error("请选择实际查看过的证据画面");
                const offset = s.data.scope?.start || 0;
                await request(
                  "corrections",
                  z.object({ review_id: z.string() }).passthrough(),
                  {
                    event_id: correctionId,
                    decision,
                    corrected_category: category,
                    track_id: Number(tid),
                    start: Number(start) + offset,
                    end: Number(end) + offset,
                    observable_action: action,
                    reason,
                    evidence_frames: frames,
                    reviewed: attest,
                  },
                );
                await load();
                await sync();
                setAttest(false);
                setMessage(
                  "纠错已保存。时间线和后续问答使用最新复核，旧报告需重算",
                );
              });
            }}
          >
            <label className={labelClass}>
              教师判断
              <select
                className={selectClass}
                aria-label="教师判断"
                value={decision}
                onChange={(e) => setDecision(e.target.value)}
              >
                <option value="uncertain">无法判断</option>
                <option value="confirmed">确认或修正行为</option>
                <option value="rejected">删除不适用候选（原记录保留）</option>
              </select>
            </label>

            <label className={labelClass}>
              修正类别
              <select
                className={selectClass}
                aria-label="修正类别"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                {Object.entries(actionLabel).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>

            <label className={labelClass}>
              行为执行轨迹
              <select
                className={selectClass}
                aria-label="修正轨迹"
                value={tid}
                onChange={(e) => setTid(e.target.value)}
              >
                {s.data.tracks.map((t) => (
                  <option key={t.track_id} value={t.track_id}>
                    匿名轨迹 {t.track_id}
                  </option>
                ))}
              </select>
            </label>

            <div className="grid grid-cols-2 gap-2.5">
              <label className={labelClass}>
                开始时间（视频秒）
                <input
                  className={fieldClass}
                  type="number"
                  step="any"
                  min="0"
                  required
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                />
              </label>
              <label className={labelClass}>
                结束时间（视频秒）
                <input
                  className={fieldClass}
                  type="number"
                  step="any"
                  min="0"
                  required
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                />
              </label>
            </div>

            <fieldset className="rounded-lg border border-slate-200 p-2.5">
              <legend className="px-1 text-xs font-medium text-slate-600">
                勾选您实际查看过的画面
              </legend>
              <div className="flex flex-wrap gap-2 pt-1">
                {item.frames.map((f) => {
                  const checked = indices
                    .split(/[,，\s]+/)
                    .filter(Boolean)
                    .includes(String(f.frame_index));
                  return (
                    <label
                      key={f.frame_id}
                      className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) =>
                          setIndices((value) => {
                            const values = value
                              .split(/[,，\s]+/)
                              .filter(Boolean);
                            return (
                              e.target.checked
                                ? Array.from(
                                    new Set([...values, String(f.frame_index)]),
                                  )
                                : values.filter(
                                    (v) => v !== String(f.frame_index),
                                  )
                            ).join(",");
                          })
                        }
                      />
                      <span className="tabular">{fmt(f.local_time)}</span>
                    </label>
                  );
                })}
              </div>
            </fieldset>

            <details className="rounded-lg border border-slate-200/80 bg-slate-50/50 px-3 py-2 text-xs">
              <summary className="cursor-pointer font-medium text-slate-600">
                高级：直接输入帧序号
              </summary>
              <label className={`${labelClass} mt-2`}>
                实际看过的帧序号
                <input
                  className={fieldClass}
                  aria-label="纠错帧序号"
                  value={indices}
                  onChange={(e) => setIndices(e.target.value)}
                  placeholder="例如：10, 20"
                />
              </label>
            </details>

            <label className={labelClass}>
              实际可观察动作
              <textarea
                className={textareaClass}
                aria-label="纠错观察动作"
                required
                minLength={5}
                value={action}
                onChange={(e) => setAction(e.target.value)}
              />
            </label>

            <label className={labelClass}>
              依据与限制
              <textarea
                className={textareaClass}
                required
                minLength={5}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>

            <label className="flex items-start gap-2 rounded-lg border border-slate-200/80 bg-slate-50/70 p-2.5 text-xs text-slate-700">
              <input
                type="checkbox"
                checked={attest}
                onChange={(e) => setAttest(e.target.checked)}
                className="mt-0.5 rounded border-slate-300"
              />
              <span>我已亲自核对所选画面、人物、动作和时间区间</span>
            </label>

            <button
              type="submit"
              className={btnClass("primary", "md", "w-full")}
              disabled={busy || !attest || !indices.trim()}
            >
              保存纠错
            </button>
          </form>
        )}

        {history.length > 0 && (
          <div className="space-y-2 border-t border-slate-100 pt-3">
            <h3 className="text-xs font-semibold text-slate-600">
              历史纠错记录
            </h3>
            {history.map((h) => (
              <div
                key={h.review_id}
                className="flex items-center justify-between gap-2 rounded-lg border border-slate-200/80 bg-slate-50/60 px-3 py-2 text-xs"
              >
                <p className="text-slate-700">
                  {h.status} · {h.created_at} · 审核人 {h.actor}
                </p>
                {s.canWrite && h.status !== "revoked"&& (
                  <button
                    type="button"
                    className={btnClass("ghost", "sm")}
                    disabled={busy}
                    onClick={() => {
                      const r = window.prompt("撤销原因（至少5字）");
                      if (r)
                        void task(async () => {
                          await request(
                            `corrections/${h.review_id}/revoke`,
                            z.object({ status: z.string() }).passthrough(),
                            { reason: r },
                          );
                          await load();
                          setMessage("人工修改已撤销，旧报告需重算");
                        });
                    }}
                  >
                    <RotateCcw size={12} />
                    撤销此修改
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
