import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import {
  FolderOpen,
  Upload,
  Search,
  FileText,
  X,
  Trash2,
} from "lucide-react";
import { request } from "../api/client";
import { useSession } from "../state/session";
import {
  materialNames,
  materialStatus,
  listMaterials,
  getMaterial,
  deleteMaterial,
  detailSchema,
  type Material,
} from "../api/materials";
import { getPlaybackIdentities } from "../api/workbench";
import { getClassroom, type Student } from "../api/classrooms";
import {
  AlertBanner,
  badgeClass,
  btnClass,
  cardClass,
  cn,
  EmptyState,
  fieldClass,
  labelClass,
  selectClass,
  textareaClass,
} from "../components/ui/primitives";

export function MaterialsPage() {
  const s = useSession();
  const uploadDialog = useRef<HTMLDialogElement>(null);
  const viewDialog = useRef<HTMLDialogElement>(null);

  const [items, setItems] = useState<Material[]>([]);
  const [selected, setSelected] = useState<z.infer<typeof detailSchema> | null>(
    null,
  );
  const [category, setCategory] = useState("all");
  const [search, setSearch] = useState("");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState("teacher_plan");
  const [example, setExample] = useState(false);
  const [inputMode, setInputMode] = useState<"file"| "text">("file");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [linked, setLinked] = useState(true);
  const [cid, setCid] = useState("");
  const [students, setStudents] = useState<Student[]>([]);
  const [subject, setSubject] = useState("");
  const [transcript, setTranscript] = useState("");
  const [textAnchor, setTextAnchor] = useState("");
  const [videoAnchor, setVideoAnchor] = useState("");
  const [basis, setBasis] = useState("");
  const [confirmed, setConfirmed] = useState(false);

  async function load() {
    setItems(await listMaterials());
  }

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

  useEffect(() => {
    void load().catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    setCid("");
    setStudents([]);
    if (s.data.job?.run_id) {
      void getPlaybackIdentities(s.data.job.run_id)
        .then(async (r) => {
          if (r.classroom) {
            setCid(r.classroom.classroom_id);
            setStudents((await getClassroom(r.classroom.classroom_id)).students);
          }
        })
        .catch((e) => setError(String(e)));
    }
  }, [s.data.job?.run_id]);

  async function open(d: Material) {
    await task(async () => {
      setSelected(await getMaterial(d.document_id));
      setTranscript("");
      viewDialog.current?.showModal();
    });
  }

  async function save() {
    await task(async () => {
      const scope = {
        run_id: linked ? s.data.job?.run_id || null : null,
        classroom_id: linked ? cid || null : null,
        subject_id: subject || null,
      };
      let result;
      if (inputMode === "file"&& file) {
        if (file.size > 10 * 1024 * 1024)
          throw Error("单份资料不能超过10 MiB");
        const form = new FormData();
        form.append("title", title);
        form.append("source_type", source);
        form.append("file", file);
        form.append("is_example", String(example));
        form.append("scope", JSON.stringify(scope));
        result = await request(
          "upload",
          z.object({ document_id: z.string(), status: z.string() }),
          form,
        );
      } else {
        result = await request(
          "documents",
          z.object({ document_id: z.string(), status: z.string() }),
          { title, text, source_type: source, is_example: example, scope },
        );
      }
      await load();
      uploadDialog.current?.close();
      setMessage(
        ["requires_ocr", "requires_transcription"].includes(result.status)
          ? "文件已保存，补录文字后可用于联合分析。"
          : "资料已保存，可在问答或报告中选择引用。",
      );
      setFile(null);
      setText("");
      setTitle("");
    });
  }

  const shown = items.filter(
    (d) =>
      (category === "all"|| d.source_type === category) &&
      (!search || d.title.toLowerCase().includes(search.toLowerCase())),
  );

  return (
    <div className="mx-auto max-w-[1440px] space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-[#231426] sm:text-2xl">
            教学资料库
          </h1>
          <p className="mt-0.5 text-xs font-medium text-[#6e5a74]">
            {shown.length === items.length
              ? `共 ${items.length} 份文件`
              : `当前筛选 ${shown.length} / ${items.length} 份文件`}
          </p>
        </div>
        {s.canWrite && (
          <button
            type="button"
            className={btnClass("primary", "md")}
            onClick={() => {
              setError("");
              uploadDialog.current?.showModal();
            }}
          >
            <Upload size={15} />
            上传资料
          </button>
        )}
      </header>

      {error ? <AlertBanner tone="danger">{error}</AlertBanner> : null}
      {message ? <AlertBanner tone="status">{message}</AlertBanner> : null}

      {/* Single-row Filter Bar: Search expands maximally, Category fits content; wraps gracefully on tiny screens */}
      <div className="flex flex-wrap items-center gap-2.5 rounded-2xl border border-[#e8dce9] bg-white p-2.5 shadow-2xs">
        <div className="relative min-w-[180px] flex-1">
          <Search
            size={14}
            className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-[#9c89a2]"
          />
          <input
            aria-label="搜索资料"
            className={`${fieldClass} pl-8`}
            placeholder="搜索资料名称"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className={`${selectClass} w-auto max-w-full shrink-0`}
          aria-label="筛选资料分类"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
        >
          <option value="all">全部分类（{items.length}）</option>
          {Object.entries(materialNames).map(([key, name]) => (
            <option key={key} value={key}>
              {name}（{items.filter((d) => d.source_type === key).length}）
            </option>
          ))}
        </select>
      </div>

      {/* Cards Grid */}
      <div className="grid gap-3.5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {shown.map((d) => (
          <button
            type="button"
            key={d.document_id}
            onClick={() => void open(d)}
            className={cn(
              cardClass,
              "group flex flex-col items-start gap-2.5 p-4 text-left transition hover:border-[#cfa1c3] hover:shadow-sm:border-[#6e3461]",
            )}
          >
            <div className="flex w-full items-center justify-between">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#f7eaf3] text-[#992777]">
                <FileText size={20} />
              </div>
              <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[10px] font-bold tracking-wider text-slate-600">
                {(d.file_type || "文本").replace(".", "").toUpperCase()}
              </span>
            </div>
            <strong className="line-clamp-2 text-sm font-semibold text-slate-900 group-hover:text-[#841f66]">
              {d.title}
            </strong>
            <span className={badgeClass("info")}>
              {materialNames[d.source_type] || d.source_type}
            </span>
            <p className="text-xs text-slate-500">
              {materialStatus[d.status] || d.status}
            </p>
            <small className="mt-auto pt-1 text-[11px] text-slate-400">
              {d.scope?.subject_id
                ? "指定人物资料"
                : d.scope?.run_id
                  ? "已关联课堂录像"
                  : "通用资料"}
              {d.is_example ? " · 项目示例": ""}
            </small>
          </button>
        ))}
      </div>

      {!shown.length && (
        <EmptyState
          icon={<FolderOpen size={22} />}
          title="这里还没有符合条件的资料"
          description="上传教案、学生作业或教师说明后，即可从问答与报告中选用。"
        />
      )}

      {/* Upload Dialog */}
      <dialog
        ref={uploadDialog}
        className="lt-dialog"
        aria-label="上传教学资料"
        onClick={(e) => {
          if (e.target === e.currentTarget) uploadDialog.current?.close();
        }}
      >
        <header className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <h2 className="text-base font-semibold text-slate-900">
            上传教学资料
          </h2>
          <button
            type="button"
            className={btnClass("ghost", "sm")}
            onClick={() => uploadDialog.current?.close()}
          >
            <X size={14} />
            关闭
          </button>
        </header>

        <div className="border-b border-slate-100 px-5 py-2.5">
          <div
            className="inline-flex rounded-lg bg-slate-100 p-1"
            role="tablist"
            aria-label="资料输入方式"
          >
            <button
              type="button"
              role="tab"
              aria-selected={inputMode === "file"}
              onClick={() => setInputMode("file")}
              className={cn(
                "rounded-md px-3 py-1 text-xs font-medium transition",
                inputMode === "file"
                  ? "bg-white text-slate-900 shadow-2xs"
                  : "text-slate-600",
              )}
            >
              上传文件
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={inputMode === "text"}
              onClick={() => setInputMode("text")}
              className={cn(
                "rounded-md px-3 py-1 text-xs font-medium transition",
                inputMode === "text"
                  ? "bg-white text-slate-900 shadow-2xs"
                  : "text-slate-600",
              )}
            >
              输入文字
            </button>
          </div>
        </div>

        <form
          className="flex-1 space-y-3.5 overflow-y-auto px-5 py-4"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          {inputMode === "file"&& (
            <label className={labelClass}>
              选择文件
              <input
                className={fieldClass}
                aria-label="教学资料文件"
                type="file"
                accept=".txt,.md,.pdf,.pptx,.docx,.png,.jpg,.jpeg,.webp"
                required
                onChange={(e) => {
                  const f = e.target.files?.[0] || null;
                  setFile(f);
                  if (f && !title.trim())
                    setTitle(f.name.replace(/\.[^.]+$/, ""));
                }}
              />
              <small className="font-normal text-slate-500">
                支持文本、PDF、Word、PPT及图片，单份不超过10 MiB。图片和扫描页需补录文字。
              </small>
            </label>
          )}

          <label className={labelClass}>
            资料标题
            <input
              className={fieldClass}
              aria-label="资料标题"
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
            />
          </label>

          <label className={labelClass}>
            文件分类
            <select
              className={selectClass}
              aria-label="资料类型"
              value={source}
              onChange={(e) => setSource(e.target.value)}
            >
              {Object.entries(materialNames).map(([key, name]) => (
                <option key={key} value={key}>
                  {name}
                </option>
              ))}
            </select>
          </label>

          {inputMode === "text"&& (
            <label className={labelClass}>
              资料内容
              <textarea
                className={textareaClass}
                aria-label="教学资料文本"
                rows={7}
                required
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </label>
          )}

          {!!s.data.job?.run_id && (
            <label className="flex items-center gap-2 text-xs text-slate-700">
              <input
                type="checkbox"
                checked={linked}
                onChange={(e) => setLinked(e.target.checked)}
              />
              关联当前课堂录像，供后续问答检索
            </label>
          )}

          {students.length > 0 && (
            <label className={labelClass}>
              资料所属人物
              <select
                className={selectClass}
                aria-label="资料所属人物"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
              >
                <option value="">全班或通用资料</option>
                {students.map((p) => (
                  <option key={p.subject_id} value={p.subject_id}>
                    {p.display_name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <label className="flex items-center gap-2 text-xs text-slate-700">
            <input
              type="checkbox"
              checked={example}
              onChange={(e) => setExample(e.target.checked)}
            />
            项目示例资料，并非原课堂的真实材料
          </label>

          {error ? <AlertBanner tone="danger">{error}</AlertBanner> : null}

          <button
            type="submit"
            className={btnClass("primary", "md", "w-full")}
            disabled={busy || !(inputMode === "file"? file : text.trim())}
          >
            {busy ? "正在保存…": "保存资料"}
          </button>
        </form>
      </dialog>

      {/* View Detail Dialog */}
      <dialog
        ref={viewDialog}
        className="lt-dialog"
        aria-label="资料内容"
        onClick={(e) => {
          if (e.target === e.currentTarget) viewDialog.current?.close();
        }}
      >
        <header className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <h2 className="truncate text-base font-semibold text-slate-900">
            {selected?.title || "资料内容"}
          </h2>
          <button
            type="button"
            className={btnClass("ghost", "sm")}
            onClick={() => viewDialog.current?.close()}
          >
            <X size={14} />
            关闭
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4 text-sm">
          {selected && (
            <>
              <p className="text-xs text-slate-500">
                {materialNames[selected.source_type]} ·{" "}
                {materialStatus[selected.status] || selected.status}
              </p>

              {[".png", ".jpg", ".jpeg", ".webp"].includes(
                selected.file_type || "",
              ) && (
                <img
                  src={
                    "/api/context/documents/"+
                    selected.document_id +
                    "/preview"
                  }
                  alt="已上传的原始资料图片"
                  className="max-h-72 w-full rounded-xl border border-slate-200 object-contain"
                />
              )}

              <div className="max-h-64 space-y-2 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50/70 p-3.5 text-xs leading-relaxed">
                {selected.passages.map((p) => (
                  <p key={p.passage_id} className="whitespace-pre-wrap">
                    {p.page_or_slide !== null && (
                      <small className="mr-1.5 rounded bg-slate-200 px-1.5 py-0.5 font-medium text-slate-600">
                        第{p.page_or_slide}页/幻灯片
                      </small>
                    )}
                    {p.content}
                  </p>
                ))}
              </div>

              {s.canWrite && (
                <div className="space-y-3 border-t border-slate-100 pt-3">
                  <label className={labelClass}>
                    修改文件分类
                    <select
                      className={selectClass}
                      aria-label="修改文件分类"
                      value={selected.source_type}
                      onChange={(e) =>
                        void task(async () => {
                          const next = await request(
                            "documents/"+ selected.document_id + "/metadata",
                            detailSchema,
                            {
                              source_type: e.target.value,
                              title: selected.title,
                              scope: selected.scope || {},
                            },
                            "PUT",
                          );
                          setSelected(next);
                          await load();
                          setMessage("分类已更新，相关旧报告将重新检查依据。");
                        })
                      }
                    >
                      {Object.entries(materialNames).map(([key, name]) => (
                        <option value={key} key={key}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </label>

                  {[
                    "requires_transcription",
                    "requires_ocr",
                    "partial_requires_ocr",
                  ].includes(selected.status) && (
                    <form
                      className="space-y-2.5"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void task(async () => {
                          setSelected(
                            await request(
                              "documents/"+
                                selected.document_id +
                                "/transcript",
                              detailSchema,
                              { text: transcript },
                            ),
                          );
                          await load();
                          setMessage("文字已按教师补录来源保存，可用于分析。");
                        });
                      }}
                    >
                      <label className={labelClass}>
                        补录可辨认的原文
                        <textarea
                          className={textareaClass}
                          aria-label="资料补录文字"
                          required
                          value={transcript}
                          onChange={(e) => setTranscript(e.target.value)}
                        />
                      </label>
                      <button
                        type="submit"
                        className={btnClass("secondary", "sm")}
                        disabled={busy || !transcript.trim()}
                      >
                        保存补录文字
                      </button>
                    </form>
                  )}

                  <details className="rounded-lg border border-slate-200/80 p-3 text-xs">
                    <summary className="cursor-pointer font-medium text-slate-600">
                      与视频建立精确时间对应（可选）
                    </summary>
                    {s.data.scope && (
                      <form
                        className="mt-3 space-y-2.5"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void task(async () => {
                            await request(
                              "alignments",
                              z.object({ link_id: z.string() }).passthrough(),
                              {
                                document_id: selected.document_id,
                                lesson_id: "recording:"+ s.data.videoId,
                                clip_id: s.data.scope!.clip_id,
                                time_reference: "lesson_elapsed",
                                text_anchor: Number(textAnchor),
                                video_global_anchor:
                                  Number(videoAnchor) +
                                  (s.data.scope?.start || 0),
                                basis,
                                confirmation: confirmed,
                              },
                            );
                            setMessage("时间映射已保存。");
                          });
                        }}
                      >
                        <div className="grid grid-cols-2 gap-2">
                          <label className={labelClass}>
                            课程开始后秒数
                            <input
                              className={fieldClass}
                              type="number"
                              step="any"
                              min="0"
                              required
                              value={textAnchor}
                              onChange={(e) => setTextAnchor(e.target.value)}
                            />
                          </label>
                          <label className={labelClass}>
                            视频内秒数
                            <input
                              className={fieldClass}
                              type="number"
                              step="any"
                              min="0"
                              required
                              value={videoAnchor}
                              onChange={(e) => setVideoAnchor(e.target.value)}
                            />
                          </label>
                        </div>
                        <label className={labelClass}>
                          映射依据
                          <textarea
                            className={textareaClass}
                            required
                            minLength={5}
                            value={basis}
                            onChange={(e) => setBasis(e.target.value)}
                          />
                        </label>
                        <label className="flex items-center gap-2">
                          <input
                            type="checkbox"
                            checked={confirmed}
                            onChange={(e) => setConfirmed(e.target.checked)}
                          />
                          已核对对应关系
                        </label>
                        <button
                          type="submit"
                          className={btnClass("secondary", "sm")}
                          disabled={busy || !confirmed}
                        >
                          保存时间对应
                        </button>
                      </form>
                    )}
                  </details>

                  <button
                    type="button"
                    className={btnClass("danger", "sm")}
                    disabled={busy}
                    onClick={() => {
                      if (window.confirm("删除这份资料并使依赖报告失效？"))
                        void task(async () => {
                          await deleteMaterial(selected.document_id);
                          viewDialog.current?.close();
                          setSelected(null);
                          await load();
                          setMessage("资料已删除，历史审计保留。");
                        });
                    }}
                  >
                    <Trash2 size={13} />
                    删除资料
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </dialog>
    </div>
  );
}
