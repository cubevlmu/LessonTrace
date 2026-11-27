import { useEffect, useState } from "react";
import { flushSync } from "react-dom";
import { useSearchParams } from "react-router-dom";
import {
  Printer,
  Edit3,
  Save,
  Sparkles,
  Download,
  CheckCircle2,
  Loader2,
  ChevronDown,
  SlidersHorizontal,
  FileText,
} from "lucide-react";
import { useSession } from "../state/session";
import { AnalysisScope, useAnalysisScope } from "../components/AnalysisScope";
import { EvidenceCitation, shortEvidenceLabel } from "../components/ReportContent";
import {
  createLessonReport,
  getLessonReport,
  listLessonReports,
  editLessonReport,
  reviseLessonReport,
  downloadLessonReport,
  type LessonReport,
  type ReportBlock,
} from "../api/lessonReports";
import {
  AlertBanner,
  badgeClass,
  btnClass,
  cardClass,
  cn,
  labelClass,
  selectClass,
  textareaClass,
} from "../components/ui/primitives";

const templates = {
  lesson: [
    "课堂基本信息与分析范围",
    "教学背景与资料依据",
    "课堂可观察表现",
    "教学任务与课堂组织分析",
    "教学改进与实施步骤",
    "后续跟进与核对",
    "覆盖限制与证据边界",
  ],
  students: [
    "群体观察范围",
    "人物区域与身份情况",
    "已观察行为与时序分布",
    "任务和作业背景",
    "群体共性与差异",
    "面向全体的教学支持",
    "分层跟进与核对",
    "未覆盖人群与时段",
  ],
  student: [
    "人物范围与身份来源",
    "个人外显动作时间线",
    "个人作业与教学任务材料",
    "行为与任务的对照分析",
    "个别反馈建议",
    "下一步支持与核对",
    "个人分析的适用边界",
  ],
};

const angleTitles = {
  lesson: "课堂整体复盘报告",
  students: "全体学生观察报告",
  student: "学生个人复盘报告",
};
const missingSource = "出处不可用（该版本缺少来源记录）";
const legacyPlaceholder = "当前所选范围暂无可支撑本节分析的独立资料；本节保留为空缺项，不编造课堂事实。";
const revisedSources = "原参考来源 · 正文已由教师修订";

function latestBlocks(report?: LessonReport | null) {
  const current = new Map<string, ReportBlock>();
  for (const block of report?.blocks ?? []) {
    const existing = current.get(block.block_id);
    if (!existing || block.version > existing.version) current.set(block.block_id, block);
  }
  return [...current.values()];
}

function versionLabel(block: ReportBlock) {
  if (block.origin === "teacher_edited") return `教师修订（第${block.version}版）`;
  if (block.origin === "adopted") return `第${block.version}版采纳`;
  return `助手撰写（第${block.version}版）`;
}

function ReportSources({report, refs, label, check, collapsed}: {
  report: LessonReport; refs: string[]; label: string; check?: boolean; collapsed?: boolean;
}) {
  if (!refs.length) return null;
  const sources = [...new Set(refs)].map((ref) => {
    const evidence = report.evidence.find((e) => e.evidence_id === ref);
    return {ref, evidence, text: report.source_labels[ref] ?? (evidence ? shortEvidenceLabel(evidence) : missingSource)};
  });
  const interactive = (
    <ul aria-label={label} className="space-y-1">
      {sources.map(({ref, evidence, text}) => (
        <li key={ref}>
          {evidence ? <EvidenceCitation e={evidence} runId={report.classroom.run_id} check={check} inlineLabel={text} /> :
            <span className="text-xs text-[#6e5a74]">{text}</span>}
        </li>
      ))}
    </ul>
  );
  return (
    <>
      {collapsed ? (
        <details className="no-print rounded-xl border border-[#ebdce9] bg-[#fcf9fd] px-3.5 py-2 text-xs">
          <summary className="flex cursor-pointer list-none items-center justify-between font-medium text-[#841f66]">
            <span>查看本节来源（{sources.length}）</span>
            <ChevronDown size={13} />
          </summary>
          <div className="mt-2.5">{interactive}</div>
        </details>
      ) : (
        <div className="no-print space-y-1 text-xs text-[#6e5a74]">
          <p>{label}</p>
          {interactive}
        </div>
      )}
      <div className="report-print-sources">
        <p>{label}</p>
        <ul aria-label={collapsed ? "本节打印来源" : label + "（打印）"}>
          {sources.map(({ref, text}) => <li key={ref}>{text}</li>)}
        </ul>
      </div>
    </>
  );
}

