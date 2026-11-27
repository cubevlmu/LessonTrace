import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { z } from "zod";
import { Users, Camera, Eye, FileText, Trash2, X } from "lucide-react";
import { request } from "../api/client";
import {
  listClassrooms,
  getClassroom,
  type Classroom,
  type Student,
} from "../api/classrooms";
import { useSession } from "../state/session";
import { observationText, fmt } from "../models/view";
import {
  personTrackLabel,
  type IdentityLabel,
  type TrackGroup,
} from "../api/workbench";
import { canonicalTrack, memberTracks } from "../adapters/trackGroups";
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

const action = z
  .object({
    observed_action: z.string(),
    group_id: z.string().optional(),
    segment_id: z.number().optional(),
    observed_times: z.array(z.number()),
    evidence_refs: z.array(
      z.object({
        frame_id: z.string(),
        window_id: z.string(),
        frame_index: z.number(),
        local_time: z.number(),
      }),
    ),
  })
  .passthrough();

const person = z
  .object({
    track_id: z.number(),
    profile_id: z.string(),
    segments: z.array(
      z.object({ local_start: z.number(), local_end: z.number() }),
    ),
    action_groups: z.array(action),
    representative_frame: z.number(),
    representative_time: z.number(),
    portrait_url: z.string(),
    continuity_status: z.string(),
  })
  .passthrough();

const profilesSchema = z.object({
  people: z.array(person),
  truncated_windows: z.boolean(),
  grouping_basis: z.string(),
});

type Person = z.infer<typeof person>;
type Shot = { frame_index: number; local_time: number; portrait_url: string };

