import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  Users,
  Plus,
  Search,
  ArrowLeft,
  Upload,
  Video,
  Activity,
  Settings,
  RefreshCw,
  Trash2,
  X,
  RotateCcw,
  Camera,
} from "lucide-react";
import * as api from "../api/classrooms";
import { useSession } from "../state/session";
import { StudentTimeline } from "../components/StudentTimeline";
import { videoTitle } from "../models/view";
import { videoUrl } from "../api/videos";
import { RegisteredTrackHistory } from "../components/PersonProfiles";
import type { Video as VideoModel, Job } from "../models/backend";
import {
  AlertBanner,
  badgeClass,
  btnClass,
  cardClass,
  cardHeadClass,
  cardTitleClass,
  cn,
  EmptyState,
  fieldClass,
  labelClass,
  selectClass,
  statusTone,
  textareaClass,
} from "../components/ui/primitives";

const tabs = [
  ["students", "学生名册", Users],
  ["videos", "课堂录像", Video],
] as const;

const statusLabel: Record<string, string> = {
  completed: "分析完成",
  running: "分析中",
  queued: "排队中",
  paused: "已暂停",
  failed: "需重试",
  cancelled: "已取消",
  interrupted: "已中断",
  pausing: "正在暂停",
  cancelling: "正在取消",
};

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    return () => d?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="lt-dialog"
      aria-label={title}
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <header className="flex items-center justify-between border-b border-[#e8dce9] px-5 py-4">
        <h2 className="text-base font-semibold text-[#231426]">{title}</h2>
        <button
          type="button"
          className={btnClass("ghost", "sm")}
          aria-label="关闭窗口"
          onClick={onClose}
        >
          <X size={14} />
          关闭
        </button>
      </header>
      <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
    </dialog>
  );
}

function ClassForm({
  initial,
  busy,
  onSave,
}: {
  initial?: api.Classroom;
  busy: boolean;
  onSave: (body: {
    name: string;
    grade: string;
    term: string;
    description: string;
  }) => void;
}) {
  const [name, setName] = useState(initial?.name || "");
  const [grade, setGrade] = useState(initial?.grade || "");
  const [term, setTerm] = useState(initial?.term || "");
  const [description, setDescription] = useState(initial?.description || "");

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({ name, grade, term, description });
      }}
    >
      <label className={labelClass}>
        班级名称
        <input
          className={fieldClass}
          aria-label="班级名称"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          maxLength={160}
          placeholder="例如：三年级语文演示班"
        />
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className={labelClass}>
          年级
          <input
            className={fieldClass}
            aria-label="年级"
            value={grade}
            onChange={(e) => setGrade(e.target.value)}
            maxLength={80}
          />
        </label>
        <label className={labelClass}>
          学期
          <input
            className={fieldClass}
            aria-label="学期"
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            maxLength={80}
          />
        </label>
      </div>
      <label className={labelClass}>
        备注
        <textarea
          className={textareaClass}
          aria-label="班级备注"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          maxLength={1000}
          rows={3}
        />
      </label>
      <div className="flex justify-end">
        <button
          type="submit"
          className={btnClass("primary", "md")}
          disabled={busy || !name.trim()}
        >
          {busy ? "正在保存…" : initial ? "保存班级设置" : "创建班级"}
        </button>
      </div>
    </form>
  );
}

