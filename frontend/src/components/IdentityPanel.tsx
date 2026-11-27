import { useEffect, useState } from "react";
import { z } from "zod";
import { UserCheck, RotateCcw } from "lucide-react";
import { getReport } from "../api/review";
import { request } from "../api/client";
import { useSession } from "../state/session";
import {
  listClassrooms,
  getClassroom,
  linkVideo,
  type Classroom,
  type Student,
} from "../api/classrooms";
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

const resultSchema = z
  .object({
    candidates: z.array(
      z
        .object({
          candidate_id: z.string(),
          track_id: z.number(),
          display_name: z.string().nullable(),
          effective_status: z.string(),
          start: z.number(),
          end: z.number(),
          raw_similarity: z.number().nullable(),
          rejection_reason: z.string().nullable(),
        })
        .passthrough(),
    ),
    bindings: z.array(
      z
        .object({
          binding_id: z.string(),
          track_id: z.number(),
          start: z.number(),
          end: z.number(),
          effective_status: z.string(),
        })
        .passthrough(),
    ),
  })
  .passthrough();

export function IdentityPanel({
  classroomId,
  currentTime = 0,
  selectedTrack = 0,
}: {
  classroomId?: string;
  currentTime?: number;
  selectedTrack?: number;
}) {
  const s = useSession();
  const rid = s.data.job?.run_id;

  const [classes, setClasses] = useState<Classroom[]>([]);
  const [cid, setCid] = useState("");
  const [students, setStudents] = useState<Student[]>([]);
  const [results, setResults] = useState<z.infer<typeof resultSchema> | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [expiry, setExpiry] = useState("");
  const [authorized, setAuthorized] = useState(false);
  const [sid, setSid] = useState("");
  const [tid, setTid] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [message, setMessage] = useState("");

  const canWrite = ["admin", "teacher"].includes(s.account?.role || "");

  useEffect(() => {
    if (classroomId) setCid(classroomId);
  }, [classroomId]);

  useEffect(() => {
    if (selectedTrack > 0) setTid(String(selectedTrack));
  }, [selectedTrack]);

  async function load() {
    if (rid)
      setResults(
        await request(
          "identity/results?run_id="+ encodeURIComponent(rid),
          resultSchema,
        ),
      );
    if (s.data.report) s.setReport(await getReport(s.data.report.report_id));
    window.dispatchEvent(new Event("lessontrace:identity-changed"));
  }

  async function task(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await work();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void listClassrooms()
      .then(setClasses)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    setResults(null);
    if (rid) void load().catch((e) => setError(String(e)));
  }, [rid]);

  useEffect(() => {
    setStudents([]);
    if (cid)
      void getClassroom(cid)
        .then((c) => setStudents(c.students.filter((p) => p.status === "active")))
        .catch((e) => setError(String(e)));
  }, [cid]);

  if (!rid) return null;
  const currentLocal = Math.max(0, currentTime).toFixed(3);

  return (
    <section className={cardClass}>
      <div className={cardHeadClass}>
        <h2 className={cardTitleClass}>
          <UserCheck size={15} className="text-[#992777]"/>
          人物身份
        </h2>
      </div>
      <div className="space-y-4 p-4 text-sm">
        {error ? <AlertBanner tone="danger">{error}</AlertBanner> : null}
        {message ? <AlertBanner tone="status">{message}</AlertBanner> : null}

        <p className="text-xs text-slate-500">
          AI候选与人工确认独立保存。无法匹配时保留匿名；相似度不是正确概率。
        </p>

        <label className={labelClass}>
          选择班级
          <select
            className={selectClass}
            aria-label="身份匹配班级"
            value={cid}
            onChange={(e) => setCid(e.target.value)}
          >
            <option value="">选择班级</option>
            {classes.map((c) => (
              <option key={c.classroom_id} value={c.classroom_id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>

        {cid && canWrite && (
          <div className="space-y-3 rounded-xl border border-slate-200/80 bg-slate-50/50 p-3.5">
            <label className={labelClass}>
              视频身份处理授权及审核依据
              <textarea
                className={textareaClass}
                aria-label="身份依据"
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </label>
            <label className={labelClass}>
              授权截止
              <input
                className={fieldClass}
                type="datetime-local"
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
              />
            </label>
            <label className="flex items-center gap-2 text-xs text-slate-700">
              <input
                type="checkbox"
                checked={authorized}
                onChange={(e) => setAuthorized(e.target.checked)}
              />
              本视频出镜者已授权本地身份处理
            </label>
            <button
              type="button"
              className={btnClass("secondary", "sm")}
              disabled={busy || !authorized || !expiry || note.length < 5}
              onClick={() =>
                void task(async () => {
                  await linkVideo(cid, s.data.videoId);
                  await request(
                    `classrooms/${cid}/match`,
                    z.object({ results: z.array(z.record(z.unknown())) }).passthrough(),
                    {
                      run_id: rid,
                      consent_record: note,
                      expires_at: new Date(expiry).toISOString(),
                      all_visible_people_authorized: authorized,
                    },
                  );
                  await load();
                  setMessage("匹配已完成，不重复运行行为分析");
                })
              }
            >
              匹配或重新匹配人物
            </button>

            <details className="rounded-lg border border-slate-200/80 bg-white p-3 text-xs">
              <summary className="cursor-pointer font-medium text-slate-700">
                人工确认指定时间段身份
              </summary>
              <div className="mt-3 space-y-3">
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    className={btnClass("ghost", "sm")}
                    onClick={() => setStart(currentLocal)}
                  >
                    起始使用当前时刻
                  </button>
                  <button
                    type="button"
                    className={btnClass("ghost", "sm")}
                    onClick={() => setEnd(currentLocal)}
                  >
                    结束使用当前时刻
                  </button>
                </div>
                <form
                  className="space-y-2.5"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void task(async () => {
                      await request(
                        "identity/bindings",
                        z.object({ binding_id: z.string() }).passthrough(),
                        {
                          subject_id: sid,
                          run_id: rid,
                          track_id: Number(tid),
                          start: Number(start) + (s.data.scope?.start || 0),
                          end: Number(end) + (s.data.scope?.start || 0),
                          decision: "human_confirmed",
                          source_kind: "authorized_reference_comparison",
                          evidence_ref: note,
                          reason: note,
                          interval_identity_reviewed: reviewed,
                          cross_clip_evidence: note,
                        },
                      );
                      await load();
                      setReviewed(false);
                      setMessage("人工身份已保存，仅适用于指定时间段");
                    });
                  }}
                >
                  <select
                    className={selectClass}
                    aria-label="确认学生"
                    required
                    value={sid}
                    onChange={(e) => setSid(e.target.value)}
                  >
                    <option value="">选择学生</option>
                    {students.map((p) => (
                      <option key={p.subject_id} value={p.subject_id}>
                        {p.display_name}
                      </option>
                    ))}
                  </select>
                  <select
                    className={selectClass}
                    aria-label="确认轨迹"
                    required
                    value={tid}
                    onChange={(e) => setTid(e.target.value)}
                  >
                    <option value="">选择轨迹</option>
                    {s.data.tracks.map((t) => (
                      <option key={t.track_id} value={t.track_id}>
                        匿名轨迹 {t.track_id}
                      </option>
                    ))}
                  </select>
                  <div className="grid grid-cols-2 gap-2">
                    <label className={labelClass}>
                      视频内起始秒
                      <input
                        className={fieldClass}
                        type="number"
                        step="any"
                        min="0"
                        max={
                          s.data.scope
                            ? s.data.scope.end - s.data.scope.start
                            : undefined
                        }
                        required
                        value={start}
                        onChange={(e) => setStart(e.target.value)}
                      />
                    </label>
                    <label className={labelClass}>
                      视频内结束秒
                      <input
                        className={fieldClass}
                        type="number"
                        step="any"
                        min="0"
                        max={
                          s.data.scope
                            ? s.data.scope.end - s.data.scope.start
                            : undefined
                        }
                        required
                        value={end}
                        onChange={(e) => setEnd(e.target.value)}
                      />
                    </label>
                  </div>
                  <label className="flex items-start gap-2 text-xs text-slate-700">
                    <input
                      type="checkbox"
                      checked={reviewed}
                      onChange={(e) => setReviewed(e.target.checked)}
                    />
                    <span>我已独立看片并核对该区间身份、依据和轨迹连续性</span>
                  </label>
                  <button
                    type="submit"
                    className={btnClass("primary", "sm")}
                    disabled={busy || !reviewed || note.length < 5}
                  >
                    保存人工确认
                  </button>
                </form>
              </div>
            </details>
          </div>
        )}

        <div className="space-y-2">
          {results?.candidates.map((c) => (
            <p
              key={c.candidate_id}
              className="rounded-lg border border-slate-200/80 bg-slate-50/70 px-3 py-2 text-xs text-slate-700"
            >
              Track {c.track_id} ·{" "}
              {c.display_name ? "AI候选 "+ c.display_name : "未知身份"} ·{" "}
              {c.effective_status} · {c.start.toFixed(2)}—{c.end.toFixed(2)}秒 ·{" "}
              {c.raw_similarity?.toFixed(3)} · {c.rejection_reason}
            </p>
          ))}
          {!results?.candidates.length && (
            <p className="text-xs text-slate-500">
              暂无身份候选；请核对授权、参考照片及可用人脸。
            </p>
          )}
          {results?.bindings.map((b) => (
            <div
              key={b.binding_id}
              className="flex items-center justify-between gap-2 rounded-lg border border-emerald-200/80 bg-emerald-50/50 px-3 py-2 text-xs text-emerald-900"
            >
              <span>
                人工记录 · Track {b.track_id} · {b.start.toFixed(2)}—
                {b.end.toFixed(2)}秒 · {b.effective_status}
              </span>
              {canWrite && b.effective_status === "human_confirmed"&& (
                <button
                  type="button"
                  className={btnClass("ghost", "sm")}
                  disabled={busy}
                  onClick={() => {
                    const reason = window.prompt("撤销原因（至少5字）");
                    if (reason)
                      void task(async () => {
                        await request(
                          `identity/bindings/${b.binding_id}/revoke`,
                          z.object({ status: z.string() }).passthrough(),
                          { reason },
                        );
                        await load();
                      });
                  }}
                >
                  <RotateCcw size={12} />
                  撤销
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