export function ReportPage() {
  const s = useSession();
  const [params] = useSearchParams();
  const scope = useAnalysisScope(
    params.get("profile") ? "student:" + params.get("profile") : "",
    false,
  );
  const [angle, setAngle] = useState<"lesson" | "students" | "student">(
    params.get("profile") ? "student" : "lesson",
  );
  const [report, setReport] = useState<LessonReport | null>(null);
  const [sections, setSections] = useState<LessonReport["sections"]>([]);
  const [blockEdits, setBlockEdits] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState(false);
  const [keepTeacherEdits, setKeepTeacherEdits] = useState(true);
  const [instruction, setInstruction] = useState("");
  const [focus, setFocus] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [panelTab, setPanelTab] = useState<"generate" | "revise">("generate");
  const blocks = latestBlocks(report);
  const changedBlocks = blocks.filter((block) =>
    blockEdits[block.block_id] !== undefined && blockEdits[block.block_id] !== block.text);
  const dirty = editing && (changedBlocks.length > 0 || sections.some(
    (section, index) => section.content !== report?.sections[index]?.content,
  ));

  function apply(doc: LessonReport) {
    setReport(doc);
    setSections(doc.sections);
    setBlockEdits({});
    setAngle(doc.angle);
    scope.setTarget(
      doc.options.roster_subject_id
        ? "student:" + doc.options.roster_subject_id
        : doc.options.track_id
          ? "track:" + doc.options.track_id
          : "",
    );
    setEditing(false);
  }

  const requestedRid = params.get("rid");
  const requestedProfile = params.get("profile");
  const requestedLesson = params.get("lesson");

  useEffect(() => {
    const run = s.data.job?.run_id;
    setReport(null);
    setEditing(false);
    setError("");
    if ((!run && !(requestedLesson && requestedRid)) || s.restoring) return;
    let active = true;
    setLoading(true);
    const loadInitial = async () => {
      if (requestedRid) {
        const d = await getLessonReport(requestedRid);
        if (!active) return;
        const docLesson = d.classroom.lesson_id ?? d.options.lesson_id;
        const inScope = requestedLesson ? (docLesson ? docLesson === requestedLesson :
          d.scope_key === "lesson:" + requestedLesson) : d.classroom.run_id === run;
        if (!inScope) {
          setError("所选报告草稿不属于当前课堂分析记录。");
          return;
        }
        apply(d);
        return;
      }
      if (!run) return;
      const rows = await listLessonReports(run);
      for (const row of rows) {
        if (requestedProfile && row.angle !== "student") continue;
        const d = await getLessonReport(row.report_id);
        if (
          requestedProfile &&
          d.options.roster_subject_id !== requestedProfile
        )
          continue;
        if (active) apply(d);
        break;
      }
    };
    void loadInitial()
      .catch((e) => {
        if (active) setError(String(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [s.data.job?.run_id, s.restoring, requestedRid, requestedProfile, requestedLesson]);

  async function saveEdits(report: LessonReport) {
    if (!changedBlocks.length) return editLessonReport(report, sections);
    const saved = await editLessonReport(report, sections, blocks.map((block) => ({
      ...block, text: blockEdits[block.block_id] ?? block.text,
    })));
    const savedBlocks = new Map(latestBlocks(saved).map((block) => [block.block_id, block]));
    if (saved.version <= report.version || changedBlocks.some((block) => {
      const updated = savedBlocks.get(block.block_id);
      return !updated || updated.version <= block.version || updated.origin !== "teacher_edited" ||
        updated.text !== blockEdits[block.block_id];
    })) throw Error("服务未确认块级修订，编辑内容仍保留。");
    return saved;
  }

  async function task(
    kind:
      | "generate"
      | "rebuild"
      | "save"
      | "revise"
      | "check"
      | "markdown"
      | "json"
      | "print",
  ) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (kind === "generate") {
        if (!s.data.job?.run_id) throw Error("请先选择已有分析记录的课堂");
        const selection = scope.payload();
        if (
          angle === "student" &&
          !selection.roster_subject_id &&
          !selection.track_id
        )
          throw Error("请选择要生成个人报告的学生或匿名人物");
        const doc = await createLessonReport({
          run_id: s.data.job.run_id,
          ...selection,
          subject_id: undefined,
          ...(angle !== "student"
            ? { roster_subject_id: null, track_id: null }
            : {}),
          angle,
          focus,
          use_llm: true,
        });
        apply(doc);
        setMessage("固定范式报告已独立保存，不会覆盖课堂问答。");
      } else {
        if (!report) throw Error("请先生成报告");
        if (kind === "rebuild") {
          const updated = await createLessonReport({
            ...report.options,
            run_id: report.classroom.run_id,
            use_llm: false,
          });
          apply(updated);
          setMessage("已按原报告范围整理当前证据，另存为一份新报告。");
        } else if (kind === "save") {
          apply(await saveEdits(report));
          setMessage("教师修改已保存为新版本，原始模型与旧版报告保留。");
        } else if (kind === "revise") {
          apply(await reviseLessonReport(report, instruction, keepTeacherEdits));
          setInstruction("");
          setMessage("报告已按要求修订，新版本已保存。");
        } else if (kind === "check") {
          const current = await getLessonReport(report.report_id);
          if (!dirty) apply(current);
          if (current.status !== "current")
            throw Error("资料或关联已变化，请按当前依据重新生成");
          setMessage("报告依据仍有效");
        } else {
          // Save against the version the teacher edited before fetching or
          // applying any newer document. A failed PUT leaves the draft intact.
          const expected = dirty ? await saveEdits(report) : report;
          if (dirty) apply(expected);
          if (expected.status !== "current")
            throw Error("资料或关联已变化，请按当前依据重新生成");
          if (kind === "print") {
            const current = await getLessonReport(expected.report_id);
            if (current.status !== "current") {
              apply(current);
              throw Error("资料或关联已变化，请按当前依据重新生成");
            }
            if (current.version !== expected.version)
              throw Error("报告版本已变化，请先检查依据并读取最新版本");
            // Print synchronously after React commits the saved paper view.
            flushSync(() => apply(current));
            window.print();
          } else {
            await downloadLessonReport(expected.report_id, kind, expected.version);
          }
        }
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-[1400px] space-y-4">
      {/* Compact Document Studio Toolbar */}
      <header
        className={cn(
          cardClass,
          "no-print flex flex-wrap items-center justify-between gap-3 px-4 py-3",
        )}
      >
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="text-base font-bold tracking-tight text-[#231426] sm:text-lg">
            复盘报告
          </h1>
          <span className={badgeClass("info")}>
            {report
              ? "第" +
                report.version +
                "版 · " +
                (report.origin === "teacher_edited"
                  ? "教师编辑"
                  : report.origin === "teacher_adopted"
                    ? "已采纳助手建议"
                    : report.options.use_llm === false
                      ? "已有证据整理"
                      : "AI辅助生成")
              : "未生成"}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {report && (
            <>
              <button
                type="button"
                className={btnClass("secondary", "sm")}
                disabled={busy || report.status !== "current"}
                onClick={() => {
                  setEditing(!editing);
                  if (editing) {
                    setSections(report.sections);
                    setBlockEdits({});
                  }
                }}
              >
                <Edit3 size={13} />
                {editing ? "取消编辑" : "直接编辑正文"}
              </button>
              {editing && (
                <button
                  type="button"
                  className={btnClass("primary", "sm")}
                  disabled={busy}
                  onClick={() => void task("save")}
                >
                  <Save size={13} />
                  保存修改
                </button>
              )}
              <button
                type="button"
                className={btnClass("secondary", "sm")}
                disabled={busy || report.status !== "current"}
                onClick={() => void task("markdown")}
              >
                <Download size={13} />
                {dirty ? "保存并导出" : "下载文字报告"}
              </button>
            </>
          )}
          <button
            type="button"
            className={btnClass("secondary", "sm")}
            disabled={busy || !report || report.status !== "current"}
            onClick={() => void task("print")}
          >
            <Printer size={13} />
            {dirty ? "保存并打印" : "打印 / 保存PDF"}
          </button>
        </div>
      </header>

      {error ? <AlertBanner tone="danger">{error}</AlertBanner> : null}
      {message ? <AlertBanner tone="status">{message}</AlertBanner> : null}
      {(busy || loading) && (
        <div
          role="status"
          className="no-print flex items-center gap-2 rounded-xl border border-[#e3bed8] bg-[#f9eff6] px-4 py-2.5 text-xs font-medium text-[#6e1a55]"
        >
          <Loader2 size={14} className="animate-spin text-[#992777]" />
          {loading ? "正在读取独立报告…" : "正在处理报告，请稍候…"}
        </div>
      )}

      {/* Main Document + Sleek Single Docked Inspector */}
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_330px]">
        {/* Editorial White-Paper Report Canvas */}
        <article
          aria-label="报告预览"
          data-report-version={report?.version}
          className="paper-sheet rounded-2xl border border-[#e8dce9] px-6 py-7 shadow-[0_4px_24px_-12px_rgba(74,21,59,0.12)] sm:px-11 sm:py-10"
        >
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#f0e5f2] pb-4 text-xs text-[#6e5a74]">
            <span className="font-bold tracking-wide text-[#992777]">
              课迹 LessonTrace
            </span>
            <span>
              {report?.product_scope?.classroom_name || "固定范式复盘"}
              {report?.product_scope?.subject_name
                ? " · " + report.product_scope.subject_name
                : ""}
            </span>
          </div>

          <h2 className="mt-6 text-2xl font-bold tracking-tight text-[#231426]">
            {report?.title || angleTitles[angle]}
          </h2>

          {report?.status === "stale" ? (
            <div className="mt-5 space-y-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
              <p role="status">{report.reason}</p>
              {s.canWrite && (
                <button
                  type="button"
                  className={btnClass("secondary", "sm")}
                  disabled={busy || loading}
                  onClick={() => void task("rebuild")}
                >
                  按原范围重新整理证据
                </button>
              )}
            </div>
          ) : report ? (
            <div className="mt-4 space-y-6">
              <div className="space-y-1 text-xs text-[#6e5a74]">
                <p>{report.scope_warning}</p>
                {report.generation?.status === "partial_success" && (
                  <p className="text-amber-700">
                    部分AI段落因引用不匹配未采纳，已保留有效内容。
                  </p>
                )}
              </div>

              <div className="divide-y divide-[#f2eaf4]">
                {(() => {
                  let visibleNumber = 0;
                  return sections.map((section, index) => {
                    const sectionBlocks = blocks.filter((block) => block.section_id === section.section_id);
                    // When a section has blocks, they supersede the old section projection
                    // and adoption snapshots. A cleared latest block must not revive old text.
                    const parts = sectionBlocks.length ? [] : report.adoptions.flatMap((adoption) =>
                      adoption.paragraphs.flatMap((part, partIdx) =>
                        part.section_id === section.section_id && part.text.trim()
                          ? [{adoption, part, partIdx}] : []));
                    const content = report.sections[index].content.trim();
                    const hasBody = !sectionBlocks.length && !!content &&
                      (content !== legacyPlaceholder || report.teacher_edited_sections?.includes(section.section_id));
                    const hasContent = hasBody || parts.length > 0 ||
                      sectionBlocks.some((block) => block.text.trim());
                    if (!hasContent && !editing) return null;

                    const body = (
                      <>
                        {sectionBlocks.length > 0 ? sectionBlocks.filter((block) => editing || block.text.trim()).map((block, blockIndex) => (
                          <div key={block.block_id} data-block-id={block.block_id}
                            className="space-y-2 rounded-xl border border-[#e3bed8] bg-[#faf4f9] p-3.5 text-sm">
                            <div className="flex flex-wrap gap-x-2 text-xs font-semibold text-[#841f66]">
                              <span>{versionLabel(block)}</span>
                              {block.actor && <span className="font-normal text-[#6e5a74]">{block.actor}</span>}
                            </div>
                            {editing ? (
                              <textarea className={cn(textareaClass, "no-print w-full")}
                                aria-label={`编辑${section.heading}第${blockIndex + 1}段`}
                                rows={4} disabled={busy} value={blockEdits[block.block_id] ?? block.text}
                                onChange={(event) => {
                                  const text = event.target.value;
                                  setBlockEdits((previous) => ({...previous, [block.block_id]: text}));
                                }} />
                            ) : (
                              <p className="whitespace-pre-wrap leading-7 text-[#231426]">{block.text}</p>
                            )}
                            {block.reference_status === "teacher_revised" && (
                              <p className="text-xs font-medium text-[#6e5a74]">{revisedSources}</p>
                            )}
                            <ReportSources report={report} refs={block.evidence_refs}
                              label={block.reference_status === "teacher_revised" ? "原参考来源" : "本段来源"} />
                          </div>
                        )) : editing ? (
                          <textarea
                            className={cn(textareaClass, "no-print w-full rounded-xl border-[#cfa1c3] bg-[#faf4f9] p-3 leading-relaxed")}
                            aria-label={"编辑" + section.heading}
                            rows={7}
                            disabled={busy}
                            value={section.content.trim() === legacyPlaceholder && !hasBody ? "" : section.content}
                            onChange={(event) => {
                              const text = event.target.value;
                              setSections((list) => list.map((item, i) => i === index ? {...item, content: text} : item));
                            }}
                          />
                        ) : hasBody && (
                          <p className="whitespace-pre-wrap text-sm leading-7 text-[#3d2542]">{section.content}</p>
                        )}
                        {parts.map(({adoption, part, partIdx}) => {
                          const checkVideoLabel = part.check_scope && report.adoption_check_labels[adoption.fusion_report_id];
                          return (
                            <div key={`${adoption.fusion_report_id}:${partIdx}`}
                              className="space-y-2 rounded-xl border border-[#e3bed8] bg-[#faf4f9] p-3.5 text-sm">
                              <div className="flex flex-wrap items-center justify-between gap-2 text-xs font-semibold text-[#841f66]">
                                <span>
                                  教师采纳的助手观察与建议
                                  {adoption.scope?.subject_label ? ` · ${adoption.scope.subject_label}` : ""}
                                </span>
                                <span className="font-normal text-[#6e5a74]">第{adoption.adopted_version}版采纳</span>
                              </div>
                              <p className="whitespace-pre-wrap leading-7 text-[#231426]">{part.text}</p>
                              <ReportSources report={report} refs={part.evidence_refs} label="采纳段依据" />
                              {checkVideoLabel && <p className="text-xs text-[#4a3450]">建议核查的录像：{checkVideoLabel}</p>}
                              <ReportSources report={report} refs={part.check_evidence_refs ?? []} label="建议核查的教案" check />
                            </div>
                          );
                        })}
                        {!sectionBlocks.length && hasBody && (
                          <ReportSources report={report} refs={section.evidence_refs} label="来源" collapsed />
                        )}
                      </>
                    );
                    if (!hasContent) return (
                      <details key={section.section_id} className="no-print space-y-2.5 py-4">
                        <summary className="cursor-pointer text-sm font-medium text-[#841f66]">添加内容 · {section.heading}</summary>
                        {body}
                      </details>
                    );
                    return (
                      <section key={section.section_id} className="space-y-2.5 py-5 first:pt-2">
                        <h3 className="flex items-center gap-2 text-base font-bold text-[#231426]">
                          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-[#f7eaf3] text-[11px] font-bold text-[#992777] tabular">
                            {++visibleNumber}
                          </span>
                          <span>{section.heading}</span>
                        </h3>
                        {body}
                      </section>
                    );
                  });
                })()}
              </div>

              <details className="no-print rounded-xl border border-[#e8dce9] px-3.5 py-2 text-xs text-[#6e5a74]">
                <summary className="cursor-pointer font-medium">
                  报告来源、版本与生成记录
                </summary>
                <pre className="mt-2 max-h-60 overflow-auto rounded-lg bg-[#faf4f9] p-3 font-mono text-[10px] text-[#4a3450]">
                  {JSON.stringify(report, null, 2)}
                </pre>
              </details>
            </div>
          ) : (
            <div className="mt-5 space-y-5">
              <p className="rounded-xl border border-dashed border-[#dfd0e2] bg-[#faf7fb] p-4 text-xs text-[#6e5a74]">
                在右侧选择报告角度与范围后点击“生成报告”。报告独立保存，不会与课堂助手对话混淆。
              </p>
              <div className="divide-y divide-[#f2eaf4]">
                {templates[angle].map((h, i) => (
                  <section key={h} className="py-3.5 first:pt-1">
                    <h3 className="flex items-center gap-2 text-sm font-semibold text-[#4a3450]">
                      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-[#f5eff7] text-[11px] font-semibold text-[#841f66] tabular">
                        {i + 1}
                      </span>
                      <span>{h}</span>
                    </h3>
                    <p className="mt-1 pl-7 text-xs text-[#9c89a2]">
                      生成后自动填入对应来源与分析。
                    </p>
                  </section>
                ))}
              </div>
            </div>
          )}
        </article>

        {/* Unified Right Configuration & Revision Dock */}
        <aside className="no-print xl:sticky xl:top-16">
          <section className={cn(cardClass, "overflow-hidden")}>
            {/* Clean 2-tab switcher inside single card */}
            <div className="grid grid-cols-2 border-b border-[#f0e5f2] bg-[#faf7fb] p-1">
              <button
                type="button"
                onClick={() => setPanelTab("generate")}
                className={cn(
                  "inline-flex items-center justify-center gap-1.5 rounded-lg py-1.5 text-xs font-medium transition",
                  panelTab === "generate"
                    ? "bg-white font-semibold text-[#992777] shadow-2xs"
                    : "text-[#6e5a74] hover:text-[#231426]",
                )}
              >
                <SlidersHorizontal size={13} />
                生成设置
              </button>
              <button
                type="button"
                disabled={!report}
                onClick={() => setPanelTab("revise")}
                className={cn(
                  "inline-flex items-center justify-center gap-1.5 rounded-lg py-1.5 text-xs font-medium transition disabled:opacity-40",
                  panelTab === "revise"
                    ? "bg-white font-semibold text-[#992777] shadow-2xs"
                    : "text-[#6e5a74] hover:text-[#231426]",
                )}
              >
                <FileText size={13} />
                AI 修订与导出
              </button>
            </div>

            {panelTab === "generate" ? (
              <form
                className="space-y-3.5 p-4"
                onSubmit={(e) => {
                  e.preventDefault();
                  void task("generate");
                }}
              >
                <label className={labelClass}>
                  报告角度
                  <select
                    className={selectClass}
                    aria-label="报告角度"
                    value={angle}
                    onChange={(e) => {
                      setAngle(e.target.value as typeof angle);
                      setReport(null);
                      setEditing(false);
                      if (e.target.value !== "student") scope.setTarget("");
                    }}
                  >
                    <option value="lesson">课堂大体 · 整体复盘</option>
                    <option value="students">全体学生 · 群体观察</option>
                    <option value="student">某位学生 · 个人复盘</option>
                  </select>
                </label>

                <AnalysisScope scope={scope} showPerson={angle === "student"} />

                {angle !== "student" && (
                  <p className="text-[11px] text-[#6e5a74]">
                    本角度汇集所选时间内已分析的人物记录，不使用单个人物筛选。
                  </p>
                )}

                <label className={labelClass}>
                  本次关注点
                  <textarea
                    className={textareaClass}
                    aria-label="报告关注点"
                    rows={3}
                    maxLength={1000}
                    value={focus}
                    onChange={(e) => setFocus(e.target.value)}
                    placeholder="如：任务执行、个别反馈、课堂组织；可留空按范式生成"
                  />
                </label>

                <button
                  type="submit"
                  className={btnClass("primary", "md", "w-full")}
                  disabled={
                    busy ||
                    loading ||
                    editing ||
                    s.restoring ||
                    !s.canWrite ||
                    !s.data.job?.run_id
                  }
                >
                  <Sparkles size={14} />
                  生成报告
                </button>
              </form>
            ) : (
              report && (
                <div className="space-y-3.5 p-4">
                  <label className={labelClass}>
                    让AI按要求修订
                    <textarea
                      className={textareaClass}
                      aria-label="报告修订要求"
                      maxLength={1000}
                      rows={4}
                      value={instruction}
                      onChange={(e) => setInstruction(e.target.value)}
                      placeholder="如：建议改成课前、课中、课后三步，保留原有观察依据"
                    />
                  </label>

                  <label className="flex items-center gap-2 text-xs text-[#4a3450]">
                    <input
                      type="checkbox"
                      checked={keepTeacherEdits}
                      onChange={(e) => setKeepTeacherEdits(e.target.checked)}
                      className="accent-[#992777]"
                    />
                    AI修订时保留我已编辑的章节
                  </label>

                  <button
                    type="button"
                    className={btnClass("primary", "md", "w-full")}
                    disabled={busy || editing || !instruction.trim()}
                    onClick={() => void task("revise")}
                  >
                    <Sparkles size={14} />
                    按要求修订
                  </button>

                  <p className="text-[11px] text-[#6e5a74]">
                    编辑和修订均保存新版本，不覆盖原始行为结果。
                  </p>

                  <div className="flex flex-wrap gap-2 border-t border-[#f0e5f2] pt-3">
                    <button
                      type="button"
                      className={btnClass("secondary", "sm", "flex-1")}
                      disabled={busy || report.status !== "current"}
                      onClick={() => void task("json")}
                    >
                      {dirty ? "保存并导出JSON" : "导出JSON"}
                    </button>
                    <button
                      type="button"
                      className={btnClass("secondary", "sm", "flex-1")}
                      disabled={busy}
                      onClick={() => void task("check")}
                    >
                      <CheckCircle2 size={13} />
                      检查依据
                    </button>
                  </div>
                </div>
              )
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