export function PersonProfiles({
  runId,
  currentTime,
  selectedTrack,
  labels,
  classroomId,
  version,
  onSelect,
  excludedTrackIds = [],
  trackGroups = [],
}: {
  runId: string;
  currentTime: number;
  selectedTrack: number;
  labels: IdentityLabel[];
  classroomId?: string;
  version: number;
  excludedTrackIds?: number[];
  trackGroups?: TrackGroup[];
  onSelect: (id: number, time?: number) => void;
}) {
  const s = useSession();
  const dialog = useRef<HTMLDialogElement>(null);

  const [people, setPeople] = useState<Person[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(true);
  const [message, setMessage] = useState("");

  const [draft, setDraft] = useState<Person | null>(null);
  const [classes, setClasses] = useState<Classroom[]>([]);
  const [cid, setCid] = useState("");
  const [students, setStudents] = useState<Student[]>([]);
  const [sid, setSid] = useState("");
  const [name, setName] = useState("");
  const [shot, setShot] = useState<Shot | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [note, setNote] = useState("");
  const [face, setFace] = useState(false);
  const [authorized, setAuthorized] = useState(false);
  const [consent, setConsent] = useState("");
  const [expiry, setExpiry] = useState("");
  const [busy, setBusy] = useState(false);
  const [demo, setDemo] = useState(false);
  const requestId = useRef("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    void request(
      "workbench/people?run_id="+ encodeURIComponent(runId),
      profilesSchema,
    )
      .then((r) => {
        if (active) setPeople(r.people);
      })
      .catch((e) => {
        if (active) setError(String(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [runId, version]);

  useEffect(() => {
    void listClassrooms()
      .then(setClasses)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (classroomId) setCid(classroomId);
  }, [classroomId]);

  useEffect(() => {
    setStudents([]);
    setSid("");
    if (cid)
      void getClassroom(cid)
        .then((c) => setStudents(c.students))
        .catch((e) => setError(String(e)));
  }, [cid]);

  useEffect(() => {
    if (draft) dialog.current?.showModal();
  }, [draft?.track_id]);

  function close() {
    dialog.current?.close();
    setDraft(null);
  }

  function open(p: Person) {
    const seg =
      p.segments.find(
        (v) =>
          v.local_start <= p.representative_time &&
          p.representative_time <= v.local_end,
      ) || p.segments[0];
    setDemo(
      Boolean(
        classes.find((c) => c.classroom_id === cid)?.name.includes("演示"),
      ),
    );
    setDraft(p);
    setShot({
      frame_index: p.representative_frame,
      local_time: p.representative_time,
      portrait_url: p.portrait_url,
    });
    setStart(String(seg?.local_start || 0));
    setEnd(String(seg?.local_end || 0));
    setSid("");
    setName("");
    setNote("将该匿名轨迹登记到班级，行为仍保留原有证据状态");
    setFace(false);
    setAuthorized(false);
    setConsent("");
    setExpiry("");
    setError("");
    requestId.current = crypto.randomUUID();
  }

  async function currentShot() {
    if (!draft) return;
    setBusy(true);
    setError("");
    try {
      const r = await request(
        `workbench/people/${draft.track_id}/capture?run_id=${encodeURIComponent(runId)}&local_time=${currentTime}`,
        z.object({
          frame_index: z.number(),
          local_time: z.number(),
          portrait_url: z.string(),
        }),
      );
      setShot(r);
      const seg = draft.segments.find(
        (v) => v.local_start <= r.local_time && r.local_time <= v.local_end,
      );
      if (seg) {
        setStart(String(seg.local_start));
        setEnd(String(seg.local_end));
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!draft || !shot) return;
    setBusy(true);
    setError("");
    try {
      if (!start.trim() || !end.trim()) throw Error("请填写有效时间范围");
      await request(
        "workbench/registrations",
        z
          .object({
            subject_id: z.string(),
            binding_id: z.string(),
            identity_confirmed: z.boolean(),
            status: z.string(),
          })
          .passthrough(),
        {
          request_id: requestId.current,
          classroom_id: cid,
          run_id: runId,
          track_id: draft.track_id,
          frame_index: shot.frame_index,
          local_start: Number(start),
          local_end: Number(end),
          subject_id: sid || null,
          display_name: name,
          note,
          authorized,
          consent_record: consent,
          expires_at: authorized ? new Date(expiry).toISOString() : "",
          enable_face_matching: face,
          label_origin: demo ? "demo_setup": "operator",
        },
      );
      close();
      setMessage(
        "人物已加入班级，截图和该时段动作已关联。轨迹登记与独立身份确认分别保存。",
      );
      window.dispatchEvent(new Event("lessontrace:identity-changed"));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const aggregate = people
    .filter((p) => canonicalTrack(p.track_id, trackGroups) === p.track_id)
    .map((p) => {
      const members = people.filter((other) =>
        memberTracks(p.track_id, trackGroups).includes(other.track_id),
      );
      return {
        ...p,
        segments: members.flatMap((m) => m.segments),
        action_groups: members.flatMap((m) => m.action_groups),
      };
    });

  const visiblePeople = aggregate.filter(
    (p) => !excludedTrackIds.includes(p.track_id),
  );
  const shown = selectedTrack
    ? aggregate.filter((p) => p.track_id === selectedTrack)
    : expanded
      ? visiblePeople
      : visiblePeople.slice(0, 6);

  return (
    <section className={cardClass}>
      <div className={cardHeadClass}>
        <h2 className={cardTitleClass}>
          <Users size={15} className="text-[#992777]"/>
          按人物归纳行为
        </h2>
        <Link
          className={btnClass("ghost", "sm")}
          to={"/classrooms"+ (classroomId ? "?classroom="+ classroomId : "")}
        >
          提前上传学生照片
        </Link>
      </div>

      <div className="space-y-3 p-4">
        <p className="text-xs text-[#6e5a74]">
          先按匿名轨迹整理动作，再登记到班级；未知姓名也能查看同一轨迹的多次观察。
        </p>

        {loading && (
          <p role="status"className="text-xs text-[#6e5a74]">
            正在归纳人物记录…
          </p>
        )}
        {error ? <AlertBanner tone="danger">{error}</AlertBanner> : null}
        {message ? <AlertBanner tone="status">{message}</AlertBanner> : null}

        <div className="max-h-[380px] space-y-2.5 overflow-y-auto pr-1">
          {shown.map((p) => (
            <article
              key={p.profile_id}
              className="flex gap-3 rounded-xl border border-[#ebdce9] bg-[#fcf9fd] p-3 transition hover:border-[#cfa1c3]"
            >
              <button
                type="button"
                onClick={() => onSelect(p.track_id, p.representative_time)}
                className="h-18 w-14 shrink-0 overflow-hidden rounded-lg border border-[#e3d2e6] bg-[#130c17]"
              >
                <img
                  loading="lazy"
                  src={p.portrait_url}
                  alt={"人物"+ p.track_id + "的实际录像截图"}
                  className="h-full w-full object-cover transition hover:scale-105"
                />
              </button>

              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="flex items-center justify-between gap-2">
                  <strong className="truncate text-sm font-semibold text-[#231426]">
                    {personTrackLabel(labels, p.track_id, currentTime)}
                  </strong>
                  <span className="shrink-0 text-[11px] text-[#6e5a74]">
                    {p.segments.length}段可见区间 ·{" "}
                    {p.continuity_status === "invalid"
                      ? "已有轨迹切换疑点"
                      : "轨迹连续性待核对"}
                  </span>
                </div>

                {p.action_groups.length ? (
                  <ul className="flex flex-wrap gap-1.5">
                    {p.action_groups.map((g) => (
                      <li key={g.group_id || g.observed_action}>
                        <button
                          type="button"
                          title={g.observed_times.map(fmt).join("、")}
                          onClick={() =>
                            onSelect(p.track_id, g.observed_times[0])
                          }
                          className="rounded-md bg-[#f7eaf3] px-2 py-0.5 text-[11px] font-medium text-[#841f66] transition hover:bg-[#edd4e6]"
                        >
                          {observationText(g.observed_action)} ·{" "}
                          {g.observed_times.length}次观察
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-[#6e5a74]">
                    已有轨迹，尚无保存的动作观察
                  </p>
                )}

                <div className="flex flex-wrap gap-1.5 pt-0.5">
                  <button
                    type="button"
                    className={btnClass("ghost", "sm")}
                    onClick={() => onSelect(p.track_id)}
                  >
                    <Eye size={12} />
                    单独查看
                  </button>
                  {s.canWrite &&
                    ["admin", "teacher"].includes(s.account?.role || "") && (
                      <button
                        type="button"
                        className={btnClass("secondary", "sm")}
                        onClick={() => open(p)}
                      >
                        <Camera size={12} />
                        截图加入班级
                      </button>
                    )}
                </div>
              </div>
            </article>
          ))}
        </div>

        {!selectedTrack && visiblePeople.length > 6 && (
          <button
            type="button"
            className={btnClass("ghost", "sm")}
            onClick={() => setExpanded(!expanded)}
          >
            {expanded
              ? "收起人物列表"
              : `展开其余${visiblePeople.length - 6}条人物轨迹`}
          </button>
        )}

        {!loading && !people.length && (
          <p className="py-4 text-center text-xs text-[#6e5a74]">
            人物跟踪完成后，这里会显示可归纳的匿名人物。
          </p>
        )}

        {draft && shot && (
          <dialog
            className="lt-dialog"
            ref={dialog}
            aria-label="截图登记人物"
            onCancel={close}
          >
            <header className="flex items-center justify-between border-b border-[#e8dce9] px-5 py-4">
              <h2 className="text-base font-semibold text-[#231426]">
                截图登记人物 {String(draft.track_id).padStart(3, "0")}
              </h2>
              <button
                type="button"
                className={btnClass("ghost", "sm")}
                onClick={close}
              >
                <X size={14} />
                关闭
              </button>
            </header>

            <form
              className="flex-1 space-y-3.5 overflow-y-auto px-5 py-4"
              onSubmit={(e) => {
                e.preventDefault();
                void save();
              }}
            >
              <div className="flex items-center gap-4 rounded-xl border border-[#e8dce9] bg-[#faf7fb] p-3">
                <img
                  src={shot.portrait_url}
                  alt="即将保存的人物截图"
                  className="h-24 w-20 rounded-lg border border-[#e3d2e6] object-cover"
                />
                <div className="space-y-1.5">
                  <p className="tabular text-xs font-medium text-[#231426]">
                    真实录像 {fmt(shot.local_time)} · 帧{shot.frame_index}
                  </p>
                  <button
                    type="button"
                    className={btnClass("secondary", "sm")}
                    disabled={busy}
                    onClick={() => void currentShot()}
                  >
                    使用当前时刻画面
                  </button>
                  <p className="text-[11px] text-[#6e5a74]">
                    截图用于资料登记；不自动证明真实姓名。
                  </p>
                </div>
              </div>

              <label className={labelClass}>
                加入班级
                <select
                  className={selectClass}
                  aria-label="登记班级"
                  required
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

              <label className={labelClass}>
                关联人物
                <select
                  className={selectClass}
                  aria-label="登记人物"
                  value={sid}
                  onChange={(e) => setSid(e.target.value)}
                >
                  <option value="">添加新人物</option>
                  {students.map((p) => (
                    <option key={p.subject_id} value={p.subject_id}>
                      {p.display_name}
                    </option>
                  ))}
                </select>
              </label>

              {!sid && (
                <label className={labelClass}>
                  姓名或演示名
                  <input
                    className={fieldClass}
                    aria-label="登记显示名"
                    required
                    maxLength={120}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
              )}

              <label className={labelClass}>
                可见区间
                <select
                  className={selectClass}
                  aria-label="登记可见区间"
                  value={draft.segments.findIndex(
                    (seg) =>
                      Number(start) >= seg.local_start &&
                      Number(end) <= seg.local_end,
                  )}
                  onChange={(e) => {
                    const seg = draft.segments[Number(e.target.value)];
                    if (seg) {
                      setStart(String(seg.local_start));
                      setEnd(String(seg.local_end));
                    }
                  }}
                >
                  {draft.segments.map((seg, i) => (
                    <option key={i} value={i}>
                      {fmt(seg.local_start)}–{fmt(seg.local_end)}
                    </option>
                  ))}
                </select>
              </label>

              <div className="grid grid-cols-2 gap-2.5">
                <label className={labelClass}>
                  视频内起始秒
                  <input
                    className={fieldClass}
                    aria-label="登记起始秒"
                    type="number"
                    min={0}
                    step="any"
                    required
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </label>
                <label className={labelClass}>
                  视频内结束秒
                  <input
                    className={fieldClass}
                    aria-label="登记结束秒"
                    type="number"
                    min={0}
                    step="any"
                    required
                    value={end}
                    onChange={(e) => setEnd(e.target.value)}
                  />
                </label>
              </div>

              <label className={labelClass}>
                登记说明
                <textarea
                  className={textareaClass}
                  aria-label="登记说明"
                  minLength={5}
                  required
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
              </label>

              <label className="flex items-center gap-2 text-xs text-[#4a3450]">
                <input
                  type="checkbox"
                  checked={demo}
                  disabled={authorized}
                  onChange={(e) => setDemo(e.target.checked)}
                />
                这是演示用轨迹标记，不代表真实学生身份确认
              </label>

              <details className="rounded-lg border border-[#e8dce9] bg-[#faf7fb] p-3 text-xs">
                <summary className="cursor-pointer font-medium text-[#4a3450]">
                  同时启用后续照片自动匹配（需实际授权）
                </summary>
                <div className="mt-2.5 space-y-2.5">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={authorized}
                      onChange={(e) => {
                        setAuthorized(e.target.checked);
                        if (e.target.checked) setDemo(false);
                        if (!e.target.checked) setFace(false);
                      }}
                    />
                    已取得该人物资料登记及所选用途的实际授权
                  </label>
                  {authorized && (
                    <>
                      <label className={labelClass}>
                        授权依据
                        <textarea
                          className={textareaClass}
                          aria-label="截图登记授权依据"
                          required
                          minLength={5}
                          value={consent}
                          onChange={(e) => setConsent(e.target.value)}
                        />
                      </label>
                      <label className={labelClass}>
                        授权到期
                        <input
                          className={fieldClass}
                          aria-label="截图登记授权到期"
                          type="datetime-local"
                          required
                          value={expiry}
                          onChange={(e) => setExpiry(e.target.value)}
                        />
                      </label>
                      <label className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={face}
                          onChange={(e) => setFace(e.target.checked)}
                        />
                        允许本地人脸特征提取与后续匹配
                      </label>
                    </>
                  )}
                </div>
              </details>

              <p className="text-[11px] text-[#6e5a74]">
                登记只适用于指定轨迹与时间段；不跨越轨迹中断，不自动确认动作类别或跨视频身份。
              </p>

              {error ? <AlertBanner tone="danger">{error}</AlertBanner> : null}

              <button
                type="submit"
                className={btnClass("primary", "md", "w-full")}
                disabled={busy || !cid || (!sid && !name.trim())}
              >
                {busy ? "正在保存…": "保存截图并加入班级"}
              </button>
            </form>
          </dialog>
        )}
      </div>
    </section>
  );
}

const historySchema = z.object({
  display_name: z.string(),
  identity_confirmed: z.boolean(),
  profiles: z.array(
    z.object({
      binding_id: z.string(),
      run_id: z.string(),
      track_id: z.number(),
      local_start: z.number(),
      local_end: z.number(),
      portrait_url: z.string(),
      action_groups: z.array(action),
    }),
  ),
});

export function RegisteredTrackHistory({ subjectId }: { subjectId: string }) {
  const s = useSession();
  const nav = useNavigate();
  const [data, setData] = useState<z.infer<typeof historySchema> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setData(null);
    setError("");
    void request(
      `workbench/students/${encodeURIComponent(subjectId)}/profiles`,
      historySchema,
    )
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [subjectId]);

  async function open(run: string, tid: number, t: number) {
    try {
      const job = s.data.jobs.find((j) => j.run_id === run);
      if (!job) throw Error("当前账户未载入该录像任务");
      await s.loadJob(job.job_id);
      nav("/review?track="+ tid + "&t="+ t);
    } catch (e) {
      setError(String(e));
    }
  }

  async function revoke(id: string) {
    const reason = window.prompt("撤销轨迹登记原因（至少5字）");
    if (!reason) return;
    try {
      await request(
        `workbench/registrations/${encodeURIComponent(id)}/revoke`,
        z.object({ status: z.string() }),
        { reason },
      );
      const updated = await request(
        `workbench/students/${encodeURIComponent(subjectId)}/profiles`,
        historySchema,
      );
      setData(updated);
      window.dispatchEvent(new Event("lessontrace:identity-changed"));
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="space-y-3 p-4">
      {error ? <AlertBanner tone="danger">{error}</AlertBanner> : null}
      {data && !data.profiles.length && (
        <p className="rounded-xl border border-dashed border-[#dfd0e2] p-4 text-xs text-[#6e5a74]">
          这份演示资料尚未建立人物身份关联。可先在视频工作台查看匿名轨迹与动作记录。
        </p>
      )}
      {data?.profiles.map((p) => (
        <article
          key={p.binding_id}
          className="flex flex-col sm:flex-row gap-3 rounded-xl border border-[#ebdce9] bg-[#fcf9fd] p-3"
        >
          <img
            src={p.portrait_url}
            alt="登记时的真实轨迹截图"
            className="h-24 w-20 shrink-0 rounded-lg border border-[#e3d2e6] object-cover"
          />
          <div className="flex-1 space-y-1.5 text-sm">
            <strong className="text-[#231426]">
              {data.display_name} · 轨迹登记
            </strong>
            <p className="tabular text-xs text-[#6e5a74]">
              {fmt(p.local_start)}–{fmt(p.local_end)} · 人物{p.track_id}
            </p>
            {p.action_groups.map((g) => (
              <p
                key={g.group_id || g.observed_action}
                className="text-xs text-[#4a3450]"
              >
                {observationText(g.observed_action)} ·{" "}
                {g.observed_times.map(fmt).join("、")}
              </p>
            ))}
            <div className="flex flex-wrap gap-2 pt-1">
              <button
                type="button"
                className={btnClass("secondary", "sm")}
                onClick={() => void open(p.run_id, p.track_id, p.local_start)}
              >
                <Eye size={13} />
                查看人物与动作
              </button>
              <button
                type="button"
                className={btnClass("ghost", "sm")}
                onClick={async () => {
                  try {
                    const job = s.data.jobs.find((j) => j.run_id === p.run_id);
                    if (!job) throw Error("请先载入该课堂");
                    await s.loadJob(job.job_id);
                    nav("/report?profile="+ encodeURIComponent(subjectId));
                  } catch (e) {
                    setError(String(e));
                  }
                }}
              >
                <FileText size={13} />
                生成此人物报告
              </button>
              {s.canWrite &&
                ["admin", "teacher"].includes(s.account?.role || "") && (
                  <button
                    type="button"
                    className={btnClass("danger", "sm")}
                    onClick={() => void revoke(p.binding_id)}
                  >
                    <Trash2 size={13} />
                    撤销登记关联
                  </button>
                )}
            </div>
            <p className="text-[11px] text-[#6e5a74]">
              按视频轨迹归纳，未作为真实姓名或行为的独立人工确认。
            </p>
          </div>
        </article>
      ))}
    </div>
  );
}
