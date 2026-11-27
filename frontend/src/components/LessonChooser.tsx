import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { z } from "zod";
import { Video, Plus, FolderOpen, PlayCircle, ChevronRight } from "lucide-react";
import { request } from "../api/client";
import { useSession } from "../state/session";
import { listClassrooms, type Classroom } from "../api/classrooms";
import { uploadVideo } from "../api/videos";
import { jobSchema } from "../models/backend";
import { materialNames } from "../api/materials";
import {
  AlertBanner,
  btnClass,
  cn,
  fieldClass,
  labelClass,
  selectClass,
} from "./ui/primitives";

const lessonSchema = z
  .object({
    lesson_id: z.string(),
    title: z.string(),
    classroom_id: z.string().nullable(),
    video_id: z.string().nullable(),
    document_ids: z.array(z.string()),
  })
  .passthrough();

type Lesson = z.infer<typeof lessonSchema>;

const wizardStages = ["课堂信息", "教学资料", "课堂录像", "开始复盘"];

export function LessonChooser({ requested = false, onClose }: { requested?: boolean; onClose?: () => void }) {
  const s = useSession();
  const location = useLocation();
  const nav = useNavigate();
  const dialog = useRef<HTMLDialogElement>(null);
  const opened = useRef(false);

  const [mode, setMode] = useState<"select"| "new">("select");
  const [step, setStep] = useState(1);
  const [title, setTitle] = useState("");
  const [cid, setCid] = useState("");
  const [classes, setClasses] = useState<Classroom[]>([]);
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [lesson, setLesson] = useState<Lesson | null>(null);
  const [category, setCategory] = useState("teacher_plan");
  const [files, setFiles] = useState<File[]>([]);
  const [video, setVideo] = useState<File | null>(null);
  const [preset, setPreset] = useState("quick");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [progress, setProgress] = useState(0);
  const [example, setExample] = useState(false);

  const idKey = "lessontrace.chosen."+ (s.account?.actor || "");

  function askAboutLesson(lessonId: string) {
    dialog.current?.close();
    nav("/qa?lesson=" + encodeURIComponent(lessonId));
  }

  function show() {
    setError("");
    dialog.current?.showModal();
    void Promise.all([
      listClassrooms(),
      request("lessons", z.array(lessonSchema)),
    ])
      .then(([c, l]) => {
        setClasses(c);
        setLessons(l);
      })
      .catch((e) => setError(String(e)));
  }

  useEffect(() => {
    if (requested && !dialog.current?.open) show();
  }, [requested]);

  useEffect(() => {
    if (!s.account || s.restoring || opened.current) return;
    if (
      location.pathname === "/review"&&
      (!sessionStorage.getItem(idKey) ||
        (location.state as { chooseLesson?: boolean } | null)?.chooseLesson)
    ) {
      opened.current = true;
      show();
    }
  }, [s.account, s.restoring, location.pathname]);

  async function task(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function openVideo(vid: string, lid?: string) {
    await task(async () => {
      const refreshed = await s.refresh();
      if (!refreshed) throw Error("课堂列表读取失败，请重试");
      const jobs = refreshed.jobs
        .filter((j) => j.video_id === vid)
        .sort(
          (a, b) =>
            Number(["running", "queued", "pausing"].includes(b.status)) -
              Number(["running", "queued", "pausing"].includes(a.status)) ||
            b.processed_windows - a.processed_windows ||
            Date.parse(String(b.created_at)) - Date.parse(String(a.created_at)),
        );
      if (jobs[0]) await s.loadJob(jobs[0].job_id);
      else s.selectVideo(vid);
      sessionStorage.setItem(idKey, lid || vid);
      dialog.current?.close();
      nav("/review", { replace: true });
    });
  }

  async function next() {
    await task(async () => {
      if (step === 1) {
        if (!lesson) {
          const l = await request("lessons", lessonSchema, {
            title,
            classroom_id: cid || null,
          });
          setLesson(l);
        }
        setStep(2);
        return;
      }
      if (!lesson) throw Error("请先填写课堂基本信息");
      if (step === 2) {
        if (files.length + (lesson.document_ids.length || 0) > 30)
          throw Error("Maximum 30 materials per lesson");
        const ids: string[] = [];
        for (const f of files) {
          if (f.size > 10 * 1024 * 1024) throw Error("每份教学资料不超过10 MiB");
          const form = new FormData();
          form.append("file", f);
          form.append("title", f.name.replace(/\.[^.]+$/, ""));
          form.append("source_type", category);
          form.append("is_example", String(example));
          const d = await request(
            "upload",
            z.object({ document_id: z.string(), status: z.string() }),
            form,
          );
          ids.push(d.document_id);
        }
        if (ids.length)
          setLesson(
            await request(
              "lessons/"+ lesson.lesson_id + "/resources",
              lessonSchema,
              { document_ids: ids },
              "PUT",
            ),
          );
        setFiles([]);
        setStep(3);
        return;
      }
      if (step === 3) {
        if (!video) throw Error("请选择课堂录像");
        if (video.size > 512 * 1024 * 1024)
          throw Error("课堂录像不超过512 MiB");
        const uploaded = await uploadVideo(video, setProgress);
        const l = await request(
          "lessons/"+ lesson.lesson_id + "/resources",
          lessonSchema,
          { video_id: uploaded.video_id },
          "PUT",
        );
        setLesson(l);
        await s.refresh();
        setStep(4);
        return;
      }
      const result = await request(
        "lessons/"+ lesson.lesson_id + "/start",
        z.object({ job: jobSchema, created: z.boolean() }),
        { preset },
      );
      await s.refresh();
      await s.loadJob(result.job.job_id);
      sessionStorage.setItem(idKey, lesson.lesson_id);
      dialog.current?.close();
      setLesson(null);
      setTitle("");
      setVideo(null);
      setStep(1);
      nav("/review", { replace: true });
    });
  }

  const used = new Set(lessons.map((l) => l.video_id));

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      className="lt-dialog"
      aria-label="选择或新建课堂"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) dialog.current?.close();
      }}
    >
      <header className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
        <div>
          <h2 className="text-base font-semibold text-slate-900">
            {mode === "select"? "选择课堂": "新建课堂"}
          </h2>
          <p className="mt-0.5 text-xs text-slate-500">
            打开已有课堂直接继续；新课堂按步骤准备材料。
          </p>
        </div>
        <button
          type="button"
          className={btnClass("ghost", "sm")}
          disabled={busy}
          onClick={() => dialog.current?.close()}
        >
          稍后选择
        </button>
      </header>

      <div className="border-b border-slate-100 px-5 py-2.5">
        <div
          className="inline-flex rounded-lg bg-slate-100 p-1"
          role="tablist"
        >
          <button
            type="button"
            role="tab"
            aria-selected={mode === "select"}
            onClick={() => setMode("select")}
            className={cn(
              "rounded-md px-3 py-1.5 text-xs font-medium transition",
              mode === "select"
                ? "bg-white text-slate-900 shadow-2xs"
                : "text-slate-600 hover:text-slate-900",
            )}
          >
            已有课堂
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "new"}
            disabled={!s.canWrite}
            onClick={() => {
              setMode("new");
              if (!lesson) setStep(1);
            }}
            className={cn(
              "inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-xs font-medium transition disabled:opacity-45",
              mode === "new"
                ? "bg-white text-slate-900 shadow-2xs"
                : "text-slate-600 hover:text-slate-900",
            )}
          >
            <Plus size={13} />
            新建课堂
          </button>
        </div>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
        {error ? <AlertBanner tone="danger">{error}</AlertBanner> : null}
        {message ? <AlertBanner tone="status">{message}</AlertBanner> : null}

        {mode === "select"? (
          <div className="grid gap-2.5">
            {lessons.map((l) => {
              const recording = s.data.videos.find(video => video.video_id === l.video_id);
              return (
              <button
                type="button"
                key={l.lesson_id}
                disabled={busy}
                onClick={() => {
                  if (l.video_id) void openVideo(l.video_id, l.lesson_id);
                  else {
                    setLesson(l);
                    setTitle(l.title);
                    setCid(l.classroom_id || "");
                    setMode("new");
                    setStep(2);
                  }
                }}
                className="group flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-3.5 text-left transition hover:border-[#cfa1c3] hover:bg-[#faf0f7]"
              >
                <div className="min-w-0">
                  <strong className="block truncate text-sm font-semibold text-slate-900">
                    {l.title}
                  </strong>
                  {recording && (
                    <p className="truncate text-xs text-slate-600"title={recording.metadata.filename}>
                      {recording.metadata.filename || "课堂录像"} · {recording.metadata.duration.toFixed(1)} 秒
                    </p>
                  )}
                  <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                    <span className="inline-flex items-center gap-1">
                      <PlayCircle size={12} />
                      {l.video_id ? "打开课堂": "继续准备资料"}
                    </span>
                    <span>·</span>
                    <small className="inline-flex items-center gap-1 text-xs">
                      <FolderOpen size={12} />
                      {l.document_ids.length}份关联资料
                    </small>
                  </div>
                </div>
                <ChevronRight
                  size={16}
                  className="shrink-0 text-slate-400 transition group-hover:translate-x-0.5 group-hover:text-[#992777]"
                />
              </button>
              );
            })}

            {s.data.videos
              .filter((v) => v.status === "active"&& !used.has(v.video_id))
              .map((v) => (
                <button
                  type="button"
                  key={v.video_id}
                  disabled={busy}
                  onClick={() => void openVideo(v.video_id)}
                  className="group flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-3.5 text-left transition hover:border-[#cfa1c3] hover:bg-[#faf0f7]"
                >
                  <div className="min-w-0">
                    <strong className="block truncate text-sm font-semibold text-slate-900">
                      {v.metadata.filename?.replace(/\.[^.]+$/, "") || "课堂录像"}
                    </strong>
                    <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                      <span className="inline-flex items-center gap-1">
                        <Video size={12} />
                        {v.metadata.duration.toFixed(1)}秒 · 已保存录像
                      </span>
                      <span>·</span>
                      <small className="text-xs">
                        {
                          s.data.jobs.filter((j) => j.video_id === v.video_id)
                            .length
                        }
                        次分析记录，直接加载
                      </small>
                    </div>
                  </div>
                  <ChevronRight
                    size={16}
                    className="shrink-0 text-slate-400 transition group-hover:translate-x-0.5 group-hover:text-[#992777]"
                  />
                </button>
              ))}

            {!lessons.length && !s.data.videos.length ? (
              <p className="rounded-xl border border-dashed border-slate-200 py-8 text-center text-sm text-slate-500">
                还没有课堂，点击“新建课堂”开始。
              </p>
            ) : null}
          </div>
        ) : (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void next();
            }}
          >
            {/* Step progress indicator */}
            <div className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-xs font-medium text-slate-700">
              <span>
                {wizardStages[step - 1]} · 第{step}/4步
              </span>
              <div className="flex gap-1.5">
                {wizardStages.map((_, idx) => (
                  <span
                    key={idx}
                    className={cn(
                      "h-1.5 w-6 rounded-full transition-colors",
                      idx + 1 <= step
                        ? "bg-[#992777]"
                        : "bg-slate-200",
                    )}
                  />
                ))}
              </div>
            </div>

            {step === 1 && (
              <div className="space-y-3">
                <label className={labelClass}>
                  课堂名称
                  <input
                    className={fieldClass}
                    aria-label="新课堂名称"
                    required
                    maxLength={200}
                    placeholder="例如：三年级语文 · 大自然的声音"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                  />
                </label>
                <label className={labelClass}>
                  所属班级
                  <select
                    className={selectClass}
                    aria-label="课堂所属班级"
                    value={cid}
                    onChange={(e) => setCid(e.target.value)}
                  >
                    <option value="">暂不关联班级</option>
                    {classes.map((c) => (
                      <option key={c.classroom_id} value={c.classroom_id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="text-xs text-slate-500">
                  班级名单与授权照片可在“班级管理”中维护。不关联班级也能观察匿名人物。
                </p>
              </div>
            )}

            {step === 2 && (
              <div className="space-y-3">
                <label className="flex items-center gap-2 text-xs text-slate-600">
                  <input
                    type="checkbox"
                    checked={example}
                    onChange={(e) => setExample(e.target.checked)}
                    className="rounded border-slate-300"
                  />
                  这是项目示例资料，并非原课堂的真实材料
                </label>
                <label className={labelClass}>
                  资料分类
                  <select
                    className={selectClass}
                    aria-label="课堂资料分类"
                    value={category}
                    onChange={(e) => setCategory(e.target.value)}
                  >
                    {Object.entries(materialNames)
                      .filter(([k]) => k !== "classroom_memory")
                      .map(([k, v]) => (
                        <option key={k} value={k}>
                          {v}
                        </option>
                      ))}
                  </select>
                </label>
                <label className={labelClass}>
                  上传教案、作业或课堂说明
                  <input
                    className={fieldClass}
                    aria-label="课堂资料文件"
                    type="file"
                    multiple
                    accept=".txt,.md,.pdf,.docx,.pptx,.png,.jpg,.jpeg,.webp"
                    onChange={(e) => setFiles(Array.from(e.target.files || []))}
                  />
                </label>
                <p className="text-xs text-slate-500">
                  {files.map((f) => f.name).join("、") ||
                    "资料可以稍后补充；图片文字需在资料库补录。"}
                </p>
                {!!lesson?.document_ids.length && (
                  <p className="text-xs font-medium text-emerald-700">
                    已关联{lesson.document_ids.length}份资料。
                  </p>
                )}
                <button
                  type="button"
                  className={btnClass("ghost", "sm")}
                  disabled={busy || !files.length}
                  onClick={() =>
                    void task(async () => {
                      const ids: string[] = [];
                      for (const f of files) {
                        const form = new FormData();
                        form.append("file", f);
                        form.append("title", f.name);
                        form.append("source_type", category);
                        form.append("is_example", String(example));
                        const d = await request(
                          "upload",
                          z.object({ document_id: z.string() }),
                          form,
                        );
                        ids.push(d.document_id);
                      }
                      setLesson(
                        await request(
                          "lessons/"+ lesson!.lesson_id + "/resources",
                          lessonSchema,
                          { document_ids: ids },
                          "PUT",
                        ),
                      );
                      setFiles([]);
                      setMessage("资料已保存，可继续上传其他分类。");
                    })
                  }
                >
                  保存这批并继续添加资料
                </button>
              </div>
            )}

            {step === 3 && (
              <div className="space-y-3">
                <label className={labelClass}>
                  选择课堂录像
                  <input
                    className={fieldClass}
                    data-testid="video-upload"
                    aria-label="课堂录像文件"
                    type="file"
                    accept=".mp4,.avi,.mov,.mkv"
                    required
                    onChange={(e) => setVideo(e.target.files?.[0] || null)}
                  />
                </label>
                <p className="text-xs text-slate-500">
                  单段录像，最大512 MiB、2小时。上传后由服务器校验解码能力。
                </p>
                {video && (
                  <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs font-medium text-slate-700">
                    {video.name} · {(video.size / 1024 / 1024).toFixed(1)} MiB
                  </p>
                )}
                {busy && (
                  <progress
                    aria-label="上传进度"
                    max={100}
                    value={progress}
                    className="h-2 w-full overflow-hidden rounded-full"
                  />
                )}
              </div>
            )}

            {lesson && step >= 2 && (
              <button type="button" className={btnClass("secondary", "sm")} disabled={busy}
                onClick={() => askAboutLesson(lesson.lesson_id)}>
                使用已保存资料提问
              </button>
            )}

            {step === 4 && (
              <div className="space-y-3">
                <h3 className="text-sm font-semibold text-slate-900">
                  {lesson?.title}
                </h3>
                <p className="text-xs text-slate-600">
                  课堂资料和录像已保存。分析完成后会留在工作台，无需重复上传。
                </p>
                <label className={labelClass}>
                  分析方式
                  <select
                    className={selectClass}
                    aria-label="课堂分析方式"
                    value={preset}
                    onChange={(e) => setPreset(e.target.value)}
                  >
                    <option value="quick">
                      快速预览（推荐）· 最多2个观察窗口
                    </option>
                    <option value="standard">
                      标准复盘 · 优先清晰目标，多时刻观察
                    </option>
                    <option value="lesson_sample">
                      长课分时段抽查 · 最多8个观察窗口
                    </option>
                    <option value="full_timeline">
                      全轨迹全时段 · 耗时较长
                    </option>
                  </select>
                </label>
                {preset === "lesson_sample" && (
                  <p className="text-xs text-slate-600">
                    整段录像分为最多 8 个时段。每段抽查一名可见人物，画面中没有合适人物的时段会留空。
                  </p>
                )}
                {lesson?.video_id &&
                  s.data.jobs.some(
                    (j) => j.video_id === lesson.video_id && j.run_id,
                  ) && (
                    <button
                      type="button"
                      className={btnClass("secondary", "sm")}
                      disabled={busy}
                      onClick={() =>
                        void openVideo(lesson.video_id!, lesson.lesson_id)
                      }
                    >
                      使用已有分析打开课堂
                    </button>
                  )}
                <p className="text-xs text-slate-500">
                  快速预览只分析少量采样时刻，不代表整节课或所有人物。需要更多观察时可继续补充分析；全时段分析在低配服务器上可能耗时很长。已有分析可直接打开。
                </p>
              </div>
            )}

            <div className="flex items-center justify-end gap-2 border-t border-slate-100 pt-3">
              {step > 1 && (
                <button
                  type="button"
                  className={btnClass("ghost", "md")}
                  disabled={busy}
                  onClick={() => setStep(step - 1)}
                >
                  上一步
                </button>
              )}
              <button
                type="submit"
                className={btnClass("primary", "md")}
                disabled={busy || !s.canWrite}
              >
                {busy
                  ? "正在保存…"
                  : step === 4
                    ? "开始课堂分析"
                    : step === 2 && !files.length
                      ? "暂不添加，下一步"
                      : "保存并继续"}
              </button>
            </div>
          </form>
        )}
      </div>
    </dialog>
  );
}