function StudentForm({
  busy,
  onSave,
  initial,
}: {
  busy: boolean;
  onSave: (body: api.StudentInput) => void;
  initial?: api.Student;
}) {
  const [name, setName] = useState(initial?.display_name || "");
  const [record, setRecord] = useState("");
  const [expiry, setExpiry] = useState("");
  const [authorized, setAuthorized] = useState(false);
  const [face, setFace] = useState(false);
  const [reference, setReference] = useState(false);

  function submit(e: FormEvent) {
    e.preventDefault();
    onSave({
      display_name: name,
      consent_record: record,
      expires_at: reference ? "" : new Date(expiry).toISOString(),
      authorized: reference ? false : authorized,
      allow_face_matching: reference ? false : face,
      reference_only: reference,
    });
  }

  return (
    <form className="space-y-3.5" onSubmit={submit}>
      <label className={labelClass}>
        学生姓名或演示名
        <input
          className={fieldClass}
          aria-label="学生姓名"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          maxLength={120}
        />
      </label>
      {!initial && (
        <label className="flex items-center gap-2 text-xs text-[#4a3450]">
          <input
            type="checkbox"
            checked={reference}
            onChange={(e) => setReference(e.target.checked)}
            className="accent-[#992777]"
          />
          仅保存演示人物资料，不进行身份识别
        </label>
      )}
      <label className={labelClass}>
        {reference ? "资料来源" : "授权依据"}
        <textarea
          className={textareaClass}
          aria-label="授权依据"
          value={record}
          onChange={(e) => setRecord(e.target.value)}
          required
          minLength={5}
          maxLength={4000}
          rows={3}
          placeholder={
            reference ? "说明截图或示例资料来自哪里" : "授权者、允许用途与依据"
          }
        />
      </label>
      {!reference && (
        <>
          <label className={labelClass}>
            授权有效期
            <input
              className={fieldClass}
              aria-label="授权有效期"
              type="datetime-local"
              value={expiry}
              onChange={(e) => setExpiry(e.target.value)}
              required
            />
          </label>
          <label className="flex items-center gap-2 text-xs text-[#4a3450]">
            <input
              type="checkbox"
              checked={authorized}
              onChange={(e) => setAuthorized(e.target.checked)}
              className="accent-[#992777]"
            />
            已获得登记及保存照片的有效授权
          </label>
          <label className="flex items-center gap-2 text-xs text-[#4a3450]">
            <input
              type="checkbox"
              checked={face}
              onChange={(e) => setFace(e.target.checked)}
              className="accent-[#992777]"
            />
            同时授权本地人脸匹配（可选）
          </label>
        </>
      )}
      <div className="flex justify-end pt-1">
        <button
          type="submit"
          className={btnClass("primary", "md")}
          disabled={busy || (!reference && !authorized)}
        >
          {busy ? "正在保存…" : initial ? "保存登记授权" : "添加并保存"}
        </button>
      </div>
    </form>
  );
}

