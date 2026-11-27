import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Video, FileText, ExternalLink } from "lucide-react";
import type { Report, Evidence } from "../models/backend";
import { fmt, toLocal, observationText } from "../models/view";
import { useSession } from "../state/session";
import { getScope } from "../api/jobs";
import { getPassage } from "../api/context";
import { StatusChip } from "./EvidencePanel";
import { materialNames } from "../api/materials";
import { AlertBanner, badgeClass, btnClass, cn } from "./ui/primitives";
import { formatCoverage } from "./AnalysisScope";
import type { PreviewHandler } from "./EvidencePreviewDrawer";

const sourceNames: Record<string, string> = {
  teacher_plan: "教案",
  teacher_report: "教师记录",
  teaching_activity: "教学活动",
  person_track: "人物轨迹",
  vlm_observation: "AI画面记录",
  model_discovered_event: "AI类别建议",
  human_confirmed_event: "教师复核",
  knowledge_rule: "分析规则",
  dataset_annotation: "原始标注",
};

const videoSources = new Set([
  "dataset_annotation", "vlm_observation", "human_visual_review", "person_track",
  "identity_binding", "event_attribution", "model_discovered_event", "human_confirmed_event",
]);
type Preview = {reportId: string; open: PreviewHandler; issues: ReadonlyMap<HTMLButtonElement, string>};

