import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  ArrowLeft,
  Check,
  Video,
  Upload,
  LogOut,
  PlayCircle,
  Loader2,
} from "lucide-react";
import { useSession } from "../state/session";
import * as classApi from "../api/classrooms";
import { uploadVideo } from "../api/videos";
import { videoTitle } from "../models/view";
import {
  AlertBanner,
  btnClass,
  cardClass,
  cn,
  fieldClass,
  labelClass,
  selectClass,
} from "../components/ui/primitives";

const steps = [
  { id: 1, title: "登录账户" },
  { id: 2, title: "选择录像" },
] as const;

export function LoginPage() {
  const s = useSession();
  const nav = useNavigate();

  // Track whether the user entered unauthenticated (so we show the 2-step stepper)
  // vs already logged in (switching videos -> hide login step cleanly).
  const [showStepper, setShowStepper] = useState(!s.account);

  // Step 1: Login state
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  // Step 2: Video state
  const [videoMode, setVideoMode] = useState<"select" | "upload">("select");
  const [classes, setClasses] = useState<classApi.Classroom[]>([]);
  const [uploadClassId, setUploadClassId] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);

  // Shared UI state
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const canWrite = s.canWrite;
  const step: 1 | 2 = s.account ? 2 : 1;

  // If user logs out while on this page, show the 2-step stepper again
  useEffect(() => {
    if (!s.account) setShowStepper(true);
  }, [s.account]);

  // Load optional classrooms list for associating newly uploaded videos
  useEffect(() => {
    if (!s.account || videoMode !== "upload") return;
    let active = true;
    void classApi
      .listClassrooms()
      .then((rows) => {
        if (active) setClasses(rows);
      })
      .catch(() => {
        if (active) setClasses([]);
      });
    return () => {
      active = false;
    };
  }, [s.account, videoMode]);

  async function handleLogin(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await s.login(username, password);
      setUsername("");
      setPassword("");
      nav("/", { replace: true });
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function enterReviewWithVideo(videoId: string) {
    setBusy(true);
    setError("");
    try {
      const refreshed = await s.refresh();
      const jobsList = refreshed?.jobs || s.data.jobs;
      const jobs = jobsList
        .filter((j) => j.video_id === videoId)
        .sort(
          (a, b) =>
            Number(["running", "queued", "pausing"].includes(b.status)) -
              Number(["running", "queued", "pausing"].includes(a.status)) ||
            b.processed_windows - a.processed_windows ||
            Date.parse(String(b.created_at)) - Date.parse(String(a.created_at)),
        );
      if (jobs[0]) await s.loadJob(jobs[0].job_id);
      else s.selectVideo(videoId);
      sessionStorage.setItem(
        "lessontrace.chosen." + (s.account?.actor || ""),
        videoId,
      );
      nav("/review", { replace: true });
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleUploadVideo(e: FormEvent) {
    e.preventDefault();
    if (!uploadFile) return;
    if (uploadFile.size > 512 * 1024 * 1024) {
      setError("课堂录像不超过 512 MiB");
      return;
    }
    setBusy(true);
    setError("");
    setUploadProgress(0);
    try {
      const uploaded = await uploadVideo(uploadFile, setUploadProgress);
      if (uploadClassId) {
        await classApi.linkVideo(uploadClassId, uploaded.video_id);
      }
      await enterReviewWithVideo(uploaded.video_id);
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  const activeVideos = s.data.videos.filter((v) => v.status === "active");

  return (
    <div className="flex min-h-screen flex-col bg-[var(--lt-bg)] text-[var(--lt-ink)]">
      {/* Minimal Top Bar */}
      <header className="flex items-center justify-between px-6 py-4">
        <span className="text-base font-bold tracking-tight text-[#992777]">
          课迹 LessonTrace
        </span>
        {s.account && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              className={btnClass("ghost", "sm")}
              onClick={() => nav("/review")}
            >
              <ArrowLeft size={13} />
              返回工作台
            </button>
            <button
              type="button"
              className={btnClass("ghost", "sm")}
              onClick={() => void s.logout().catch((e) => setError(String(e)))}
            >
              <LogOut size={13} />
              退出
            </button>
          </div>
        )}
      </header>

      <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center px-4 pb-16 sm:px-6">
        {/* Heading (step 2 only) */}
        {step !== 1 && (
          <div className="mb-6 text-center">
            <h1 className="text-2xl font-bold tracking-tight text-[#231426]">
              选择课堂录像
            </h1>
            <p className="mt-1.5 text-sm text-[#6e5a74]">
              打开已有录像进入复盘工作台，或上传新的课堂录像。
            </p>
          </div>
        )}

        {/* 2-Step Visual Stepper (shown during login flow; hidden when already logged in switching videos) */}
        {showStepper && (
          <nav aria-label="引导步骤" className="mx-auto mb-6 w-full max-w-sm">
            <ol className="grid grid-cols-2 gap-2">
              {steps.map((st, idx) => {
                const completed = st.id === 1 && !!s.account;
                const current = step === st.id;
                return (
                  <li key={st.id} className="relative flex flex-col items-center">
                    {idx > 0 && (
                      <div
                        aria-hidden="true"
                        className={cn(
                          "absolute top-4 right-1/2 left-[-50%] h-0.5 -z-0 transition-colors duration-300",
                          step >= st.id ? "bg-[#992777]" : "bg-[#e3d2e6]",
                        )}
                      />
                    )}
                    <div
                      className={cn(
                        "relative z-10 flex h-8 w-8 items-center justify-center rounded-full text-xs font-bold transition-all",
                        completed
                          ? "bg-[#992777] text-white shadow-xs"
                          : current
                            ? "border-2 border-[#992777] bg-white text-[#992777] ring-4 ring-[#f7eaf3]"
                            : "border border-[#dfd0e2] bg-white text-[#9c89a2]",
                      )}
                    >
                      {completed ? <Check size={14} strokeWidth={2.5} /> : st.id}
                    </div>
                    <span
                      className={cn(
                        "mt-1.5 text-xs font-medium",
                        current
                          ? "font-semibold text-[#992777]"
                          : completed
                            ? "text-[#4a3450]"
                            : "text-[#9c89a2]",
                      )}
                    >
                      {st.title}
                    </span>
                  </li>
                );
              })}
            </ol>
          </nav>
        )}

        {/* Step Content Card */}
        <section
          key={step}
          className={cn(cardClass, "lt-page-enter p-6 shadow-sm")}
        >
          {/* STEP 1: LOGIN */}
          {step === 1 && (
            <form className="mx-auto max-w-sm space-y-4" onSubmit={handleLogin}>
              <div>
                <h2 className="text-base font-semibold text-[#231426]">
                  登录工作空间
                </h2>
                <p className="mt-0.5 text-xs text-[#6e5a74]">
                  使用教师账户登录。密码仅发送到当前服务端，不保存到本地存储。
                </p>
              </div>

              <label className={labelClass}>
                用户名
                <input
                  className={fieldClass}
                  aria-label="用户名"
                  type="text"
                  autoComplete="username"
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </label>

              <label className={labelClass}>
                密码
                <input
                  className={fieldClass}
                  aria-label="密码"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>

              <button
                type="submit"
                className={btnClass("primary", "lg", "w-full")}
                disabled={busy || !username.trim() || !password}
              >
                {busy ? "正在登录……" : "登录并继续"}
                <ArrowRight size={15} />
              </button>

              {(error || s.error) && (
                <AlertBanner tone="danger">{error || s.error}</AlertBanner>
              )}
            </form>
          )}

          {/* STEP 2: SELECT OR UPLOAD VIDEO */}
          {step === 2 && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-base font-semibold text-[#231426]">
                    {videoMode === "select" ? "已有录像" : "上传新录像"}
                  </h2>
                  <p className="mt-0.5 text-xs text-[#6e5a74]">
                    {videoMode === "select"
                      ? `共 ${activeVideos.length} 段可用录像，点击立即进入复盘工作台。`
                      : "上传单段课堂录像，可选关联至所属班级。"}
                  </p>
                </div>

                <div
                  className="inline-flex rounded-xl bg-[#f4ebf6] p-1"
                  role="tablist"
                  aria-label="录像操作"
                >
                  <button
                    type="button"
                    role="tab"
                    aria-selected={videoMode === "select"}
                    onClick={() => {
                      setVideoMode("select");
                      setError("");
                    }}
                    className={cn(
                      "rounded-lg px-3 py-1 text-xs font-medium transition",
                      videoMode === "select"
                        ? "bg-white font-semibold text-[#992777] shadow-2xs"
                        : "text-[#5f4965] hover:text-[#231426]",
                    )}
                  >
                    已有录像
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={videoMode === "upload"}
                    disabled={!canWrite}
                    onClick={() => {
                      setVideoMode("upload");
                      setError("");
                    }}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-lg px-3 py-1 text-xs font-medium transition disabled:opacity-45",
                      videoMode === "upload"
                        ? "bg-white font-semibold text-[#992777] shadow-2xs"
                        : "text-[#5f4965] hover:text-[#231426]",
                    )}
                  >
                    <Upload size={12} />
                    上传录像
                  </button>
                </div>
              </div>

              {error ? <AlertBanner tone="danger">{error}</AlertBanner> : null}

              {videoMode === "select" ? (
                <>
                  {s.loading && !activeVideos.length ? (
                    <div
                      role="status"
                      className="flex items-center justify-center gap-2 py-8 text-xs text-[#6e5a74]"
                    >
                      <Loader2
                        size={15}
                        className="animate-spin text-[#992777]"
                      />
                      正在读取课堂录像…
                    </div>
                  ) : activeVideos.length > 0 ? (
                    <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
                      {activeVideos.map((v) => {
                        const count = s.data.jobs.filter(
                          (j) => j.video_id === v.video_id,
                        ).length;
                        const isCurrent = v.video_id === s.data.videoId;
                        return (
                          <button
                            type="button"
                            key={v.video_id}
                            disabled={busy}
                            onClick={() =>
                              void enterReviewWithVideo(v.video_id)
                            }
                            className={cn(
                              "group flex w-full items-center justify-between gap-3 rounded-xl border p-3.5 text-left transition",
                              isCurrent
                                ? "border-[#cfa1c3] bg-[#faf0f7]"
                                : "border-[#e8dce9] bg-[#fcf9fd] hover:border-[#cfa1c3] hover:bg-[#f7eaf3]/60",
                            )}
                          >
                            <div className="min-w-0">
                              <strong className="block truncate text-sm font-semibold text-[#231426] group-hover:text-[#992777]">
                                {videoTitle(v)}
                              </strong>
                              <span className="mt-1 flex items-center gap-2 text-xs text-[#6e5a74]">
                                <Video size={12} />
                                <span>{v.metadata.duration.toFixed(1)} 秒</span>
                                <span>·</span>
                                <span>{count} 次分析记录</span>
                                {isCurrent && (
                                  <>
                                    <span>·</span>
                                    <span className="font-medium text-[#992777]">
                                      当前已打开
                                    </span>
                                  </>
                                )}
                              </span>
                            </div>
                            <span className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-[#6e5a74] group-hover:text-[#992777]">
                              <PlayCircle size={15} />
                              进入复盘
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="rounded-xl border border-dashed border-[#dfd0e2] py-10 text-center text-xs text-[#6e5a74]">
                      还没有可用的课堂录像，请点击上方“上传录像”。
                    </div>
                  )}
                </>
              ) : (
                <form className="space-y-4" onSubmit={handleUploadVideo}>
                  <label className={labelClass}>
                    选择课堂录像文件
                    <input
                      className={fieldClass}
                      aria-label="课堂录像文件"
                      type="file"
                      accept=".mp4,.avi,.mov,.mkv"
                      required
                      onChange={(e) =>
                        setUploadFile(e.target.files?.[0] || null)
                      }
                    />
                  </label>

                  <label className={labelClass}>
                    所属班级（可选）
                    <select
                      className={selectClass}
                      aria-label="录像所属班级"
                      value={uploadClassId}
                      onChange={(e) => setUploadClassId(e.target.value)}
                    >
                      <option value="">暂不关联班级</option>
                      {classes.map((c) => (
                        <option key={c.classroom_id} value={c.classroom_id}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </label>

                  <p className="text-xs text-[#6e5a74]">
                    支持 MP4 / AVI / MOV / MKV，单段最大 512 MiB。不关联班级也可以直接复盘匿名人物轨迹。
                  </p>

                  {uploadFile && (
                    <p className="rounded-lg bg-[#faf4f9] px-3 py-2 text-xs font-medium text-[#4a3450]">
                      {uploadFile.name} ·{" "}
                      {(uploadFile.size / 1024 / 1024).toFixed(1)} MiB
                    </p>
                  )}

                  {busy && (
                    <progress
                      aria-label="上传进度"
                      max={100}
                      value={uploadProgress}
                      className="h-2 w-full overflow-hidden rounded-full"
                    />
                  )}

                  <div className="flex items-center justify-end gap-2 border-t border-[#f0e5f2] pt-3">
                    <button
                      type="button"
                      className={btnClass("ghost", "sm")}
                      disabled={busy}
                      onClick={() => setVideoMode("select")}
                    >
                      取消
                    </button>
                    <button
                      type="submit"
                      className={btnClass("primary", "md")}
                      disabled={busy || !uploadFile || !canWrite}
                    >
                      {busy ? "正在上传…" : "上传并进入复盘"}
                      <ArrowRight size={14} />
                    </button>
                  </div>
                </form>
              )}
            </div>
          )}
        </section>

        {!__PRODUCT__ && !s.account && (
          <p className="mt-5 text-center text-xs text-[#6e5a74]">
            <Link
              to="/demo"
              className="font-medium text-[#992777] underline hover:text-[#7f1e62]"
            >
              查看独立演示（模拟数据）
            </Link>
          </p>
        )}
      </main>
    </div>
  );
}