export function ClassroomsPage() {
  const s = useSession();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();

  const cid = params.get("classroom") || "";
  const tab = tabs.some((t) => t[0] === params.get("tab"))
    ? params.get("tab")!
    : "students";
  const cidRef = useRef(cid);
  cidRef.current = cid;

  const [classes, setClasses] = useState<api.Classroom[]>([]);
  const [detail, setDetail] = useState<Awaited<
    ReturnType<typeof api.getClassroom>
  > | null>(null);
  const [videos, setVideos] = useState<VideoModel[]>([]);
  const [selected, setSelected] = useState<api.Student | null>(null);
  const [refs, setRefs] = useState<Awaited<ReturnType<typeof api.references>>>(
    [],
  );
  const [modal, setModal] = useState<
    "class" | "settings" | "student" | "authorize" | null
  >(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [search, setSearch] = useState("");
  const [attach, setAttach] = useState("");
  const [profileTab, setProfileTab] = useState<"photos" | "timeline">("photos");

  const canEdit = ["admin", "teacher"].includes(s.account?.role || "");

  async function loadClasses() {
    const result = await api.listClassrooms();
    setClasses(result);
    return result;
  }

  async function loadClass(id: string) {
    const [d, v] = await Promise.all([
      api.getClassroom(id),
      api.classVideos(id),
    ]);
    if (cidRef.current !== id) return;
    setDetail(d);
    setVideos(v.filter((x) => x.status === "active"));
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
    let alive = true;
    void loadClasses()
      .then((rows) => {
        if (alive && !params.get("classroom") && rows.length)
          setParams(
            { classroom: rows[0].classroom_id, tab: "students" },
            { replace: true },
          );
      })
      .catch((e) => setError(String(e)))
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    setSelected(null);
    setRefs([]);
    setDetail(null);
    setVideos([]);
    setSearch("");
    if (cid) {
      setLoading(true);
      void loadClass(cid)
        .catch((e) => {
          if (cidRef.current === cid) setError(String(e));
        })
        .finally(() => {
          if (cidRef.current === cid) setLoading(false);
        });
    }
  }, [cid]);

  async function choose(p: api.Student) {
    setSelected(p);
    setRefs([]);
    setProfileTab("photos");
    setRefs(await api.references(p.subject_id));
  }

  function selectTab(next: string) {
    setSelected(null);
    setParams({ classroom: cid, tab: next });
    setMessage("");
  }

  const students = detail?.students || [];
  const classJobs = s.data.jobs.filter((j) =>
    videos.some((v) => v.video_id === j.video_id),
  );
  const completedJobs = classJobs.filter((j) => j.status === "completed").length;
  const shown = students.filter((p) => p.display_name.includes(search.trim()));
  const subtitleText = detail
    ? [detail.grade, detail.term].filter(Boolean).join(" · ")
    : "";

  async function openVideo(video: VideoModel, job?: Job) {
    if (job) {
      await s.loadJob(job.job_id);
      nav("/review");
    } else {
      s.selectVideo(video.video_id);
      nav("/?classroom=" + encodeURIComponent(cid));
    }
  }

  const available = s.data.videos.filter(
    (v) => v.status === "active" && !videos.some((x) => x.video_id === v.video_id),
  );

  return (
    <div className="mx-auto max-w-[1360px] space-y-5">
      {/* Unified Header: Title + Grade/Term Subtitle + Classroom Picker + Actions */}
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-bold tracking-tight text-[#231426]">
              班级管理
            </h1>
            {classes.length > 0 && (
              <select
                className={`${selectClass} w-auto max-w-[260px] py-1 text-sm font-medium`}
                aria-label="我的班级"
                value={cid}
                onChange={(e) => {
                  setError("");
                  setParams({ classroom: e.target.value, tab: "students" });
                }}
              >
                <option value="" disabled>
                  请选择班级
                </option>
                {classes.map((c) => (
                  <option key={c.classroom_id} value={c.classroom_id}>
                    {c.name} · {c.student_count || 0}人
                  </option>
                ))}
              </select>
            )}
          </div>
          {detail && (
            <p className="text-xs text-[#6e5a74]">
              {subtitleText ? (
                <>
                  <span>{subtitleText}</span>
                  <span className="mx-2 opacity-40">·</span>
                </>
              ) : null}
              <span>
                {students.length} 人物 · {videos.length} 录像 · {completedJobs}{" "}
                已完成分析
              </span>
            </p>
          )}
        </div>

        <div className="flex items-center gap-2">
          {detail && canEdit && (
            <button
              type="button"
              className={btnClass("secondary", "md")}
              onClick={() => setModal("settings")}
            >
              <Settings size={15} />
              班级设置
            </button>
          )}
          {canEdit && (
            <button
              type="button"
              className={btnClass("primary", "md")}
              onClick={() => setModal("class")}
            >
              <Plus size={15} />
              创建班级
            </button>
          )}
        </div>
      </header>

      {error ? (
        <AlertBanner
          tone="danger"
          action={
            <button
              type="button"
              className={btnClass("ghost", "sm")}
              onClick={() =>
                void task(async () => {
                  await loadClasses();
                  if (cid) await loadClass(cid);
                })
              }
            >
              <RotateCcw size={13} />
              重试
            </button>
          }
        >
          {error}
        </AlertBanner>
      ) : null}

      {message ? <AlertBanner tone="status">{message}</AlertBanner> : null}

      {loading && (
        <section
          className={cn(cardClass, "p-8 text-center text-sm text-[#6e5a74]")}
          role="status"
        >
          正在载入班级…
        </section>
      )}

      {!loading && !cid && (
        <EmptyState
          icon={<Users size={22} />}
          title="从一个班级开始"
          description="创建班级，添加学生照片，再上传课堂录像。"
          action={
            canEdit ? (
              <button
                type="button"
                className={btnClass("primary", "md")}
                onClick={() => setModal("class")}
              >
                创建第一个班级
              </button>
            ) : null
          }
        />
      )}

      {detail && (
        <div className="space-y-4">
          {/* Two Clean Underline Tabs: 学生名册 / 课堂录像（含分析记录） */}
          <div
            className="flex items-center gap-1 overflow-x-auto border-b border-[#e8dce9]"
            role="tablist"
            aria-label="班级内容"
          >
            {tabs.map(([key, label, TabIcon]) => {
              const active = tab === key;
              const count = key === "students" ? students.length : videos.length;
              return (
                <button
                  type="button"
                  role="tab"
                  aria-label={label}
                  aria-selected={active}
                  id={"class-tab-" + key}
                  aria-controls={"class-panel-" + key}
                  key={key}
                  onClick={() => selectTab(key)}
                  className={cn(
                    "relative inline-flex shrink-0 items-center gap-1.5 px-4 py-2.5 text-sm font-medium transition",
                    active
                      ? "text-[#992777] font-semibold after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:rounded-full after:bg-[#992777]"
                      : "text-[#6e5a74] hover:text-[#231426]",
                  )}
                >
                  <TabIcon size={15} />
                  <span>{label}</span>
                  <span
                    aria-hidden="true"
                    className={cn(
                      "rounded-full px-1.5 py-0.2 text-[11px] tabular",
                      active
                        ? "bg-[#f7eaf3] text-[#992777]"
                        : "bg-[#f3ebf5] text-[#7d6984]",
                    )}
                  >
                    {count}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Tab Panels */}
          <section
            role="tabpanel"
            id={"class-panel-" + tab}
            aria-labelledby={"class-tab-" + tab}
            className="space-y-5"
          >
            {/* STUDENTS TAB */}
            {tab === "students" && (
              <>
                {!selected ? (
                  <>
                    <div className="flex flex-wrap items-center gap-2.5">
                      <label className="relative min-w-[180px] flex-1">
                        <span className="sr-only">查找学生</span>
                        <Search
                          size={14}
                          className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-[#9c89a2]"
                        />
                        <input
                          className={`${fieldClass} pl-8`}
                          aria-label="查找学生"
                          placeholder="查找姓名或演示名"
                          value={search}
                          onChange={(e) => setSearch(e.target.value)}
                        />
                      </label>
                      {canEdit && (
                        <button
                          type="button"
                          className={btnClass("primary", "md")}
                          onClick={() => setModal("student")}
                        >
                          <Plus size={15} />
                          添加学生
                        </button>
                      )}
                    </div>

                    {shown.length > 0 ? (
                      <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
                        {shown.map((p) => (
                          <button
                            type="button"
                            key={p.subject_id}
                            onClick={() => void task(() => choose(p))}
                            aria-label={"查看" + p.display_name}
                            className={cn(
                              cardClass,
                              "group flex items-center gap-3.5 p-3.5 text-left transition hover:border-[#cfa1c3] hover:shadow-sm",
                            )}
                          >
                            {p.photo_reference_id ? (
                              <img
                                src={api.photoUrl(
                                  p.subject_id,
                                  p.photo_reference_id,
                                )}
                                alt={p.display_name + "的参考照片"}
                                loading="lazy"
                                className="h-14 w-14 shrink-0 rounded-xl border border-[#e8dce9] object-cover"
                              />
                            ) : (
                              <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-[#f7eaf3] text-sm font-bold text-[#992777]">
                                {p.display_name.slice(0, 2)}
                              </div>
                            )}
                            <div className="min-w-0 flex-1">
                              <strong className="block truncate text-sm font-semibold text-[#231426] group-hover:text-[#992777]">
                                {p.display_name}
                              </strong>
                              <span className="mt-1 block truncate text-xs text-[#6e5a74]">
                                {p.status === "reference_only"
                                  ? p.track_registration_count
                                    ? "轨迹已登记"
                                    : "演示人物 · 未作身份登记"
                                  : p.status === "active"
                                    ? "已登记"
                                    : "登记已撤销"}
                              </span>
                              <span className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-[#8f7a96]">
                                <Camera size={11} />
                                {p.photo_count || 0}张照片
                              </span>
                            </div>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <EmptyState
                        icon={<Users size={22} />}
                        title={search ? "没有找到匹配的学生" : "还没有学生资料"}
                        description={
                          search
                            ? "试试其他姓名。"
                            : "添加学生后，可以上传照片并查看其课堂记录。"
                        }
                      />
                    )}
                  </>
                ) : (
                  <section className={cardClass}>
                    <header className={cardHeadClass}>
                      <div className="flex items-center gap-2.5">
                        <button
                          type="button"
                          className={btnClass("ghost", "sm")}
                          onClick={() => setSelected(null)}
                        >
                          <ArrowLeft size={14} />
                          返回名册
                        </button>
                        <div className="h-4 w-px bg-[#e8dce9]" />
                        <h2 className="text-base font-bold text-[#231426]">
                          {selected.display_name}
                        </h2>
                      </div>
                      <span className={badgeClass("info")}>
                        {selected.status === "reference_only"
                          ? "演示参考"
                          : "人物资料"}
                      </span>
                    </header>

                    <div className="border-b border-[#f0e5f2] px-4 py-2">
                      <div
                        className="inline-flex rounded-xl bg-[#f4ebf6] p-1"
                        role="tablist"
                        aria-label="人物资料"
                      >
                        {(["photos", "timeline"] as const).map((key) => (
                          <button
                            type="button"
                            key={key}
                            role="tab"
                            aria-selected={profileTab === key}
                            onClick={() => setProfileTab(key)}
                            className={cn(
                              "rounded-lg px-3 py-1 text-xs font-medium transition",
                              profileTab === key
                                ? "bg-white font-semibold text-[#992777] shadow-2xs"
                                : "text-[#5f4965] hover:text-[#231426]",
                            )}
                          >
                            {key === "photos" ? "照片与资料" : "个人时间线"}
                          </button>
                        ))}
                      </div>
                    </div>

                    {profileTab === "photos" ? (
                      <div className="space-y-4 p-5">
                        <div className="flex flex-wrap gap-3">
                          {refs
                            .filter((r) => r.status === "active")
                            .map((r) => (
                              <figure
                                key={r.reference_image_id}
                                className="w-36 overflow-hidden rounded-xl border border-[#e8dce9] bg-[#faf7fb]"
                              >
                                <img
                                  src={api.photoUrl(
                                    selected.subject_id,
                                    r.reference_image_id,
                                  )}
                                  alt={selected.display_name + "参考照片"}
                                  className="h-36 w-full object-cover"
                                />
                                {canEdit && (
                                  <div className="p-1.5 text-center">
                                    <button
                                      type="button"
                                      className={btnClass("danger", "sm", "w-full")}
                                      disabled={busy}
                                      onClick={() => {
                                        if (
                                          window.confirm(
                                            "删除这张照片及其相关特征？",
                                          )
                                        )
                                          void task(async () => {
                                            await api.deletePhoto(
                                              selected.subject_id,
                                              r.reference_image_id,
                                            );
                                            await choose(selected);
                                            await loadClass(cid);
                                            setMessage("照片已删除");
                                          });
                                      }}
                                    >
                                      <Trash2 size={12} />
                                      删除照片
                                    </button>
                                  </div>
                                )}
                              </figure>
                            ))}
                        </div>

                        <div className="flex flex-wrap items-center gap-2.5">
                          {canEdit && (
                            <label
                              className={btnClass(
                                "primary",
                                "sm",
                                "cursor-pointer",
                              )}
                            >
                              <Upload size={14} />
                              {busy ? "正在保存…" : "上传参考照片"}
                              <input
                                aria-label="参考照片"
                                type="file"
                                accept="image/jpeg,image/png,image/webp"
                                multiple
                                disabled={busy}
                                className="sr-only"
                                onChange={(e) => {
                                  const files = Array.from(e.target.files || []);
                                  e.target.value = "";
                                  void task(async () => {
                                    for (const f of files) {
                                      if (f.size > 8 * 1024 * 1024)
                                        throw Error("单张照片不得超过8 MiB");
                                      await api.uploadStudentPhoto(
                                        selected.subject_id,
                                        f,
                                      );
                                    }
                                    await choose(selected);
                                    await loadClass(cid);
                                    setMessage("照片已保存");
                                  });
                                }}
                              />
                            </label>
                          )}

                          {canEdit && (
                            <button
                              type="button"
                              className={btnClass("secondary", "sm")}
                              disabled={busy}
                              onClick={() => setModal("authorize")}
                            >
                              {selected.status === "reference_only"
                                ? "完成登记与匹配授权"
                                : "更新登记与匹配授权"}
                            </button>
                          )}
                        </div>

                        <p className="text-xs text-[#6e5a74]">
                          {selected.status === "reference_only"
                            ? "同源视频截图仅作演示资料展示，不代表已确认身份；未提取人脸特征。"
                            : "照片保存在本地。登记照片后，在课堂录像中核对人物关联。"}
                        </p>

                        {canEdit && (
                          <details className="rounded-xl border border-[#e8dce9] p-3 text-xs">
                            <summary className="cursor-pointer font-medium text-[#5f4965]">
                              修改或移除人物资料
                            </summary>
                            <div className="mt-2.5 flex flex-wrap gap-2">
                              <button
                                type="button"
                                className={btnClass("secondary", "sm")}
                                disabled={busy}
                                onClick={() => {
                                  const n = window.prompt(
                                    "修改显示名",
                                    selected.display_name,
                                  );
                                  if (n)
                                    void task(async () => {
                                      await api.editStudentName(
                                        selected.subject_id,
                                        n,
                                      );
                                      setSelected({
                                        ...selected,
                                        display_name: n,
                                      });
                                      await loadClass(cid);
                                    });
                                }}
                              >
                                修改显示名
                              </button>
                              <button
                                type="button"
                                className={btnClass("danger", "sm")}
                                disabled={busy}
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      "移除这份人物资料及其照片、身份关联？",
                                    )
                                  )
                                    void task(async () => {
                                      await api.revokeStudent(
                                        selected.subject_id,
                                      );
                                      setSelected(null);
                                      await loadClass(cid);
                                      await loadClasses();
                                    });
                                }}
                              >
                                移除资料
                              </button>
                            </div>
                          </details>
                        )}
                      </div>
                    ) : (
                      <div className="space-y-4">
                        <RegisteredTrackHistory
                          subjectId={selected.subject_id}
                        />
                        {selected.status !== "reference_only" && (
                          <div className="px-4 pb-4">
                            <StudentTimeline subjectId={selected.subject_id} />
                          </div>
                        )}
                      </div>
                    )}
                  </section>
                )}
              </>
            )}

            {/* VIDEOS + MERGED ANALYSIS JOBS TAB */}
            {tab === "videos" && (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-xs text-[#6e5a74]">
                    共 {videos.length} 段录像 · 点击进入分析与复盘
                  </p>
                  <Link
                    className={btnClass("primary", "sm")}
                    to={"/?classroom=" + encodeURIComponent(cid)}
                  >
                    <Upload size={14} />
                    上传课堂录像
                  </Link>
                </div>

                {videos.length > 0 ? (
                  <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    {videos.map((v, index) => {
                      const job =
                        classJobs.find(
                          (j) =>
                            j.video_id === v.video_id &&
                            j.status === "completed",
                        ) || classJobs.find((j) => j.video_id === v.video_id);
                      return (
                        <article
                          key={v.video_id}
                          className={cn(cardClass, "overflow-hidden")}
                        >
                          <div className="relative bg-[#130c17]">
                            <video
                              preload="metadata"
                              muted
                              playsInline
                              src={videoUrl(v.video_id)}
                              aria-label={"录像预览" + (index + 1)}
                              className="aspect-video w-full object-cover"
                            />
                            <span className="tabular absolute right-2 bottom-2 rounded-md bg-[#130c17]/80 px-2 py-0.5 text-[11px] font-medium text-white backdrop-blur-xs">
                              {v.metadata.duration.toFixed(0)}秒
                            </span>
                          </div>
                          <div className="space-y-2 p-4">
                            <div className="flex items-center justify-between gap-2">
                              <h3 className="text-sm font-bold text-[#231426]">
                                课堂片段 {index + 1}
                              </h3>
                              <span
                                className={badgeClass(
                                  job ? statusTone(job.status) : "neutral",
                                )}
                              >
                                {job
                                  ? statusLabel[job.status] || job.status
                                  : "尚未分析"}
                              </span>
                            </div>
                            <p className="truncate text-xs text-[#6e5a74]">
                              {videoTitle(v)}
                            </p>
                            <button
                              type="button"
                              className={btnClass("primary", "sm", "w-full")}
                              disabled={busy}
                              onClick={() => void task(() => openVideo(v, job))}
                            >
                              {job?.status === "completed"
                                ? "查看课堂复盘"
                                : job
                                  ? "查看分析进度"
                                  : "开始分析"}
                            </button>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                ) : (
                  <EmptyState
                    icon={<Video size={22} />}
                    title="尚未添加课堂录像"
                    description="上传视频后，将在这里保存每次分析和复盘。"
                  />
                )}

                {canEdit && available.length > 0 && (
                  <details className={cn(cardClass, "p-4 text-xs")}>
                    <summary className="cursor-pointer font-medium text-[#4a3450]">
                      关联已有录像
                    </summary>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <select
                        className={`${selectClass} w-auto max-w-xs`}
                        aria-label="已有录像"
                        value={attach}
                        onChange={(e) => setAttach(e.target.value)}
                      >
                        <option value="">选择一段已上传录像</option>
                        {available.map((v) => (
                          <option key={v.video_id} value={v.video_id}>
                            {videoTitle(v)}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        className={btnClass("secondary", "sm")}
                        disabled={busy || !attach}
                        onClick={() =>
                          void task(async () => {
                            await api.linkVideo(cid, attach);
                            await loadClass(cid);
                            setAttach("");
                            setMessage("录像已关联");
                          })
                        }
                      >
                        关联到本班
                      </button>
                    </div>
                  </details>
                )}

                {/* Merged Analysis Records Section */}
                <section className={cardClass}>
                  <div className={cardHeadClass}>
                    <h2 className={cardTitleClass}>
                      <Activity size={15} className="text-[#992777]" />
                      分析记录
                    </h2>
                    <button
                      type="button"
                      className={btnClass("ghost", "sm")}
                      disabled={busy}
                      onClick={() =>
                        void task(async () => {
                          await s.refresh();
                          await loadClass(cid);
                        })
                      }
                    >
                      <RefreshCw size={13} />
                      刷新
                    </button>
                  </div>
                  <div className="divide-y divide-[#f2eaf4] p-4">
                    {classJobs.map((j) => (
                      <div
                        key={j.job_id}
                        className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
                      >
                        <div>
                          <strong className="text-sm font-semibold text-[#231426]">
                            {videoTitle(
                              videos.find((v) => v.video_id === j.video_id),
                            )}
                          </strong>
                          <p className="mt-1 flex items-center gap-2 text-xs text-[#6e5a74]">
                            <span className={badgeClass(statusTone(j.status))}>
                              {statusLabel[j.status] || j.status}
                            </span>
                            <span className="tabular">
                              已处理 {j.processed_windows || 0}/
                              {j.total_windows || 0} 个窗口
                            </span>
                          </p>
                        </div>
                        <button
                          type="button"
                          className={btnClass("secondary", "sm")}
                          disabled={busy}
                          onClick={() =>
                            void task(async () => {
                              await s.loadJob(j.job_id);
                              nav(
                                j.status === "completed"
                                  ? "/review"
                                  : "/?classroom=" + encodeURIComponent(cid),
                              );
                            })
                          }
                        >
                          {j.status === "completed" ? "查看结果" : "查看进度"}
                        </button>
                      </div>
                    ))}
                    {!classJobs.length && (
                      <p className="py-5 text-center text-xs text-[#6e5a74]">
                        暂无分析记录，请先上传并分析课堂录像。
                      </p>
                    )}
                  </div>
                </section>
              </>
            )}
          </section>
        </div>
      )}

      {/* Modals */}
      {modal === "class" && (
        <Modal title="创建班级" onClose={() => setModal(null)}>
          <ClassForm
            busy={busy}
            onSave={(body) =>
              void task(async () => {
                const c = await api.createClassroom(body);
                await loadClasses();
                setParams({ classroom: c.classroom_id, tab: "students" });
                setModal(null);
                setMessage("班级已创建");
              })
            }
          />
        </Modal>
      )}

      {modal === "settings" && detail && (
        <Modal title="班级设置" onClose={() => setModal(null)}>
          <ClassForm
            key={cid}
            initial={detail}
            busy={busy}
            onSave={(body) =>
              void task(async () => {
                await api.editClassroom(cid, body.name, body);
                await loadClasses();
                await loadClass(cid);
                setModal(null);
                setMessage("班级设置已保存");
              })
            }
          />
        </Modal>
      )}

      {modal === "student" && (
        <Modal title="添加学生资料" onClose={() => setModal(null)}>
          <StudentForm
            busy={busy}
            onSave={(body) =>
              void task(async () => {
                await api.addStudent(cid, body);
                await loadClass(cid);
                await loadClasses();
                setModal(null);
                setMessage("人物资料已保存，可点击卡片添加照片");
              })
            }
          />
        </Modal>
      )}

      {modal === "authorize" && selected && (
        <Modal title="登记与身份匹配授权" onClose={() => setModal(null)}>
          <StudentForm
            initial={selected}
            busy={busy}
            onSave={(body) =>
              void task(async () => {
                await api.editStudent(selected.subject_id, body);
                await loadClass(cid);
                await loadClasses();
                const fresh = await api.getClassroom(cid);
                const person = fresh.students.find(
                  (p) => p.subject_id === selected.subject_id,
                );
                if (person) await choose(person);
                setModal(null);
                setMessage(
                  "登记授权已保存。照片质量与匹配候选可在视频人物管理中查看。",
                );
              })
            }
          />
        </Modal>
      )}
    </div>
  );
}