export function EvidenceCitation({ e, runId, preview, compact = false, check = false, inlineLabel }: {
  e: Evidence; runId?: string | null; preview?: Preview; compact?: boolean; check?: boolean;
  inlineLabel?: string;
}) {
  const s = useSession();
  const nav = useNavigate();
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);

  const documentSource = [
    "teacher_plan",
    "teacher_report",
    "teaching_activity",
  ].includes(e.evidence_type);
  const videoSource = videoSources.has(e.evidence_type);
  const track = e.subject_ref?.startsWith("track:")
    ? Number(e.subject_ref.split(":").pop())
    : (preview ? undefined : e.provenance.track_id);
  const trackId =
    typeof track === "number"&& Number.isInteger(track) && track > 0
      ? track
      : 0;
  const sourceName =
    (typeof e.provenance.source_kind === "string"&&
      materialNames[e.provenance.source_kind]) ||
    sourceNames[e.evidence_type] ||
    "来源资料";

  async function open() {
    setBusy(true);
    setError("");
    try {
      if (documentSource) {
        const id =
          typeof e.provenance.passage_ref === "string"
            ? e.provenance.passage_ref.replace(/^text:/, "")
            : e.source_id;
        const p = await getPassage(id);
        setText(p.content);
      } else if (videoSource && e.time_scope) {
        const rid =
          typeof e.provenance.run_id === "string"? e.provenance.run_id : runId;
        if (!rid) throw Error("当前来源没有可定位的录像。");
        const scope = await getScope(rid);
        const t = toLocal(
          e.time_scope[0],
          scope.start,
          scope.end - scope.start,
        );
        const matching = s.data.jobs.find((j) => j.run_id === rid);
        if (!matching)
          throw Error(
            "该历史来源尚未载入当前分析任务，不能跳转到另一段录像冒充证据。",
          );
        if (s.data.job?.run_id !== rid) await s.loadJob(matching.job_id);
        nav("/review?t="+ t + (trackId ? "&track="+ trackId : ""));
      } else setText(e.content);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  const playbackIssue = trigger.current ? preview?.issues.get(trigger.current) : undefined;
  if (inlineLabel !== undefined) return (
    <span className="ml-1 inline" data-testid="inline-source">
      <button
        ref={trigger}
        type="button"
        disabled={busy}
        onClick={(event) => {
          if (preview && videoSource) preview.open(preview.reportId, {evidence_id: e.evidence_id}, event.currentTarget);
          else void open();
        }}
        title={inlineLabel}
        className="inline-block max-w-full truncate rounded-full bg-[#f7eaf3] px-2 py-0.5 align-middle text-xs font-medium text-[#841f66] hover:bg-[#edd4e6] disabled:opacity-50"
      >
        {inlineLabel}
      </button>
      {text && <span className="mt-1 block whitespace-pre-wrap border-l-2 border-[#cfa1c3] pl-2 text-xs text-[#4a3450]">{text}</span>}
      {(error || playbackIssue) && <span role="alert" className="block text-xs text-rose-700">{error || playbackIssue}</span>}
    </span>
  );
  return (
    <div className={cn("space-y-1.5 text-xs", !compact && "rounded-lg border border-slate-200/80 bg-slate-50/70 p-2.5")}>
      <button
        ref={trigger}
        type="button"
        disabled={busy}
        onClick={(event) => {
          if (preview && videoSource) preview.open(preview.reportId, {evidence_id: e.evidence_id}, event.currentTarget);
          else void open();
        }}
        className={cn(
          "inline-flex flex-wrap items-center gap-1.5 rounded-md px-2.5 py-1 text-left text-xs font-medium transition disabled:opacity-50",
          documentSource
            ? "bg-[#f1e8f5] text-[#6c347c] hover:bg-[#e5d4ed]"
            : "bg-[#f7eaf3] text-[#992777] hover:bg-[#edd4e6]",
        )}
      >
        {documentSource ? (
          <FileText size={13} className="shrink-0"/>
        ) : videoSource ? (
          <Video size={13} className="shrink-0"/>
        ) : (
          <ExternalLink size={13} className="shrink-0"/>
        )}
        <span>
          {busy
            ? "正在定位……"
            : documentSource
              ? (check ? "查教案原文" : "查看资料原文")
              : videoSource
                ? "回放视频证据"
                : "查看来源"}{" "}
          · {sourceName}
          {documentSource &&
            typeof e.provenance.title === "string"&&
            ` · ${e.provenance.title}`}
          {!documentSource &&
            e.time_scope &&
            ` · 来源时间 ${fmt(e.time_scope[0])}${
              e.time_scope[1] !== e.time_scope[0]
                ? "–"+ fmt(e.time_scope[1])
                : ""
            }`}
          {!documentSource && e.time_scope && trackId > 0 && ` · 轨迹 ${trackId}`}
        </span>
      </button>

      {text && (
        <blockquote className="rounded-md border-l-2 border-[#cfa1c3] bg-white px-3 py-2 text-xs leading-relaxed text-slate-700">
          {text}
        </blockquote>
      )}

      {error ? <AlertBanner tone="danger">{error}</AlertBanner> : null}
      {playbackIssue && <p role="alert" className="text-rose-700">{playbackIssue}</p>}

      {!compact && <details className="text-[11px] text-slate-500">
        <summary className="cursor-pointer hover:text-[#841f66]">
          来源及范围
        </summary>
        <pre className="mt-1.5 max-h-48 overflow-auto rounded bg-[#faf4f9] p-2 font-mono text-[10px] text-[#4a3450]">
          {JSON.stringify(e, null, 2)}
        </pre>
      </details>}
    </div>
  );
}

function SelectedRange({preview}: {preview: Preview}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const issue = trigger.current ? preview.issues.get(trigger.current) : undefined;
  return (
    <div className="space-y-1">
      <button type="button" ref={trigger} className="inline-flex items-center gap-1.5 rounded-md bg-[#f7eaf3] px-2.5 py-1 text-xs font-medium text-[#992777]"
        onClick={(event) => preview.open(preview.reportId, {target: "selected_range"}, event.currentTarget)}>
        <Video size={13} />回看所选片段
      </button>
      {issue && <p role="alert" className="text-xs text-rose-700">{issue}</p>}
    </div>
  );
}

export function shortEvidenceLabel(e: Evidence) {
  const stringField = (key: string) => {
    const value = e.provenance[key];
    return typeof value === "string" ? value.trim() : "";
  };
  const label = stringField("short_label");
  if (label) return label;
  const sourceName = stringField("title") || materialNames[stringField("source_kind")] ||
    sourceNames[e.evidence_type] || "来源资料";
  const locator = stringField("question_label") || stringField("item_label") ||
    (e.time_scope && videoSources.has(e.evidence_type) ? fmt(e.time_scope[0]) : "");
  return [sourceName, locator].filter(Boolean).join(" · ");
}

const bodyRoles = new Set(["fact", "analysis", "recommendation", "change_note"]);

export function ReportContent({ report, onPreview, previewIssues, onSelectCandidate }: {
  report: Report; onPreview?: PreviewHandler; previewIssues?: ReadonlyMap<HTMLButtonElement, string>;
  onSelectCandidate?: (subjectId: string) => void;
}) {
  if (report.status === "stale") {
    return (
      <p className="text-xs text-slate-500">
        这条历史消息的来源已撤回。您可以继续提问。
      </p>
    );
  }

  const evidence = new Map(report.evidence.map((e) => [e.evidence_id, e]));
  const preview = onPreview && {reportId: report.report_id, open: onPreview, issues: previewIssues ?? new Map()};
  const coverage = <p className="text-xs text-slate-500">{formatCoverage(report.coverage)}</p>;
  if (report.clarification_candidates?.length) {
    return (
      <section aria-label="同名学生澄清" className="space-y-2 rounded-xl border border-[#e8dce9] bg-[#fcf9fd] p-3 text-sm">
        <p className="text-[#4a3450]">找到多位同名学生，请选择您要询问的学生。</p>
        <ul className="space-y-1.5">
          {report.clarification_candidates.map((candidate) => (
            <li key={candidate.subject_id} className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <button type="button" className={btnClass("secondary", "sm")} disabled={!onSelectCandidate}
                aria-label={["选择", candidate.display_name, candidate.classroom_name,
                  candidate.has_submission === true ? "已有作业" : candidate.has_submission === false ? "暂无作业" : ""].filter(Boolean).join(" ")}
                onClick={() => onSelectCandidate?.(candidate.subject_id)}>
                {candidate.display_name}
              </button>
              <span className="text-xs text-[#6e5a74]">
                {[candidate.classroom_name, candidate.has_submission === true ? "已有作业" :
                  candidate.has_submission === false ? "暂无作业" : ""].filter(Boolean).join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      </section>
    );
  }
  if (report.statements?.length) {
    const statements = report.statements.filter((statement) => statement.text.trim());
    const paragraphs: typeof statements[] = [];
    for (const statement of statements.filter((item) => bodyRoles.has(item.role))) {
      const paragraph = paragraphs[paragraphs.length - 1];
      const last = paragraph?.[paragraph.length - 1];
      if (!last || (paragraphs.length < 3 && (
        (statement.section_id && statement.section_id !== last.section_id) ||
        (statement.role === "recommendation" && last.role !== "recommendation")
      ))) paragraphs.push([statement]);
      else paragraph.push(statement);
    }
    const renderStatement = (statement: (typeof statements)[number], withGap = false) => (
      <span key={statement.statement_id}>
        {withGap && " "}
        {statement.text.trim()}
        {[...new Set(statement.evidence_refs)].map((ref) => {
          const source = evidence.get(ref);
          return source ? <EvidenceCitation key={ref} e={source} runId={report.classroom.run_id}
            preview={preview} inlineLabel={shortEvidenceLabel(source)} /> :
            <span key={ref} className="ml-1 rounded-full bg-[#f7eaf3] px-2 text-xs text-[#6e5a74]">来源已不可用</span>;
        })}
      </span>
    );
    return (
      <div className="space-y-2.5 text-sm leading-7 text-[#3d2542]">
        {paragraphs.map((paragraph) => (
          <p key={paragraph[0].statement_id}>{paragraph.map((statement, index) => renderStatement(statement, index > 0))}</p>
        ))}
        {statements.filter((item) => item.role === "coverage_note").map((item) => (
          <p key={item.statement_id} className="text-xs text-[#6e5a74]">{renderStatement(item)}</p>
        ))}
        <details className="text-xs text-[#6e5a74]">
          <summary className="cursor-pointer">覆盖范围与完整记录</summary>
          {coverage}
          <pre className="mt-2 max-h-60 overflow-auto rounded-lg bg-[#faf4f9] p-2.5 text-[#4a3450]">{JSON.stringify(report, null, 2)}</pre>
        </details>
      </div>
    );
  }
  const conflicts = new Set(report.conflicts.flatMap((risk) => risk.evidence_refs));
  const citation = (ref: string, compact = false, check = false) => {
    const e = evidence.get(ref);
    return e ? <EvidenceCitation key={ref} e={e} runId={report.classroom.run_id}
      preview={preview} compact={compact} check={check} />
      : <p key={ref} role="alert" className="text-xs text-rose-600">引用已不可用</p>;
  };
  const citations = (refs: string[], check = false) => {
    const unique = [...new Set(refs)];
    if (!unique.length) return null;
    const sources = new Set();
    const primary = [...unique].sort((a, b) => Number(conflicts.has(b)) - Number(conflicts.has(a)))
      .filter((ref) => {
        const e = evidence.get(ref);
        if (!e) return false;
        const key = typeof e.provenance.document_id === "string"
          ? "document:" + e.provenance.document_id : `${e.evidence_type}:${e.source_id}`;
        if (sources.has(key)) return false;
        sources.add(key);
        return true;
      }).slice(0, 3);
    const others = unique.filter((ref) => !primary.includes(ref));
    return (
      <div className="space-y-1.5">
        <div className="flex flex-wrap items-start gap-1.5">{primary.map((ref) => citation(ref, true, check))}</div>
        {!!others.length && (
          <details className="rounded-lg border border-slate-200/80 bg-slate-50/50 px-3 py-2 text-xs">
            <summary className="cursor-pointer font-medium text-[#841f66]">
              {check ? "更多教案段落" : "更多本段依据"}（{others.length}）
            </summary>
            <div className="mt-2 space-y-2">{others.map((ref) => citation(ref, false, check))}</div>
          </details>
        )}
      </div>
    );
  };

  if (report.query_plan?.intent === "cognitive_boundary") {
    const checkRefs = (report.check_evidence_refs ?? []).filter((ref) => evidence.get(ref)?.evidence_type === "teacher_plan");
    const ruleRefs = (report.direct_answer?.evidence_refs ?? report.claims.flatMap((c) => c.evidence_refs))
      .filter((ref) => evidence.get(ref)?.evidence_type === "knowledge_rule");
    const selected = preview && report.query_plan.effective_time_range;
    return (
      <div className="space-y-3 text-sm leading-relaxed text-slate-800">
        <p className="font-medium" data-testid="direct-answer">{report.boundary_text ?? report.answer}</p>
        {report.boundary_text && report.check_text && <p>{report.check_text}</p>}
        {(selected || checkRefs.length > 0) && (
          <section aria-label="核查入口" className="space-y-1.5">
            <h3 className="text-xs font-semibold text-slate-600">核查入口</h3>
            <div className="flex flex-wrap items-start gap-1.5">
              {selected && preview && <SelectedRange preview={preview} />}
              {citations(checkRefs, true)}
            </div>
          </section>
        )}
        {coverage}
        {ruleRefs.length > 0 && (
          <section aria-label="判断依据" className="space-y-1.5">
            <h3 className="text-xs font-semibold text-slate-600">判断依据</h3>
            {citations(ruleRefs)}
          </section>
        )}
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer">查看完整证据、原始模型裁决与运行记录</summary>
          <pre className="mt-2 max-h-60 overflow-auto rounded bg-[#faf4f9] p-2.5 text-[#4a3450]">{JSON.stringify(report, null, 2)}</pre>
        </details>
      </div>
    );
  }
  const texts = report.evidence
    .filter(
      (e) =>
        ["teacher_plan", "teacher_report"].includes(e.evidence_type) &&
        e.content.trim().length > 10,
    )
    .slice(0, 2);

  if (report.narrative?.length) {
    const full = report.output_mode === "report";
    const hasAnalysis =
      report.narrative.some(
        (p) =>
          p.analysis_section === "analysis" || p.analysis_section === "recommendations" ||
          p.heading.startsWith("AI分析") ||
          p.heading === "AI综合分析"||
          p.heading.startsWith("教学建议"),
      ) &&
      ["success", "partial_success"].includes(
        report.narrative_generation?.status || "success",
      );
    const background = (p: NonNullable<typeof report.narrative>[number]) =>
      ["结合教学与学习资料", "结论边界"].includes(p.heading) ||
      (p.heading === "依据与限制"&& !report.conflicts.length);

    return (
      <div className="space-y-3 text-sm leading-relaxed text-slate-800">
        {!full && !hasAnalysis && (
          <>
            <p className="font-medium" data-testid="direct-answer">{report.answer}</p>
            {citations(report.direct_answer?.evidence_refs ?? [])}
          </>
        )}
        {coverage}
        {report.product_scope && (
          <p className="text-xs text-slate-500">
            {report.product_scope.classroom_name}
            {report.product_scope.subject_name
              ? " · 资料对象："+ report.product_scope.subject_name
              : ""}
          </p>
        )}
        {report.narrative_generation?.status === "partial_success"&& (
          <p className="text-xs text-amber-700">
            部分段落未通过证据校验，已保留有效内容。详情中可查看生成记录。
          </p>
        )}
        {report.narrative_generation?.status &&
          /fallback|busy|failed/.test(report.narrative_generation.status) && (
            <p
              className="text-xs text-slate-500"
              role="status"
            >
              本次模型未完成有效回答，以下先展示已有证据整理。您可以稍后重试。
            </p>
          )}

        {report.narrative
          .filter(
            (p) =>
              full ||
              (!["本次复盘范围", "可观察行为"].includes(p.heading) &&
                !background(p)),
          )
          .map((p, i) => (
            <section key={i} className="space-y-1.5">
              {(full ||
                !["AI综合分析", "教学建议与跟进"].includes(p.heading)) && (
                <h3 className="text-sm font-semibold text-slate-900">
                  {p.heading.replace(/^AI分析：/, "")}
                </h3>
              )}
              <p
                className="whitespace-pre-wrap"
                data-testid={(full || hasAnalysis) && i === 0 ? "direct-answer": undefined}
              >
                {p.text}
              </p>
              {citations(p.evidence_refs)}
            </section>
          ))}

        {!full && report.narrative.some(background) && (
          <details className="rounded-lg border border-slate-200/80 bg-slate-50/50 px-3 py-2 text-xs">
            <summary className="cursor-pointer font-medium text-slate-600">
              本轮参考资料与适用范围
            </summary>
            <div className="mt-2 space-y-3">
              {report.narrative.filter(background).map((p, i) => (
                <section key={i} className="space-y-1">
                  <h4 className="font-semibold text-slate-800">
                    {p.heading}
                  </h4>
                  <p className="text-slate-600">{p.text}</p>
                  {citations(p.evidence_refs)}
                </section>
              ))}
            </div>
          </details>
        )}

        <details className="rounded-lg border border-slate-200/70 px-3 py-2 text-xs text-slate-500">
          <summary className="cursor-pointer font-medium">
            查看完整证据、原始模型裁决与运行记录
          </summary>
          <p className="mt-1.5 text-[11px]">
            文字组织不改变原始模型状态，也不将教师补充升级为视频事实。
          </p>
          <pre className="mt-2 max-h-60 overflow-auto rounded bg-slate-950 p-2.5 font-mono text-[10px] text-slate-200">
            {JSON.stringify(report, null, 2)}
          </pre>
        </details>
      </div>
    );
  }

  return (
    <div className="space-y-3 text-sm leading-relaxed text-slate-800">
      {report.product_scope && (
        <p className="text-xs font-medium text-slate-500">
          班级：{report.product_scope.classroom_name}
          {report.product_scope.subject_name
            ? ` · 学生：${report.product_scope.subject_name}`
            : " · 课堂观察报告"}
        </p>
      )}
      <p className="font-medium"data-testid="direct-answer">
        {report.answer}
      </p>

      {citations(report.direct_answer?.evidence_refs ?? [])}
      {coverage}
      {!report.direct_answer && texts.length > 0 && (
        <section aria-label="引用的教学资料"className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            本次引用的教学资料
          </h3>
          {citations(texts.map((e) => e.evidence_id))}
        </section>
      )}

      <p className="text-xs text-slate-500">
        来源范围：{report.classroom.time_range?.join("—")}秒（登记来源全局时间）
        · 单段片段，不代表整节课
      </p>

      {!!report.recognition_records?.length && (
        <section aria-label="行为识别记录"className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            行为识别记录
          </h3>
          {report.recognition_records.map((record, i) => (
            <article
              key={i}
              className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <strong>
                  识别状态：{observationText(record.recognition_state)}
                </strong>
                <span
                  className={badgeClass("info")}
                  title={record.reliability_basis}
                >
                  可靠性：{record.reliability}
                </span>
              </div>
              <p className="text-xs text-slate-500">
                {record.time_label} · {record.source_label}
              </p>
              {citations(record.evidence_refs)}
            </article>
          ))}
          <p className="text-[11px] text-slate-500">
            可靠性是当前模型与证据检查的提示，不是经过测定的识别准确率。教师可在回放中修改记录。
          </p>
        </section>
      )}

      <details
        open={!report.recognition_records?.length}
        className="rounded-lg border border-slate-200/80 p-3 text-xs"
      >
        <summary className="cursor-pointer font-medium text-slate-700">
          分类建议、来源与裁决详情
        </summary>
        <div className="mt-2.5 space-y-2.5">
          {report.claims.map((c) => (
            <article
              key={c.fact_id}
              className="space-y-2 rounded-lg border border-slate-200/80 bg-slate-50/60 p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <StatusChip status={c.final_claim_status} />
                <span className={badgeClass("neutral")}>{c.scope}</span>
              </div>
              <p className="text-slate-800">{c.statement}</p>
              <p className="text-[11px] text-slate-500">
                主体范围：{c.subject_ref || "来源或区域"}；时间：
                {c.time_scope?.join("—") || "未给出"}；只有所述作用范围得到支持。
              </p>
              {citations(c.evidence_refs)}
            </article>
          ))}
        </div>
      </details>

      {report.conflicts.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            识别分歧与核对事项
          </h3>
          {report.conflicts.map((r, i) => (
            <div
              key={i}
              className="rounded-lg border border-amber-200 bg-amber-50/80 p-3 text-xs text-amber-900"
            >
              <strong className="block">{r.kind}</strong>
              <p className="mt-0.5">{r.reason}</p>
              {citations(r.evidence_refs)}
            </div>
          ))}
        </section>
      )}

      {report.review_queue.length > 0 && (
        <details className="rounded-lg border border-slate-200/80 p-3 text-xs">
          <summary className="cursor-pointer font-medium">
            可靠性说明与需核对记录（{report.review_queue.length}项）
          </summary>
          <div className="mt-2 space-y-2">
            {report.review_queue.map((r, i) => (
              <div
                key={i}
                className="rounded-lg bg-slate-50 p-2.5"
              >
                <p>{r.reason}</p>
                <details className="mt-1 text-[11px] text-slate-500">
                  <summary>原始风险标记</summary>
                  {r.kind}
                </details>
              </div>
            ))}
          </div>
        </details>
      )}

      <div className="space-y-1">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          分析限制
        </h3>
        <ul className="list-disc space-y-1 pl-4 text-xs text-slate-600">
          {report.limitations.map((l, i) => (
            <li key={i}>{l}</li>
          ))}
        </ul>
      </div>

      <details className="rounded-lg border border-slate-200/70 px-3 py-2 text-xs text-slate-500">
        <summary className="cursor-pointer font-medium">
          完整Agent执行轨迹、原始状态和证据版本
        </summary>
        <pre className="mt-2 max-h-60 overflow-auto rounded bg-slate-950 p-2.5 font-mono text-[10px] text-slate-200">
          {JSON.stringify(report, null, 2)}
        </pre>
      </details>
    </div>
  );
}
